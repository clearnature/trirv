#!/usr/bin/env python3
"""trirv_wasm_e2e —— 路径 A 最小切片的功能与差分验证。

三路对比：
    ① 参考解释器（Python，直接执行原始栈式字节码，i32 语义按 Wasm 规范）
    ② TriRV Python VM（跑翻译出来的机器码）
    ③ TriRV C VM（同一份 .trv，验证两套 VM 一致）

没有 wat2wasm，所以 .wasm 二进制由本文件的构造器手工拼出。
"""
import struct
import subprocess
import sys
import tempfile
import os

import trirv_asm
import trirv_vm
import trirv_wasm
from trirv_wasm import compile_wasm, build_trv, Unsupported

M32 = 2 ** 32
M45 = 3 ** 45


# ---------------------------------------------------------------- i32 语义
def s32(v):
    v &= M32 - 1
    return v - M32 if v >= 2 ** 31 else v


def u32(v):
    return v & (M32 - 1)


# ---------------------------------------------------------------- wasm 构造
def u(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def si(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if (n == 0 and not b & 0x40) or (n == -1 and b & 0x40):
            out.append(b)
            return bytes(out)
        out.append(b | 0x80)


def vec(items):
    return u(len(items)) + b"".join(items)


def nm(t):
    b = t.encode()
    return u(len(b)) + b


I32 = 0x7F


class W:
    def __init__(self):
        self.types, self.imports, self.funcs = [], [], []
        self.mems, self.globals, self.exports = [], [], []
        self.codes, self.datas = [], []

    def type(self, params, results):
        self.types.append(b"\x60" + vec([bytes([p]) for p in params]) +
                          vec([bytes([r]) for r in results]))
        return len(self.types) - 1

    def imp(self, mod, fld, tidx):
        self.imports.append(nm(mod) + nm(fld) + b"\x00" + u(tidx))
        return len(self.imports) - 1

    def func(self, tidx, body, nlocals=0):
        """body 已含末尾 0x0B。nlocals 个 i32 局部变量。"""
        self.funcs.append(u(tidx))
        loc = vec([u(nlocals) + bytes([I32])] if nlocals else [])
        self.codes.append(loc + body)
        return len(self.imports) + len(self.funcs) - 1

    def export(self, name, kind, idx):
        self.exports.append(nm(name) + bytes([kind]) + u(idx))

    def data(self, off, blob):
        self.datas.append(u(0) + b"\x41" + si(off) + b"\x0b" + u(len(blob)) + blob)

    def build(self):
        out = bytearray(b"\0asm" + struct.pack("<I", 1))
        if self.types:
            out += b"\x01" + u(len(vec(self.types))) + vec(self.types)
        if self.imports:
            out += b"\x02" + u(len(vec(self.imports))) + vec(self.imports)
        if self.funcs:
            out += b"\x03" + u(len(vec(self.funcs))) + vec(self.funcs)
        if self.mems:
            out += b"\x05" + u(len(vec(self.mems))) + vec(self.mems)
        if self.globals:
            out += b"\x06" + u(len(vec(self.globals))) + vec(self.globals)
        if self.exports:
            out += b"\x07" + u(len(vec(self.exports))) + vec(self.exports)
        if self.codes:
            cs = [u(len(c)) + c for c in self.codes]
            out += b"\x0a" + u(len(vec(cs))) + vec(cs)
        if self.datas:
            out += b"\x0b" + u(len(vec(self.datas))) + vec(self.datas)
        return bytes(out)


# 指令简写
END = b"\x0b"
def I32C(v): return b"\x41" + si(v)
def LGET(i): return b"\x20" + u(i)
def LSET(i): return b"\x21" + u(i)
def LTEE(i): return b"\x22" + u(i)
def GGET(i): return b"\x23" + u(i)
def GSET(i): return b"\x24" + u(i)
def CALL(i): return b"\x10" + u(i)
def BR(d): return b"\x0c" + u(d)
def BRIF(d): return b"\x0d" + u(d)
BLOCK = b"\x02\x40"
LOOP = b"\x03\x40"
IF = b"\x04\x40"
ELSE = b"\x05"
RET = b"\x0f"
DROP = b"\x1a"
def LOAD(off=0): return b"\x28" + u(0) + u(off)
def LOAD8S(off=0): return b"\x2c" + u(0) + u(off)
def LOAD8U(off=0): return b"\x2d" + u(0) + u(off)
def STORE(off=0): return b"\x36" + u(0) + u(off)
def STORE8(off=0): return b"\x3a" + u(0) + u(off)
ADD, SUB, MUL = b"\x6a", b"\x6b", b"\x6c"
DIVS, DIVU, REMS, REMU = b"\x6d", b"\x6e", b"\x6f", b"\x70"
EQZ, EQ, NE = b"\x45", b"\x46", b"\x47"
LTS, LTU, GTS, GTU = b"\x48", b"\x49", b"\x4a", b"\x4b"
LES, LEU, GES, GEU = b"\x4c", b"\x4d", b"\x4e", b"\x4f"
SELECT = b"\x1b"


# ---------------------------------------------------------------- 参考解释器
class Ref:
    """直接执行栈式字节码（Wasm 规范语义），作为翻译器的差分 oracle。
    内存模型与翻译器保持一致：1 byte = 1 tryte，i32 用 4 个平衡 243 进制位。"""

    def __init__(self, mod, segs):
        self.mod = mod
        self.mem = bytearray(trirv_wasm.WASM_MEM_SIZE)
        for a, d in segs.items():
            for i, c in enumerate(bytes(d)):
                self.mem[a + i] = c % 243
        self.glob = [g[2] & (M32 - 1) for g in mod["globals"]]
        self.out = bytearray()
        self.ftype = [mod["types"][t] for k, m, f, t in mod["imports"] if k == "func"]
        self.ftype += [mod["types"][t] for t in mod["funcs"]]
        self.nimp = sum(1 for i in mod["imports"] if i[0] == "func")

    # ---- 内存 ----
    def tryte(self, a):
        return self.mem[a] if 0 <= a < len(self.mem) else 0

    def sty(self, i):
        return i - 243 if i > 121 else i

    def ld32(self, a):
        v, p = 0, 1
        for k in range(4):
            v += self.sty(self.tryte(a + k)) * p
            p *= 243
        return s32(v)

    def st32(self, a, v):
        v = s32(v)
        for k in range(4):
            d = v % 243
            if d > 121:
                d -= 243
            self.mem[a + k] = d % 243
            v = (v - d) // 243

    # ---- 结构预处理：给每个 block/loop/if 找到 else / end 位置 ----
    @staticmethod
    def scan(body):
        match, stack, p = {}, [], 0
        while p < len(body):
            op = body[p]
            if op in (0x02, 0x03, 0x04):
                stack.append([p, None, None]); p += 2; continue
            if op == 0x05:
                stack[-1][1] = p; p += 1; continue
            if op == 0x0B:
                if stack:
                    e = stack.pop()
                    match[e[0]] = (e[1], p)
                p += 1; continue
            p += 1
            p = trirv_wasm.skip_imm(body, p, op)
        return match

    # ---- 执行 ----
    def call(self, fi, args):
        if fi < self.nimp:
            if self.mod["imports"][fi][2] == "print":
                p, n = args
                self.out += bytes(self.mem[p:p + n])
                return []
            raise RuntimeError("未知导入")
        groups, body = self.mod["codes"][fi - self.nimp]
        params, results = self.ftype[fi]
        locs = list(args) + [0] * sum(n for n, _ in groups)
        match = self.scan(body)
        st, ctrl, pc = [], [], 0
        while pc < len(body):
            op = body[pc]; pc += 1

            if op in (0x02, 0x03):
                pc += 1
                els, e = match[pc - 2]
                if op == 0x03:
                    ctrl.append({"kind": "loop", "start": pc, "end": e, "h": len(st)})
                else:
                    ctrl.append({"kind": "block", "end": e, "h": len(st)})
            elif op == 0x04:
                pc += 1
                els, e = match[pc - 2]
                c = st.pop()
                ctrl.append({"kind": "if", "end": e, "h": len(st)})
                if not c:
                    pc = (els + 1) if els is not None else e
            elif op == 0x05:
                pc = ctrl[-1]["end"]
            elif op == 0x0B:
                if not ctrl:
                    return st[len(st) - len(results):] if results else []
                ctrl.pop()
            elif op in (0x0C, 0x0D):
                d, pc = trirv_wasm.uleb(body, pc)
                take = True if op == 0x0C else (st.pop() != 0)
                if take:
                    tgt = ctrl[-1 - d]
                    del st[tgt["h"]:]
                    if tgt["kind"] == "loop":
                        pc = tgt["start"]
                    else:
                        del ctrl[len(ctrl) - d - 1:]
                        pc = tgt["end"] + 1
            elif op == 0x0F:
                return st[len(st) - len(results):] if results else []
            elif op == 0x00:
                raise RuntimeError("unreachable")
            elif op == 0x01:
                pass
            elif op == 0x10:
                fi2, pc = trirv_wasm.uleb(body, pc)
                n = len(self.ftype[fi2][0])
                a = st[len(st) - n:] if n else []
                if n:
                    del st[len(st) - n:]
                st += self.call(fi2, a)
            elif op == 0x20:
                i, pc = trirv_wasm.uleb(body, pc); st.append(locs[i])
            elif op in (0x21, 0x22):
                i, pc = trirv_wasm.uleb(body, pc)
                locs[i] = st.pop()
                if op == 0x22:
                    st.append(locs[i])
            elif op == 0x23:
                i, pc = trirv_wasm.uleb(body, pc); st.append(self.glob[i])
            elif op == 0x24:
                i, pc = trirv_wasm.uleb(body, pc); self.glob[i] = st.pop()
            elif op == 0x41:
                v, pc = trirv_wasm.sleb(body, pc); st.append(s32(v))
            elif op == 0x6A:
                b = st.pop(); a = st.pop(); st.append(s32(a + b))
            elif op == 0x6B:
                b = st.pop(); a = st.pop(); st.append(s32(a - b))
            elif op == 0x6C:
                b = st.pop(); a = st.pop(); st.append(s32(a * b))
            elif op in (0x6D, 0x6E, 0x6F, 0x70):
                b = st.pop(); a = st.pop()
                if b == 0:
                    raise RuntimeError("除零")
                if op == 0x6D:
                    q = abs(a) // abs(b) * (1 if (a < 0) == (b < 0) else -1)
                    st.append(s32(q))
                elif op == 0x6E:
                    st.append(s32(u32(a) // u32(b)))
                elif op == 0x6F:
                    q = abs(a) // abs(b) * (1 if (a < 0) == (b < 0) else -1)
                    st.append(s32(a - q * b))
                else:
                    st.append(s32(u32(a) % u32(b)))
            elif op == 0x45:
                st.append(1 if st.pop() == 0 else 0)
            elif op in (0x46, 0x47):
                b = st.pop(); a = st.pop()
                st.append(1 if (a == b) == (op == 0x46) else 0)
            elif 0x48 <= op <= 0x4F:
                b = st.pop(); a = st.pop()
                uns = op in (0x49, 0x4B, 0x4D, 0x4F)
                x, y = (u32(a), u32(b)) if uns else (a, b)
                r = {0x48: x < y, 0x49: x < y, 0x4A: x > y, 0x4B: x > y,
                     0x4C: x <= y, 0x4D: x <= y, 0x4E: x >= y, 0x4F: x >= y}[op]
                st.append(1 if r else 0)
            elif op == 0x1A:
                st.pop()
            elif op == 0x1B:
                c = st.pop(); b = st.pop(); a = st.pop()
                st.append(a if c else b)
            elif op == 0x28:
                _, pc = trirv_wasm.uleb(body, pc); off, pc = trirv_wasm.uleb(body, pc)
                st.append(self.ld32(st.pop() + off))
            elif op in (0x2C, 0x2D):
                _, pc = trirv_wasm.uleb(body, pc); off, pc = trirv_wasm.uleb(body, pc)
                t = self.tryte(st.pop() + off)
                st.append(self.sty(t) if op == 0x2C else t)
            elif op == 0x36:
                _, pc = trirv_wasm.uleb(body, pc); off, pc = trirv_wasm.uleb(body, pc)
                v = st.pop(); a = st.pop()
                self.st32(a + off, v)
            elif op == 0x3A:
                _, pc = trirv_wasm.uleb(body, pc); off, pc = trirv_wasm.uleb(body, pc)
                v = st.pop(); a = st.pop()
                self.mem[a + off] = v % 243
            else:
                raise RuntimeError(f"参考解释器未实现 0x{op:02x}")
        return st[len(st) - len(results):] if results else []

    def run(self, entry):
        return self.call(entry, [])


# ---------------------------------------------------------------- 执行三路
def run_py_vm(asm, segs):
    prog, _ = trirv_asm.assemble(asm)
    m = {a: list(bytes(d)) for a, d in segs.items()}
    v = trirv_vm.VM(prog, mem_init=m)
    v.run(max_steps=20_000_000)
    return v.exit_code & 0xFF, trirv_vm.s(v.regs[10]), bytes(v.output)


def run_c_vm(asm, segs):
    with tempfile.TemporaryDirectory() as td:
        trv = os.path.join(td, "a.trv")
        build_trv(asm, segs, trv)
        r = subprocess.run(["./trirv_vm", trv], capture_output=True)
        for ln in r.stderr.decode("utf-8", "replace").splitlines():
            if "exit=" in ln:
                return int(ln.split("exit=")[1].split()[0]), bytes(r.stdout)
    return None, b""


CASES = []


def case(name, w, entry="run"):
    CASES.append((name, w, entry))


def wrap1(body, args, nlocals=0, params=None):
    """把带形参的函数体包一个无参 wrapper，导出成 run —— 顺带测 call 路径。"""
    w = W()
    nps = len(args) if params is None else params
    t = w.type([I32] * nps, [I32])
    f = w.func(t, body, nlocals=nlocals)
    tw = w.type([], [I32])
    wb = b"".join(I32C(a) for a in args) + CALL(f) + END
    fw = w.func(tw, wb)
    w.export("run", 0, fw)
    w.mems.append(b"\x00")
    return w


def main():
    # 1) 基本算术与回绕
    body = LGET(0) + LGET(1) + ADD + END
    case("i32.add 3+4", wrap1(body, [3, 4]))
    case("i32.add 溢出 (2^31-1)+1", wrap1(body, [2**31 - 1, 1]))
    case("i32.add 下溢 (-2^31)+(-1)", wrap1(body, [-(2**31), -1]))
    case("i32.sub 小值", wrap1(LGET(0) + LGET(1) + SUB + END, [100, 234]))
    case("i32.mul 溢出 65536*65536", wrap1(LGET(0) + LGET(1) + MUL + END, [65536, 65536]))
    case("i32.mul 负数", wrap1(LGET(0) + LGET(1) + MUL + END, [-1234, 567]))

    # 2) 阶乘：循环 + 比较 + 乘法回绕
    # 退出循环必须 br 到外层 block：只 br 到 loop 是"重来"，不是"跳出"
    fact = (I32C(1) + LSET(1) +
            I32C(1) + LSET(2) +
            BLOCK + LOOP +
            LGET(2) + LGET(0) + GTU + IF + BR(2) + END +
            LGET(1) + LGET(2) + MUL + LSET(1) +
            LGET(2) + I32C(1) + ADD + LSET(2) +
            BR(0) +
            END + END + LGET(1) + END)
    for n in (0, 1, 5, 10, 12):
        case(f"阶乘 {n}", wrap1(fact, [n], nlocals=2))

    # 3) 内存读写（含负数）
    mem = (I32C(0) + LGET(0) + STORE() +
           I32C(4) + LGET(1) + STORE() +
           I32C(0) + LOAD() + LSET(2) +
           I32C(4) + LOAD() + LGET(2) + SUB + END)
    for a, b in ((1000, 234), (-234, 1000), (1743392200, -1743392200)):
        case(f"内存 {a}-({b})", wrap1(mem, [a, b], nlocals=1))

    # 4) 单字节存取
    mem8 = (I32C(100) + LGET(0) + STORE8() + I32C(100) + LOAD8U() + END)
    case("store8/load8_u 200", wrap1(mem8, [200]))
    case("store8/load8_s 200", wrap1(I32C(100) + LGET(0) + STORE8() +
                                    I32C(100) + LOAD8S() + END, [200]))

    # 5) 除 / 余
    for nm2, op, a, b in [("div_s 7/2", DIVS, 7, 2), ("div_s -7/2", DIVS, -7, 2),
                          ("rem_s -7%2", REMS, -7, 2), ("div_u -7/2", DIVU, -7, 2),
                          ("rem_u -7%2", REMU, -7, 2), ("div_s 大数", DIVS, 2**31 - 1, 7)]:
        case(nm2, wrap1(LGET(0) + LGET(1) + op + END, [a, b]))

    # 6) 比较
    for nm2, op in [("lt_s", LTS), ("lt_u", LTU), ("gt_s", GTS), ("gt_u", GTU),
                    ("le_s", LES), ("le_u", LEU), ("ge_s", GES), ("ge_u", GEU),
                    ("eq", EQ), ("ne", NE)]:
        for a, b in ((3, 5), (-1, 1), (7, 7)):
            case(f"cmp {nm2} {a},{b}", wrap1(LGET(0) + LGET(1) + op + END, [a, b]))
    case("eqz 0", wrap1(LGET(0) + EQZ + END, [0]))
    case("eqz -5", wrap1(LGET(0) + EQZ + END, [-5]))

    # 7) 递归调用：fib
    w = W()
    t = w.type([I32], [I32])
    fib = (LGET(0) + I32C(2) + LTS + IF + LGET(0) + RET + END +
           LGET(0) + I32C(1) + SUB + CALL(0) +
           LGET(0) + I32C(2) + SUB + CALL(0) + ADD + END)
    w.func(t, fib)
    tw = w.type([], [I32])
    fw = w.func(tw, I32C(12) + CALL(0) + END)
    w.export("run", 0, fw)
    w.mems.append(b"\x00")
    case("递归 fib(12)", w)

    # 8) select
    case("select 真", wrap1(LGET(0) + LGET(1) + I32C(1) + SELECT + END, [111, 222]))
    case("select 假", wrap1(LGET(0) + LGET(1) + I32C(0) + SELECT + END, [111, 222]))

    # 9) hello world：data 段 + 导入 print
    w = W()
    tp = w.type([I32, I32], [])
    tm = w.type([], [I32])
    imp = w.imp("env", "print", tp)
    msg = "三进制 TriRV 跑起了 Wasm!\n".encode()
    f = w.func(tm, I32C(0) + I32C(len(msg)) + CALL(imp) + I32C(0) + END)
    w.export("main", 0, f)
    w.mems.append(b"\x00")
    w.data(0, msg)
    case("hello wasm-on-trirv", w, "main")

    # ---- 执行 ----
    ok = fail = skip = 0
    for nm2, w, entry in CASES:
        data = w.build()
        try:
            asm, segs, _ = compile_wasm(data, entry)
        except Unsupported as e:
            print(f"SKIP  {nm2}: {e}")
            skip += 1
            continue
        mod = trirv_wasm.parse_module(data)
        ref = Ref(mod, segs)
        want = ref.run(mod["exports"][0][entry])
        want_v = trirv_vm.s(want[0]) if want else 0
        want_out = bytes(ref.out)

        py_exit, py_a0, py_out = run_py_vm(asm, segs)
        c_exit, c_out = run_c_vm(asm, segs)

        good = (py_a0 == want_v and py_out == want_out and
                c_exit == (want_v & 0xFF) and c_out == want_out)
        if good:
            print(f"PASS  {nm2:30s} = {want_v:12d}  {want_out!r}")
            ok += 1
        else:
            print(f"FAIL  {nm2:30s} ref={want_v} py(a0={py_a0},out={py_out!r}) "
                  f"c(exit={c_exit},out={c_out!r})")
            fail += 1
    print(f"\n总计 {ok} PASS / {fail} FAIL / {skip} SKIP")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
