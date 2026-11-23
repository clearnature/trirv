#!/usr/bin/env python3
"""TriRV TC sf5（C-J/C-JAL）契约（批次 1.2–1.4，ADR-03）。

sf5 布局：t5=link（0=C-J→不写 / 1=C-JAL→rd=ra），t6–t14=imm9 跳转量
（9-trit 3 补码 ±9841；单位随容器：TRV1 槽制=槽、TRV2 流制=tryte——
TC v2 既有惯例，跨容器由 isa.migrate_trv1_stream 重编）。

全部期望值为手算（ADR-28②），不依赖任何 VM 的输出推导。
"""
import os
import sys

import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import trirv_isa as isa
import trirv_vm as vm

passed = 0
failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print("PASS %s: %r" % (name, got))
    else:
        failed += 1
        print("FAIL %s: got=%r want=%r" % (name, got, want))


def W(trytes):
    """3 tryte → 槽词（高 5 trit 零填充）。"""
    return sum(t * 243 ** i for i, t in enumerate(trytes))


E = isa.encode

# ================ §1 编解码黄金 ================
d = isa.decode_c(isa.encode_c_jump(1, -5))
check("编码黄金·link=1 delta=-5 → imm9", d["imm9"], 19678)
check("编码黄金·link", d["link"], 1)
check("编码黄金·s9 逆解", d["imm9"] - 19683 if d["imm9"] > 9841 else d["imm9"], -5)
d2 = isa.decode_c(isa.encode_c_jump(0, 9841))
check("编码边界·+9841", (d2["link"], d2["imm9"]), (0, 9841))
d3 = isa.decode_c(isa.encode_c_jump(0, -9841))
check("编码边界·-9841", (d3["link"], d3["imm9"]), (0, 19683 - 9841))
try:
    isa.encode_c_jump(0, 9842)
    check("编码超窗·+9842 报错", "no-raise", "raise")
except ValueError:
    check("编码超窗·+9842 报错", "raise", "raise")

# ================ §2 C-JAL（link=1）================
# slot0 C-JAL +2 | slot1 ADDI a0,99（被跳过）| slot2 ADDI a0,7 | slot3 a7=93 | ECALL
prog = [W(isa.encode_c_jump(1, 2)), E("ADDI", rd=10, rs1=0, imm=99),
        E("ADDI", rd=10, rs1=0, imm=7), E("ADDI", rd=17, rs1=0, imm=93), E("ECALL")]
c = vm.VM(prog).run(max_steps=100)
check("C-JAL 槽制·跳过+链接", (c.exit_code, c.regs[1], c.regs[10]), (7, 4, 7))
s1 = isa.migrate_trv1_stream(prog)
c5 = vm.VM.from_stream(s1).run(max_steps=100)
# 流制本指令宽 3 tryte → 链接地址 = 0+3 = slot1 的流地址
check("C-JAL 流制·链接=下一条流地址", (c5.exit_code, c5.regs[1], c5.regs[10]), (7, 3, 7))

# ================ §3 C-J（link=0，x0 硬连线）================
prog2 = [W(isa.encode_c_jump(0, 2)), E("ADDI", rd=1, rs1=0, imm=99),
         E("ADDI", rd=10, rs1=0, imm=7), E("ADDI", rd=17, rs1=0, imm=93), E("ECALL")]
c3 = vm.VM(prog2).run(max_steps=100)
check("C-J 槽制·ra 不被写", (c3.exit_code, c3.regs[1], c3.regs[10]), (7, 0, 7))

# ================ §4 负跳循环（回边）================
# slot0 SLTI t0,a0,5 | slot1 BEQ t0,zero,+3（出口）| slot2 ADDI a0,1
# slot3 C-J -3（回 slot0）| slot4 a7=93 | ECALL   → 5 圈后退出
cj3 = W(isa.encode_c_jump(0, -3))
prog3 = [E("SLTI", rd=5, rs1=10, imm=5), E("BEQ", rs1=5, rs2=0, imm=3),
         E("ADDI", rd=10, rs1=10, imm=1), cj3,
         E("ADDI", rd=17, rs1=0, imm=93), E("ECALL")]
c4 = vm.VM(prog3).run(max_steps=1000)
check("C-J 负跳循环槽制·5 圈", (c4.exit_code, c4.regs[10]), (5, 5))
s3 = isa.migrate_trv1_stream(prog3)
c6 = vm.VM.from_stream(s3).run(max_steps=1000)
check("C-J 负跳循环流制·5 圈", (c6.exit_code, c6.regs[10]), (5, 5))

# ================ §5 流制非法目标诚实停机 ================
# 构造 delta 使流制目标落在指令中间（迁移器不会生成，直测执行端鲁棒性）
# 流布局：C-J(3)@0 | ADDI(4)@3 | ADDI(4)@7 ...   delta=2 → 目标 2 ∈ C-J 内部
bad = [W(isa.encode_c_jump(0, 2)), E("ADDI", rd=10, rs1=0, imm=7),
       E("ADDI", rd=17, rs1=0, imm=93), E("ECALL")]
sb = isa.pack_stream(bad)
cb = vm.VM.from_stream(sb).run(max_steps=100)
check("流制非对齐目标·停机", (cb.halted, cb.regs[10]), (True, 0))

print("\nTC sf5 契约：%d PASS / %d FAIL" % (passed, failed))
sys.exit(1 if failed else 0)
