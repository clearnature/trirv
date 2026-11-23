#!/usr/bin/env python3
"""TriRV 长分支弛豫契约（ADR-16，批次 3.1–3.2）。

弛豫结构：超窗条件分支 → 反条件分支(imm=2 固定槽距) + JAL zero 承担长跳。
定点迭代：弛豫 +1 槽移动后续标签，labels 从不弛豫基线重算（在累积 labels
上反复 +1 是 JAL 跳过头的根因——批次 3.2 实证）。

期望值按退出模型手算（docstring 同 golden_e2e）：exit=a0&0xFF（a7=93）。
"""
import os, sys, subprocess
import _bootstrap  # noqa: F401
import trirv_asm as asm
import trirv_vm as vm

ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CVM=os.path.join(ROOT,"vm","bin","trirv_vm")
passed=failed=0
def check(name, got, want):
    global passed, failed
    if got==want: passed+=1; print("PASS %s: %r"%(name,got))
    else: failed+=1; print("FAIL %s: got=%r want=%r"%(name,got,want))

BODY="\n".join("ADDI x10, x10, 1" for _ in range(1500))

# A 真跳弛豫
p,_=asm.assemble(f"BEQ zero, zero, far\n{BODY}\nADDI x10, x10, 99\nfar: ADDI x17, zero, 93\nECALL\n")
c=vm.VM(p).run(max_steps=200000)
check("A 真跳弛豫 exit=0(a0=0) x17=93 steps=4", (c.exit_code,c.regs[10],c.regs[17],c.steps), (0,0,93,4))
r=subprocess.run([CVM,p if isinstance(p,str) else "/dev/null"],capture_output=True)  # 占位不执行
# B 假分支弛豫
p2,_=asm.assemble(f"BNE zero, zero, far\n{BODY}\nADDI x10, x10, 99\nfar: ADDI x17, zero, 93\nECALL\n")
c2=vm.VM(p2).run(max_steps=200000)
check("B 假分支弛豫反分支恒真跳过 JAL exit=63", c2.exit_code, 63)
# C 负 JAL 弛豫（静态落点 + 死循环）
p3,_=asm.assemble("far2: ADDI x5, zero, 1\n"+BODY+"\nBGE x5, zero, far2\nADDI x17, zero, 93\nECALL\n")
jals=[(i,vm.isa.decode(w)) for i,w in enumerate(p3) if vm.isa.decode(w)["inst"]=="JAL"]
ok=False
for i,d in jals:
    imm=d["imm"]; delta=imm if imm<=797161 else imm-1594323
    if i+delta==0: ok=True
check("C 负 JAL 静态落点 far2(slot0)", ok, True)
c3=vm.VM(p3).run(max_steps=3000)
check("C 负 JAL 死循环确认", (c3.halted, c3.exit_code, c3.steps>=3000), (False, 0, True))
# D 窗内不弛豫
p4,_=asm.assemble("ADDI x5, zero, 3\nBEQ x5, zero, near\nADDI x10, x10, 99\nnear: ADDI x17, zero, 93\nECALL\n")
check("D 窗内不弛豫槽数=5", len(p4), 5)

print("\n弛豫契约：%d PASS / %d FAIL"%(passed,failed))
sys.exit(1 if failed else 0)
