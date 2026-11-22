#!/usr/bin/env python3
"""TriRV Zicsr + 特权态专项 e2e：CSR 读写、trap 陷入、MRET 返回、定时器中断。"""
import os
import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import trirv_isa as isa
import trirv_vm as vm
from trirv_vm import s, u, VM, SYS_EXIT

e = isa.encode
C = isa.CSR
passed = failed = 0

def chk(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1; print(f"  PASS {name} {extra}")
    else:
        failed += 1; print(f"  FAIL {name} {extra}")

def prog_exit(code):
    return [e("ADDI", rd=10, rs1=0, imm=code % (3**7)),
            e("ADDI", rd=17, rs1=0, imm=SYS_EXIT % (3**7)), e("ECALL")]

# ---- T1: CSRRW 写 mstatus 读回 ----
p = [e("ADDI", rd=1, rs1=0, imm=isa.MSTATUS_MIE % (3**7)),   # x1 = MIE bit
     e("CSRRW", rd=2, rs1=1, imm=C["mstatus"]),              # old=0, mstatus=MIE
     e("CSRRW", rd=3, rs1=0, imm=C["mstatus"]),              # x3 = 当前 mstatus
     e("ADD", rd=4, rs1=2, rs2=0)] + prog_exit(0)
v = VM(p).run(max_steps=100)
chk("CSRRW 旧值为 0", v.regs[2] == 0, f"old={v.regs[2]}")
chk("CSRRW 写后读回 MIE", (v.regs[3] & isa.MSTATUS_MIE) != 0, f"mstatus={v.regs[3]}")

# ---- T2: CSRRS 置位 / CSRRC 清位 ----
p = [e("ADDI", rd=1, rs1=0, imm=isa.MSTATUS_MIE % (3**7)),
     e("CSRRW", rd=0, rs1=1, imm=C["mstatus"]),              # mstatus = MIE
     e("ADDI", rd=5, rs1=0, imm=isa.MSTATUS_MPIE % (3**7)),
     e("CSRRS", rd=6, rs1=5, imm=C["mstatus"]),              # 置 MPIE，旧值=MIE
     e("CSRRC", rd=7, rs1=1, imm=C["mstatus"]),              # 清 MIE，旧值=MIE|MPIE
     e("CSRRW", rd=8, rs1=0, imm=C["mstatus"])] + prog_exit(0)
v = VM(p).run(max_steps=100)
chk("CSRRS 旧值含 MIE", (v.regs[6] & isa.MSTATUS_MIE) != 0)
chk("CSRRC 旧值含 MPIE", (v.regs[7] & isa.MSTATUS_MPIE) != 0)
chk("CSRRC 清位后 MIE=0 MPIE=1", (v.regs[8] & isa.MSTATUS_MIE) == 0 and (v.regs[8] & isa.MSTATUS_MPIE) != 0,
    f"mstatus={v.regs[8]}")

# PC 按 tryte 编址：指令 i 地址 = 4*i（与实现一致）。mtvec/mepc 均为 tryte 地址。
# ---- T3: 非法指令陷入 -> handler 修正 mepc -> MRET 返回 ----
# 布局（handler 放末尾，MRET 返回点后直接是 exit）:
#  0: x1=32  1: mtvec=x1(=32=槽8)
#  2: 非法指令(raw opcode=26) -> trap, mepc=8(tryte), 跳 32
#  3: x10=7  4: x17=EXIT  5: ECALL -> exit(7)   (MRET 将返回 tryte 地址 12 = 槽3)
#  6,7: 占位
#  8: CSRRS x3,x0,mepc 读 mepc(=8)  9: ADDI x3,x3,4(=12)  10: CSRRW x0,x3,mepc 写回  11: MRET -> 槽3
bad = 26                                            # opcode=26 未定义 -> decode 失败 -> 非法
p = [e("ADDI", rd=1, rs1=0, imm=32),
     e("CSRRW", rd=0, rs1=1, imm=C["mtvec"]),       # mtvec = 32 (槽8)
     bad,                                            # 非法指令 @ 槽2 (tryte 8)
     e("ADDI", rd=10, rs1=0, imm=7),                # 槽3: 恢复点 x10=7
     e("ADDI", rd=17, rs1=0, imm=SYS_EXIT % (3**7)),
     e("ECALL"),                                     # 槽5: exit(7)
     e("ADDI", rd=0, rs1=0, imm=0),                  # 槽6 占位
     e("ADDI", rd=0, rs1=0, imm=0),                  # 槽7 占位
     e("CSRRS", rd=3, rs1=0, imm=C["mepc"]),        # 槽8: 读 mepc
     e("ADDI", rd=3, rs1=3, imm=4),                  # 槽9: mepc+4 -> tryte 12 (槽3)
     e("CSRRW", rd=0, rs1=3, imm=C["mepc"]),        # 槽10: 写回 mepc
     e("MRET"),                                      # 槽11: 返回槽3
     ]
v = VM(p).run(max_steps=60)
chk("非法指令触发 trap", len(v.trap_log) >= 1, f"trap_log={v.trap_log}")
if v.trap_log:
    chk("trap cause=非法指令(2)", v.trap_log[0][0] == isa.CAUSE_ILLEGAL, f"cause={v.trap_log[0][0]}")
    chk("trap 跳转到 mtvec=32(tryte)", v.trap_log[0][2] == 32, f"to={v.trap_log[0][2]}")
chk("MRET 返回后正确退出 exit=7", (v.exit_code & 0xFF) == 7, f"exit={v.exit_code}")

# ---- T4: 定时器中断 ----
# 布局:
#  0: x2=6  1: mtvec=x2(=6)
#  2: x1=MIE  3: mstatus=x1 (MIE=1) -> 本条提交后中断挂起, mepc=4, 跳 6
#  4: x10=9  5: x17=EXIT  6: ECALL  <-- 注意 handler 需与恢复点分离
#  handler 放槽7 -> tryte 28
#  0: x2=28 1: mtvec=28 2: x1=MIE 3: mstatus(开中断) -> mepc=16(tryte), 跳 28
#  4: x10=9 5: x17=EXIT 6: ECALL 7: MRET
p = [e("ADDI", rd=2, rs1=0, imm=28),
     e("CSRRW", rd=0, rs1=2, imm=C["mtvec"]),       # 槽1: mtvec=28 (槽7)
     e("ADDI", rd=1, rs1=0, imm=isa.MSTATUS_MIE % (3**7)),
     e("CSRRW", rd=0, rs1=1, imm=C["mstatus"]),     # 槽3: MIE=1, 之后触发中断 -> mepc=16
     e("ADDI", rd=10, rs1=0, imm=9),                # 槽4: 恢复点
     e("ADDI", rd=17, rs1=0, imm=SYS_EXIT % (3**7)),
     e("ECALL"),                                     # 槽6: exit
     e("MRET"),                                      # 槽7: handler
     ]
v = VM(p)
v.irq_timer = True     # 注入定时器中断
v.run(max_steps=50)
chk("定时器中断触发 trap", len(v.trap_log) >= 1, f"trap_log={v.trap_log}")
if v.trap_log:
    chk("中断 cause=CAUSE_MTIMER", v.trap_log[0][0] == s(u(isa.CAUSE_MTIMER)),
        f"cause={v.trap_log[0][0]} exp={s(u(isa.CAUSE_MTIMER))}")
    chk("中断跳 mtvec=28(tryte)", v.trap_log[0][2] == 28, f"to={v.trap_log[0][2]}")
chk("中断返回后正确退出 exit=9", (v.exit_code & 0xFF) == 9, f"exit={v.exit_code}")

print(f"\n=== CSR 专项: {passed}/{passed+failed} PASS ===")
import sys
sys.exit(0 if failed == 0 else 1)
