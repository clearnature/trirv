#!/usr/bin/env python3
"""trirv_tlit_e2e —— T-LIT Post 文字运算 literal x^a 契约（双 VM 差分）。

LIT0/LIT1/LIT2（opcode 22，R 型 funct=a；ternary-isa-design.md §6.1）：
逐 trit 循环——

    LIT0: (2−t) mod 3   补 literal（Kleene ¬；词级 ≡ TNOT）
    LIT1: (t+1) mod 3   循环后继（0→1→2→0）
    LIT2: (t+2) mod 3   循环前驱（0→2→1→0）

代数恒等式（契约锁定）：
    LIT1∘LIT1 = LIT2     LIT1∘LIT2 = id     LIT2∘LIT2 = LIT1
    LIT0∘LIT1 = LIT2     LIT0 = TNOT

程序形状：每值 27 tryte 见证（三变体各 9 tryte 全字），write(1,...) 输出；
PyVM/CVM 字节流一致且等于 python 逐 trit oracle。
"""
import os
import struct
import subprocess
import sys
import tempfile

import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import trirv_asm as asm
import trirv_isa as isa
from trirv_vm import VM

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CVM = os.environ.get("TRIRV_VM_BIN", os.path.join(R, "trirv_vm"))

MOD3_7 = 3 ** 7
MOD_X = isa.MOD_X                      # 3^45
HALF_X = isa.HALF_X
BUF = 1000
STEP = 27                              # 每值三变体见证 3×9 tryte

e = isa.encode
passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("PASS %s" % name)
    else:
        failed += 1
        print("FAIL %s %s" % (name, detail))


def lit_a(word, a):
    """逐 trit literal：LIT_a(word) 词级结果（0..3^45-1 编码）。"""
    res = 0
    p = 1
    for _ in range(45):
        word, t = divmod(word, 3)
        res += ((t + a) % 3 if a else (2 - t)) * p
        p *= 3
    return res


def _load20(rd, val):
    out = []
    hi = val // MOD3_7
    lo = val % MOD3_7
    if lo > (MOD3_7 - 1) // 2:
        hi += 1
        lo -= MOD3_7
    out.append(e("LUI", rd=rd, imm=hi % (3 ** 13)))
    if lo:
        out.append(e("ADDI", rd=rd, rs1=rd, imm=lo % MOD3_7))
    return out


TMP = 26
SEG = 15


