#!/usr/bin/env python3
"""M 扩展差分测试（Python VM vs C VM）。
段内存预置两个 45-trit word(a,b)，程序执行 MULH/MULHSU/MULHU/DIVU/REMU，
把结果 word 写回内存并 write(1,...) 输出 9 个 tryte，逐字节比对两版 VM。
"""
import struct, subprocess, os, random, sys
import os
import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import trirv_isa as isa
from trirv_vm import VM, s, u, SYS_EXIT, SYS_WRITE

e = isa.encode
MOD = isa.MOD_X
HALF = isa.HALF_X
BASE = 100

def word_trytes(w):
    w %= MOD
    return bytes((w // (243 ** i)) % 243 for i in range(9))

def build(inst_name, a, b):
    """生成程序 + 段内存字节。"""
    prog = [
        e("ADDI", rd=5, rs1=0, imm=BASE),          # x5 = 100 基址
        e("LW", rd=1, rs1=5, imm=0),                # x1 = a
        e("LW", rd=2, rs1=5, imm=9),                # x2 = b
        e(inst_name, rd=3, rs1=1, rs2=2),           # x3 = a OP b
        e("SW", rd=0, rs1=5, rs2=3, imm=18),        # mem[118] = x3
        e("ADDI", rd=10, rs1=0, imm=1),             # a0 = fd 1
        e("ADDI", rd=11, rs1=5, imm=18),            # a1 = 118
        e("ADDI", rd=12, rs1=0, imm=9),             # a2 = 9
        e("ADDI", rd=17, rs1=0, imm=SYS_WRITE % (3**7)),
        e("ECALL"),
        e("ADDI", rd=17, rs1=0, imm=SYS_EXIT % (3**7)),
        e("ECALL"),
    ]
    seg = bytearray(200)
    seg[0:9] = word_trytes(a)
    seg[9:18] = word_trytes(b)
    return prog, seg

def write_trv(path, prog, seg):
    with open(path, "wb") as f:
        f.write(b"TRV1")
        f.write(struct.pack("<III", 1, len(prog), 1))
        for ins in prog:
            f.write(struct.pack("<I", ins & 0xFFFFFFFF))
        f.write(struct.pack("<II", 0, len(seg)))
        f.write(seg)

def py_run(inst_name, a, b):
    prog, seg = build(inst_name, a, b)
    v = VM(prog, mem_init={0: bytes(seg)})
    v.run(max_steps=100)
    return bytes(v.output)

def c_run(inst_name, a, b, vmbin):
    prog, seg = build(inst_name, a, b)
    path = _bootstrap.scratch("mdiff.trv")
    write_trv(path, prog, seg)
    r = subprocess.run([vmbin, path], capture_output=True)
    return r.stdout

def main():
    vmbin = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vm", "bin", "trirv_vm")
    random.seed(20261117)
    # 操作数集合：0/1/-1、大小正数、负数（3 补码 word）、随机
    specials = [0, 1, 2, MOD - 1, MOD - 2, HALF, HALF + 1, 242, 243, 3**7]
    def rnd():
        return random.randrange(MOD)
    cases = []
    for _ in range(40):
        cases.append((rnd(), rnd()))
    for a in specials:
        for b in specials:
            cases.append((a, b))
    names = ["MULH", "MULHSU", "MULHU", "DIVU", "REMU"]
    passed = failed = 0
    for name in names:
        local_pass = 0
        for a, b in cases:
            pe = py_run(name, a, b)
            ce = c_run(name, a, b, vmbin)
            if pe == ce:
                passed += 1; local_pass += 1
            else:
                failed += 1
                if failed <= 8:
                    print(f"  FAIL {name} a={s(a)} b={s(b)} py={list(pe)} c={list(ce)}")
        print(f"  {name:8} {local_pass}/{len(cases)}")
    print(f"总计 {passed}/{passed+failed} {'PASS' if failed==0 else 'FAIL'}")
    return 0 if failed == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
