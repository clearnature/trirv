#!/bin/bash
# TriRV C VM 优化构建脚本（Apple Silicon / x86-64 通用）
#
# 产出：
#   trirv_vm           —— 最终交付：PGO + -O3 -march=native（本机最优，吞吐较 O2 +50%+）
#   trirv_vm_portable  —— 可移植兜底：-O3，不依赖 native 特性（跨机器运行）
#
# PGO 三阶段：训练插桩 -> 训练运行生成 .profraw -> merge -> 用 profile 重新优化编译。
set -e
cd "$(dirname "$0")"

CC=${CC:-clang}                       # Apple clang / gcc 均可
CFLAGS="-std=gnu11 -Wno-unknown-pragmas -fopenmp"
OPT="-O3 -march=native"
OPT_PORTABLE="-O3"
TRAIN="/tmp/trirv_pgo_train.c"
PROFRAW="default.profraw"
PROFDATA="/tmp/trirv_train.profdata"
PROFTOOL=${PROFTOOL:-llvm-profdata}   # gcc 用户可改为: PROFTOOL=gcov 变体见文末

echo "=== [1/5] 生成 PGO 训练程序（覆盖 算术/乘除/访存/控制流）==="
cat > "$TRAIN" <<'SRC'
int sum(int n){int s=0;int i=1;while(i<=n){s=s+i;i=i+1;}return s;}
int main(){
  int s=0; int i=1;
  while(i<=4000){ s=s+i; i=i+1; }        /* ADD/BRANCH 密集 */
  i=1;
  while(i<2000){ s=s*3+7; i=i+1; }        /* MUL/ADDI 密集 */
  int a[32]; i=0;
  while(i<32){ a[i]=i*3; i=i+1; }         /* SW 密集 */
  i=0;
  while(i<32){ s=s+a[i]; i=i+1; }         /* LW 密集 */
  return s+sum(200);
}
SRC
python3 export_trv.py "$TRAIN" /tmp/trirv_train.trv >/dev/null
echo "  训练程序就绪"

echo "=== [2/5] 编译插桩版 ==="
$CC $OPT $CFLAGS -fprofile-instr-generate -o /tmp/trirv_train_bin trirv_vm.c -lm
echo "  插桩版就绪"

echo "=== [3/5] 运行训练，收集 profile ==="
rm -f "$PROFRAW" "$PROFRAW".*
/tmp/trirv_train_bin -n 40 -t 1 /tmp/trirv_train.trv >/dev/null 2>&1
if [ ! -f "$PROFRAW" ]; then
  echo "  警告：未生成 $PROFRAW，回退到无 PGO 构建"; USE_PGO=0
else
  $PROFTOOL merge -sparse "$PROFRAW" -o "$PROFDATA"
  USE_PGO=1
fi
rm -f "$PROFRAW" "$PROFRAW".*
echo "  profile 阶段完成 (USE_PGO=$USE_PGO)"

echo "=== [4/5] 用 profile 重编译最终二进制 ==="
if [ "$USE_PGO" = "1" ]; then
  $CC $OPT $CFLAGS -fprofile-instr-use="$PROFDATA" -o trirv_vm trirv_vm.c -lm
else
  $CC $OPT $CFLAGS -o trirv_vm trirv_vm.c -lm
fi
echo "  trirv_vm 就绪"

echo "=== [5/5] 编译可移植兜底 ==="
$CC $OPT_PORTABLE $CFLAGS -o trirv_vm_portable trirv_vm.c -lm
echo "  trirv_vm_portable 就绪"

echo ""
echo "完成。本机最优: ./trirv_vm <prog.trv>；跨机器: ./trirv_vm_portable <prog.trv>"
echo "GCC 用户替代 PGO 流程: 编译加 -fprofile-generate，训练后改 -fprofile-use 重编。"
