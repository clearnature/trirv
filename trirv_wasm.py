#!/usr/bin/env python3
"""trirv_wasm —— Wasm 栈式字节码 → TriRV 汇编（路径 A 最小切片）。

定位
    这是"Wasm 栈机 → 寄存器机"的翻译层，用来验证"三进制能不能跑栈式字节码"本身。
    不做生态、不做完整规范：只覆盖 i32 整数、控制流、线性内存、主机调用。

内存模型（本切片最关键的取舍）
    TriRV 内存按 tryte(5 trit) 编址，Wasm 按 byte(8 bit) 编址，两者互质：
        byte 要 256 个值，tryte 只有 243 个 —— 2^8 > 3^5
    因此采用"1 wasm byte = 1 tryte"，wasm 地址 == TriRV 地址（零偏移），
    代价是每种宽度都少一截：

        类型    Wasm 值域           本切片值域              缺多少
        i8      -128..127           -121..121               边界各 7 个值
        i16     -32768..32767       未支持                  —
        i32     -2^31..2^31-1       ±1,743,392,200          约 ±19% 区间
        i64     未支持              —                       —

    寄存器里 i32 始终是完整的 45-trit 值（经 wrap 规整），只有写回内存时受上述上限约束。

帧布局
    fp = s0：
        fp + i*9            : 局部变量 i（形参 0..nparams-1 也在其中）
        fp + (nlocals+j)*9  : 操作数栈槽 j
    操作数栈槽按"静态高度"寻址，运行时不动 sp —— 高度在翻译期就算得出来。

寄存器约定
    s0=fp  ra  sp=t0-t6 表达式临时  s9=-2^31  s10=2^32  s11=2^31
    （s9/s10/s11 在 _start 装好，wasm 代码只读，用于 i32 回绕与无符号比较）

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
    def slot(self, i):
        return (self.nlocals + i) * WORD

    def push(self, r):
        self.L(f"    SW {r}, {self.slot(self.h)}(s0)")
        self.h += 1
        self.maxh = max(self.maxh, self.h)

    def pop(self, r):
        self.h -= 1
        if self.h < 0:
            raise Unsupported("操作数栈下溢（模块未通过校验？）")
        self.L(f"    LW {r}, {self.slot(self.h)}(s0)")

    # -- i32 语义辅助 ----------------------------------------------
    def eqz(self, rd, rs):
        """rd = (rs == 0) ? 1 : 0。三进制没有二进制位运算，(x<1)-(x<0) 才正确。"""
        self.L(f"    SLTI t3, {rs}, 1")
        self.L(f"    SLTI t4, {rs}, 0")
        self.L(f"    SUB {rd}, t3, t4")

    def not_(self, rd, rs):
        """布尔取反（rs 必须是 0/1）。"""
        self.L(f"    SLTI {rd}, {rs}, 1")

    def wrap(self, r):
        """规整回 i32 值域 [-2^31, 2^31)。加减最多越界一次，两条判定即可。"""
        hi, lo = self.lab("w"), self.lab("w")
        self.L(f"    SLT t3, {r}, s11")
        self.jif("t3", hi)
        self.L(f"    SUB {r}, {r}, s10")
        self.L(f"{hi}:")
        self.L(f"    SLT t3, {r}, s9")
        self.jnot("t3", lo)
        self.L(f"    ADD {r}, {r}, s10")
        self.L(f"{lo}:")

    def wrap_mul(self, r):
        """(a*b) mod 2^32：先取整除到 (-2^32, 2^32)，再按 ±2^31 折回 i32 值域。
        三进制没有"高位截断"，这一次除法是必须付的税。"""
        self.L(f"    DIV t2, {r}, s10")
        self.L(f"    MUL t2, t2, s10")
        self.L(f"    SUB {r}, {r}, t2")
        self.wrap(r)

    def to_unsigned(self, r):
        ok = self.lab("u")
        self.L(f"    SLT t3, {r}, zero")
        self.jnot("t3", ok)
        self.L(f"    ADD {r}, {r}, s10")
        self.L(f"{ok}:")

    def lt_u(self, rd, ra, rb):
        """rd = (ra <u rb)。同号退化为有符号比较，异号由符号直接定。"""
        same, done = self.lab("lu"), self.lab("lu")
        self.L(f"    SLT t5, {ra}, zero")
        self.L(f"    SLT t6, {rb}, zero")
        self.L(f"    BEQ t5, t6, {same}")
        self.L(f"    ADDI {rd}, t5, 0")          # ra 为负 -> ra_u 更大 -> 结果 0
        self.L(f"    SLTI {rd}, {rd}, 1")        # 取反：ra 非负而 rb 负时为 1
        self.jmp(done)
        self.L(f"{same}:")
        self.L(f"    SLT {rd}, {ra}, {rb}")
        self.L(f"{done}:")

    def addr_off(self, r, offset):
        if offset == 0:
            return
        self.li("t2", offset)
        self.L(f"    ADD {r}, {r}, t2")

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
        self.dead = False
        self.ret = self.lab("ret")
        name = f"wasm_f{fidx}"

        self.L(f"{name}:")
        self.L("    SW s0, 0(sp)")
        self.L("    SW ra, 9(sp)")
        self.L("    ADDI s0, sp, 18")
        frame_at = len(self.lines)
        self.L("    __FRAME__")
        for i in range(len(params)):
            self.L(f"    SW a{i}, {i * WORD}(s0)")

        self.ctrl = [("func", None, None, 0)]
        p, n = 0, len(body)
        while p < n:
            p = self.gen_instr(body, p, fidx)

        if results and self.h > 0:
            # 函数末尾隐式返回值：栈顶搬到 a0（return 指令已自行搬过，此处兜底）
            self.L(f"    LW a0, {self.slot(self.h - 1)}(s0)")
        self.L(f"{self.ret}:")
        self.L("    ADDI sp, s0, -18")
        self.L("    LW ra, 9(sp)")
        self.L("    LW s0, 0(sp)")
        self.L("    JALR zero, 0(ra)")
        size = 18 + (self.nlocals + self.maxh) * WORD
        self.frames.append((frame_at, size))
        return name

    # -- 分派 ------------------------------------------------------
    def gen_instr(self, b, p, fidx):
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
                self.pop("t0")
                els = self.lab("else")
                self.jnot("t0", els)
                self.ctrl.append(("if", els, self.lab("end"), self.h))
            elif op == 0x03:
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
            self.jmp(end)
            self.L(f"{els}:")
            self.dead = False
            self.h = h0
            self.ctrl[-1] = ("ifelse", None, end, h0)
            return p

        if op == 0x0B:                            # end
            kind, els, end, h0 = self.ctrl.pop()
            if kind == "func":
                self.dead = True
                return p
            if kind == "if":
                self.L(f"{els}:")                 # 无 else：假分支直落 end
            self.L(f"{end}:")
            self.h = h0
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
                self.jif("t0", tgt)
            else:
                self.jmp(tgt)
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
            self.h -= len(ps)
            for i in range(len(ps)):
                self.L(f"    LW a{i}, {self.slot(self.h + i)}(s0)")
            tgt = self.callee.get(fi) or f"wasm_f{fi}"
            self.L(f"    JAL ra, {tgt}")
            if rs:
                self.push("a0")
            return p

        if op == 0x00:
            self.jmp("__wasm_trap"); self.dead = True; return p
        if op == 0x01:
            return p

        # ---- 参数化 ----
        if op == 0x1A:
            self.h -= 1; return p
        if op == 0x1B:                            # select
            self.h -= 3
            self.L(f"    LW t0, {self.slot(self.h + 2)}(s0)")
            self.L(f"    LW t1, {self.slot(self.h + 1)}(s0)")
            self.L(f"    LW t2, {self.slot(self.h)}(s0)")
            keep = self.lab("sel")
            self.jif("t0", keep)
            self.L("    ADDI t2, t1, 0")
            self.L(f"{keep}:")
            self.L(f"    SW t2, {self.slot(self.h)}(s0)")
            self.h += 1
            self.maxh = max(self.maxh, self.h)
            return p

        # ---- 变量 ----
        if op == 0x20:
            i, p = uleb(b, p)
            self.L(f"    LW t0, {i * WORD}(s0)")
            self.push("t0"); return p
        if op in (0x21, 0x22):
            i, p = uleb(b, p)
            self.pop("t0")
            self.L(f"    SW t0, {i * WORD}(s0)")
            if op == 0x22:
                self.push("t0")
            return p
        if op == 0x23:
            i, p = uleb(b, p)
            self.li("t0", GLOBAL_BASE + i * WORD)
            self.L("    LW t0, 0(t0)")
            self.push("t0"); return p
        if op == 0x24:
            i, p = uleb(b, p)
            self.pop("t0")
            self.li("t1", GLOBAL_BASE + i * WORD)
            self.L("    SW t0, 0(t1)")
            return p

        # ---- 常量 ----
        if op == 0x41:
            v, p = sleb(b, p)
            self.li("t0", v)
            self.push("t0"); return p
        if op == 0x42:
            raise Unsupported("i64 未支持")
        if op == 0x43:
            raise Unsupported("f32 未支持")
        if op == 0x44:
            raise Unsupported("f64 未支持")

        # ---- 比较 ----
        if op == 0x45:                            # eqz
            self.h -= 1
            self.L(f"    LW t0, {self.slot(self.h)}(s0)")
            self.eqz("t0", "t0")
            self.L(f"    SW t0, {self.slot(self.h)}(s0)")
            self.h += 1
            return p

        if op in (0x46, 0x47):                    # eq / ne
            self.h -= 2
            self.L(f"    LW t0, {self.slot(self.h)}(s0)")
            self.L(f"    LW t1, {self.slot(self.h + 1)}(s0)")
            self.L("    SUB t2, t0, t1")
            self.eqz("t2", "t2")
            if op == 0x47:
                self.not_("t2", "t2")
            self.L(f"    SW t2, {self.slot(self.h)}(s0)")
            self.h += 1
            return p

        if 0x48 <= op <= 0x4F:                    # lt/gt/le/ge (s/u)
            self.h -= 2
            self.L(f"    LW t0, {self.slot(self.h)}(s0)")
            self.L(f"    LW t1, {self.slot(self.h + 1)}(s0)")
            if op in (0x48, 0x4C, 0x4E):          # lt_s / le_s / ge_s
                self.L("    SLT t2, t0, t1")
            elif op in (0x49, 0x4D, 0x4F):
                self.lt_u("t2", "t0", "t1")
            if op in (0x4A, 0x4B):                # gt = b < a
                if op == 0x4A:
                    self.L("    SLT t2, t1, t0")
                else:
                    self.lt_u("t2", "t1", "t0")
            if op in (0x4C, 0x4D):                # le = !(b < a)
                if op == 0x4C:
                    self.L("    SLT t2, t1, t0")
                else:
                    self.lt_u("t2", "t1", "t0")
                self.not_("t2", "t2")
            if op in (0x4E, 0x4F):                # ge = !(a < b)
                if op == 0x4E:
                    self.L("    SLT t2, t0, t1")
                else:
                    self.lt_u("t2", "t0", "t1")
                self.not_("t2", "t2")
            self.L(f"    SW t2, {self.slot(self.h)}(s0)")
            self.h += 1
            return p

        # ---- 数值 ----
        if op in (0x6A, 0x6B):                    # add / sub
            self.h -= 2
            self.L(f"    LW t0, {self.slot(self.h)}(s0)")
            self.L(f"    LW t1, {self.slot(self.h + 1)}(s0)")
            self.L(f"    {'ADD' if op == 0x6A else 'SUB'} t0, t0, t1")
            self.wrap("t0")
            self.L(f"    SW t0, {self.slot(self.h)}(s0)")
            self.h += 1
            return p

        if op == 0x6C:                            # mul
            self.h -= 2
            self.L(f"    LW t0, {self.slot(self.h)}(s0)")
            self.L(f"    LW t1, {self.slot(self.h + 1)}(s0)")
            self.L("    MUL t0, t0, t1")
            self.wrap_mul("t0")
            self.L(f"    SW t0, {self.slot(self.h)}(s0)")
            self.h += 1
            return p

        if op in (0x6D, 0x6E, 0x6F, 0x70):        # div_s div_u rem_s rem_u
            self.h -= 2
            self.L(f"    LW t0, {self.slot(self.h)}(s0)")
            self.L(f"    LW t1, {self.slot(self.h + 1)}(s0)")
            self.L("    BEQ t1, zero, __wasm_trap")
            if op in (0x6E, 0x70):
                self.to_unsigned("t0")
                self.to_unsigned("t1")
            self.L(f"    {'DIV' if op in (0x6D, 0x6E) else 'REM'} t0, t0, t1")
            if op in (0x6E, 0x70):
                self.wrap("t0")
            self.L(f"    SW t0, {self.slot(self.h)}(s0)")
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
            self.li("t0", WASM_MEM_SIZE // 65536)
            self.push("t0"); return p
        if op == 0x40:
            raise Unsupported("memory.grow 未实现")

        raise Unsupported(f"未实现操作码 0x{op:02x}")

    # -- 访存 ------------------------------------------------------
    def gen_load(self, b, p, op):
        _, p = uleb(b, p)
        offset, p = uleb(b, p)
        self.pop("t0")
        self.addr_off("t0", offset)
        if op == 0x28:                            # i32.load：4 tryte 平衡进制
            self.L("    LB t1, 0(t0)")
            for k, sh in ((1, 5), (2, 10), (3, 15)):
                self.L(f"    LB t2, {k}(t0)")
                self.L(f"    SLLI t2, t2, {sh}")
                self.L("    ADD t1, t1, t2")
            self.push("t1")
            return p
        if op in (0x2C, 0x2D):                    # load8_s / load8_u
            self.L(f"    {'LB' if op == 0x2C else 'LBU'} t1, 0(t0)")
            self.push("t1")
            return p
        if op in (0x2E, 0x2F):
            raise Unsupported("i16 存取：2 tryte 只有 ±29524，与 wasm 的 ±32768 错位")
        raise Unsupported(f"载入 0x{op:02x} 未实现（多为 i64/f32/f64）")

    def gen_store(self, b, p, op):
        _, p = uleb(b, p)
        offset, p = uleb(b, p)
        self.h -= 2
        self.L(f"    LW t0, {self.slot(self.h)}(s0)")       # 地址
        self.L(f"    LW t1, {self.slot(self.h + 1)}(s0)")   # 值
        self.addr_off("t0", offset)
        if op == 0x36:                            # i32.store：拆成 4 个平衡 243 位
            self.L("    ADDI t3, zero, 243")
            for k in range(4):
                self.L("    REM t2, t1, t3")       # t2 ∈ (-243, 243)
                # 平衡位归位：d>121 -> -243；d<-121 -> +243；中间不动
                # 修正量 = 243 * ((d<122) + (d<-121) - 1)，无分支
                self.L("    SLTI t4, t2, 122")
                self.L("    SLTI t5, t2, -121")
                self.L("    ADD t4, t4, t5")
                self.L("    ADDI t4, t4, -1")
                self.L("    SLLI t4, t4, 5")       # ×243
                self.L("    ADD t2, t2, t4")
                self.L(f"    SB t2, {k}(t0)")
                self.L("    SUB t1, t1, t2")
                self.L("    DIV t1, t1, t3")
            return p
        if op == 0x3A:                            # store8
            self.L("    SB t1, 0(t0)")
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
