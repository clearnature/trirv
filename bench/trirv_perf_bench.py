#!/usr/bin/env python3
"""TriRV 三进制 VM 性能基准 —— 量化模拟器开销与数量级损失。

分层：
  A. 微基准：单操作成本（decode / 符号转换 / 指令分发 / tryte 内存）
  B. 语言层基线：纯 Python 直接做同样计算（剥离"三进制模拟"额外开销）
  C. 宏基准：同一累加算法在 TriRV VM 上 vs 原生 gcc -O2
"""
import time
import subprocess
import trirv_isa as isa
import trirv_vm as vm
import trirv_cc_backend as cc

def now():
    return time.perf_counter()

def report(label, dt, ops, unit="op"):
    ns = dt / ops * 1e9
    print(f"  {label:34s} {dt*1000:8.2f} ms   {1e9/ns:11.0f} {unit}/s   {ns:8.1f} ns/{unit}")

print("=" * 76)
print("A. 微基准（TriRV VM 内部单操作成本）")
print("=" * 76)
N = 200000
inst = isa.encode("ADD", rd=1, rs1=2, rs2=3)
word = 123456789 % isa.MOD_X

t = now()
for _ in range(N):
    isa.decode(inst)
report("isa.decode(ADD)", now() - t, N)

t = now()
for _ in range(N):
    vm.u(vm.s(word))
report("s()+u() 平衡三进制符号转换", now() - t, N)

mc = vm.VM([inst] * N)
t = now()
mc.run(max_steps=N)
report("VM step 全流程(ADD 指令流)", now() - t, N)

mc2 = vm.VM([inst])
t = now()
for _ in range(N):
    mc2._store_word(1000, word)
    mc2._load_word(1000)
report("store+load word(9 tryte)", now() - t, N)

print()
print("=" * 76)
print("B. 语言层基线（纯 Python 直接累加，剥离三进制模拟层）")
print("=" * 76)
N2 = 200000
t = now()
a = 0
for i in range(N2):
    a = (a + 1) % 1000000
report("Python: a=a+1 循环", now() - t, N2)

print()
print("=" * 76)
print("C. 宏基准：累加 1+2+...+n（同一算法跨三层对比）")
print("=" * 76)
LOOP = 20000
src = f"int main(){{ int s=0; int i=0; while(i<{LOOP}){{ s=s+i; i=i+1; }} return s; }}"
prog, labels, txt, strs = cc.compile_c(src)
c = vm.VM(prog, mem_init=strs or None)
t = now()
c.run(max_steps=3_000_000)                 # 每迭代约 78 条指令(栈式表达式)，给足步数
dt_vm = now() - t
steps = c.steps
vm_us_iter = dt_vm / LOOP * 1e6
expect = LOOP * (LOOP - 1) // 2
print(f"  TriRV VM : {LOOP} 次迭代 = {steps} 条指令, {dt_vm*1000:.1f} ms, halted={c.halted}")
print(f"             {steps/dt_vm/1e6:.3f} MIPS, {steps/LOOP:.1f} 指令/迭代, {vm_us_iter:.1f} us/迭代")
print(f"             exit_code={c.exit_code & 0xFF} (低8位, 完整值由 s(a0) 不可得-已被exit覆盖)")

# 原生：用 volatile 累加器阻止 gcc 把循环优化成闭式公式，保证真实逐次迭代
with open("/tmp/native_bench.c", "w") as f:
    f.write("#include <stdio.h>\n#include <stdlib.h>\n"
            "int main(int c,char**v){int n=c>1?atoi(v[1]):20000000;"
            "volatile long long s=0;for(int i=0;i<n;i++)s+=i;"
            "printf(\"%lld\\n\",s);return 0;}\n")
subprocess.run(["gcc", "-O2", "-o", "/tmp/native_bench", "/tmp/native_bench.c"], check=True)
BN = 20000000
t = now()
out = subprocess.run(["/tmp/native_bench", str(BN)], capture_output=True, text=True)
dt_nat = now() - t
nat_ns_iter = dt_nat / BN * 1e9
print(f"  原生 gcc -O2(volatile): {BN} 次迭代, {dt_nat*1000:.1f} ms, "
      f"{nat_ns_iter:.2f} ns/迭代 (结果 {out.stdout.strip()}, 期望 {BN*(BN-1)//2})")
print(f"             ~{3*BN/dt_nat/1e6:.0f} MIPS (按 3 机器指令/迭代估)")

print()
print("=" * 76)
print("D. 结论：性能损失量化")
print("=" * 76)
print(f"  总损失(同一累加算法): VM {vm_us_iter:.1f} us/迭代 vs 原生 {nat_ns_iter:.2f} ns/迭代")
print(f"    → 慢 {vm_us_iter*1000/nat_ns_iter:,.0f} 倍 ({vm_us_iter*1000/nat_ns_iter:.0f}x)")
print(f"  VM 绝对吞吐: {steps/dt_vm/1e6:.3f} MIPS (每秒百万条三进制虚拟指令)")
