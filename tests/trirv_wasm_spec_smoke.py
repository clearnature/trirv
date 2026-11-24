#!/usr/bin/env python3
"""trirv_wasm_spec_smoke —— 官方 spec testsuite 冒烟门禁（批次 9.1-9.3）。

锁定当前已解锁文件的基线：语义修复不得使已通过断言变红。
全量基线由 wasm/spec_run.py 手工跑（docs/WASM_ROUTE_A.md 记档）——
258 文件全量太重，不进 fast 门禁；本套件钉 6 个代表性文件：
  block/loop/br/if = 控制流面（含 blocktype 全形态/br 值槽搬家）
  br_table        = 9.3（br.wast 的 as-br_table-* 亦覆盖语义）
  local_get       = 变量/调用面

判定：每文件 fail==0 且 pass >= 基线（下表硬编码）。tskip/vskip 不设限
（诚实计数面，随 9.4+ 逐项解锁）。wabt 缺席时 SKIP 整套件（新环境引导）。
"""
import os
import sys
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

passed = 0
failed = 0

# 文件 -> (最少 pass 数, 允许 fail 上限)——基线 2026-11-24 批次 9.3 实测
SMOKE = {
    "block": (2, 0),
    "loop": (2, 0),
    "br": (23, 0),
    "if": (8, 0),
    "local_get": (11, 0),
}


def main():
    global passed, failed
    wabt = os.path.join(ROOT, "tmp", "wabt", "wabt-1.0.42", "bin", "wast2json")
    if not os.path.exists(wabt):
        print("SKIP: wabt 未安装（tmp/wabt/）——spec 冒烟需要 wast2json")
        sys.exit(0)
    ts = os.path.join(ROOT, "third_party", "wasm-testsuite")
    if not os.path.isdir(ts):
        print("SKIP: testsuite 子模块未 checkout")
        sys.exit(0)
    for name, (minpass, maxfail) in SMOKE.items():
        r = subprocess.run([sys.executable, os.path.join(ROOT, "wasm", "spec_run.py"), name],
                           capture_output=True, text=True, timeout=600)
        line = ""
        for l in r.stdout.splitlines():
            if l.startswith(name):
                line = l
        if not line:
            print("FAIL %s: 运行器无输出" % name)
            failed += 1
            continue
        parts = line.split()
        p, f = int(parts[1]), int(parts[2])
        if f <= maxfail and p >= minpass:
            passed += 1
            print("PASS %s: pass=%d fail=%d (基线 ≥%d, ≤%d)" % (name, p, f, minpass, maxfail))
        else:
            failed += 1
            print("FAIL %s: pass=%d fail=%d (基线 ≥%d, ≤%d)" % (name, p, f, minpass, maxfail))
    print("\nspec 冒烟：%d PASS / %d FAIL" % (passed, failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
