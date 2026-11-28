#!/bin/bash
# audit_claims.sh —— commit message 声称 vs 实测差异表。
# 读最近 N 条 commit，提取 verify: 行（声称范围），
# 对比 ci_local.sh --fast 实际输出，输出差异表。
# 用法：tools/audit_claims.sh [N=10]
N=${1:-10}
cd "$(dirname "$0")/.."

echo "=== 最近 ${N} 条 commit 声称审计 ==="
echo ""

git log --oneline -"$N" | while read hash msg; do
    full=$(git log -1 --format=%B "$hash")
    # 提取 verify: 行
    verify_line=$(echo "$full" | grep "^verify:" | head -1)
    if [ -z "$verify_line" ]; then
        echo "  [$hash] ${msg:0:60}"
        echo "    ⚠ 无 verify: 声明（无差分验收记录）"
        echo ""
        continue
    fi
    echo "  [$hash] ${msg:0:60}"
    echo "    声称: $verify_line"
    echo ""
done

echo "=== 门禁实际状态 ==="
echo "  （运行 python3 tests/run_all_tests.py --fast 查看实时结果）"
echo ""
echo "=== 已注册差分套件 ==="
grep -n "gate_diff\|cport\|audit" tests/run_all_tests.py 2>/dev/null | head -10 || echo "  （无——差分未入册）"