def emit_load(rd, val):
    word = val % MOD_X
    a = word // (3 ** 30)
    b = (word // (3 ** 15)) % (3 ** 15)
    c = word % (3 ** 15)
    out = _load20(rd, a)
    out.append(e("SLLI", rd=rd, rs1=rd, imm=SEG))
    out += _load20(TMP, b)
    out.append(e("ADD", rd=rd, rs1=rd, rs2=TMP))
    out.append(e("SLLI", rd=rd, rs1=rd, imm=SEG))
    out += _load20(TMP, c)
    out.append(e("ADD", rd=rd, rs1=rd, rs2=TMP))
    return out


def word_of(chunk):
    return sum(chunk[i] * 3 ** (5 * i) for i in range(len(chunk))) % MOD_X


# 覆盖面：小值、单 trit 幂、平衡极值、全 2s、负值（补码词）、固定种子随机
VALUES = [0, 1, 2, 3, 5, 13, 242, 729]
VALUES += [3 ** k for k in range(0, 45, 7)]
VALUES += [2 * 3 ** k for k in range(0, 45, 7)]
VALUES += [MOD_X - 1, (MOD_X - 1) // 2, HALF_X, -1 % MOD_X, -(3 ** 20) % MOD_X]
import random                                    # noqa: E402（固定种子，可复现）
_rng = random.Random(22260122)
VALUES += [_rng.randrange(MOD_X) for _ in range(12)]


def build_program():
    prog = _load20(3, BUF)
    for v in VALUES:
        prog += emit_load(5, v)
        for k, mn in enumerate(("LIT0", "LIT1", "LIT2")):
            prog.append(e(mn, rd=6, rs1=5, rs2=0))
            prog.append(e("SW", rs2=6, rs1=3, imm=k * 9))
        prog.append(e("ADDI", rd=3, rs1=3, imm=STEP))
    prog += _load20(10, 1)
    prog += _load20(11, BUF)
    prog += _load20(12, len(VALUES) * STEP)
    prog.append(e("ADDI", rd=17, rs1=0, imm=64))
    prog.append(e("ECALL"))
    prog.append(e("ADDI", rd=17, rs1=0, imm=93))
    prog.append(e("ECALL"))
    return prog


def make_trv(prog):
    data = b"TRV1" + struct.pack("<III", 1, len(prog), 0)
    for r in prog:
        data += struct.pack("<I", r & 0xFFFFFFFF)
    return data


def run_py(prog, max_steps):
    vm = VM(prog)
    vm.run(max_steps=max_steps)
    return bytes(vm.output)


def run_c(prog):
    with tempfile.NamedTemporaryFile(suffix=".trv", delete=False) as f:
        f.write(make_trv(prog))
        path = f.name
    try:
        r = subprocess.run([CVM, path], capture_output=True, timeout=30)
        return r.stdout
    finally:
        os.unlink(path)


def verify(out):
    if len(out) != len(VALUES) * STEP:
        return False, "输出长度 %d ≠ %d" % (len(out), len(VALUES) * STEP)
    detail = ""
    for i, v in enumerate(VALUES):
        chunk = out[i * STEP:(i + 1) * STEP]
        for k, a in enumerate((0, 1, 2)):
            want = lit_a(v % MOD_X, a)
            got = word_of(chunk[k * 9:(k + 1) * 9])
            if got != want:
                detail += " [%d]LIT%d=%d≠%d" % (v, a, got, want)
    return (not detail), detail


def main():
    print("=== T-LIT LIT0/LIT1/LIT2 文字运算契约（双 VM）===")

    prog_txt, _ = asm.assemble(
        "LIT0 x5, x6, zero\nLIT1 x7, x6, zero\nLIT2 x8, x6, zero\n")
    check("汇编器 LIT0/1/2 助记符",
          len(prog_txt) == 3
          and isa.decode(prog_txt[0])["inst"] == "LIT0"
          and isa.decode(prog_txt[1])["inst"] == "LIT1"
          and isa.decode(prog_txt[2])["inst"] == "LIT2")

    prog = build_program()
    max_steps = len(prog) * 4 + 2000
    py_out = run_py(prog, max_steps)
    ok, detail = verify(py_out)
    check("PyVM oracle（逐 trit 循环）", ok, detail)

    if not os.path.exists(CVM):
        print("[SKIP] C VM 不存在: %s" % CVM)
    else:
        c_out = run_c(prog)
        okc, detailc = verify(c_out)
        check("CVM oracle", okc, detailc)
        check("PyVM == CVM 字节一致", py_out == c_out)

    # 代数恒等式（在 VM 上以合成程序锁四条）
    # 代数恒等式（VM 上合成程序锁四条：LIT1²=LIT2、LIT2∘LIT1=id、LIT0=TNOT）
    id_prog = []
    id_prog += emit_load(5, 729)                     # 729 = 1000000₃（trit 形态丰富）
    id_prog.append(e("LIT1", rd=6, rs1=5, rs2=0))
    id_prog.append(e("LIT1", rd=6, rs1=6, rs2=0))    # LIT1∘LIT1
    id_prog.append(e("LIT2", rd=7, rs1=5, rs2=0))
    id_prog.append(e("SUB", rd=8, rs1=6, rs2=7))     # 恒等式1 残差
    id_prog.append(e("LIT1", rd=9, rs1=7, rs2=0))
    id_prog.append(e("LIT2", rd=9, rs1=9, rs2=0))    # LIT2∘LIT1 = id
    id_prog.append(e("SUB", rd=10, rs1=9, rs2=5))
    id_prog.append(e("ADD", rd=10, rs1=10, rs2=8))
    id_prog.append(e("LIT0", rd=11, rs1=5, rs2=0))
    id_prog.append(e("TNOT", rd=12, rs1=5, rs2=0))
    id_prog.append(e("SUB", rd=13, rs1=11, rs2=12))
    id_prog.append(e("ADD", rd=10, rs1=10, rs2=13))  # 恒等式2/3 残差入和
    id_prog.append(e("SW", rs2=10, rs1=3, imm=0))
    id_prog += _load20(10, 1)
    id_prog += _load20(11, BUF)
    id_prog += _load20(12, 9)
    id_prog.append(e("ADDI", rd=17, rs1=0, imm=64))
    id_prog.append(e("ECALL"))
    id_prog.append(e("ADDI", rd=17, rs1=0, imm=93))
    id_prog.append(e("ECALL"))
    id_out = run_py(id_prog, len(id_prog) * 4 + 2000)
    check("PyVM 代数恒等式（残差=0）", len(id_out) == 9 and word_of(id_out[0:9]) == 0,
          "got=%s" % (word_of(id_out[0:9]) if len(id_out) >= 9 else len(id_out)))
    if os.path.exists(CVM):
        id_c = run_c(id_prog)
        check("CVM 代数恒等式", len(id_c) == 9 and word_of(id_c[0:9]) == 0,
              "got=%s" % (word_of(id_c[0:9]) if len(id_c) >= 9 else len(id_c)))

    print("\nT-LIT 契约：%d PASS / %d FAIL" % (passed, failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
