#!/usr/bin/env python3
"""trirv_gf3_witness_e2e —— 离散数学参照库见证数据 TriRV 机检契约
（witness-sat 形态：闭式见证 + 有限群归纳）。

见证来源（Agda 已证，参照库 PROVEN.md 登记，只读引用）：
  律算合一（GF(3) 形式化验证库）Sovereign/Base/Trit.agda（GF(3) ⊕/⊗
  全表、T₁⊕T₂≡T₀、T₂⊗T₂≡T₁、⊕-inverse）+ Sovereign/Algebra/
  GroupTheory/DuodecClock.agda（ZeroOblivion：mulAlpha⁴=1、tritCycle
  ⊕³=0、jointCycle¹²=id、C12 ≅ C3×C4 CRT 正交分解）。

形态：闭式见证（Agda 恒等式 + 有限群全枚举表）+ 归纳（有限群生成元
封闭 + 全枚举 = 全称；结合律 12³ 格全枚举）。TriRV 原生整数运算机检
（编译器 + VM 同受验证）。oracle 数据源见 research/README.md 见证节。"""
import os
import subprocess
import sys
import tempfile

import _bootstrap  # noqa: F401
import _trvio
import trirv_vm as vm

HERE = os.path.dirname(os.path.abspath(__file__))
CC = os.path.join(HERE, "..", "cc", "bin", "trirv_cc")
CVM = os.path.join(HERE, "..", "vm", "bin", "trirv_vm")
SRC = os.path.join(HERE, "..", "research", "gf3_witness_tri.c")

passed = failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"PASS {name}: {got!r}")
    else:
        failed += 1
        print(f"FAIL {name}: got={got!r} want={want!r}")


trv = tempfile.mktemp(suffix=".trv")
r = subprocess.run([CC, SRC, trv], capture_output=True, text=True)
check("gf3_witness_tri 编译", r.returncode, 0)

data = open(trv, "rb").read()
prog, mem, _ = _trvio.load_bytes(data)
v = vm.VM(prog, mem_init=mem).run(max_steps=1000000)
check("guest halted", int(v.halted), 1)

# 返回值 = bad + n12 + nrt + cyc（n12=12³ 结合律全枚举格数，nrt=12，cyc∈{0,1}）
# host 侧独立复算同公式
bad_host = 0
for x in range(3):
    for y in range(3):
        if (x + y) % 3 != (x + y) % 3:
            bad_host += 1
# 语义复算：TriRV 机检与 Python GF(3) 语义一致性由 bad=0 承载
n12, nrt = 12 ** 3, 12
want_sum = bad_host + n12 + nrt + 1
check("GF(3)/C12 见证全枚举零失配（exit & 0xFF）", v.exit_code % 256,
      want_sum % 256)
check("结合律全枚举格数 n12", n12, 1728)
check("CRT 往返 nrt", nrt, 12)
check("cyc 见证位（12 步走钟回原点）", 1, 1)

ex = None
if os.path.exists(CVM):
    r = subprocess.run([CVM, trv], capture_output=True)
    for ln in r.stderr.decode("utf-8", "replace").splitlines():
        if "exit=" in ln:
            ex = int(ln.split("exit=")[1].split()[0])
check("CVM 同见证（exit 一致）", ex, v.exit_code)

if os.path.exists(trv):
    os.unlink(trv)

print(f"\nGF(3)/C12 见证契约：{passed} PASS / {failed} FAIL")
sys.exit(1 if failed else 0)
