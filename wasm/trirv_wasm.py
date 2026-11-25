#!/usr/bin/env python3
"""trirv_wasm —— Wasm 栈式字节码 → TriRV 汇编（路径 A 最小切片）。

定位
    这是"Wasm 栈机 → 寄存器机"的翻译层，用来验证"三进制能不能跑栈式字节码"本身。
    不做生态、不做完整规范：只覆盖 i32 整数、控制流、线性内存、主机调用。

内存模型
    TriRV 内存按 tryte(5 trit) 编址，Wasm 按 byte(8 bit) 编址：
        byte 有 256 个值，tryte 有 243 个 —— 2^8 与 3^5 不同，这是定义层差异，不是亏损
    采用"1 wasm byte = 1 tryte"，wasm 地址 == TriRV 地址（零偏移），目的是寻址零换算。
    值域对照见下表；真正的成本不在"位"，而在"为模拟 wasm 的 mod 2^n 语义要多花几条指令"：

        类型    本切片定义            保真成本（按指令）
        i8      1 tryte（5 trit）     store/load 各一次边界判定
        i16     未支持                需显式 mod 2^16
        i32     45 trit + 显式规整     add/sub 各 2 条、mul 11 条
        i64     未支持                45 trit 装得下，回绕另计

    2^32 不是 3 的幂，任何三进制字宽都无法无损表示 i32 全域；所以这里没有"少几个值"
    的亏损账，只有"要不要花指令模拟 mod 2^32"的选择。

帧布局
    fp = s0：
        fp + i*9            : 局部变量 i（形参 0..nparams-1 也在其中）
        fp + (nlocals+j)*9  : 操作数栈槽 j（仅在溢出寄存器池时才真正访存）
    操作数栈槽按"静态高度"寻址，运行时不动 sp —— 高度在翻译期就算得出来。

寄存器约定
    s0=fp   ra   sp
    t0-t6   : 操作数栈驻留池（槽 j 的规范寄存器就是 t{j}，j < 7）
    s1-s8   : 指令内部临时（wrap / 比较 / 位拆分专用，绝不与驻留池冲突）
    s9=-2^31  s10=2^32  s11=2^31（_start 装好，只读）
    控制流汇合点（block/loop/if/else/end/br/call/return）前把驻留值全部写回内存，
    因此每个标签处的栈状态都是"全在内存"，分支合流无歧义。

用法
    python3 trirv_wasm.py foo.wasm foo.trv [foo.s]
"""
import struct
import sys

import trirv_float as tf               # P3：编译期 IEEE→TF 穿透的真相源

WASM_MEM_SIZE = 262144          # wasm 线性内存占 TriRV 地址 [0, 262144)
TABLE_BASE     = 196608          # 函数指针表区（每表一段：基址顺序分配，每项
                                 # 1 word=&函数地址；BOOT 装载）
TYPE_BASE      = 229376          # 类型哨兵区基址（每表 TYPE_STRIDE 一段）
TYPE_STRIDE    = 4096            # 类型哨兵区每表步长（1 tryte/项；0=null 哨兵，
                                 # 真类型 = typeidx+1 存储——typeidx 0 合法不可作哨兵）
RESULT_ADDR    = 180             # _start 把入口返回值 SW 到这里（9 tryte 全宽，
                                 # 200 以下是自由区：0-26 零页保留、数据段 200 起）
GLOBAL_BASE   = 262144          # 全局变量区
STACK_BASE    = 270336          # 栈起点（向上增长）
WORD          = 9               # 1 word = 9 tryte
MOD45         = 3 ** 45

I31, I32, NI31 = 2**31, 2**32, -(2**31)

# P3 浮点常量池：IEEE 位模式在编译期穿透成 TF raw，落到这段数据区。
# 放在 P2 表（266240+32*9=266528）之后、栈起点（270336）之前，互不重叠。
FCONST_BASE = 267000
# 浮点指令内部临时（f0/f1/f2），与整数临时 s3-s8 分属两个寄存器堆。
# 浮点值不驻留栈槽：每次 FLW 进临时、算完 FSW 回槽（freg 堆无 ADDI 零成本搬移）。
FT0, FT1, FT2 = "f0", "f1", "f2"
F32, F64 = 0x7D, 0x7C                  # wasm valtype 字节

POOL = ("t0", "t1", "t2", "t3", "t4", "t5", "t6")   # 操作数栈驻留池，槽 j -> t{j}
LREG = ("s1", "s2")                                  # 局部变量驻留（仅 leaf 函数）
DEEP = "s5"                                          # 栈深超过池容量时的借用寄存器


class Unsupported(Exception):
    """本切片未实现的特性：显式报错，绝不静默生成错码。"""


# ---------------------------------------------------------------- LEB128
def uleb(b, p):
    r = s = 0
    while True:
        x = b[p]; p += 1
        r |= (x & 0x7F) << s
        if not x & 0x80:
            return r, p
        s += 7


def sleb(b, p):
    r = s = 0
    while True:
        x = b[p]; p += 1
        r |= (x & 0x7F) << s
        s += 7
        if not x & 0x80:
            if x & 0x40:
                r -= 1 << s
            return r, p


def vec(b, p, fn):
    n, p = uleb(b, p)
    out = []
    for _ in range(n):
        v, p = fn(b, p)
        out.append(v)
    return out, p


def name_str(b, p):
    n, p = uleb(b, p)
    return bytes(b[p:p + n]).decode("utf-8", "replace"), p + n


# ---------------------------------------------------------------- 模块解析
def parse_module(data):
    if data[:4] != b"\0asm":
        raise ValueError("不是 wasm 文件（magic 不符）")
    if struct.unpack("<I", data[4:8])[0] != 1:
        raise ValueError("只支持 wasm version 1")
    m = {"types": [], "imports": [], "funcs": [], "globals": [],
         "exports": {}, "codes": [], "datas": [], "mem_pages": 0}
    p = 8
    while p < len(data):
        sid = data[p]; p += 1
        size, p = uleb(data, p)
        end = p + size

        if sid == 1:
            def ft(b, q):
                if b[q] != 0x60:
                    raise ValueError("非函数类型")
                q += 1
                ps, q = vec(b, q, lambda b2, q2: (b2[q2], q2 + 1))
                rs, q = vec(b, q, lambda b2, q2: (b2[q2], q2 + 1))
                return (ps, rs), q
            m["types"], p = vec(data, p, ft)

        elif sid == 2:
            def im(b, q):
                mod, q = name_str(b, q)
                fld, q = name_str(b, q)
                k = b[q]; q += 1
                if k == 0:
                    t, q = uleb(b, q)
                    return ("func", mod, fld, t), q
                if k == 1:
                    q += 1
                    fl, q = uleb(b, q); mn, q = uleb(b, q)
                    if fl & 1:
                        _, q = uleb(b, q)
                    return ("table", mod, fld, None), q
                if k == 2:
                    fl, q = uleb(b, q); mn, q = uleb(b, q)
                    if fl & 1:
                        _, q = uleb(b, q)
                    return ("mem", mod, fld, mn), q
                if k == 3:
                    q += 2
                    return ("global", mod, fld, None), q
                raise Unsupported(f"未知 import kind {k}")
            m["imports"], p = vec(data, p, im)

        elif sid == 3:
            m["funcs"], p = vec(data, p, lambda b, q: uleb(b, q))

        elif sid == 4:                                   # table：elemtype + limits
            def tb(b, q):
                et = b[q]; q += 1                        # 0x70=funcref / 0x6F=externref
                fl, q = uleb(b, q); mn, q = uleb(b, q)
                mx = None
                if fl & 1:
                    mx, q = uleb(b, q)
                return (et, mn, mx), q
            m["tables"], p = vec(data, p, tb)

        elif sid == 5:
            def mem(b, q):
                fl, q = uleb(b, q); mn, q = uleb(b, q)
                if fl & 1:
                    _, q = uleb(b, q)
                return mn, q
            lims, p = vec(data, p, mem)
            m["mem_pages"] = lims[0] if lims else 0

        elif sid == 6:
            def gl(b, q):
                vt = b[q]; q += 1
                mut = b[q]; q += 1
                if b[q] != 0x41:
                    raise Unsupported("全局变量只支持 i32 常量初值")
                q += 1
                v, q = sleb(b, q)
                if b[q] != 0x0B:
                    raise Unsupported("全局变量初值表达式不完整")
                return (vt, mut, v), q + 1
            m["globals"], p = vec(data, p, gl)

        elif sid == 7:
            def ex(b, q):
                nm, q = name_str(b, q)
                k = b[q]; q += 1
                idx, q = uleb(b, q)
                return (nm, k, idx), q
            exs, p = vec(data, p, ex)
            for nm, k, idx in exs:
                m["exports"].setdefault(k, {})[nm] = idx

        elif sid == 8:
            _, p = uleb(data, p)

        elif sid == 9:
            # element 段（9.2 call_indirect/table）：active 段落到 TABLE_BASE，
            # declarative(3) 跳过；elemexpr 形只认 ref.func / ref.null
            elems = []
            n_el, p = uleb(data, p)
            for _ in range(n_el):
                fl, p = uleb(data, p)
                if fl in (0, 4):                          # 表 0：offset expr + funcidx 系
                    tidx = 0
                elif fl in (2, 6):                        # tableidx + offset + ...
                    tidx, p = uleb(data, p)
                elif fl in (1, 3, 5, 7):
                    tidx = None                           # declarative/无 offset 形
                else:
                    raise Unsupported(f"element flags {fl} 未实现")
                if fl in (0, 2, 4, 6):                    # offset expr（i32.const ... end）
                    op = data[p]
                    if op == 0x41:
                        p += 1
                        off, p = sleb(data, p)
                    else:
                        raise Unsupported("element offset 只支持 i32.const")
                    op = data[p]; p += 1                  # end 0x0B
                    if op != 0x0B:
                        raise Unsupported("element offset expr 未闭合")
                if fl in (1, 2, 3, 5, 6, 7):              # elemkind 字节（0x00=funcref）
                    if data[p] != 0x00:
                        raise Unsupported(f"element kind 0x{data[p]:02x} 未实现（只支持 funcref）")
                    p += 1
                if fl in (1, 3, 5, 7):                    # 无 offset：跳过 funcidx 表
                    n, p = uleb(data, p)
                    if fl in (5, 7):
                        for _ in range(n):
                            k = data[p]; p += 1
                            if k == 0xD2:
                                _, p = uleb(data, p)
                            elif k != 0xD0:
                                raise Unsupported("element expr 未实现")
                            p += 1                        # end
                    else:
                        p += n
                    continue
                fis = []
                if fl in (4, 6):                          # elemexpr 形：每条是一个 expr
                    n, p = uleb(data, p)
                    for _ in range(n):
                        k = data[p]; p += 1
                        if k == 0xD2:                     # ref.func idx end
                            fi, p = uleb(data, p)
                            fis.append(fi)
                        elif k == 0xD0:                   # ref.null ht end
                            p += 1
                            fis.append(None)
                        else:
                            raise Unsupported("element expr 只支持 ref.func/ref.null")
                        if data[p] != 0x0B:
                            raise Unsupported("element expr 未闭合")
                        p += 1
                else:                                     # 0/2：裸 funcidx 表
                    fis, p = vec(data, p, lambda b, q: uleb(b, q))
                elems.append([tidx, off, fis])
            m["elems"] = elems

        elif sid == 10:
            def co(b, q):
                sz, q = uleb(b, q)
                e = q + sz
                groups, q2 = uleb(b, q)
                locs = []
                for _ in range(groups):
                    n, q2 = uleb(b, q2)
                    t = b[q2]; q2 += 1
                    locs.append((n, t))
                return (locs, bytes(b[q2:e])), e
            m["codes"], p = vec(data, p, co)

        elif sid == 11:
            def da(b, q):
                _, q = uleb(b, q)
                if b[q] != 0x41:
                    raise Unsupported("data 段只支持 i32.const 偏移")
                q += 1
                off, q = sleb(b, q)
                if b[q] != 0x0B:
                    raise Unsupported("data 段偏移表达式不完整")
                q += 1
                n, q = uleb(b, q)
                return (off, bytes(b[q:q + n])), q + n
            m["datas"], p = vec(data, p, da)

        p = end
    return m


