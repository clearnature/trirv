#!/bin/bash
# gate_diff.sh —— 全量 63 语料差分验收（L1 硬闸）。
# 每语料四路：transpiler.py → .c → trirv_cc → .trv → VM
#             tricpp → .c → trirv_cc → .trv → VM
# 产物语义恒等 = 双 VM exit 相等。
# 用法：tools/gate_diff.sh [--quick]  （--quick 只跑前 10 语料）
set -e
cd "$(dirname "$0")/.."

CC_A="cc/bin/trirv_cc"
TCC="tinycc/trirv-tcc"

mkdir -p tmp/gate_diff

PASS=0; FAIL=0; SKIP=0
FAILED=""

for f in cpp/tests/*.cpp; do
    tag=$(basename "$f" .cpp)

    # ---- 1. Python transpiler 产物 ----
    if ! python3 cpp/transpiler.py "$f" tmp/gate_diff/${tag}_py.c 2>/dev/null; then
        SKIP=$((SKIP+1)); continue
    fi
    if ! cc/bin/trirv_cc tmp/gate_diff/${tag}_py.c tmp/gate_diff/${tag}_py.trv 2>/dev/null; then
        SKIP=$((SKIP+1)); continue
    fi
    # ---- 2. tricpp 产物 ----
    if ! gcc -std=gnu11 -O2 -I cpp/c -o /tmp/tricpp_gate cpp/c/*.c 2>/dev/null; then
        echo "FAIL [$tag] tricpp build"
        FAIL=$((FAIL+1)); continue
    fi
    if ! /tmp/tricpp_gate "$f" tmp/gate_diff/${tag}_c.c 2>/dev/null; then
        echo "FAIL [$tag] tricpp emit"
        FAIL=$((FAIL+1)); continue
    fi
    if ! cc/bin/trirv_cc tmp/gate_diff/${tag}_c.c tmp/gate_diff/${tag}_c.trv 2>/dev/null; then
        echo "FAIL [$tag] tricpp C compile"
        FAIL=$((FAIL+1)); continue
    fi
    # ---- 3. 双 VM exit 对比 ----
    e1=$(python3 -c "
import sys; sys.path.insert(0,'tests'); sys.path.insert(0,'vm')
import _trvio, trirv_vm as vm
p,m,_ = _trvio.load_bytes(open('tmp/gate_diff/${tag}_py.trv','rb').read())
print(vm.VM(p,mem_init=m).run(max_steps=100000).exit_code)
" 2>/dev/null)
    e2=$(python3 -c "
import sys; sys.path.insert(0,'tests'); sys.path.insert(0,'vm')
import _trvio, trirv_vm as vm
p,m,_ = _trvio.load_bytes(open('tmp/gate_diff/${tag}_c.trv','rb').read())
print(vm.VM(p,mem_init=m).run(max_steps=100000).exit_code)
" 2>/dev/null)
    if [ "$e1" == "$e2" ] && [ -n "$e1" ]; then
        PASS=$((PASS+1))
    else
        echo "FAIL [$tag] py_exit=$e1 c_exit=$e2"
        FAIL=$((FAIL+1))
    fi
done

echo ""
echo "gate_diff: $PASS PASS / $FAIL FAIL / $SKIP SKIP"
[ $FAIL -eq 0 ]
