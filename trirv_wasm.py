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

WASM_MEM_SIZE = 262144          # wasm 线性内存占 TriRV 地址 [0, 262144)
GLOBAL_BASE   = 262144          # 全局变量区
STACK_BASE    = 270336          # 栈起点（向上增长）
WORD          = 9               # 1 word = 9 tryte
MOD45         = 3 ** 45

I31, I32, NI31 = 2**31, 2**32, -(2**31)

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

        elif sid == 4:                                   # table：本切片不用，但要吃掉
            m["tables"], p = vec(data, p, lambda b, q: (None, q + 4))

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
            raise Unsupported("不支持 table/element（无函数指针）")

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
    return any(op == 0x10 for op, _ in walk_instrs(body))


def pick_lreg(body, nlocals):
    """挑最热的局部变量给 s1/s2：按 local.set/tee 次数排序（只写的才是热的）。"""
    cnt = {}
    for op, p in walk_instrs(body):
        if op in (0x21, 0x22):
            i, _ = uleb(body, p)
            if i < nlocals:
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
        self.h -= 1
        if self.h < 0:
            raise Unsupported("操作数栈下溢（模块未通过校验？）")
        i = self.h
        r = self.vs.get(i)
        if r:
            self.vs.pop(i, None)
            if rd != r:
                self.L(f"    ADDI {rd}, {r}, 0")
            return
        self.L(f"    LW {rd}, {self.slot(i)}(s0)")
        self.vs.pop(i, None)

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

    def wrap(self, r):
        """规整回 i32 值域 [-2^31, 2^31)。加减最多越界一次，两条判定即可。
        跳转距离只有 1 条指令，用 B 型短跳（±1093 槽），不走 jif 的 BEQ+JAL 三步。"""
        hi, lo = self.lab("w"), self.lab("w")
        self.L(f"    SLT s7, {r}, s11")
        self.L(f"    BNE s7, zero, {hi}")         # r < 2^31 -> 不动
        self.L(f"    SUB {r}, {r}, s10")
        self.L(f"{hi}:")
        self.L(f"    SLT s7, {r}, s9")
        self.L(f"    BEQ s7, zero, {lo}")         # r >= -2^31 -> 不动
        self.L(f"    ADD {r}, {r}, s10")
        self.L(f"{lo}:")

    def wrap_mul(self, r):
        """(a*b) mod 2^32：先取整除到 (-2^32, 2^32)，再按 ±2^31 折回 i32 值域。
        三进制没有"高位截断"，这一次除法是必须付的税。"""
        self.L(f"    DIV s6, {r}, s10")
        self.L(f"    MUL s6, s6, s10")
        self.L(f"    SUB {r}, {r}, s6")
        self.wrap(r)

    def to_unsigned(self, r):
        ok = self.lab("u")
        self.L(f"    SLT s7, {r}, zero")
        self.L(f"    BEQ s7, zero, {ok}")
        self.L(f"    ADD {r}, {r}, s10")
        self.L(f"{ok}:")

    def lt_u(self, rd, ra, rb):
        """rd = (ra <u rb)。同号退化为有符号比较，异号由符号直接定。"""
        same, done = self.lab("lu"), self.lab("lu")
        self.L(f"    SLT s7, {ra}, zero")
        self.L(f"    SLT s8, {rb}, zero")
        self.L(f"    BEQ s7, s8, {same}")
        self.L(f"    ADDI {rd}, s7, 0")          # ra 为负 -> ra_u 更大 -> 结果 0
        self.L(f"    SLTI {rd}, {rd}, 1")        # 取反：ra 非负而 rb 负时为 1
        self.jmp(done)
        self.L(f"{same}:")
        self.L(f"    SLT {rd}, {ra}, {rb}")
        self.L(f"{done}:")

    def addr_off(self, r, offset):
        if offset == 0:
            return
        self.li("s6", offset)
        self.L(f"    ADD {r}, {r}, s6")

    # -- 函数 ------------------------------------------------------
    def gen_func(self, fidx):
        params, results = self.ftype[fidx]
        if len(params) > 8:
            raise Unsupported(f"函数 {fidx} 形参 {len(params)} 个，超过 8（栈传参未实现）")
        if len(results) > 1:
            raise Unsupported("多返回值未实现")
        groups, body = self.mod["codes"][fidx - self.nimp]
        for n, t in groups:
            if t != 0x7F:
                raise Unsupported(f"局部变量类型 0x{t:02x} 不是 i32")

        self.nlocals = len(params) + sum(n for n, _ in groups)
        self.h = 0
        self.maxh = 0
        self.vs = {}                     # 槽号 -> 持有该值的寄存器名
        self.pending = None              # 被就地消费掉的 local.set 之后的游标
        self.dead = False
        # 局部变量驻留只对 leaf 函数启用：有 call 时被调用者会破坏 s1/s2
        self.lreg = {} if has_call(body) else pick_lreg(body, self.nlocals)
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

        self.ctrl = [("func", None, None, 0)]
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

        # ---- 结构 ----
        if op in (0x02, 0x03, 0x04):
            bt = b[p]; p += 1
            if bt != 0x40:
                raise Unsupported("暂不支持带结果的 block/if（blocktype 非空）")
            if op == 0x04:
                self.pop("t0")                    # 条件先出栈
                self.spill_all()                  # 两条分支都以"全内存"状态开局
                els = self.lab("else")
                self.jnot("t0", els)
                self.ctrl.append(("if", els, self.lab("end"), self.h))
            elif op == 0x03:
                self.spill_all()                  # 回边目标必须与入口同态
                top = self.lab("top")
                self.L(f"{top}:")
                self.ctrl.append(("loop", top, self.lab("end"), self.h))
            else:
                self.ctrl.append(("block", None, self.lab("end"), self.h))
            return p

        if op == 0x05:                            # else
            kind, els, end, h0 = self.ctrl[-1]
            if kind != "if":
                raise Unsupported("else 不在 if 内")
            self.spill_all()
            self.jmp(end)
            self.L(f"{els}:")
            self.dead = False
            self.h = h0
            self.vs.clear()
            self.ctrl[-1] = ("ifelse", None, end, h0)
            return p

        if op == 0x0B:                            # end
            kind, els, end, h0 = self.ctrl.pop()
            if kind == "func":
                self.dead = True
                return p
            self.spill_all()
            if kind == "if":
                self.L(f"{els}:")                 # 无 else：假分支直落 end
            self.L(f"{end}:")
            self.h = h0
            self.vs.clear()
            self.dead = False
            if not self.ctrl:
                self.dead = True
            return p

        # ---- 控制流 ----
        if op in (0x0C, 0x0D):
            d, p = uleb(b, p)
            kind, start, end, h0 = self.ctrl[-1 - d]
            tgt = start if kind == "loop" else end
            if tgt is None:
                raise Unsupported("br 指向非 loop 的入口（不应发生）")
            if op == 0x0D:
                self.pop("t0")
                self.spill_all()
                self.jif("t0", tgt)
            else:
                self.spill_all()
                self.jmp(tgt)
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
            if i in self.lreg:
                self.push(self.lreg[i])        # 别名压栈，零指令
            else:
                r = self.alloc()
                self.L(f"    LW {r}, {i * WORD}(s0)")
                self.push(r)
            return p
        if op in (0x21, 0x22):
            i, p = uleb(b, p)
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
        if op == 0x43:
            raise Unsupported("f32 未支持")
        if op == 0x44:
            raise Unsupported("f64 未支持")

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

        if 0x48 <= op <= 0x4F:                    # lt/gt/le/ge (s/u)
            self.h -= 2
            src = self.read(self.h)
            rd = self.writable(self.h)
            b2 = self.read(self.h + 1, "s4")
            # rd 只写不读，所以左操作数可以是别名，不必物化
            if op == 0x48:                        # lt_s
                self.L(f"    SLT {rd}, {src}, {b2}")
            elif op == 0x49:                      # lt_u
                self.lt_u(rd, src, b2)
            elif op == 0x4A:                      # gt_s = b < a
                self.L(f"    SLT {rd}, {b2}, {src}")
            elif op == 0x4B:                      # gt_u
                self.lt_u(rd, b2, src)
            elif op == 0x4C:                      # le_s = !(b < a)
                self.L(f"    SLT {rd}, {b2}, {src}")
                self.not_(rd, rd)
            elif op == 0x4D:                      # le_u
                self.lt_u(rd, b2, src)
                self.not_(rd, rd)
            elif op == 0x4E:                      # ge_s = !(a < b)
                self.L(f"    SLT {rd}, {src}, {b2}")
                self.not_(rd, rd)
            else:                                 # 0x4F ge_u
                self.lt_u(rd, src, b2)
                self.not_(rd, rd)
            self.kill(self.h + 1)
            self.set(self.h)
            self.h += 1
            return p

        # ---- 数值 ----
        if op in (0x6A, 0x6B):                    # add / sub
            self.h -= 2
            i0, i1 = self.h, self.h + 1
            inl = self.inline_target(b, p, i0)
            rd = inl[2] if inl else self.get(i0)
            b2 = self.read(i1, "s4")
            self.L(f"    {'ADD' if op == 0x6A else 'SUB'} {rd}, {rd}, {b2}")
            self.wrap(rd)
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
            self.wrap_mul(rd)
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

        if op in (0x6D, 0x6E, 0x6F, 0x70):        # div_s div_u rem_s rem_u
            self.h -= 2
            i0, i1 = self.h, self.h + 1
            inl = self.inline_target(b, p, i0)
            rd = inl[2] if inl else self.get(i0)
            b2 = self.read(i1, "s4")
            self.L(f"    BEQ {b2}, zero, __wasm_trap")
            if op in (0x6E, 0x70):
                self.to_unsigned(rd)
                self.to_unsigned(b2)
            self.L(f"    {'DIV' if op in (0x6D, 0x6E) else 'REM'} {rd}, {rd}, {b2}")
            if op in (0x6E, 0x70):
                self.wrap(rd)
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

        if op in (0x71, 0x72, 0x73):
            raise Unsupported("i32.and/or/xor：三进制没有二进制位运算的免费映射")
        if op in (0x74, 0x75, 0x76, 0x77, 0x78):
            raise Unsupported("i32 移位/旋转：需按 2 的幂展开，本切片未实现")
        if op in (0x67, 0x68, 0x69):
            raise Unsupported("clz/ctz/popcnt 未实现")

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
        if op == 0x28:                            # i32.load：4 tryte 平衡进制
            self.L(f"    LB {res}, 0(s3)")
            for k, sh in ((1, 5), (2, 10), (3, 15)):
                self.L(f"    LB s5, {k}(s3)")
                self.L(f"    SLLI s5, s5, {sh}")
                self.L(f"    ADD {res}, {res}, s5")
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
        ai, vi = self.h, self.h + 1
        rv = self.vs.get(ai)
        if rv:
            self.L(f"    ADDI s3, {rv}, 0")       # 地址固定搬到 s3，与值的寄存器解耦
        else:
            self.L(f"    LW s3, {self.slot(ai)}(s0)")
        self.kill(ai)
        v = self.get(vi, "s4")
        self.kill(vi)
        self.addr_off("s3", offset)
        if op == 0x36:                            # i32.store：拆成 4 个平衡 243 位
            self.L("    ADDI s6, zero, 243")
            for k in range(4):
                self.L(f"    REM s5, {v}, s6")     # s5 ∈ (-243, 243)
                # 平衡位归位：d>121 -> -243；d<-121 -> +243；中间不动
                # 修正量 = 243 * ((d<122) + (d<-121) - 1)，无分支
                self.L("    SLTI s7, s5, 122")
                self.L("    SLTI s8, s5, -121")
                self.L("    ADD s7, s7, s8")
                self.L("    ADDI s7, s7, -1")
                self.L("    SLLI s7, s7, 5")       # ×243
                self.L("    ADD s5, s5, s7")
                self.L(f"    SB s5, {k}(s3)")
                self.L(f"    SUB {v}, {v}, s5")
                self.L(f"    DIV {v}, {v}, s6")
            return p
        if op == 0x3A:                            # store8
            self.L(f"    SB {v}, 0(s3)")
            return p
        if op == 0x3B:
            raise Unsupported("i16 存取：2 tryte 只有 ±29524，与 wasm 的 ±32768 错位")
        raise Unsupported(f"存储 0x{op:02x} 未实现")

    # -- 收尾 ------------------------------------------------------
    def finish(self, entry):
        for i in range(self.nimp, len(self.ftype)):
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
    JAL ra, wasm_f{ENTRY}
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


def compile_wasm(data, entry=None):
    """wasm 字节 → (汇编文本, 数据段 {地址: bytes}, 入口标签)。"""
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
    boot = li_lines("sp", STACK_BASE) + li_lines("s9", NI31) + \
        li_lines("s10", I32) + li_lines("s11", I31)
    head = RUNTIME.replace("{BOOT}", "\n".join(boot)).replace("{ENTRY}", str(entry))
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
