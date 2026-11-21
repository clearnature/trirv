#!/bin/bash
# TriRV C VM 本轮优化整合基准：
#   (1) 修复 u() 负数语义 bug（对齐 Python）
#   (2) computed goto 直接线程化执行引擎
# 验证：负数回归 + 全套差分 + 分发对照 + 软件除法计数 + OpenMP 扩展
set -e
cd "$(dirname "$0")"
export PYTHONPATH=/workspace

echo "========== 1. 编译三版 =========="
gcc -O3 -march=native -fopenmp -o trirv_vm          trirv_vm.c                 # computed goto
gcc -O3 -march=native -fopenmp -DTRIRV_SWITCH -o trirv_vm_switch trirv_vm.c    # switch 对照
gcc -O3 -fopenmp -o trirv_vm_portable                 trirv_vm.c                 # 可移植
echo "  computed goto / switch 对照 / 可移植 三版编译完成"

echo ""
echo "========== 2. 负数语义正确性回归（本轮修复的 bug）=========="
python3.11 - <<'PY'
import subprocess
import trirv_cc_backend as cc, trirv_vm as vm
cases=[('return -1','int main(){ return -1; }',255),
       ('1-3','int main(){ return 1-3; }',254),
       ('return -100','int main(){ return -100; }',156),
       ('-5*7','int main(){ return -5*7; }',221)]
ok=0
for name,src,exp in cases:
    p,_,_,_=cc.compile_c(src); c=vm.VM(p).run(max_steps=10000)
    py=c.exit_code&0xFF
    open('/tmp/neg.c','w').write(src)
    subprocess.run(['python3.11','export_trv.py','/tmp/neg.c','/tmp/neg.trv'],capture_output=True)
    r=subprocess.run(['./trirv_vm','/tmp/neg.trv'],capture_output=True)
    ce=int([l for l in r.stderr.decode().split() if l.startswith('exit=')][0].split('=')[1].split()[0])
    good=(ce==exp==py); ok+=good
    print(f"  {'PASS' if good else 'FAIL'} {name:12} py={py:3} c={ce:3} 期望={exp:3}")
print(f"  负数回归 {ok}/{len(cases)}")
PY

echo ""
echo "========== 3. Python vs C VM 差分测试 =========="
python3.11 /tmp/difftest.py 2>&1 | grep -v "^导出\|^标签" | tail -2

echo ""
echo "========== 4. 软件除法调用数 =========="
echo -n "  __umodti3/__udivti3 调用点: "
objdump -d trirv_vm 2>/dev/null | grep -cE 'call.*(umodti3|udivmodti4|udivti3)' || true
echo "  （u() 已改为条件加减零除法；剩余调用来自 OP_DIV/OP_REM 及 store/load 里 POW3 变除数除法）"

echo ""
echo "========== 5. 分发引擎对照（各3次）=========="
cat > /tmp/bench_sum.c <<'SRC'
int sum(int n){int s=0;int i=1;while(i<=n){s=s+i;i=i+1;}return s;}
int main(){return sum(20000);}
SRC
python3.11 export_trv.py /tmp/bench_sum.c /tmp/bench_sum.trv >/dev/null
for bin in trirv_vm_switch trirv_vm; do
  echo -n "  $bin: "
  for i in 1 2 3; do ./$bin -n 100 -t 1 /tmp/bench_sum.trv 2>&1 | tail -1 | grep -o '吞吐=[0-9.]* MIPS' | tr '\n' ' '; done
  echo
done

echo ""
echo "========== 6. OpenMP 多实例扩展（computed goto）=========="
for t in 1 2 4; do
  echo -n "  线程=$t: "; ./trirv_vm -n 200 -t $t /tmp/bench_sum.trv 2>&1 | tail -1 | grep -o '吞吐=[0-9.]* MIPS'
done
