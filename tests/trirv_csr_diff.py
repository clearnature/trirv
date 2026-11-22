#!/usr/bin/env python3
"""C VM vs Python VM 的 CSR/trap 差分测试。
用相同的指令序列构造程序：Python VM 直接跑，C VM 通过 TRV 二进制跑，比对 exit_code。
"""
import struct, subprocess, sys, os
import os
import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import trirv_isa as isa
from trirv_vm import s, u, VM, SYS_EXIT

e = isa.encode
C = isa.CSR

def write_trv(path, prog):
    with open(path, "wb") as f:
        f.write(b"TRV1")
        f.write(struct.pack("<III", 1, len(prog), 0))
        for inst in prog:
            f.write(struct.pack("<I", inst & 0xFFFFFFFF))

def c_exit(prog, irq=False):
    write_trv("/tmp/csr_diff.trv", prog)
    env = dict(os.environ)
    r = subprocess.run(["./trirv_vm", "/tmp/csr_diff.trv"], capture_output=True, env=env)
    line = [l for l in r.stderr.decode().split("\n") if "exit=" in l]
    return int(line[0].split("exit=")[1].split()[0]) if line else None

def py_exit(prog, irq=False):
    v = VM(prog)
    if irq:
        v.irq_timer = True
    v.run(max_steps=200)
    return v.exit_code & 0xFF, v

def prog_exit(code):
    return [e("ADDI", rd=10, rs1=0, imm=code % (3**7)),
            e("ADDI", rd=17, rs1=0, imm=SYS_EXIT % (3**7)), e("ECALL")]

passed = failed = 0
def chk(name, pe, ce):
    global passed, failed
    ok = (pe == ce)
    if ok: passed += 1
    else: failed += 1
    print(f"  {'PASS' if ok else 'FAIL'} {name:24} py={pe:4} c={ce}")

# --- D1: CSRRW 读写 ---
p = [e("ADDI", rd=1, rs1=0, imm=isa.MSTATUS_MIE % (3**7)),
     e("CSRRW", rd=2, rs1=1, imm=C["mstatus"]),
     e("CSRRW", rd=3, rs1=0, imm=C["mstatus"]),
     e("ADD", rd=10, rs1=2, rs2=3), e("ADDI", rd=17, rs1=0, imm=SYS_EXIT % (3**7)), e("ECALL")]
pe, _ = py_exit(p); ce = c_exit(p)
chk("CSRRW 读写(exit=old+now)", pe, ce)

# --- D2: CSRRS/CSRRC ---
p = [e("ADDI", rd=1, rs1=0, imm=isa.MSTATUS_MIE % (3**7)),
     e("CSRRW", rd=0, rs1=1, imm=C["mstatus"]),
     e("ADDI", rd=5, rs1=0, imm=isa.MSTATUS_MPIE % (3**7)),
     e("CSRRS", rd=6, rs1=5, imm=C["mstatus"]),
     e("CSRRC", rd=7, rs1=1, imm=C["mstatus"]),
     e("CSRRW", rd=8, rs1=0, imm=C["mstatus"]),
     e("ADD", rd=10, rs1=6, rs2=7), e("ADDI", rd=17, rs1=0, imm=SYS_EXIT % (3**7)), e("ECALL")]
pe, _ = py_exit(p); ce = c_exit(p)
chk("CSRRS/CSRRC", pe, ce)

# --- D3: 非法指令 trap + MRET（handler 在槽8 = tryte 地址 32）---
# 注意：mtvec/mepc 均为 tryte 地址（指令 i 在 4*i）。原版写 mtvec=8 指向槽2
# 的非法指令自身，形成自陷循环（48 次 trap 后两版退出路径不同）；
# 且 mepc 应 +4 跳到下一条指令（原写 +1 会 floor 回槽2 再陷）。
bad = 26
p = [e("ADDI", rd=1, rs1=0, imm=32), e("CSRRW", rd=0, rs1=1, imm=C["mtvec"]),
     bad,
     e("ADDI", rd=10, rs1=0, imm=7), e("ADDI", rd=17, rs1=0, imm=SYS_EXIT % (3**7)), e("ECALL"),
     e("ADDI", rd=0, rs1=0, imm=0), e("ADDI", rd=0, rs1=0, imm=0),
     e("CSRRS", rd=3, rs1=0, imm=C["mepc"]), e("ADDI", rd=3, rs1=3, imm=4),
     e("CSRRW", rd=0, rs1=3, imm=C["mepc"]), e("MRET")]
pe, pv = py_exit(p); ce = c_exit(p)
chk("非法指令trap+MRET", pe, ce)

# --- D4: 定时器中断（写 mip.MTIP 注入） + MRET ---
# 布局: 0:x2=8  1:mtvec=8  2:x1=MIE  3:mstatus=MIE(开中断)  4:x3=MTIP
#       5:csrw mip,x3 (触发挂起,本条提交后陷入,mepc=6 -> 跳8)
#       6:x10=9  7:exit  ... handler 需后移
# 重排: 0:x2=9 1:mtvec=9 2:x1=MIE 3:mstatus 4:x3=MTIP 5:csrw mip
#       6:x10=9 7:x17=EXIT 8:ECALL  9:MRET(handler)
MTIP = 1 << 7
p = [e("ADDI", rd=2, rs1=0, imm=9), e("CSRRW", rd=0, rs1=2, imm=C["mtvec"]),
     e("ADDI", rd=1, rs1=0, imm=isa.MSTATUS_MIE % (3**7)),
     e("CSRRW", rd=0, rs1=1, imm=C["mstatus"]),
     e("ADDI", rd=3, rs1=0, imm=MTIP % (3**7)),
     e("CSRRW", rd=0, rs1=3, imm=C["mip"]),          # 触发中断, mepc=6
     e("ADDI", rd=10, rs1=0, imm=9),                 # pc6: 恢复点
     e("ADDI", rd=17, rs1=0, imm=SYS_EXIT % (3**7)),
     e("ECALL"),                                     # pc8: exit
     e("MRET"),                                      # pc9: handler
     ]
pe, pv = py_exit(p); ce = c_exit(p)
chk("定时器中断(写mip)+MRET", pe, ce)
print(f"  INFO trap_log={pv.trap_log}")

print(f"\n=== CSR 差分: {passed}/{passed+failed} PASS ===")
sys.exit(0 if failed == 0 else 1)
