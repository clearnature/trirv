#!/bin/bash
# TriRV 三进制无预测分支优化：正确性 + 性能基准
# 对比：原始基线 / if对照 / 强制预测(if-conversion禁用) / 无预测branchless
set -e
cd "$(dirname "$0")"

echo "========== 1. 编译四版 =========="
gcc -O3 -march=native -fopenmp -o trirv_vm              trirv_vm.c                                              # 无预测(默认)
gcc -O3 -march=native -fopenmp -DTRIRV_PREDICTED -o trirv_vm_predopt trirv_vm.c                               # if 对照
gcc -O3 -march=native -fopenmp -DTRIRV_PREDICTED -fno-if-conversion -fno-if-conversion2 -o trirv_vm_predforce trirv_vm.c  # 强制预测
gcc -O3 -fopenmp -o trirv_vm_portable trirv_vm.c                                                                 # 可移植无预测

echo "========== 2. 正确性：11 项差分测试（无预测版 vs Python VM）=========="
PYTHONPATH=/workspace python3.11 /tmp/difftest.py 2>&1 | grep -v "^导出\|^标签" | tail -1

echo "========== 3. 反汇编结构证据：step() 中分支机器码 =========="
gcc -O3 -march=native -fno-inline -c trirv_vm.c -o /tmp/nopred.o 2>/dev/null
gcc -O3 -march=native -fno-inline -DTRIRV_PREDICTED -fno-if-conversion -fno-if-conversion2 -c trirv_vm.c -o /tmp/pred.o 2>/dev/null
objdump -d /tmp/nopred.o | sed -n '/<step>:/,/^$/p' > /tmp/s_nopred.asm
objdump -d /tmp/pred.o   | sed -n '/<step>:/,/^$/p' > /tmp/s_pred.asm
echo "data-dependent jcc:  无预测=$(grep -cE '\bj(e|ne|l|le|g|ge|a|ae|b|be|s|ns)[a-z]* ' /tmp/s_nopred.asm)  强制预测=$(grep -cE '\bj(e|ne|l|le|g|ge|a|ae|b|be|s|ns)[a-z]* ' /tmp/s_pred.asm)"
echo "setcc/cmov 数据选择: 无预测=$(grep -cE '\b(set|cmov)[a-z]+ ' /tmp/s_nopred.asm)  强制预测=$(grep -cE '\b(set|cmov)[a-z]+ ' /tmp/s_pred.asm)"

echo "========== 4. 性能基准：可预测(sum) 与 不可预测(randbr) 分支 =========="
cat > /tmp/bench_sum.c <<'SRC'
int sum(int n){int s=0;int i=1;while(i<=n){s=s+i;i=i+1;}return s;}
int main(){return sum(20000);}
SRC
cat > /tmp/randbr.c <<'SRC'
int main(){
  int s=0, x=1;
  int i=0;
  while(i<20000){
    x = x*13 + 7;
    if((x%4) >= 2){ s = s+1; } else { s = s+2; }
    i = i+1;
  }
  return s;
}
SRC
python3.11 export_trv.py /tmp/bench_sum.c /tmp/bench_sum.trv >/dev/null
python3.11 export_trv.py /tmp/randbr.c   /tmp/randbr.trv   >/dev/null

for prog in bench_sum randbr; do
  echo "--- $prog ---"
  for bin in trirv_vm_pred trirv_vm_predopt trirv_vm_predforce trirv_vm; do
    vals=""
    for i in 1 2 3; do
      m=$(./$bin -n 100 -t 1 /tmp/$prog.trv 2>&1 | grep -o '吞吐=[0-9.]*' | cut -d= -f2)
      vals="$vals $m"
    done
    echo "$(printf '%-18s' $bin):$vals MIPS"
  done
done

echo "========== 5. OpenMP 多实例扩展（无预测版）=========="
for t in 1 2 4; do
  echo -n "线程=$t: "; ./trirv_vm -n 100 -t $t /tmp/bench_sum.trv 2>&1 | tail -1 | grep -o '吞吐=[0-9.]* MIPS'
done
