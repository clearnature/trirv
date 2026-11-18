#!/bin/bash
# TriRV 三值运算：逐元素 / 向量化 / 并行 三层优化基准
set -e
cd "$(dirname "$0")"

echo "========== 1. 微基准：拆分 tritwise 各阶段 + 逐元素优化 =========="
gcc -O3 -march=native -o trirv_trit_bench trirv_trit_bench.c
gcc -O3              -o trirv_trit_bench_scalar trirv_trit_bench.c
echo "--- AVX2 版（对照：min/max 已被 SIMD 加速）---"
./trirv_trit_bench 2000000
echo "--- 标量版 ---"
./trirv_trit_bench_scalar 2000000 | grep -E "unpack45|max45|完整|OPT|总优化"

echo ""
echo "========== 2. 向量化三种尝试的结论 =========="
echo "min/max SIMD : 117→6 cyc (19.6x) 但仅占基线 22% → 整体无感"
echo "ILP 2-way 交错: 74→136 cyc (0.55x) → gcc 已自动调度，手动交错反慢"
echo "AVX2 gather 查表: 6→51 cyc (0.12x) → 小表 L1 命中，gather 开销反超"
echo "结论: ISA 逐 word 标量语义 + 小表 L1 命中 → 向量化无正收益"

echo ""
echo "========== 3. 正确性：等价性 + 差分 =========="
gcc -O3 -o /tmp/verify_trit trirv_verify_trit.c && /tmp/verify_trit
PYTHONPATH=/workspace python3.11 /tmp/difftest.py 2>&1 | grep -v "^导出\|^标签" | tail -1

echo ""
echo "========== 4. 端到端吞吐：逐元素优化前后 =========="
gcc -O3 -march=native -fopenmp -o /tmp/trirv_before /tmp/trirv_vm_before_tritopt.c
echo -n "三值密集 优化前: "; /tmp/trirv_before -n 100 -t 1 /tmp/tri_heavy.trv 2>&1 | tail -1 | grep -o '吞吐=[0-9.]* MIPS'
echo -n "三值密集 优化后: "; ./trirv_vm -n 100 -t 1 /tmp/tri_heavy.trv 2>&1 | tail -1 | grep -o '吞吐=[0-9.]* MIPS'
echo -n "通用负载 优化前: "; /tmp/trirv_before -n 100 -t 1 /tmp/bench_sum.trv 2>&1 | tail -1 | grep -o '吞吐=[0-9.]* MIPS'
echo -n "通用负载 优化后: "; ./trirv_vm -n 100 -t 1 /tmp/bench_sum.trv 2>&1 | tail -1 | grep -o '吞吐=[0-9.]* MIPS'

echo ""
echo "========== 5. 并行：OpenMP 多实例扩展（三值密集）=========="
for t in 1 2 4; do
  echo -n "线程=$t: "; ./trirv_vm -n 100 -t $t /tmp/tri_heavy.trv 2>&1 | tail -1 | grep -o '吞吐=[0-9.]* MIPS'
done
