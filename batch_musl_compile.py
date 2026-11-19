#!/usr/bin/env python3
"""musl 目录批量编译统计（string + stdlib）"""
import os, subprocess, sys

PP = "preprocess_musl.py"
OUTDIR = "/tmp/musl_batch"
os.makedirs(OUTDIR, exist_ok=True)

DIRS = ["/Users/zesheng/ai-lab/musl/src/string",
        "/Users/zesheng/ai-lab/musl/src/stdlib"]

# 三进制原生重写表：这些文件的语义适配版在 musl_tri/（纯三进制实现，
# 与上游差异逐条注释）。批处理跳过上游版本、编译 tri 版。
TRI_REWRITE = {
    "memset.c": "musl_tri/memset_tri.c",
    "memcpy.c": "musl_tri/memcpy_tri.c",
}

ok, fail, skip = [], [], []
details = {}
total = 0
for d in DIRS:
    files = sorted(f for f in os.listdir(d)
                   if f.endswith('.c') and not f.startswith(('wc','wcs','wmem')))
    for fn in files:
        total += 1
        if fn in TRI_REWRITE:
            # 三进制原生重写版：直接编译（无需预处理——已是 TriRV C 子集）
            tvo = os.path.join(OUTDIR, fn.replace('.c', '.trvo'))
            rc = subprocess.run(['./trirv_cc', TRI_REWRITE[fn], tvo], capture_output=True, text=True)
            if rc.returncode == 0:
                ok.append(d.split('/')[-1]+'/'+fn+' [tri]')
            else:
                fail.append(d.split('/')[-1]+'/'+fn+' [tri]')
                details[fn] = rc.stderr.strip().split('\n')[0][:90]
            continue
        src = os.path.join(d, fn)
        pp = os.path.join(OUTDIR, fn)
        r = subprocess.run([sys.executable, PP, src], capture_output=True, text=True)
        if r.returncode != 0:
            fail.append(d.split('/')[-1]+'/'+fn); details[fn] = f"PP: {r.stderr[:60]}"
            continue
        with open(pp, 'w') as f: f.write(r.stdout)
        if 'TRIRV_SKIP' in r.stdout:
            skip.append(d.split('/')[-1]+'/'+fn); details[fn] = r.stdout.split('TRIRV_SKIP:')[1].split('\\n')[0]
            continue
        tvo = os.path.join(OUTDIR, fn.replace('.c', '.trvo'))
        rc = subprocess.run(['./trirv_cc', pp, tvo], capture_output=True, text=True)
        if rc.returncode == 0:
            ok.append(d.split('/')[-1]+'/'+fn)
        else:
            fail.append(d.split('/')[-1]+'/'+fn)
            err = rc.stderr.strip().split('\n')[0][:90]
            details[d.split('/')[-1]+'/'+fn] = err

print(f"OK: {len(ok)}  SKIP: {len(skip)}  FAIL: {len(fail)}  (total {total})")
for fn in skip: print(f"  ⊘ SKIP {fn}: {details.get(fn,'')}")
print(f"FAIL: {len(fail)}")
for fn in fail: print(f"  ✗ {fn}: {details.get(fn,'')}")
sys.exit(1 if fail else 0)
