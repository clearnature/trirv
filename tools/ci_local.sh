#!/bin/sh
# 本地门禁（ADR-29/30）：提交前最低标准；--san 与随机种子为增强项
set -e
cd "$(dirname "$0")/.."
python3 tests/run_all_tests.py --fast "$@"
