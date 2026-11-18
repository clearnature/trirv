#!/bin/bash
# TriRV 无除法器优化：magic mul+shift 替代 128 位硬件 div
set -e
cd "$(dirname "$0")"

echo "========== 1. 反汇编证据：128位除法库调用数 =========="
gcc -O3 -march=native -fopenmp -o /tmp/trirv_with_div /tmp/trirv_vm_before_nodev.c
echo -n "优化前 __udivmodti4 调用: "; objdump -d /tmp/trirv_with_div | grep -c '__udivmodti4' || true
echo -n "优化后 __udivmodti4 调用: "; objdump -d trirv_vm | grep -c '__udivmodti4' || true
echo "=== div243 函数汇编（应零 div，纯 mul+shift+sbb）==="
gcc -O3 -march=native -fno-inline -c trirv_vm.c -o /tmp/nodev_vm.o
objdump -d /tmp/nodev_vm.o | sed -n '/<div243>:/,/^$/p' | grep -E 'mul|imul|shr|sbb|div|cmp' | head -8

echo ""
echo "========== 2. 微基准：tritwise 优化链 =========="
gcc -O3 -march=native -o trirv_trit_bench trirv_trit_bench.c
./trirv_trit_bench 2000000 | grep -E "基线|OPT|NO-DIV|vs 常数|总优化"

echo ""
echo "========== 3. 等价性 + 差分 =========="
gcc -O3 -o /tmp/verify_trit trirv_verify_trit.c && /tmp/verify_trit
PYTHONPATH=/workspace python3.11 /tmp/difftest.py 2>&1 | grep -v "^导出\|^标签" | tail -1

echo ""
echo "========== 4. 端到端吞吐（三值密集 / 通用）=========="
for prog in tri_heavy bench_sum; do
  echo "--- $prog ---"
  echo -n "  优化前: "; /tmp/trirv_with_div -n 100 -t 1 /tmp/$prog.trv 2>&1 | tail -1 | grep -o '吞吐=[0-9.]* MIPS'
  echo -n "  优化后: "; ./trirv_vm -n 100 -t 1 /tmp/$prog.trv 2>&1 | tail -1 | grep -o '吞吐=[0-9.]* MIPS'
done

echo ""
echo "========== 5. 并行扩展（无除法器版，三值密集）=========="
for t in 1 2 4; do
  echo -n "  线程=$t: "; ./trirv_vm -n 100 -t $t /tmp/tri_heavy.trv 2>&1 | tail -1 | grep -o '吞吐=[0-9.]* MIPS'
done