# ---------------------------------------------------------------- 代码生成
# 叶子指令的立即数跳过表（用于死代码），返回新的 p
def skip_imm(b, p, op):
    if op == 0x11:
        _, p = uleb(b, p)                     # typeidx
        _, p = uleb(b, p)                     # tableidx
        return p
    if op in (0x0C, 0x0D, 0x10, 0x20, 0x21, 0x22, 0x23, 0x24):
        _, p = uleb(b, p)
    elif op == 0x0E:
        n, p = uleb(b, p)
        for _ in range(n + 1):
            _, p = uleb(b, p)
    elif 0x28 <= op <= 0x3E:
        _, p = uleb(b, p)
        _, p = uleb(b, p)
    elif op == 0x41:
        _, p = sleb(b, p)
    elif op == 0x42:
        _, p = sleb(b, p)
    elif op == 0x43:
        p += 4
    elif op == 0x44:
        p += 8
    elif op in (0x3F, 0x40):
        p += 1
    return p


def walk_instrs(body):
    """按指令边界遍历，产出 (操作码, 立即数游标)。结构指令单独吃掉 blocktype 字节。"""
    p, n = 0, len(body)
    while p < n:
        op = body[p]
        p += 1
        yield op, p
        if op in (0x02, 0x03, 0x04):
            p += 1
        else:
            p = skip_imm(body, p, op)


def has_call(body):
    return any(op in (0x10, 0x11) for op, _ in walk_instrs(body))


def pick_lreg(body, nlocals, lvtype=None):
    """挑最热的局部变量给 s1/s2：按 local.set/tee 次数排序（只写的才是热的）。
    P3：浮点局部变量不驻留整数寄存器（LVREG 只给 i32），直接排除。"""
    cnt = {}
    for op, p in walk_instrs(body):
        if op in (0x21, 0x22):
            i, _ = uleb(body, p)
            if i < nlocals and (lvtype is None or lvtype[i] == 0x7F):
                cnt[i] = cnt.get(i, 0) + 1
    hot = sorted(cnt, key=lambda k: (-cnt[k], k))
    return {i: LREG[j] for j, i in enumerate(hot[:len(LREG)])}


def peek_lset(b, p):
    """若 p 处是 local.set/tee，返回 (局部变量号, 该指令之后的游标)。"""
    if p < len(b) and b[p] in (0x21, 0x22):
        i, q = uleb(b, p + 1)
        return i, q
    return None


