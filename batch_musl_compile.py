#!/usr/bin/env python3
"""musl string/ 全目录批量编译统计"""
import os, subprocess, sys, glob

MUSL_STR = "/Users/zesheng/ai-lab/musl/src/string"
PP = "preprocess_musl.py"
OUTDIR = "/tmp/musl_batch"
os.makedirs(OUTDIR, exist_ok=True)

ok, fail, skip = [], [], []
details = {}
files = sorted(f for f in os.listdir(MUSL_STR)
               if f.endswith('.c') and not f.startswith(('wc','wcs','wmem')))
for fn in files:
    src = os.path.join(MUSL_STR, fn)
    # 预处理
    pp = os.path.join(OUTDIR, fn)
    r = subprocess.run([sys.executable, PP, src], capture_output=True, text=True)
    if r.returncode != 0:
        fail.append(fn); details[fn] = f"PP: {r.stderr[:60]}"
        continue
    with open(pp, 'w') as f: f.write(r.stdout)
    if 'TRIRV_SKIP' in r.stdout:
        skip.append(fn); details[fn] = 'byte-bitmap trick (binary bit ops), not applicable on tryte'
        continue
    # 编译
    tvo = os.path.join(OUTDIR, fn.replace('.c', '.trvo'))
    rc = subprocess.run(['./trirv_cc', pp, tvo], capture_output=True, text=True)
    if rc.returncode == 0:
        ok.append(fn)
    else:
        fail.append(fn)
        err = rc.stderr.strip().split('\n')[0][:90]
        details[fn] = err

print(f"OK: {len(ok)}  SKIP: {len(skip)}  FAIL: {len(fail)}  (total {len(ok)+len(skip)+len(fail)})")
for fn in skip: print(f"  ⊘ SKIP {fn}: {details[fn]}")
for fn in ok: print(f"  ✓ {fn}")
print(f"FAIL: {len(fail)}")
for fn in fail: print(f"  ✗ {fn}: {details[fn]}")

# 汇总退出码：FAIL>0 则失败
sys.exit(1 if fail else 0)
