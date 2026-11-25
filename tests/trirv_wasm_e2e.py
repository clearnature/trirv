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

import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import trirv_asm
import trirv_vm
import trirv_wasm
import trirv_float as tf
from trirv_wasm import compile_wasm, build_trv, Unsupported

M32 = 2 ** 32


def _tf_is_nan(F, raw):
    """TF raw → NaN 判定（E 全 1 且 mant ≠ 0）。"""
    rest = raw % F.P_SIGN
    E = rest // F.P_MANT
    M = rest % F.P_MANT
    return E == F.max_exp and M != 0                          # 历史名（仅个别残留；§九 后不参与语义）
MOD45 = 3 ** 45


def s45(v):
    """45-trit word 的带符号视图（原生 word 语义，§九）。"""
    v %= MOD45
    return v - MOD45 if v >= MOD45 // 2 else v


def tritwise(a, b, fn):
    """45-trit 逐 trit：fn(ta,tb)∈[0,2] → word。and=min, or=max, xor=(ta-tb)%3。"""
    ua, ub = a % MOD45, b % MOD45
    out = 0
    pw = 1
    for _ in range(45):
        t = fn(ua % 3, ub % 3)
        ua //= 3
        ub //= 3
        out += t * pw
        pw *= 3
    return out
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
F32 = 0x7D
F64 = 0x7C


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

    def func(self, tidx, body, nlocals=0, local_types=None):
        """body 已含末尾 0x0B。nlocals 个 i32 局部变量；
        P3：local_types 可给每个 local 指定 valtype（长度须等于 nlocals）。"""
        self.funcs.append(u(tidx))
        if local_types is None:
            loc = vec([u(nlocals) + bytes([I32])] if nlocals else [])
        else:
            groups = {}
            for t in local_types:
                groups[t] = groups.get(t, 0) + 1
            loc = vec([u(c) + bytes([t]) for t, c in groups.items()])
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

# ---- 浮点指令简写（P3）----
def F64C(v): return b"\x44" + struct.pack("<d", v)
def F32C(v): return b"\x43" + struct.pack("<f", v)
F64ADD, F64SUB, F64MUL, F64DIV = b"\xa0", b"\xa1", b"\xa2", b"\xa3"
F32ADD, F32SUB, F32MUL, F32DIV = b"\x92", b"\x93", b"\x94", b"\x95"
F64SQRT, F32SQRT = b"\x9f", b"\x91"
F64ABS, F64NEG, F64MIN, F64MAX, F64CPS = (bytes([x]) for x in (0x99, 0x9a, 0xa4, 0xa5, 0xa6))
F32ABS, F32NEG, F32MIN, F32MAX, F32CPS = (bytes([x]) for x in (0x8b, 0x8c, 0x96, 0x97, 0x98))
F64EQ, F64NE, F64LT, F64GT, F64LE, F64GE = (bytes([x]) for x in range(0x61, 0x67))
F32EQ, F32NE, F32LT, F32GT, F32LE, F32GE = (bytes([x]) for x in range(0x5b, 0x61))
def FLOAD64(off=0): return b"\x2b" + u(0) + u(off)
def FLOAD32(off=0): return b"\x2a" + u(0) + u(off)
def FSTORE64(off=0): return b"\x39" + u(0) + u(off)
def FSTORE32(off=0): return b"\x38" + u(0) + u(off)
LTS, LTU, GTS, GTU = b"\x48", b"\x49", b"\x4a", b"\x4b"
LES, LEU, GES, GEU = b"\x4c", b"\x4d", b"\x4e", b"\x4f"
SELECT = b"\x1b"
BAND, BOR, BXOR = b"\x71", b"\x72", b"\x73"
SHL, SHRS, SHRU, ROTL, ROTR = b"\x74", b"\x75", b"\x76", b"\x77", b"\x78"
CLZ, CTZ, POPCNT = b"\x67", b"\x68", b"\x69"