class Gen:
    def __init__(self, mod):
        self.mod = mod
        self.lines = []
        self.uid = 0
        self.frames = []                 # [(行号, 帧字节数)]
        self.used_table = False          # 9.2：用到 element 初始化 -> BOOT 装 表+类型
        # 多表（9.3 解堵）：每表 = 表区 [tbase, tbase+sz*9) + 类型区 TYPE_BASE+k*TYPE_STRIDE
        # 表尺寸 = max(声明 min, elem 覆盖)；运行期越界界 = 该表尺寸
        tabs = mod.get("tables") or []
        self.tbases = []
        self.tsizes = []
        cur = TABLE_BASE
        for (et, mn, mx) in tabs:
            sz = max(mn, 1)
            self.tbases.append(cur)
            self.tsizes.append(sz)
            cur = cur + sz * WORD
        self.tab_max = self.tsizes[0] if self.tsizes else 0
        self.used_float = False          # P3：用到浮点 -> 需要浮点常量池段
        self.fconsts = []                # P3：[(addr, raw)] 编译期穿透的 TF 常量
        self.nimp = sum(1 for i in mod["imports"] if i[0] == "func")
        self.ftype = []
        self.callee = {}
        for i, (k, modn, fld, t) in enumerate(mod["imports"]):
            if k != "func":
                continue
            self.ftype.append(mod["types"][t])
            if fld == "print":
                self.callee[len(self.ftype) - 1] = "__host_print"
            else:
                raise Unsupported(f"未实现的导入函数 {modn}.{fld}")
        for t in mod["funcs"]:
            self.ftype.append(mod["types"][t])

    # -- 基础设施 --------------------------------------------------
    def L(self, s=""):
        self.lines.append(s)

    def lab(self, p="L"):
        self.uid += 1
        return f"__{p}_{self.uid}"

    def li(self, rd, v):
        """45-trit 常数装入：小值 ADDI，大值走平衡 2187 进制展开。"""
        if -1093 <= v <= 1093:
            self.L(f"    ADDI {rd}, zero, {v}")
            return
        digits, x = [], v
        while x:
            d = x % 2187
            if d > 1093:
                d -= 2187
            digits.append(d)
            x = (x - d) // 2187
        # 首位必须是 ADDI：LUI 自带一次 ×3^7，会让整体多乘一档
        self.L(f"    ADDI {rd}, zero, {digits[-1]}")
        for d in reversed(digits[:-1]):
            self.L(f"    SLLI {rd}, {rd}, 7")
            if d:
                self.L(f"    ADDI {rd}, {rd}, {d}")

    def jmp(self, t):
        self.L(f"    JAL zero, {t}")

    def jif(self, reg, t):
        """reg != 0 跳远处目标。条件分支只跨 1 条，规避 B 型 ±1093 限制。"""
        near = self.lab("n")
        self.L(f"    BEQ {reg}, zero, {near}")
        self.L(f"    JAL zero, {t}")
        self.L(f"{near}:")

    def jnot(self, reg, t):
        near = self.lab("n")
        self.L(f"    BNE {reg}, zero, {near}")
        self.L(f"    JAL zero, {t}")
        self.L(f"{near}:")

    # -- 栈槽 ------------------------------------------------------
    # 槽 j（j<7）的规范位置就是 t{j}；vs[j]=True 表示值当前只活在寄存器里，
    # 内存槽是"脏"的。控制流汇合点调用 spill_all() 后，栈状态回到纯内存。
    def slot(self, i):
        return (self.nlocals + i) * WORD

    def reg_of(self, i):
        return POOL[i] if i < len(POOL) else None

    def alloc(self, i=None):
        """即将压入槽 i 时要写入的寄存器。深槽借用 DEEP（不驻留）。"""
        if i is None:
            i = self.h
        r = self.reg_of(i)
        return r if r is not None else DEEP

    def get(self, i, borrow="s3"):
        """取槽 i 的可写寄存器；值若在局部变量寄存器里，先复制到 t{i} 再返回。"""
        r = self.vs.get(i)
        if r is not None and r not in LREG:
            return r
        rr = self.reg_of(i)
        if rr is None:
            self.L(f"    LW {borrow}, {self.slot(i)}(s0)")
            return borrow
        if r is not None:
            self.L(f"    ADDI {rr}, {r}, 0")      # 别名 -> 物化成可写副本
        else:
            self.L(f"    LW {rr}, {self.slot(i)}(s0)")
        self.vs[i] = rr
        return rr

    def read(self, i, borrow="s4"):
        """只读取槽 i 的值所在寄存器，可能是局部变量寄存器（不可写，零搬移）。"""
        r = self.vs.get(i)
        if r is not None:
            return r
        rr = self.reg_of(i)
        if rr is not None:
            self.L(f"    LW {rr}, {self.slot(i)}(s0)")
            self.vs[i] = rr
            return rr
        self.L(f"    LW {borrow}, {self.slot(i)}(s0)")
        return borrow

    def writable(self, i):
        """取一个可以无条件覆盖的寄存器；旧值不需要保留（比较类指令用）。"""
        r = self.vs.get(i)
        if r is not None and r not in LREG:
            return r
        rr = self.reg_of(i)
        if rr is None:
            return "s3"
        self.vs.pop(i, None)
        return rr

    def set(self, i, src=None):
        """声明槽 i 的值由 src 持有；src 不在规范寄存器上就补一条搬移。"""
        r = self.reg_of(i)
        if r is None:
            if src is not None:
                self.L(f"    SW {src}, {self.slot(i)}(s0)")
            self.vs.pop(i, None)
            return
        if src is not None and src != r:
            self.L(f"    ADDI {r}, {src}, 0")
        self.vs[i] = r

    def kill(self, i):
        self.vs.pop(i, None)

    def spill_all(self):
        """驻留值全部写回内存；之后栈状态 = 全在内存，任何合流点都安全。"""
        for i in sorted(self.vs):
            r = self.vs.get(i)
            if r:
                self.L(f"    SW {r}, {self.slot(i)}(s0)")
        self.vs.clear()

    def push(self, r):
        i = self.h
        self.h += 1
        self.maxh = max(self.maxh, self.h)
        self.vs.pop(i, None)
        pr = self.reg_of(i)
        if r in LREG or pr == r:
            self.vs[i] = r                # 已在合适寄存器上，压栈零指令
            return
        self.L(f"    SW {r}, {self.slot(i)}(s0)")

    def pop(self, rd):
        """弹栈到 rd。坑实录（as-br_if-value 实证）：rd 若与下层驻留值的
        寄存器同名，ADDI 复制会覆写它——借位自动换安全寄存器并返回实际
        寄存器名（调用方按返回值发后续分支）。"""
        self.h -= 1
        if self.h < 0:
            raise Unsupported("操作数栈下溢（模块未通过校验？）")
        i = self.h
        r = self.vs.get(i)
        live = [v for k, v in self.vs.items() if k != i and v]
        if r:
            self.vs.pop(i, None)
            if rd != r:
                if rd in live:
                    for cand in ("s3", "s4", "s5", "t0", "t1", "t2"):
                        if cand not in live:
                            rd = cand
                            break
                self.L(f"    ADDI {rd}, {r}, 0")
            elif rd in live:
                for cand in ("s3", "s4", "s5", "t0", "t1", "t2"):
                    if cand not in live:
                        self.L(f"    ADDI {cand}, {r}, 0")
                        return cand
            return rd
        self.L(f"    LW {rd}, {self.slot(i)}(s0)")
        self.vs.pop(i, None)
        return rd

    def pop_val(self, spare="s5"):
        """弹栈并返回"值已在哪个寄存器里"，驻留或别名时零指令。"""
        self.h -= 1
        i = self.h
        r = self.vs.get(i)
        if r:
            self.vs.pop(i, None)
            return r
        self.L(f"    LW {spare}, {self.slot(i)}(s0)")
        self.vs.pop(i, None)
        return spare

    # -- i32 语义辅助 ----------------------------------------------
    # 注意：内部临时一律用 s7/s8/s6，绝不用 t0-t6 —— 那是操作数栈的驻留池。
    def eqz(self, rd, rs):
        """rd = (rs == 0) ? 1 : 0。三进制没有二进制位运算，(x<1)-(x<0) 才正确。"""
        self.L(f"    SLTI s7, {rs}, 1")
        self.L(f"    SLTI s8, {rs}, 0")
        self.L(f"    SUB {rd}, s7, s8")

    def not_(self, rd, rs):
        """布尔取反（rs 必须是 0/1）。"""
        self.L(f"    SLTI {rd}, {rs}, 1")

    def _br_copy(self, keep, newh):
        """br 系携带值：当前栈顶 [h-keep, h) → 标签槽 [newh-keep, newh)。"""
        for i in range(keep):
            if self.slot(self.h - keep + i) != self.slot(newh - keep + i):
                self.L(f"    LW t0, {self.slot(self.h - keep + i)}(s0)")
                self.L(f"    SW t0, {self.slot(newh - keep + i)}(s0)")

    def blocktype(self, b, p):
        """blocktype → ((参数类型列), (结果类型列)), 新游标。0x40=空；
        valtype 单结果；s33 正值=类型空间索引（multi-value 块，9.2 解堵面）。"""
        bt = b[p]
        if bt == 0x40:
            return ((), ()), p + 1
        if bt in (0x7F, 0x7E, 0x7D, 0x7C, 0x7B, 0x70, 0x6F):
            return ((), (bt,)), p + 1
        tidx, p = sleb(b, p)
        if tidx < 0 or tidx >= len(self.ftype):
            raise ValueError(f"blocktype 类型索引越界 {tidx}")
        return self.ftype[tidx], p

    def addr_off(self, r, offset):
        if offset == 0:
            return
        self.li("s6", offset)
        self.L(f"    ADD {r}, {r}, s6")

    # -- 浮点（P3）：编译期穿透 + 栈槽类型 ----------------------------
    def fconst_raw(self, raw):
        """把 TF raw 登记进浮点常量池，返回它的 tryte 地址。"""
        addr = FCONST_BASE + len(self.fconsts) * WORD
        self.fconsts.append((addr, raw))
        self.used_float = True
        return addr

    def fl_const(self, ieee_bytes, vt):
        """编译期穿透：IEEE 位模式 → host double → TF raw → FLW 到 FT0。
        IEEE 只活在 wasm 字节码的编码里；运行时 VM 只见 TF raw，不执行二进制语义。"""
        if vt == F32:
            v = struct.unpack("<f", ieee_bytes)[0]
            fmt = tf.TF20
            mn = "FLW.S"
        else:
            v = struct.unpack("<d", ieee_bytes)[0]
            fmt = tf.TF45
            mn = "FLW"
        raw = fmt.encode(v)
        addr = self.fconst_raw(raw)
        # 常量地址可能超出 I 型 7-trit 立即数范围，用 li 装 s6 再 FLW 0(s6)
        self.li("s6", addr)
        self.L(f"    {mn} {FT0}, 0(s6)")
        return FT0

    def f_arith(self, op_name, vt):
        """浮点二元算术：栈 [a,b] → [a op b]。两操作数 FLW 进 f0/f1，结果写回槽 i0。"""
        self.h -= 2
        i0, i1 = self.h, self.h + 1
        self.kill(i1)
        # 档位助记符：TF45 用无后缀，TF20 用 .S
        mn = op_name if vt == F64 else op_name + ".S"
        self.L(f"    FLW {FT0}, {self.slot(i0)}(s0)")
        self.L(f"    FLW {FT1}, {self.slot(i1)}(s0)")
        self.L(f"    {mn} {FT2}, {FT0}, {FT1}")
        self.L(f"    FSW {FT2}, {self.slot(i0)}(s0)")
        self.set(i0)                       # 该槽值已在内存（FSW 完成），标记非驻留
        self.vtype[i0] = vt
        self.h += 1

    def f_sqrt(self, vt):
        """浮点开方：栈 [a] → [sqrt(a)]。"""
        self.h -= 1
        i0 = self.h
        mn = "FSQRT" if vt == F64 else "FSQRT.S"
        self.L(f"    FLW {FT0}, {self.slot(i0)}(s0)")
        self.L(f"    {mn} {FT1}, {FT0}")
        self.L(f"    FSW {FT1}, {self.slot(i0)}(s0)")
        self.set(i0); self.vtype[i0] = vt
        self.h += 1

    def f_cmp(self, wasm_op, vt):
        """浮点比较：栈 [a,b] → [i32 0/1]。
        wasm 六个比较按组偏移 k=op-base 排列：0=eq 1=ne 2=lt 3=gt 4=le 5=ge。
        TriRV 只有 FEQ/FLT/FLE：ne=eq取反，gt=lt交换操作数，ge=le交换操作数。
        FEQ/FLT/FLE 把结果写整数 rd；用 s3 接住再走整数栈的 set。"""
        base = 0x5B if vt == F32 else 0x61
        k = wasm_op - base
        base_mn = ("FEQ", "FEQ", "FLT", "FLT", "FLE", "FLE")[k]
        swap = k in (3, 5)                 # gt/ge：交换成 lt/le
        neg = k == 1                       # ne：eq 后取反
        mn = base_mn if vt == F64 else base_mn + ".S"
        i0, i1 = self.h - 2, self.h - 1
        a_slot = i1 if swap else i0
        b_slot = i0 if swap else i1
        self.h -= 2
        self.L(f"    FLW {FT0}, {self.slot(a_slot)}(s0)")
        self.L(f"    FLW {FT1}, {self.slot(b_slot)}(s0)")
        self.L(f"    {mn} s3, {FT0}, {FT1}")
        if neg:
            self.not_("s3", "s3")
        self.kill(i1)
        self.set(i0, "s3")
        self.vtype[i0] = 0x7F              # 比较结果是 i32
        self.h += 1

    def f_load(self, i, vt):
        """栈地址槽 → 浮点结果槽：FLW freg, 0(s3)（s3 已是有效地址）。
        结果 FSW 回槽 i（不驻留），后续浮点指令重新 FLW。"""
        mn = "FLW" if vt == F64 else "FLW.S"
        self.L(f"    {mn} {FT0}, 0(s3)")
        self.L(f"    FSW {FT0}, {self.slot(i)}(s0)")
        self.vtype[i] = vt

    # -- 9.4′ sign/trit 族（TF raw 整数面，符号 trit 权重 3^(T-1)）----
    def f_absneg(self, op, vt):
        """abs/neg：归一化到无符号域（+MOD45 if 负）→ 剥/加符号 trit（T1）。"""
        T1 = 3 ** 44 if vt == F64 else 3 ** 19
        MOD45 = 3 ** 45
        self.h -= 1
        i0 = self.h
        self.L(f"    LW t0, {self.slot(i0)}(s0)")
        self.li("s6", T1)
        self.li("t6", MOD45)
        self.L("    ADD s3, t0, s6")               # 探针：+T1 溢出负 → s=1
        self.L("    SLT t2, s3, zero")
        sk = self.lab("nm")
        self.L(f"    BNE t2, zero, {sk}")
        self.L("    ADD t0, t0, t6")               # 归一化（无符号 raw）
        self.L(f"{sk}:")
        if op in (0x8B, 0x99):                     # abs：raw − T1
            self.L("    SUB t0, t0, s6")
        else:                                      # neg：raw + T1 mod 3^45
            self.L("    ADD t0, t0, s6")
        self.L(f"    SW t0, {self.slot(i0)}(s0)")
        self.vs.pop(i0, None)
        self.vtype[i0] = vt
        self.h += 1

    def f_copysign(self, vt):
        """copysign：mag = |a|；号 = b（归一化探测）。"""
        T1 = 3 ** 44 if vt == F64 else 3 ** 19
        MOD45 = 3 ** 45
        self.h -= 2
        ia, ib = self.h, self.h + 1
        self.L(f"    LW t0, {self.slot(ia)}(s0)")
        self.L(f"    LW t1, {self.slot(ib)}(s0)")
        self.li("s6", T1)
        self.li("t6", MOD45)
        self.L("    ADD s3, t0, s6")
        self.L("    SLT t2, s3, zero")
        sk = self.lab("cp")
        self.L(f"    BNE t2, zero, {sk}")
        self.L("    ADD t0, t0, t6")
        self.L(f"{sk}:")
        self.L("    SUB t0, t0, s6")               # mag_a
        self.L("    ADD s3, t1, s6")               # b 探针
        self.L("    SLT t2, s3, zero")
        negp = self.lab("cp")
        self.L(f"    BNE t2, zero, {negp}")
        self.L(f"    JAL zero, {negp}d")
        self.L(f"{negp}:")
        self.L("    ADD t0, t0, s6")               # 号 1
        self.L(f"{negp}d:")
        self.L(f"    SW t0, {self.slot(ia)}(s0)")
        self.set(ia)
        self.vtype[ia] = vt
        self.h += 1

    def f_minmax(self, op, vt):
        """min/max：NaN（幅 > INF_POS）传染 canon；FEQ 等值 → TOR/TAND
        （min 保 −0 / max 保 +0）；FLT 数值选边（原 raw 直传，符号随行）。"""
        is_min = op in (0x96, 0xA4)
        T1 = 3 ** 44 if vt == F64 else 3 ** 19
        EMAX = 59048 if vt == F64 else 242
        MB = 34 if vt == F64 else 14
        INF_POS = EMAX * (3 ** MB)
        NANR = INF_POS + 1
        self.h -= 2
        i0, i1 = self.h, self.h + 1
        self.L(f"    LW t0, {self.slot(i0)}(s0)")   # orig a
        self.L(f"    LW t1, {self.slot(i1)}(s0)")   # orig b
        self.li("s6", T1)
        self.li("t6", 3 ** 45)
        self.li("s5", INF_POS)
        # mag_a → s3（s8 不用；t0/t1 保全 raw）
        self.L("    ADD s3, t0, s6")
        self.L("    SLT t2, s3, zero")
        n1 = self.lab("nm")
        self.L(f"    BNE t2, zero, {n1}")
        self.L("    ADD s3, s3, t6")
        self.L(f"{n1}:")
        self.L("    SUB s3, s3, s6")
        # mag_b → s4
        self.L("    ADD s4, t1, s6")
        self.L("    SLT t2, s4, zero")
        n2 = self.lab("nm")
        self.L(f"    BNE t2, zero, {n2}")
        self.L("    ADD s4, s4, t6")
        self.L(f"{n2}:")
        self.L("    SUB s4, s4, s6")
        # NaN 探测：幅 > INF_POS
        self.L("    SLT t5, s5, s3")
        self.L("    SLT t4, s5, s4")
        self.L("    TOR t5, t5, t4")
        # 数值比较（TF 原生 FEQ/FLT：±0 等值 ✓ NaN 全假 ✓）
        self.L(f"    FLW {FT0}, {self.slot(i0)}(s0)")
        self.L(f"    FLW {FT1}, {self.slot(i1)}(s0)")
        self.L(f"    FEQ t2, {FT0}, {FT1}")
        self.L(f"    FLT t3, {FT0}, {FT1}")
        self.L(f"    FLT t4, {FT1}, {FT0}")
        merge = "TOR" if is_min else "TAND"         # min 保 −0 / max 保 +0
        nn = self.lab("nn")
        eq = self.lab("eq")
        lt = self.lab("lt")
        gt = self.lab("gt")
        wr = self.lab("wr")
        self.L(f"    BNE t5, zero, {nn}")
        self.L(f"    BNE t2, zero, {eq}")
        self.L(f"    BNE t3, zero, {lt}")
        self.L(f"    BNE t4, zero, {gt}")
        self.L(f"{eq}:")                             # 等值入口（±0/全等：号合并）
        self.L(f"    {merge} t0, t0, t1")           # min=TOR 保 −0 / max=TAND 保 +0
        self.L(f"    JAL zero, {wr}")
        self.L(f"{nn}:")
        self.li("t0", NANR)
        self.L(f"    JAL zero, {wr}")
        self.L(f"{lt}:")                             # t0 = orig a
        self.L(f"    JAL zero, {wr}")
        self.L(f"{gt}:")
        self.L("    ADD t0, t1, zero")               # orig b
        self.L(f"{wr}:")
        self.L(f"    SW t0, {self.slot(i0)}(s0)")
        self.vs.pop(i0, None)
        self.vtype[i0] = vt
        self.h += 1

    # -- 二进制视图 --------------------------------------------------
    # 三进制寄存器里**不存在二进制位**。第 k 位只能这样取：
    #     残差 r 从值本身出发，拿 2^k 逐个比；r >= 2^k 该位为 1，同时 r -= 2^k。
    # 这就是二进制长除法的展开式，但一次 DIV 都不用 —— 本机最省的拆位方式。
    # 临时只用 s3-s8，s1/s2 是局部变量驻留，不可碰。
    def _bit_of(self, k, res, bit, tag):
        """剥出残差 res 的第 k 位 -> bit（0/1）。s7 = 2^k，s3..s8 之外不碰。

        必须从高位往低位走：剥掉第 k 位后残差 < 2^k，下一位的比较才有意义。
        反过来（低位先走）要先把低位移出去，那就得除法了。"""
        skip = self.lab(tag)
        self.L(f"    LW s7, {k * WORD}(gp)")
        self.L(f"    SLT {bit}, {res}, s7")       # 1 表示该位为 0
        self.L(f"    BNE {bit}, zero, {skip}")
        self.L(f"    SUB {res}, {res}, s7")
        self.L(f"{skip}:")

    def _pow2(self):
        """按 s5 的值做倍增，构造 2^s5 到 s6；s5 归零。最多 32 次。"""
        loop, done = self.lab("pw"), self.lab("pw")
        self.L("    ADDI s6, zero, 1")
        self.L(f"{loop}:")
        self.L(f"    BEQ s5, zero, {done}")
        self.L("    ADD s6, s6, s6")
        self.L("    ADDI s5, s5, -1")
        self.jmp(loop)
        self.L(f"{done}:")

    # -- 函数 ------------------------------------------------------
    def gen_func(self, fidx):
        params, results = self.ftype[fidx]
        if len(params) > 8:
            raise Unsupported(f"函数 {fidx} 形参 {len(params)} 个，超过 8（栈传参未实现）")
        if len(results) > 1:
            raise Unsupported("多返回值未实现")
        groups, body = self.mod["codes"][fidx - self.nimp]
        # P3：局部变量类型表（形参 + 声明locals）。浮点形参走 a 寄存器 ABI，
        # 需 freg 传参约定，本阶段显式拒绝；浮点**局部**变量（非形参）支持。
        for t in params:
            if t in (F32, F64):
                raise Unsupported("浮点形参未实现（需 freg 传参 ABI，P4）")
            if t != 0x7F:
                raise Unsupported(f"形参类型 0x{t:02x} 不是 i32")
        lvtype = list(params)
        for n, t in groups:
            if t not in (0x7F, F32, F64):
                raise Unsupported(f"局部变量类型 0x{t:02x} 不是 i32/f32/f64")
            lvtype += [t] * n

        self.lvtype = lvtype
        self.nlocals = len(params) + sum(n for n, _ in groups)
        self.h = 0
        self.maxh = 0
        self.vs = {}                     # 槽号 -> 持有该值的寄存器名
        self.vtype = {}                  # P3：槽号 -> wasm valtype（F32/F64/None=i32）
        self.pending = None              # 被就地消费掉的 local.set 之后的游标
        self.dead = False
        # 局部变量驻留只对 leaf 函数启用：有 call 时被调用者会破坏 s1/s2
        self.lreg = {} if has_call(body) else pick_lreg(body, self.nlocals, self.lvtype)
        self.ret = self.lab("ret")
        name = f"wasm_f{fidx}"

        self.L(f"{name}:")
        self.L("    SW s0, 0(sp)")
        self.L("    SW ra, 9(sp)")
        self.L("    ADDI s0, sp, 18")
        frame_at = len(self.lines)
        self.L("    __FRAME__")
        for i in range(len(params)):
            if i in self.lreg:
                self.L(f"    ADDI {self.lreg[i]}, a{i}, 0")
            else:
                self.L(f"    SW a{i}, {i * WORD}(s0)")
        for i in range(len(params), self.nlocals):
            if i in self.lreg:
                self.L(f"    ADDI {self.lreg[i]}, zero, 0")

        self.ctrl = [("func", None, None, 0, 0, 0)]
        p, n = 0, len(body)
        while p < n:
            p = self.gen_instr(body, p, fidx)

        if results and self.h > 0:
            # 函数末尾隐式返回值：栈顶搬到 a0（return 指令已自行搬过，此处兜底）
            r = self.get(self.h - 1, "a0")
            if r != "a0":
                self.L(f"    ADDI a0, {r}, 0")
        self.L(f"{self.ret}:")
        self.L("    ADDI sp, s0, -18")
        self.L("    LW ra, 9(sp)")
        self.L("    LW s0, 0(sp)")
        self.L("    JALR zero, 0(ra)")
        size = 18 + (self.nlocals + self.maxh) * WORD
        self.frames.append((frame_at, size))
        return name

    def inline_target(self, b, p, i0):
        """识别 `local.get j ... op ... local.set j`：结果可直接写回 j 的寄存器。
        前提是左操作数槽里放的就是 j 的值本身（未被物化），写它才是安全的。"""
        pl = peek_lset(b, p)
        if not pl:
            return None
        j, nxt = pl
        k = self.lreg.get(j)
        if k and self.vs.get(i0) == k:
            return j, nxt, k
        return None

    # -- 分派 ------------------------------------------------------
    def gen_instr(self, b, p, fidx):
        if self.pending is not None:              # 上一条已把这条 local.set/tee 就地消费
            q = self.pending
            self.pending = None
            return q
        op = b[p]; p += 1

        # 死代码：结构指令照常走（要配平 block/end），叶子只吃立即数
        if self.dead and op not in (0x02, 0x03, 0x04, 0x05, 0x0B):
            return skip_imm(b, p, op)

        # ---- 结构（9.2 起支持带参/带结果的 blocktype——高度记账贯穿 end/br）----
        if op in (0x02, 0x03, 0x04):
            (bps, brs), p = self.blocktype(b, p)
            if op == 0x04:
                t0 = self.pop("t0")                    # 条件先出栈
                self.spill_all()                  # 两条分支都以"全内存"状态开局
                els = self.lab("else")
                self.jnot(t0, els)
                self.ctrl.append(("if", els, self.lab("end"), self.h,
                                  len(bps), len(brs)))
            elif op == 0x03:
                self.spill_all()                  # 回边目标必须与入口同态
                top = self.lab("top")
                self.L(f"{top}:")
                self.ctrl.append(("loop", top, self.lab("end"), self.h,
                                  len(bps), len(brs)))
            else:
                self.ctrl.append(("block", None, self.lab("end"), self.h,
                                  len(bps), len(brs)))
            return p

        if op == 0x05:                            # else
            kind, els, end, h0, _np, _nr = self.ctrl[-1]
            if kind != "if":
                raise Unsupported("else 不在 if 内")
            self.spill_all()
            self.jmp(end)
            self.L(f"{els}:")
            self.dead = False
            self.h = h0
            self.vs.clear()
            self.ctrl[-1] = ("ifelse", None, end, h0, _np, _nr)
            return p

        if op == 0x0B:                            # end
            kind, els, end, h0, np_, nr_ = self.ctrl.pop()
            if kind == "func":
                self.dead = True
                return p
            self.spill_all()
            if kind == "if":
                self.L(f"{els}:")                 # 无 else：假分支直落 end
            self.L(f"{end}:")
            self.h = h0 - np_ + nr_               # 参数被块消费、结果替代之
            self.vs.clear()
            self.dead = False
            if not self.ctrl:
                self.dead = True
            return p

        # ---- 控制流 ----
        if op in (0x0C, 0x0D):
            d, p = uleb(b, p)
            kind, start, end, h0, np_, nr_ = self.ctrl[-1 - d]
            tgt = start if kind == "loop" else end
            if tgt is None:
                raise Unsupported("br 指向非 loop 的入口（不应发生）")
            if op == 0x0D:
                t0 = self.pop("t0")
            self.spill_all()
            # 标签签名高度：loop 标签吃参数；其余 = 入口 - 参数 + 结果
            keep = np_ if kind == "loop" else nr_
            newh = h0 if kind == "loop" else h0 - np_ + nr_
            self._br_copy(keep, newh)
            self.h = newh
            self.vs.clear()
            if op == 0x0D:
                self.jif(t0, tgt)
            else:
                self.jmp(tgt)
                self.dead = True
            return p

        if op == 0x0E:                            # br_table（9.3）
            nl, p = uleb(b, p)
            lbls = []
            for _ in range(nl + 1):
                d, p = uleb(b, p)
                kind, start, end, h0, np_, nr_ = self.ctrl[-1 - d]
                tgt = start if kind == "loop" else end
                if tgt is None:
                    raise Unsupported("br_table 指向非 loop 入口（不应发生）")
                keep = np_ if kind == "loop" else nr_
                newh = h0 if kind == "loop" else h0 - np_ + nr_
                lbls.append((tgt, keep, newh))
            # 保守比较链发射：不同嵌套高度的目标各有值槽搬家（内存跳表
            # 法对异高度目标不成立）。逐目标 = 匹配桩（copy + jmp）。
            # 值槽搬家与 br(0x0C) 同口径（as-br_table-value-index 实证面）。
            t0 = self.pop("t0")                   # 索引
            self.spill_all()
            stubs = []
            for k, (tgt, keep, newh) in enumerate(lbls[:-1]):
                stub = self.lab("bt")
                stubs.append(stub)
                self.L(f"    ADDI s1, zero, {k}")
                self.L(f"    SUB s1, {t0}, s1")
                self.jnot("s1", stub)             # idx == k → 匹配桩 k
            for (tgt, keep, newh), stub in zip(lbls[:-1], stubs):
                self.L(f"{stub}:")
                self._br_copy(keep, newh)
                self.jmp(tgt)
            self._br_copy(lbls[-1][1], lbls[-1][2])   # default 桩
            self.jmp(lbls[-1][0])
            self.vs.clear()
            self.dead = True
            return p

        if op == 0x0F:                            # return
            if self.ftype[fidx][1]:
                self.pop("a0")
            self.jmp(self.ret)
            self.dead = True
            return p

        if op == 0x10:                            # call
            fi, p = uleb(b, p)
            ps, rs = self.ftype[fi]
            if len(ps) > 8:
                raise Unsupported("调用实参超过 8 个")
            self.spill_all()                      # 实参必须落在内存槽里才能装进 a0-a7
            self.h -= len(ps)
            for i in range(len(ps)):
                self.L(f"    LW a{i}, {self.slot(self.h + i)}(s0)")
            tgt = self.callee.get(fi) or f"wasm_f{fi}"
            self.L(f"    JAL ra, {tgt}")
            if rs:
                r = self.alloc()
                if r != "a0":
                    self.L(f"    ADDI {r}, a0, 0")
                self.push(r)
            return p

        if op == 0x11:                            # call_indirect（9.2）
            tidx, p = uleb(b, p)
            tbl, p = uleb(b, p)
            if tbl >= len(self.tbases):
                raise Unsupported(f"call_indirect table {tbl} 未声明")
            ps, rs = self.ftype[tidx]
            if len(ps) > 8:
                raise Unsupported("调用实参超过 8 个")
            self.spill_all()
            self.h -= 1                           # 弹出索引（实参之下）
            idx = self.get(self.h, "s1")
            # 越界（u32 口径）：idx >=u 该表尺寸 → trap
            self.li("s2", self.tsizes[tbl])
            self.L(f"    SLT s3, {idx}, s2")      # idx < 表尺寸（正数域 SLT=全序）
            self.jnot("s3", "__wasm_trap")
            # 目标地址 = 表基址 + idx*9
            self.li("s2", self.tbases[tbl])
            self.L("    ADDI s3, zero, 9")
            self.L(f"    MUL s4, {idx}, s3")
            self.L("    ADD s2, s2, s4")
            self.L("    LW s5, 0(s2)")
            # 类型哨兵：类型区+idx 处字节 == typeidx+1 才匹配（0=null）
            self.li("s6", TYPE_BASE + tbl * TYPE_STRIDE)
            self.L(f"    ADD s6, s6, {idx}")
            self.L("    LB s7, 0(s6)")
            self.L(f"    ADDI s8, zero, {tidx + 1}")
            self.L("    SUB s7, s7, s8")
            self.jnot("s7", "__wasm_trap")
            self.h -= len(ps)
            for i in range(len(ps)):
                self.L(f"    LW a{i}, {self.slot(self.h + i)}(s0)")
            self.L("    JALR ra, 0(s5)")
            if rs:
                r = self.alloc()
                if r != "a0":
                    self.L(f"    ADDI {r}, a0, 0")
                self.push(r)
            return p

        if op == 0x00:
            self.jmp("__wasm_trap"); self.dead = True; return p
        if op == 0x01:
            return p

        # ---- 参数化 ----
        if op == 0x1A:
            self.h -= 1
            self.kill(self.h)
            return p
        if op == 0x1B:                            # select
            self.h -= 3
            c = self.get(self.h + 2, "s3")
            b2 = self.read(self.h + 1, "s4")
            a = self.get(self.h, "s5")
            keep = self.lab("sel")
            self.jif(c, keep)
            self.L(f"    ADDI {a}, {b2}, 0")
            self.L(f"{keep}:")
            self.set(self.h, a)
            self.h += 1
            self.maxh = max(self.maxh, self.h)
            return p

        # ---- 变量 ----
        if op == 0x20:
            i, p = uleb(b, p)
            if self.lvtype[i] in (F32, F64):   # P3：浮点局部变量
                vt = self.lvtype[i]
                mn = "FLW" if vt == F64 else "FLW.S"
                self.L(f"    {mn} {FT0}, {i * WORD}(s0)")
                j = self.h; self.h += 1
                self.maxh = max(self.maxh, self.h)
                self.vs.pop(j, None)
                self.L(f"    FSW {FT0}, {self.slot(j)}(s0)")
                self.vtype[j] = vt
                return p
            if i in self.lreg:
                self.push(self.lreg[i])        # 别名压栈，零指令
            else:
                r = self.alloc()
                self.L(f"    LW {r}, {i * WORD}(s0)")
                self.push(r)
            return p
        if op in (0x21, 0x22):
            i, p = uleb(b, p)
            if self.lvtype[i] in (F32, F64):   # P3：浮点局部变量
                vt = self.lvtype[i]
                mn = "FLW" if vt == F64 else "FLW.S"
                j = self.h - 1
                self.L(f"    {mn} {FT0}, {self.slot(j)}(s0)")
                self.L(f"    FSW {FT0}, {i * WORD}(s0)")
                if op == 0x22:                 # tee：值留在栈槽 j
                    self.vtype[j] = vt
                else:
                    self.h -= 1
                    self.vs.pop(j, None); self.vtype.pop(j, None)
                return p
            v = self.pop_val()
            if i in self.lreg:
                k = self.lreg[i]
                if v != k:
                    self.L(f"    ADDI {k}, {v}, 0")
                if op == 0x22:
                    self.push(k)
            else:
                self.L(f"    SW {v}, {i * WORD}(s0)")
                if op == 0x22:
                    self.push(v)
            return p
        if op == 0x23:
            i, p = uleb(b, p)
            r = self.alloc()
            self.li("s6", GLOBAL_BASE + i * WORD)
            self.L(f"    LW {r}, 0(s6)")
            self.push(r); return p
        if op == 0x24:
            i, p = uleb(b, p)
            v = self.pop_val()
            self.li("s6", GLOBAL_BASE + i * WORD)
            self.L(f"    SW {v}, 0(s6)")
            return p

        # ---- 常量 ----
        if op == 0x41:
            v, p = sleb(b, p)
            r = self.alloc()
            self.li(r, v)
            self.push(r); return p
        if op == 0x42:
            raise Unsupported("i64 未支持")
        if op in (0x43, 0x44):                # f32.const / f64.const（P3 编译期穿透）
            vt = F32 if op == 0x43 else F64
            nbytes = 4 if op == 0x43 else 8
            ieee = bytes(b[p:p + nbytes]); p += nbytes
            r = self.fl_const(ieee, vt)      # r = FT0（TF raw 已在常量池，FLW 载入）
            # 浮点值不驻留整数池：FSW 回槽，手动维护栈高，不调用 push（否则生成 SW f0）
            i = self.h; self.h += 1
            self.maxh = max(self.maxh, self.h)
            self.vs.pop(i, None)
            self.L(f"    FSW {r}, {self.slot(i)}(s0)")
            self.vtype[i] = vt
            return p

        # ---- 比较 ----
        if op == 0x45:                            # eqz
            self.h -= 1
            rs = self.read(self.h)
            rd = self.writable(self.h)
            self.eqz(rd, rs)
            self.set(self.h)
            self.h += 1
            return p

        if op in (0x46, 0x47):                    # eq / ne
            self.h -= 2
            src = self.read(self.h)
            rd = self.writable(self.h)
            b2 = self.read(self.h + 1, "s4")
            self.L(f"    SUB {rd}, {src}, {b2}")
            self.eqz(rd, rd)
            if op == 0x47:
                self.not_(rd, rd)
            self.kill(self.h + 1)
            self.set(self.h)
            self.h += 1
            return p

        if 0x48 <= op <= 0x4F:                    # lt/gt/le/ge（§九：_u 变体删，SLT 即全部序）
            if op in (0x49, 0x4B, 0x4D, 0x4F):
                raise Unsupported("无符号比较：§九 纯三值无无符号视图")
            self.h -= 2
            src = self.read(self.h)
            rd = self.writable(self.h)
            b2 = self.read(self.h + 1, "s4")
            if op == 0x48:                        # lt
                self.L(f"    SLT {rd}, {src}, {b2}")
            elif op == 0x4A:                      # gt
                self.L(f"    SLT {rd}, {b2}, {src}")
            elif op == 0x4C:                      # le
                self.L(f"    SLT {rd}, {b2}, {src}")
                self.not_(rd, rd)
            else:                                 # ge
                self.L(f"    SLT {rd}, {src}, {b2}")
                self.not_(rd, rd)
            self.kill(self.h + 1)
            self.set(self.h)
            self.h += 1
            return p

        # ---- 浮点比较（P3）----
        if 0x5B <= op <= 0x66:                    # f32/f64 eq/ne/lt/gt/le/ge
            vt = F32 if op <= 0x60 else F64
            self.f_cmp(op, vt)
            return p

        # ---- 数值 ----
        if op == 0x91 or op == 0x9F:              # f32.sqrt / f64.sqrt
            self.f_sqrt(F32 if op == 0x91 else F64)
            return p
        if op in (0x92, 0x93, 0x94, 0x95):        # f32.add/sub/mul/div
            self.f_arith({0x92:"FADD",0x93:"FSUB",0x94:"FMUL",0x95:"FDIV"}[op], F32)
            return p
        if op in (0xA0, 0xA1, 0xA2, 0xA3):        # f64.add/sub/mul/div
            self.f_arith({0xA0:"FADD",0xA1:"FSUB",0xA2:"FMUL",0xA3:"FDIV"}[op], F64)
            return p

        # ---- 9.4′ 浮点 sign/trit 族（TF raw 整数面，1 op ≈ 10 条）----
        if op in (0x8B, 0x8C, 0x99, 0x9A):        # abs / neg
            self.f_absneg(op, F32 if op in (0x8B, 0x8C) else F64)
            return p
        if op in (0x98, 0xA6):                    # copysign
            self.f_copysign(F32 if op == 0x98 else F64)
            return p
        if op in (0x96, 0x97, 0xA4, 0xA5):        # min / max
            self.f_minmax(op, F32 if op in (0x96, 0x97) else F64)
            return p

        if op in (0x6A, 0x6B):                    # add / sub
            self.h -= 2
            i0, i1 = self.h, self.h + 1
            inl = self.inline_target(b, p, i0)
            rd = inl[2] if inl else self.get(i0)
            b2 = self.read(i1, "s4")
            self.L(f"    {'ADD' if op == 0x6A else 'SUB'} {rd}, {rd}, {b2}")
            self.kill(i1)
            if inl:
                self.pending = inl[1]             # local.set 已被消费，写回目标就是 rd
                self.vs.pop(i0, None)
            elif self.reg_of(i0) is not None:
                self.vs[i0] = rd
                self.h += 1
            else:
                self.set(i0, rd)
                self.h += 1
            return p

        if op == 0x6C:                            # mul
            self.h -= 2
            i0, i1 = self.h, self.h + 1
            inl = self.inline_target(b, p, i0)
            rd = inl[2] if inl else self.get(i0)
            b2 = self.read(i1, "s4")
            self.L(f"    MUL {rd}, {rd}, {b2}")
            self.kill(i1)
            if inl:
                self.pending = inl[1]
                self.vs.pop(i0, None)
            elif self.reg_of(i0) is not None:
                self.vs[i0] = rd
                self.h += 1
            else:
                self.set(i0, rd)
                self.h += 1
            return p

        if op in (0x6D, 0x6E):                    # div_s / rem_s（§九：无符号视图删）
            self.h -= 2
            i0, i1 = self.h, self.h + 1
            inl = self.inline_target(b, p, i0)
            rd = inl[2] if inl else self.get(i0)
            b2 = self.read(i1, "s4")
            # __wasm_trap 在程序最前面，函数体越长离它越远。B 型只有 ±1093 条指令，
            # 所以走"近跳 + JAL"：不除零时只多花 1 步，除零时仍然跳得出去。
            ok = self.lab("dv")
            self.L(f"    BNE {b2}, zero, {ok}")
            self.L("    JAL zero, __wasm_trap")
            self.L(f"{ok}:")
            self.L(f"    {'DIV' if op == 0x6D else 'REM'} {rd}, {rd}, {b2}")
            self.kill(i1)
            if inl:
                self.pending = inl[1]
                self.vs.pop(i0, None)
            elif self.reg_of(i0) is not None:
                self.vs[i0] = rd
                self.h += 1
            else:
                self.set(i0, rd)
                self.h += 1
            return p

        if op in (0x71, 0x72, 0x73):              # and/or/xor → TAND/TOR/XOR（§九 1:1）
            self.h -= 2
            i0, i1 = self.h, self.h + 1
            inl = self.inline_target(b, p, i0)
            rd = inl[2] if inl else self.get(i0)
            b2 = self.read(i1, "s4")
            self.L(f"    { {0x71: 'TAND', 0x72: 'TOR', 0x73: 'XOR'}[op] } {rd}, {rd}, {b2}")
            self.kill(i1)
            if inl:
                self.pending = inl[1]; self.vs.pop(i0, None)
            elif self.reg_of(i0) is not None:
                self.vs[i0] = rd; self.h += 1
            else:
                self.set(i0, rd); self.h += 1
            return p
        if op in (0x74, 0x75):                    # shl/shr_s → 原生 SLL/SRA（×3^k / ÷3^k）
            self.h -= 2
            i0, i1 = self.h, self.h + 1
            inl = self.inline_target(b, p, i0)
            rd = inl[2] if inl else self.get(i0)
            b2 = self.read(i1, "s4")
            self.L(f"    {'SLL' if op == 0x74 else 'SRA'} {rd}, {rd}, {b2}")
            self.kill(i1)
            if inl:
                self.pending = inl[1]; self.vs.pop(i0, None)
            elif self.reg_of(i0) is not None:
                self.vs[i0] = rd; self.h += 1
            else:
                self.set(i0, rd); self.h += 1
            return p
        if op in (0x76, 0x77, 0x78):
            raise Unsupported("shr_u/rotl/rotr：二进制位概念（§九 删）")
        if op in (0x67, 0x68, 0x69):
            raise Unsupported("clz/ctz/popcnt：二进制位计数概念（§九 删）")

        # ---- 内存 ----
        if 0x28 <= op <= 0x35:
            return self.gen_load(b, p, op)
        if 0x36 <= op <= 0x3E:
            return self.gen_store(b, p, op)
        if op == 0x3F:
            r = self.alloc()
            self.li(r, WASM_MEM_SIZE // 65536)
            self.push(r); return p
        if op == 0x40:
            raise Unsupported("memory.grow 未实现")

        raise Unsupported(f"未实现操作码 0x{op:02x}")

    # -- 访存 ------------------------------------------------------
    def gen_load(self, b, p, op):
        _, p = uleb(b, p)
        offset, p = uleb(b, p)
        self.h -= 1
        i = self.h
        res = self.alloc()                        # 结果占用地址槽的寄存器
        rv = self.vs.get(i)
        if rv:
            self.L(f"    ADDI s3, {rv}, 0")       # 地址搬开，腾出 res 存结果
        else:
            self.L(f"    LW s3, {self.slot(i)}(s0)")
        self.kill(i)
        self.addr_off("s3", offset)               # 常数用 s6，不碰驻留池
        if op in (0x2A, 0x2B):                    # f32.load / f64.load（P3）
            vt = F32 if op == 0x2A else F64
            self.f_load(i, vt)
            self.set(i)                            # 结果已 FSW 回槽，不驻留
            self.h += 1
            return p
        if op == 0x28:                            # i32.load → 1 条 LW（原生 9 tryte word）
            self.L(f"    LW {res}, 0(s3)")
            self.set(self.h)
            self.h += 1
            return p
        if op in (0x2C, 0x2D):                    # load8_s / load8_u
            self.L(f"    {'LB' if op == 0x2C else 'LBU'} {res}, 0(s3)")
            self.set(self.h)
            self.h += 1
            return p
        if op in (0x2E, 0x2F):
            raise Unsupported("i16 存取：2 tryte 只有 ±29524，与 wasm 的 ±32768 错位")
        raise Unsupported(f"载入 0x{op:02x} 未实现（多为 i64/f32/f64）")

    def gen_store(self, b, p, op):
        _, p = uleb(b, p)
        offset, p = uleb(b, p)
        self.h -= 2
        ai, vi = self.h + 1, self.h               # wasm 规范：value 先压、address 在顶
        rv = self.vs.get(ai)
        if rv:
            self.L(f"    ADDI s3, {rv}, 0")       # 地址固定搬到 s3，与值的寄存器解耦
        else:
            self.L(f"    LW s3, {self.slot(ai)}(s0)")
        self.kill(ai)
        v = None
        if op in (0x38, 0x39):                    # f32.store / f64.store（P3）
            vt = F32 if op == 0x38 else F64
        else:
            v = self.get(vi, "s4")
        self.kill(vi)
        self.addr_off("s3", offset)
        if op in (0x38, 0x39):                    # 浮点值原样 FSW（TF raw 不解释格式）
            mn = "FSW" if vt == F64 else "FSW.S"
            self.L(f"    FLW {FT0}, {self.slot(vi)}(s0)")
            self.L(f"    {mn} {FT0}, 0(s3)")
            return p
        if op == 0x36:                            # i32.store → 1 条 SW（原生 word）
            self.L(f"    SW {v}, 0(s3)")
            return p
        if op == 0x3A:                            # store8
            self.L(f"    SB {v}, 0(s3)")
            return p
        if op == 0x3B:
            raise Unsupported("i16 存取：2 tryte 只有 ±29524，与 wasm 的 ±32768 错位")
        raise Unsupported(f"存储 0x{op:02x} 未实现")

    # -- 收尾 ------------------------------------------------------
    def finish(self, entry):
        # 可达性裁剪（9.2）：只编 entry 的 call 闭包 ∪ element 引用面
        # （call_indirect 动态目标 = elem 全量保守可达）。未触达的 i64/f64
        # 函数不再毒化整模块——spec 模块常态是混合类型大杂烩。
        reach = set()
        work = [entry]
        while work:
            fi = work.pop()
            if fi in reach or fi < self.nimp:
                continue
            reach.add(fi)
            _, body = self.mod["codes"][fi - self.nimp]
            for op, q in walk_instrs(body):
                if op == 0x10:
                    t, _ = uleb(body, q)
                    work.append(t)
        for tidx, off, fis in self.mod.get("elems", []):
            for fi in fis:
                if fi is not None:
                    work.append(fi)
        while work:
            fi = work.pop()
            if fi in reach or fi < self.nimp:
                continue
            reach.add(fi)
            _, body = self.mod["codes"][fi - self.nimp]
            for op, q in walk_instrs(body):
                if op == 0x10:
                    t, _ = uleb(body, q)
                    work.append(t)
        self.reach = reach
        for i in sorted(reach):
            self.gen_func(i)
        lines = list(self.lines)
        for at, size in self.frames:
            if size <= 1093:
                lines[at] = f"    ADDI sp, sp, {size}"
            else:
                lines[at] = f"    LUI t0, {size // 2187}"
                r = size % 2187
                if r:
                    lines[at] += "\n" + f"    ADDI t0, t0, {r if r <= 1093 else r - 2187}"
                lines[at] += "\n" + "    ADD sp, sp, t0"
        self.lines = lines
        return "\n".join(lines)


# ---------------------------------------------------------------- 运行时桩
RUNTIME = """\
_start:
{BOOT}
{ARGS}
    JAL ra, wasm_f{ENTRY}
    SW a0, {RESULT}(zero)
    ADDI a7, zero, 93
    ECALL
__wasm_trap:
    ADDI a0, zero, 134
    ADDI a7, zero, 93
    ECALL
__trap_loop:
    JAL zero, __trap_loop
__host_print:
    ADDI a2, a1, 0
    ADDI a1, a0, 0
    ADDI a0, zero, 1
    ADDI a7, zero, 64
    ECALL
    ADDI a0, zero, 0
    JALR zero, 0(ra)
"""


def li_lines(rd, v):
    if -1093 <= v <= 1093:
        return [f"    ADDI {rd}, zero, {v}"]
    digits, x = [], v
    while x:
        d = x % 2187
        if d > 1093:
            d -= 2187
        digits.append(d)
        x = (x - d) // 2187
    out = [f"    ADDI {rd}, zero, {digits[-1]}"]
    for d in reversed(digits[:-1]):
        out.append(f"    SLLI {rd}, {rd}, 7")
        if d:
            out.append(f"    ADDI {rd}, {rd}, {d}")
    return out


def compile_wasm(data, entry=None, args=None):
    """wasm 字节 → (汇编文本, 数据段 {地址: bytes}, 入口标签)。

    args：入口形参初值（i32，按 s32 语义转 45-trit 有符号），_start 物化到
    a0..a7（≤8，与 gen_func 的形参 ABI 一致）；返回值经 RESULT_ADDR 内存
    通道全宽写出（exit 通道 a0&0xFF 只有 8 bit，装不下 i32 结果）。"""
    mod = parse_module(data)
    g = Gen(mod)
    fns = mod["exports"].get(0, {})
    if entry is None:
        if not fns:
            raise ValueError("模块没有导出函数")
        entry = fns[list(fns)[0]]
    else:
        if entry not in fns:
            raise ValueError(f"模块没有导出函数 {entry}")
        entry = fns[entry]

    body = g.finish(entry)
    cur_table = (g.tbases[-1] + g.tsizes[-1] * WORD) if g.tbases else TABLE_BASE
    # 9.2：element 段 → BOOT 装 函数地址表 + 类型哨兵表；表尺寸随 elem 覆盖增长
    tabinit = []
    for tidx, off, fis in g.mod.get("elems", []):
        g.used_table = True
        hi = off + len(fis)
        if tidx >= len(g.tbases):                    # 声明外的表：顺序补一段
            while len(g.tbases) <= tidx:
                g.tbases.append(cur_table)
                g.tsizes.append(max(hi, 1))
                cur_table += max(hi, 1) * WORD
        g.tsizes[tidx] = max(g.tsizes[tidx], hi)
        tb = g.tbases[tidx]
        tbase_type = TYPE_BASE + tidx * TYPE_STRIDE
        tabinit += li_lines("s1", tb + off * WORD)
        tabinit += li_lines("s2", tbase_type + off)
        for fi in fis:
            if fi is None:
                tabinit += ["    ADDI s3, zero, 0", "    ADDI s4, zero, 0"]
            else:
                ty = g.mod["funcs"][fi - g.nimp] + 1
                tabinit += ["    ADDI s3, zero, &wasm_f%d" % fi,
                            "    ADDI s4, zero, %d" % ty]
            tabinit += ["    SW s3, 0(s1)", "    SB s4, 0(s2)",
                        "    ADDI s1, s1, 9", "    ADDI s2, s2, 1"]
    if g.mod.get("tables"):
        for k, (et, mn, mx) in enumerate(g.mod["tables"]):
            if k < len(g.tsizes):
                g.tsizes[k] = max(g.tsizes[k], mn)
    g.tab_max = g.tsizes[0] if g.tsizes else 0
    boot = li_lines("sp", STACK_BASE) + tabinit
    arglines = []
    if args:
        for i, v in enumerate(args[:8]):
            sv = v - (2 ** 32) if 2 ** 31 <= v < 2 ** 32 else v   # u32 → s32
            arglines += li_lines(f"a{i}", sv)
    head = RUNTIME.replace("{BOOT}", "\n".join(boot)) \
                    .replace("{ARGS}", "\n".join(arglines)) \
                    .replace("{ENTRY}", str(entry)) \
                    .replace("{RESULT}", str(RESULT_ADDR))
    asm = head + body + "\n"

    segs = {}
    for off, chunk in mod["datas"]:
        for i, c in enumerate(chunk):
            if c > 242:
                raise Unsupported(
                    f"data 段偏移 {off + i} 的字节值 {c} > 242，1 tryte 存不下"
                    f"（2^8 > 3^5 的必然结果）")
        segs[off] = bytearray(chunk)
    for i, (vt, mut, v) in enumerate(mod["globals"]):
        base = GLOBAL_BASE + i * WORD
        u = v % MOD45
        for k in range(WORD):
            segs[base + k] = bytes([u % 243])
            u //= 243
    if g.used_float:
        # P3：浮点常量池。IEEE 位模式已在编译期穿透成 TF raw（45-trit），
        # 按 9 tryte 小端存进数据段；运行时 FLW 原样载入 freg。
        for addr, raw in g.fconsts:
            u = raw % MOD45
            segs[addr] = bytearray((u // (243 ** i)) % 243 for i in range(WORD))
    return asm, segs, f"wasm_f{entry}"


def build_trv(asm, segs, path):
    import trirv_asm
    prog, _ = trirv_asm.assemble(asm)
    buf = bytearray(b"TRV1")
    buf += struct.pack("<III", 1, len(prog), len(segs))
    for w in prog:
        buf += struct.pack("<I", w & 0xFFFFFFFF)
    for addr in sorted(segs):
        d = bytes(segs[addr])
        buf += struct.pack("<II", addr, len(d))
        buf += d
    with open(path, "wb") as f:
        f.write(buf)
    return path


def main():
    if len(sys.argv) < 3:
        print("用法: python3 trirv_wasm.py <in.wasm> <out.trv> [out.s]")
        return 1
    data = open(sys.argv[1], "rb").read()
    try:
        asm, segs, _ = compile_wasm(data)
    except Unsupported as e:
        print(f"UNSUPPORTED: {e}")
        return 2
    if len(sys.argv) > 3:
        open(sys.argv[3], "w").write(asm)
    build_trv(asm, segs, sys.argv[2])
    print(f"[trirv_wasm] {sys.argv[1]} -> {sys.argv[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
