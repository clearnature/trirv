#!/usr/bin/env python3
"""F 扩展指令契约（P2，TDD RED → GREEN）。

覆盖：
  1. ISA 编码往返（opcode 19/20/21 无撞码）
  2. PyVM 直接跑 F 指令的语义正确性（分离表示穿透，1.5+2.25=3.75 等）
  3. PyVM vs C VM 差分（同一 .trv 程序，结果字节一致）

期望值由 trirv_float 的数学定义独立算出，不依赖被测的 VM 指令实现。
"""
import os
import struct
import subprocess
import sys
import tempfile

import trirv_isa as isa
import trirv_float as tf
import trirv_vm as vm
from export_trv import export as _unused_export  # 仅确认模块存在；本测试自造 TRV1

_HERE = os.path.dirname(os.path.abspath(__file__))
CVM = os.environ.get("TRIRV_VM_BIN", os.path.join(_HERE, "trirv_vm"))
F = tf.TF64
PASS = FAIL = 0


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"PASS {name}: {got}")
    else:
        FAIL += 1
        print(f"FAIL {name}: got={got!r} want={want!r}")


def raw_to_trytes(raw):
    """45-trit raw → 9 tryte（小端，每 tryte 0..242），用于内存数据段。"""
    return bytes((raw // (243 ** i)) % 243 for i in range(9))


def trytes_to_raw(data):
    v = 0
    for i, t in enumerate(data[:9]):
        v += t * (243 ** i)
    return v % (3 ** 45)


# ---------------- 1. 编码往返 ----------------
for mn, rd, rs1, rs2, imm in [
    ("FADD", 5, 3, 2, 0), ("FSUB", 6, 1, 4, 0), ("FMUL", 7, 2, 5, 0),
    ("FDIV", 8, 3, 6, 0), ("FSQRT", 9, 4, 0, 0),
    ("FLW", 10, 2, 0, 5), ("FSW", 0, 2, 11, 5),
    ("FEQ", 12, 5, 6, 0), ("FLT", 13, 5, 6, 0), ("FLE", 14, 5, 6, 0),
]:
    w = isa.encode(mn, rd=rd, rs1=rs1, rs2=rs2, imm=imm)
    d = isa.decode(w)
    # S 型没有 rd 字段（t3..t6 是立即数低位），只断言 inst/rs1/rs2
    ok = d["inst"] == mn and d["rs1"] == rs1
    if mn != "FSW":
        ok = ok and d["rd"] == rd
    check(f"编码往返 {mn}", ok, True)

# opcode 唯一性：opcode 19/20/21 内部 (funct[,funct2]) 无撞码
from collections import defaultdict
groups = defaultdict(list)
for mn, (op, fu, f2, fmt) in isa.INST.items():
    if op in (19, 20, 21):
        groups[op].append((fu, f2, fmt, mn))
for op, entries in groups.items():
    seen = {}
    collide = False
    for fu, f2, fmt, mn in entries:
        key = (fu, f2) if fmt == "R" else (fu,)
        if key in seen:
            collide = True
        seen[key] = mn
    check(f"opcode {op} 无撞码", collide, False)


# ---------------- 2. PyVM F 指令语义 ----------------
def build_program(ops, data_seg):
    """ops: [(助记符,rd,rs1,rs2,imm)...]；data_seg: {addr: bytes}。返回 (prog, mem_init)。"""
    prog = [isa.encode(mn, rd=rd, rs1=rs1, rs2=rs2, imm=imm)
            for (mn, rd, rs1, rs2, imm) in ops]
    mem = {a: list(b) for a, b in data_seg.items()}
    return prog, mem


def run_py(ops, data_seg, max_steps=200):
    prog, mem = build_program(ops, data_seg)
    c = vm.VM(prog, mem_init=mem).run(max_steps=max_steps)
    return c


# 数据段布局：三个 45-trit 字 @ 100/109/118，结果写 @ 127
A_ADDR, B_ADDR, C_ADDR = 100, 109, 127
a_raw = F.encode(1.5)
b_raw = F.encode(2.25)

# FADD: f0←A, f1←B, f2=f0+f1, FSW f2→C
ops_add = [
    ("FLW", 0, 2, 0, A_ADDR), ("FLW", 1, 2, 0, B_ADDR),  # rs1=x2? 用 x0 基址，imm=地址
    ("FADD", 2, 0, 1, 0), ("FSW", 0, 2, 2, C_ADDR),
]
# 注意 FLW 用 x0 作基址更简单：imm 就是绝对 tryte 地址
ops_add = [
    ("FLW", 0, 0, 0, A_ADDR), ("FLW", 1, 0, 0, B_ADDR),
    ("FADD", 2, 0, 1, 0), ("FSW", 0, 0, 2, C_ADDR),
]
data = {A_ADDR: list(raw_to_trytes(a_raw)), B_ADDR: list(raw_to_trytes(b_raw))}
c = run_py(ops_add, data)
res_raw = trytes_to_raw(bytes(c.mem[C_ADDR:C_ADDR + 9]))
check("PyVM 1.5+2.25=3.75", F.decode(res_raw), 3.75)

# FMUL / FDIV / FSUB / FSQRT
for op_name, expr, expect in [
    ("FMUL", "1.5*2.25", 3.375),
    ("FDIV", "1.5/2.25", 1.5 / 2.25),
    ("FSUB", "1.5-2.25", -0.75),
]:
    ops = [("FLW", 0, 0, 0, A_ADDR), ("FLW", 1, 0, 0, B_ADDR),
           (op_name, 2, 0, 1, 0), ("FSW", 0, 0, 2, C_ADDR)]
    c = run_py(ops, data)
    r = trytes_to_raw(bytes(c.mem[C_ADDR:C_ADDR + 9]))
    check(f"PyVM {expr}", round(F.decode(r), 6), round(expect, 6))

# FSQRT: sqrt(9)=3
data9 = {A_ADDR: list(raw_to_trytes(F.encode(9.0)))}
c = run_py([("FLW", 0, 0, 0, A_ADDR), ("FSQRT", 1, 0, 0, 0),
            ("FSW", 0, 0, 1, C_ADDR)], data9)
r = trytes_to_raw(bytes(c.mem[C_ADDR:C_ADDR + 9]))
check("PyVM sqrt(9)=3", F.decode(r), 3.0)

# FEQ/FLT/FLE 写整数寄存器（用 ADDI 把结果搬到 a0 作为退出码）
# 1.5 < 2.25 且 1.5 ≤ 2.25，但不相等
for cmp_op, expect in [("FEQ", 0), ("FLT", 1), ("FLE", 1)]:
    ops = [("FLW", 0, 0, 0, A_ADDR), ("FLW", 1, 0, 0, B_ADDR),
           (cmp_op, 5, 0, 1, 0)]
    c = run_py(ops, data)
    check(f"PyVM {cmp_op}(1.5,2.25)", c.regs[5], expect)


# ---------------- 3. PyVM vs C VM 差分 ----------------
def write_trv(path, prog, data):
    buf = bytearray(b"TRV1")
    buf += struct.pack("<III", 1, len(prog), len(data))
    for raw in prog:
        buf += struct.pack("<I", raw & 0xFFFFFFFF)
    for addr, vals in data.items():
        b = bytes(vals)
        buf += struct.pack("<II", addr, len(b))
        buf += b
    with open(path, "wb") as f:
        f.write(buf)


def make_result_program(op_name):
    """构造程序：读 A/B → 运算 → 把结果 raw 的 9 tryte 用 ECALL write 输出。"""
    ops = [("FLW", 0, 0, 0, A_ADDR), ("FLW", 1, 0, 0, B_ADDR),
           (op_name, 2, 0, 1, 0), ("FSW", 0, 0, 2, C_ADDR),
           ("ADDI", 10, 0, 0, 1),     # a0 = fd 1
           ("ADDI", 11, 0, 0, C_ADDR),  # a1 = buf
           ("ADDI", 12, 0, 0, 9),     # a2 = len
           ("ADDI", 17, 0, 0, 64),    # a7 = write(64)
           ("ECALL", 0, 0, 0, 0)]
    prog, mem = build_program(ops, data)
    return prog, mem


if os.path.exists(CVM):
    for op_name, expect in [("FADD", 3.75), ("FMUL", 3.375), ("FDIV", 1.5 / 2.25)]:
        prog, mem = make_result_program(op_name)
        c = vm.VM(prog, mem_init=mem).run(max_steps=200)
        py_out = bytes(c.output)
        with tempfile.NamedTemporaryFile(suffix=".trv", delete=False) as tf_:
            path = tf_.name
        write_trv(path, prog, mem)
        r = subprocess.run([CVM, path, "/dev/null"], capture_output=True)
        os.unlink(path)
        c_out = r.stdout
        check(f"C VM {op_name} 差分", c_out, py_out)
        if c_out == py_out:
            check(f"C VM {op_name} 结果", round(F.decode(trytes_to_raw(c_out)), 6),
                  round(expect, 6))
else:
    print("SKIP C VM 差分：未找到 trirv_vm 二进制")

print()
print(f"F 扩展指令契约：{PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