# ---------------------------------------------------------------- 参考解释器
class Ref:
    """直接执行栈式字节码（Wasm 规范语义），作为翻译器的差分 oracle。
    内存模型与翻译器保持一致：1 byte = 1 tryte，i32 用 4 个平衡 243 进制位。"""

    def __init__(self, mod, segs):
        self.mod = mod
        self.mem = bytearray(trirv_wasm.WASM_MEM_SIZE)
        for a, d in segs.items():
            for i, c in enumerate(bytes(d)):
                if a + i < len(self.mem):        # 全局变量与 2^k 表不在线性内存里
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
        u = sum(self.tryte(a + k) * 3 ** (5 * k) for k in range(9))
        return u - MOD45 if u >= MOD45 // 2 else u

    def st32(self, a, v):
        # §九：i32.store = 原生 word（9 tryte，= VM _store_word 口径）
        v %= MOD45
        for k in range(9):
            self.mem[a + k] = (v // 3 ** (5 * k)) % 243

    def ld_raw(self, a):
        """9 tryte 小端 → TF raw（P3：f32/f64 的 raw 都占 9 tryte 槽）。"""
        return sum(self.tryte(a + k) * 243 ** k for k in range(9)) % (3 ** 45)

    def st_raw(self, a, raw):
        raw %= 3 ** 45
        for k in range(9):
            self.mem[a + k] = (raw // 243 ** k) % 243

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
                v, pc = trirv_wasm.sleb(body, pc); st.append(v)   # §九：原生 word 无 s32 截断
            elif op == 0x43:                       # f32.const：IEEE → TF20 raw
                v = struct.unpack("<f", body[pc:pc + 4])[0]; pc += 4
                st.append(tf.TF20.encode(v))
            elif op == 0x44:                       # f64.const：IEEE → TF45 raw
                v = struct.unpack("<d", body[pc:pc + 8])[0]; pc += 8
                st.append(tf.TF45.encode(v))
            elif op in (0x92, 0x93, 0x94, 0x95):   # f32.add/sub/mul/div
                b2 = st.pop(); a2 = st.pop(); F = tf.TF20
                st.append({0x92: lambda x,y,f: tf.f_add(x,y,f),
                           0x93: lambda x,y,f: tf.f_add(x,y,f,subtract=True),
                           0x94: tf.f_mul, 0x95: tf.f_div}[op](a2, b2, F))
            elif op in (0xA0, 0xA1, 0xA2, 0xA3):   # f64.add/sub/mul/div
                b2 = st.pop(); a2 = st.pop(); F = tf.TF45
                st.append({0xA0: lambda x,y,f: tf.f_add(x,y,f),
                           0xA1: lambda x,y,f: tf.f_add(x,y,f,subtract=True),
                           0xA2: tf.f_mul, 0xA3: tf.f_div}[op](a2, b2, F))
            elif op in (0x91, 0x9F):               # f32.sqrt / f64.sqrt
                F = tf.TF20 if op == 0x91 else tf.TF45
                st.append(tf.f_sqrt(st.pop(), F))
            elif 0x5B <= op <= 0x66:              # f32/f64 比较
                b2 = st.pop(); a2 = st.pop()
                F = tf.TF20 if op <= 0x60 else tf.TF45
                base = 0x5B if op <= 0x60 else 0x61
                k = op - base
                opn = ("eq", "eq", "lt", "lt", "le", "le")[k]
                # ne = !eq；gt = lt(b,a)；ge = le(b,a)
                if k == 1:
                    r = tf.f_cmp(a2, b2, F, "eq"); st.append(1 - r)
                elif k in (3, 5):
                    st.append(tf.f_cmp(b2, a2, F, opn))
                else:
                    st.append(tf.f_cmp(a2, b2, F, opn))
            elif op == 0x6A:                       # §九：原生 word，无回绕
                b = st.pop(); a = st.pop(); st.append(s45(a + b))
            elif op == 0x6B:
                b = st.pop(); a = st.pop(); st.append(s45(a - b))
            elif op == 0x6C:
                b = st.pop(); a = st.pop(); st.append(s45(a * b))
            elif op in (0x6D, 0x6E):               # div/rem 有符号截断（div 零=trap）
                b = st.pop(); a = st.pop()
                if b == 0:
                    raise RuntimeError("trap: 除零")
                q = abs(a) // abs(b) * (1 if (a < 0) == (b < 0) else -1)
                st.append(s45(q) if op == 0x6D else s45(a - q * b))
            elif op in (0x6F, 0x70):
                raise RuntimeError("div_u/rem_u：§九 无符号视图已删")
            elif op in (0x8B, 0x8C, 0x99, 0x9A):   # abs / neg（TF raw 符号 trit）
                v = st.pop()
                F = tf.TF45 if op in (0x99, 0x9A) else tf.TF20
                ps = F.P_SIGN
                st.append(v % ps if op in (0x8B, 0x99) else (v + ps if v < ps else v - ps))
            elif op in (0x98, 0xA6):               # copysign：幅 a、号 b
                b = st.pop(); a = st.pop()
                F = tf.TF45 if op == 0xA6 else tf.TF20
                ps = F.P_SIGN
                st.append((a % ps) + (b // ps) * ps)
            elif op in (0x96, 0x97, 0xA4, 0xA5):   # min / max（NaN 传染 + ±0 号合并）
                b = st.pop(); a = st.pop()
                F = tf.TF45 if op in (0xA4, 0xA5) else tf.TF20
                is_min = op in (0x96, 0xA4)
                na = _tf_is_nan(F, a); nb = _tf_is_nan(F, b)
                if na or nb:
                    st.append(F.NAN)
                else:
                    va, vb = F.decode(a), F.decode(b)
                    if va == 0.0 and vb == 0.0:
                        st.append(F.P_SIGN if is_min else 0)   # ±0：min −0 / max +0
                    else:
                        st.append(a if ((va < vb) == is_min or va == vb) else b)
            elif op in (0x71, 0x72, 0x73):         # 逐 trit：TAND/TOR/XOR
                b = st.pop(); a = st.pop()
                fn = {0x71: min, 0x72: max,
                      0x73: lambda x, y: (x + y) % 3}[op]   # ISA XOR=(ta+tb)%3 非自反
                st.append(tritwise(a, b, fn))
            elif op in (0x74, 0x75):               # 原生三进移位：×3^k / ÷3^k 截断
                b = st.pop(); a = st.pop()
                k = b % (3 ** 7)
                if op == 0x74:
                    st.append(s45(a * pow(3, k, MOD45)))
                else:
                    av = s45(a)
                    if k >= 45:
                        st.append(0)
                    else:
                        q = abs(av) // (3 ** k)
                        st.append(s45(q if av >= 0 else -q))
            elif op in (0x76, 0x77, 0x78):
                raise RuntimeError("shr_u/rotl/rotr：§九 删")
            elif op in (0x67, 0x68, 0x69):
                raise RuntimeError("clz/ctz/popcnt：§九 删")
            elif op == 0x45:
                st.append(1 if st.pop() == 0 else 0)
            elif op in (0x46, 0x47):
                b = st.pop(); a = st.pop()
                st.append(1 if (a == b) == (op == 0x46) else 0)
            elif 0x48 <= op <= 0x4F:
                if op in (0x49, 0x4B, 0x4D, 0x4F):
                    raise RuntimeError("无符号比较：§九 删")
                b = st.pop(); a = st.pop()
                x, y = s45(a), s45(b)
                r = {0x48: x < y, 0x4A: x > y,
                     0x4C: x <= y, 0x4E: x >= y}[op]
                st.append(1 if r else 0)
            elif op == 0x1A:
                st.pop()
            elif op == 0x1B:
                c = st.pop(); b = st.pop(); a = st.pop()
                st.append(a if c else b)
            elif op in (0x2A, 0x2B):               # f32.load / f64.load
                _, pc = trirv_wasm.uleb(body, pc); off, pc = trirv_wasm.uleb(body, pc)
                st.append(self.ld_raw(st.pop() + off))
            elif op in (0x38, 0x39):               # f32.store / f64.store
                _, pc = trirv_wasm.uleb(body, pc); off, pc = trirv_wasm.uleb(body, pc)
                a = st.pop(); v = st.pop()   # wasm 规范：address 在顶
                self.st_raw(a + off, v)
            elif op == 0x28:
                _, pc = trirv_wasm.uleb(body, pc); off, pc = trirv_wasm.uleb(body, pc)
                st.append(self.ld32(st.pop() + off))
            elif op in (0x2C, 0x2D):
                _, pc = trirv_wasm.uleb(body, pc); off, pc = trirv_wasm.uleb(body, pc)
                t = self.tryte(st.pop() + off)
                st.append(self.sty(t) if op == 0x2C else t)
            elif op == 0x36:
                _, pc = trirv_wasm.uleb(body, pc); off, pc = trirv_wasm.uleb(body, pc)
                a = st.pop(); v = st.pop()   # wasm 规范：address 在顶
                self.st32(a + off, v)
            elif op == 0x3A:
                _, pc = trirv_wasm.uleb(body, pc); off, pc = trirv_wasm.uleb(body, pc)
                a = st.pop(); v = st.pop()   # wasm 规范：address 在顶
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
        r = subprocess.run([os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vm", "bin", "trirv_vm"), trv], capture_output=True)
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


def run_float_case(name, w, entry, check_raw=None):
    """P3 浮点专用三路差分：编译 → Ref(TF oracle) → PyVM → C VM。
    check_raw: 可选 lambda(raw)->bool，对 Ref 输出的 TF raw 再做语义断言。"""
    data = w.build()
    try:
        asm, segs, _ = compile_wasm(data, entry)
    except Unsupported as e:
        print(f"SKIP  {name}: {e}")
        return 0, 0, 1
    mod = trirv_wasm.parse_module(data)
    ref = Ref(mod, segs)
    want = ref.run(mod["exports"][0][entry])
    want_v = trirv_vm.s(want[0]) if want else 0
    want_out = bytes(ref.out)
    py_exit, py_a0, py_out = run_py_vm(asm, segs)
    c_exit, c_out = run_c_vm(asm, segs)
    good = (py_a0 == want_v and py_out == want_out and
            c_exit == (want_v & 0xFF) and c_out == want_out)
    if check_raw is not None and want_out:
        raw = sum(want_out[k] * 243 ** k for k in range(min(9, len(want_out))))
        good = good and check_raw(raw)
    if good:
        print(f"PASS  {name:32s} = a0:{want_v:3d} out:{want_out!r}")
        return 1, 0, 0
    print(f"FAIL  {name:32s} ref={want_v},{want_out!r} "
          f"py({py_a0},{py_out!r}) c({c_exit},{c_out!r})")
    return 0, 1, 0


def float_suite():
    """P3 浮点用例集。统一 import print，函数把 TF raw f64.store 到内存再输出，
    三路差分对比 9 tryte 输出；期望值由 Ref 用 trirv_float 语义独立算出。"""
    ok = fail = skip = 0
    PRINT_ADDR = 100                       # 线性内存里的结果槽

    def mk(body):
        w = W()
        tp = w.type([I32, I32], [])
        tm = w.type([], [I32])
        imp = w.imp("env", "print", tp)
        # §九 规范序：value（body 结果）先压、address 后压在顶
        fn = w.func(tm, body + I32C(PRINT_ADDR) + FSTORE64(0) +
                    I32C(PRINT_ADDR) + I32C(9) + CALL(imp) + I32C(0) + END)
        w.export("run", 0, fn)
        w.mems.append(b"\x00")
        return w

    def near(v, tol=1e-4):
        return lambda raw: abs(tf.TF45.decode(raw) - v) <= tol

    def near32(v, tol=1e-2):
        return lambda raw: abs(tf.TF20.decode(raw) - v) <= tol

    cases64 = [
        ("wasm f64 1.5+2.25=3.75", F64C(1.5) + F64C(2.25) + F64ADD, near(3.75)),
        ("wasm f64 3.0*2.0=6.0", F64C(3.0) + F64C(2.0) + F64MUL, near(6.0)),
        ("wasm f64 9.0/2.0=4.5", F64C(9.0) + F64C(2.0) + F64DIV, near(4.5)),
        ("wasm f64 2.25-1.5=0.75", F64C(2.25) + F64C(1.5) + F64SUB, near(0.75)),
        ("wasm f64 sqrt(9)=3", F64C(9.0) + F64SQRT, near(3.0)),
        ("wasm f64 abs(-3)=3", F64C(-3.0) + F64ABS, near(3.0)),
        ("wasm f64 neg(3)=-3", F64C(3.0) + F64NEG, near(-3.0)),
        ("wasm f64 copysign(-3,+5)=3", F64C(-3.0) + F64C(5.0) + F64CPS, near(3.0)),
        ("wasm f64 min(-3,5)=-3", F64C(-3.0) + F64C(5.0) + F64MIN, near(-3.0)),
        ("wasm f64 max(-3,5)=5", F64C(-3.0) + F64C(5.0) + F64MAX, near(5.0)),
        ("wasm f64 min(+5,-3)=-3", F64C(5.0) + F64C(-3.0) + F64MIN, near(-3.0)),
    ]
    for name, body, chk in cases64:
        a, b, s = run_float_case(name, mk(body), "run", chk)
        ok += a; fail += b; skip += s

    cases32 = [
        ("wasm f32 1.5+2.25=3.75", F32C(1.5) + F32C(2.25) + F32ADD, near32(3.75)),
        ("wasm f32 3.0*2.0=6.0", F32C(3.0) + F32C(2.0) + F32MUL, near32(6.0)),
        ("wasm f32 sqrt(9)=3", F32C(9.0) + F32SQRT, near32(3.0)),
        ("wasm f32 abs(-3)=3", F32C(-3.0) + F32ABS, near32(3.0)),
        ("wasm f32 min(-3,5)=-3", F32C(-3.0) + F32C(5.0) + F32MIN, near32(-3.0)),
    ]
    for name, body, chk in cases32:
        a, b, s = run_float_case(name, mk(body), "run", chk)
        ok += a; fail += b; skip += s

    # 比较类：结果 0/1 直接由 a0 观察（函数返回比较结果，无 print）
    def mkcmp(body):
        w = W()
        tm = w.type([], [I32])
        fn = w.func(tm, body + END)
        w.export("run", 0, fn)
        w.mems.append(b"\x00")
        return w

    cmp_cases = [
        ("wasm f64 1.5<2.25", F64C(1.5) + F64C(2.25) + F64LT, 1),
        ("wasm f64 2.25>1.5", F64C(2.25) + F64C(1.5) + F64GT, 1),
        ("wasm f64 1.5==2.25", F64C(1.5) + F64C(2.25) + F64EQ, 0),
        ("wasm f64 1.5!=2.25", F64C(1.5) + F64C(2.25) + F64NE, 1),
        ("wasm f64 1.5<=1.5", F64C(1.5) + F64C(1.5) + F64LE, 1),
        ("wasm f64 2.25>=1.5", F64C(2.25) + F64C(1.5) + F64GE, 1),
        ("wasm f32 1.5<2.25", F32C(1.5) + F32C(2.25) + F32LT, 1),
        ("wasm f32 1.5==2.25", F32C(1.5) + F32C(2.25) + F32EQ, 0),
    ]
    for name, body, expected in cmp_cases:
        a, b, s = run_float_case(name, mkcmp(body), "run", lambda raw, e=expected: True)
        # 比较类 a0 由三路差分已锁定；再单独断言 a0 == expected
        if a:
            w = mkcmp(body)
            data = w.build()
            asm, segs, _ = compile_wasm(data, "run")
            py_a0 = run_py_vm(asm, segs)[1]
            if py_a0 != expected:
                a, b = 0, 1
                print(f"FAIL  {name:32s} a0={py_a0} want={expected}")
        ok += a; fail += b; skip += s

    # 浮点局部变量：local f64 / f32 的 set/get/tee
    w = W()
    tp = w.type([I32, I32], [])
    tm = w.type([], [I32])
    imp = w.imp("env", "print", tp)
    body = (F64C(5.0) + LSET(0) +                       # local 0 ← 5.0
            LGET(0) + F64C(3.0) + F64ADD +                      # 5.0 + 3.0
            I32C(PRINT_ADDR) + FSTORE64(0) + I32C(PRINT_ADDR) + I32C(9) + CALL(imp) + I32C(0) + END)
    fn = w.func(tm, body, nlocals=1, local_types=[F64])
    w.export("run", 0, fn); w.mems.append(b"\x00")
    a, b, s = run_float_case("wasm f64 局部变量 set/get", w, "run", near(8.0))
    ok += a; fail += b; skip += s

    body32 = (F32C(4.0) + LSET(0) +
              LGET(0) + F32C(2.0) + F32MUL +
              I32C(PRINT_ADDR) + FSTORE64(0) + I32C(PRINT_ADDR) + I32C(9) + CALL(imp) + I32C(0) + END)
    w2 = W(); tp2 = w2.type([I32, I32], []); tm2 = w2.type([], [I32])
    imp2 = w2.imp("env", "print", tp2)
    fn2 = w2.func(tm2, body32, nlocals=1, local_types=[F32])
    w2.export("run", 0, fn2); w2.mems.append(b"\x00")
    a, b, s = run_float_case("wasm f32 局部变量 set/get", w2, "run", near32(8.0))
    ok += a; fail += b; skip += s

    # 双档共存：同模块两个函数，一个 f64、一个 f32，验证档位切换不串
    w = W()
    tp = w.type([I32, I32], [])
    tm = w.type([], [I32])
    imp = w.imp("env", "print", tp)
    f64 = w.func(tm, F64C(1.5) + F64C(2.25) + F64ADD +
                 I32C(PRINT_ADDR) + FSTORE64(0) + I32C(PRINT_ADDR) + I32C(9) +
                 CALL(imp) + I32C(0) + END)
    f32 = w.func(tm, F32C(3.0) + F32C(2.0) + F32MUL +
                 I32C(PRINT_ADDR) + FSTORE64(0) + I32C(PRINT_ADDR) + I32C(9) +
                 CALL(imp) + I32C(0) + END)
    w.export("run", 0, f64)                    # 入口跑 f64 档
    w.mems.append(b"\x00")
    a, b, s = run_float_case("wasm 双档共存(入口 f64)", w, "run", near(3.75))
    ok += a; fail += b; skip += s

    return ok, fail, skip


def main():
    # 1) 基本算术与回绕
    body = LGET(0) + LGET(1) + ADD + END
    case("i32.add 3+4", wrap1(body, [3, 4]))
    case("i32.add 大数 (2^31-1)+1", wrap1(body, [2**31 - 1, 1]))
    case("i32.add 大负数 (-2^31)+(-1)", wrap1(body, [-(2**31), -1]))
    case("i32.sub 小值", wrap1(LGET(0) + LGET(1) + SUB + END, [100, 234]))
    case("i32.mul 65536*65536", wrap1(LGET(0) + LGET(1) + MUL + END, [65536, 65536]))
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
    mem = (LGET(0) + I32C(0) + STORE() +
           LGET(1) + I32C(9) + STORE() +
           I32C(0) + LOAD() + LSET(2) +
           I32C(9) + LOAD() + LGET(2) + SUB + END)
    for a, b in ((1000, 234), (-234, 1000), (1743392200, -1743392200)):
        case(f"内存 {a}-({b})", wrap1(mem, [a, b], nlocals=1))

    # 4) 单字节存取
    mem8 = (LGET(0) + I32C(100) + STORE8() + I32C(100) + LOAD8U() + END)
    case("store8/load8_u 200", wrap1(mem8, [200]))
    case("store8/load8_s 200", wrap1(LGET(0) + I32C(100) + STORE8() +
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

    # 6b) 二进制位视图：三进制寄存器里没有二进制位，全靠比较走查拆出来
    #     这组用例既验语义，也验"负数走无符号位模式"和 32 位边界
    bits = [
        ("and 0x0F0F", 0x0F0F, 0x00FF), ("and 负数", -1, 0x0FF0),
        ("and 2^31", 2**31, -1), ("or 高低位", 0x00FF, 0x0F00),
        ("or 负数", -256, 0x0F), ("xor 同值", 12345, 12345),
        ("xor 负数", -12345, 0x1234), ("xor 2^31-1", 2**31 - 1, 2**31),
    ]
    for nm2, a, b in bits:
        op = BAND if nm2.startswith("and") else BOR if nm2.startswith("or") else BXOR
        case(f"bit {nm2} {a},{b}", wrap1(LGET(0) + LGET(1) + op + END, [a, b]))

    sh = [
        ("shl 1<<31", SHL, 1, 31), ("shl 负数", SHL, -5, 1),
        ("shl 0", SHL, 7, 0), ("shl 越界 k=33", SHL, 1, 33),
        ("shr_s -5>>1", SHRS, -5, 1), ("shr_s -4>>1", SHRS, -4, 1),
        ("shr_s 2^31>>31", SHRS, -(2**31), 31), ("shr_u -1>>1", SHRU, -1, 1),
        ("shr_u 0>>31", SHRU, 0, 31), ("rotl 1,1", ROTL, 1, 1),
        ("rotl 2^31,1", ROTL, -(2**31), 1), ("rotl 0 位", ROTL, 0x12345678, 0),
        ("rotr 1,1", ROTR, 1, 1), ("rotr 负数", ROTR, -12345, 7),
        ("rotr 0 位", ROTR, 0x12345678, 0),
    ]
    for nm2, op, a, b in sh:
        case(f"bit {nm2}", wrap1(LGET(0) + LGET(1) + op + END, [a, b]))

    cnt = [("clz 1", CLZ, 1), ("clz 0", CLZ, 0), ("clz -1", CLZ, -1),
           ("clz 0x00FF", CLZ, 0x00FF),
           ("ctz 1", CTZ, 1), ("ctz 0", CTZ, 0), ("ctz -8", CTZ, -8),
           ("ctz 0x100", CTZ, 0x100),
           ("popcnt -1", POPCNT, -1), ("popcnt 0", POPCNT, 0),
           ("popcnt 0x0F0F", POPCNT, 0x0F0F)]
    for nm2, op, a in cnt:
        case(f"bit {nm2}", wrap1(LGET(0) + op + END, [a]))

    # 6c) 端到端：三值 FNV 变体哈希内存 tryte —— load8_u + xor + mul + 循环
    #     （§九：无 2^32 回绕/xor 语义，FNV 常数三值化：hash = (hash XOR
    #     trit) × 16777619 原生 word 运算；load8_u 读 tryte 0..242）
    blob = b"ternary wasm on trirv"
    fnv = (I32C(2166136261) + LSET(2) +
           BLOCK + LOOP +
           LGET(0) + LGET(1) + GTS + IF + BR(2) + END +
           LGET(2) + LGET(0) + LOAD8U() + BXOR + LSET(2) +
           LGET(2) + I32C(16777619) + MUL + LSET(2) +
           LGET(0) + I32C(1) + ADD + LSET(0) +
           BR(0) + END + END + LGET(2) + END)
    w = wrap1(fnv, [0, len(blob)], nlocals=1)
    w.data(0, blob)
    case("三值FNV 哈希内存 tryte", w)

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

    # P3 浮点套件
    fo, ff, fs = float_suite()
    ok += fo; fail += ff; skip += fs

    print(f"\n总计 {ok} PASS / {fail} FAIL / {skip} SKIP")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
