#!/usr/bin/env python3
"""trirv_mem4_e2e —— LW4/SW4 4-tryte（20-trit）存取契约，双 VM 差分。

C2 方案 A（2026-11-23 用户拍板）：__tint2 = 4-tryte/20-trit 档（值域
3^20 = 3,486,784,401 ≈ 2^31.6，"32 位量级档"，对标 char/short/int 阶梯补档）。
ISA 长档头寸（funct 字段 t7..t8，2 trit = 0..8）：
  LW4 = LOAD  funct 5（LOAD 0..4 已用，5..8 空闲）
  SW4 = STORE funct 3（STORE 0..2 已用，3..8 空闲）

语义口径（与 LB/LH 同款半阈值三补码）：
- LW4：4 tryte 合成 t（0..3^20-1），t > (3^20-1)//2 → t - 3^20，符号扩展进
  45-trit 寄存器；越界 tryte 读 = 0。
- SW4：寄存器低 20-trit 截断（mod 3^20）存 4 tryte；越界写丢弃。
- tryte = 5 trit（243 = 3^5），地址按 tryte 编址，与 SB/SH/SW 一致。

程序形状（每 case 22 tryte 结果区）：
  SW4 x5 -> [p]      截断见证（4 tryte，直接检验 mod 3^20）
  SW  x5 -> [p+4]    原寄存器全字见证（9 tryte，可信参照路径）
  LW4 x6 <- [p]      从截断槽重载（检验符号扩展）
  SW  x6 -> [p+13]   重载全字见证
全部结果经 write(1, BUF, N*22) 输出；PyVM 与 CVM 字节流必须一致且等于
python oracle。汇编器文本路径（trirv_asm）一并锁定 LW4/SW4 助记符。
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
MOD20 = 3 ** 20
HALF20 = (MOD20 - 1) // 2          # 1,743,392,200 = 20-trit 最大正
MOD_X = isa.MOD_X                  # 3^45
BUF = 1000                         # 结果区起始地址（<1093，单 ADDI 可装载）
STEP_TRYTES = 22                   # 每 case 结果区步长：4 + 9 + 9

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


def sx20(t):
    """20-trit 三补码半阈值符号扩展：t > (3^20-1)/2 → t - 3^20。"""
    return t - MOD20 if t > HALF20 else t


def _load20(rd, val):
    """装载幅值 < 3^20 的有符号值到 rd：LUI(imm13*3^7) + ADDI(7-trit)。"""
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
SEG = 15                      # 每段 15 trit（< 3^15，_load20 可容纳）


def emit_load(rd, val):
    """把任意有符号 45-trit 值 val 装到 rd（补码 word 语义）。"""
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
    """tryte 字节流 -> 45-trit word（小端：chunk[i] 权重 3^(5i)）。"""
    return sum(chunk[i] * 3 ** (5 * i) for i in range(len(chunk))) % MOD_X


# ------------------------------------------------------------------ 用例集
# (名字, 装入寄存器的 45-trit 有符号值)；oracle 见 expected()
CASES = [
    ("零",            0),
    ("正一",          1),
    ("负一",         -1),
    ("tryte上限",    242),
    ("正小值",        123456),
    ("3^19",          3 ** 19),
    ("-3^19",        -(3 ** 19)),
    ("最大正",        HALF20),
    ("最大负",       -HALF20),
    ("过半正溢",      HALF20 + 1),        # mod 3^20 后重载 = -HALF20-1? 见 oracle
    ("过半负回正",   -HALF20 - 1),        # mod 3^20 后重载 = 正
    ("超20trit正",    MOD20 + 5),         # 截断 = 5
    ("超20trit负",   -MOD20 - 7),         # 截断槽 = 3^20-7 → 重载 = -7
    ("3^30大值",      3 ** 30 + 12345),
    ("-3^40大值",    -(3 ** 40) - 999),
]


def expected(v):
    """返回 (slot_0..3^20-1, reload_有符号45trit)。"""
    slot = v % MOD20
    return slot, sx20(slot)


def build_program():
    prog = []
    prog += _load20(3, BUF)                       # x3 = 结果区游标
    for _name, v in CASES:
        prog += emit_load(5, v)
        prog.append(e("SW4", rs2=5, rs1=3, imm=0))    # [x3]   = 截断见证（4 tryte）
        prog.append(e("SW", rs2=5, rs1=3, imm=4))     # [x3+4] = 原值全字见证
        prog.append(e("LW4", rd=6, rs1=3, imm=0))     # x6 = 重载
        prog.append(e("SW", rs2=6, rs1=3, imm=13))    # [x3+13] = 重载全字见证
        prog.append(e("ADDI", rd=3, rs1=3, imm=STEP_TRYTES))
    # write(1, BUF, N*22) + exit(0)
    prog += _load20(10, 1)
    prog += _load20(11, BUF)
    prog += _load20(12, len(CASES) * STEP_TRYTES)
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


def verify(out, tag):
    ok = True
    detail = ""
    if len(out) != len(CASES) * STEP_TRYTES:
        return False, "输出长度 %d ≠ %d" % (len(out), len(CASES) * STEP_TRYTES)
    for i, (name, v) in enumerate(CASES):
        chunk = out[i * STEP_TRYTES:(i + 1) * STEP_TRYTES]
        slot_want, reload_want = expected(v)
        slot_got = word_of(chunk[0:4])            # 4 tryte 槽按 word 读（<3^20 无回绕）
        w1 = word_of(chunk[4:13])
        w2 = word_of(chunk[13:22])
        if slot_got != slot_want:
            ok = False
            detail += " [%s]槽=%d≠%d" % (name, slot_got, slot_want)
        if w1 != v % MOD_X:
            ok = False
            detail += " [%s]原值见证=%d≠%d" % (name, w1, v % MOD_X)
        if w2 != reload_want % MOD_X:
            ok = False
            detail += " [%s]重载见证=%d≠%d" % (name, w2, reload_want % MOD_X)
    return ok, detail


def main():
    print("=== LW4/SW4 4-tryte（20-trit）存取契约（双 VM）===")

    # 0. 汇编器文本路径：LW4/SW4 助记符可解析且编码 == 真相源
    prog_txt, _ = asm.assemble("SW4 x5, 0(x6)\nLW4 x7, 0(x6)\n")
    check("汇编器 SW4/LW4 助记符",
          len(prog_txt) == 2
          and isa.decode(prog_txt[0])["inst"] == "SW4"
          and isa.decode(prog_txt[1])["inst"] == "LW4"
          and prog_txt[1] == e("LW4", rd=7, rs1=6, imm=0))

    # 1. 双 VM 差分 + oracle
    prog = build_program()
    max_steps = len(prog) * 4 + 1000
    py_out = run_py(prog, max_steps)
    check("PyVM 输出非空（程序跑通）", len(py_out) > 0)
    ok, detail = verify(py_out, "PyVM")
    check("PyVM oracle（截断 mod 3^20 + sx20 符号扩展）", ok, detail)

    if not os.path.exists(CVM):
        print("[SKIP] C VM 不存在: %s" % CVM)
    else:
        c_out = run_c(prog)
        check("CVM 输出非空（程序跑通）", len(c_out) > 0,
              "stderr=%r" % _tail_stderr(c_out))
        okc, detailc = verify(c_out, "CVM")
        check("CVM oracle", okc, detailc)
        check("PyVM == CVM 字节一致", py_out == c_out)

    print("\nLW4/SW4 契约：%d PASS / %d FAIL" % (passed, failed))
    sys.exit(1 if failed else 0)


def _tail_stderr(_out):
    return ""


if __name__ == "__main__":
    main()
