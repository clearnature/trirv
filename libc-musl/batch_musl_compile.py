#!/usr/bin/env python3
"""musl 目录批量编译统计（string + stdlib）"""
import os, subprocess, sys

_SCRIPT = os.path.dirname(os.path.abspath(__file__))
PP = os.path.join(_SCRIPT, "preprocess_musl.py")
OUTDIR = "/tmp/musl_batch"
os.makedirs(OUTDIR, exist_ok=True)

DIRS = ["/Users/zesheng/ai-lab/trirv/third_party/musl/src/string",
        "/Users/zesheng/ai-lab/trirv/third_party/musl/src/stdlib"]

# 三进制原生重写表：这些文件的语义适配版在 musl_tri/（纯三进制实现，
# 与上游差异逐条注释）。批处理跳过上游版本、编译 tri 版。
import os as _os
_SCRIPT = _os.path.dirname(_os.path.abspath(__file__))
TRI_REWRITE = {
    "memset.c": f"{_SCRIPT}/musl_tri/memset_tri.c",
    "memcpy.c": f"{_SCRIPT}/musl_tri/memcpy_tri.c",
    "strcspn.c": f"{_SCRIPT}/musl_tri/strcspn_tri.c",
    "strspn.c": f"{_SCRIPT}/musl_tri/strspn_tri.c",
    "strstr.c": f"{_SCRIPT}/musl_tri/strstr_tri.c",
    "memmem.c": f"{_SCRIPT}/musl_tri/memmem_tri.c",
    "div.c": f"{_SCRIPT}/musl_tri/div_tri.c",
    "ldiv.c": f"{_SCRIPT}/musl_tri/ldiv_tri.c",
    "lldiv.c": f"{_SCRIPT}/musl_tri/lldiv_tri.c",
    "imaxdiv.c": f"{_SCRIPT}/musl_tri/imaxdiv_tri.c",
    "qsort.c": f"{_SCRIPT}/musl_tri/qsort_tri.c",
    "strtol.c": f"{_SCRIPT}/musl_tri/strtol_tri.c",
    "strsignal.c": f"{_SCRIPT}/musl_tri/strsignal_tri.c",
    "strtod.c": f"{_SCRIPT}/musl_tri/strtod_tri.c",
    "atof.c": f"{_SCRIPT}/musl_tri/strtod_tri.c",
    "ecvt.c": f"{_SCRIPT}/musl_tri/ecvt_tri.c",
    "fcvt.c": f"{_SCRIPT}/musl_tri/ecvt_tri.c",
    "gcvt.c": f"{_SCRIPT}/musl_tri/ecvt_tri.c",
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
            rc = subprocess.run(['/Users/zesheng/ai-lab/trirv/cc/bin/trirv_cc', TRI_REWRITE[fn], tvo], capture_output=True, text=True)
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
            reason = r.stdout.split('TRIRV_SKIP', 1)[1].lstrip(':')
            reason = reason.split('\n')[0]
            skip.append(d.split('/')[-1] + '/' + fn)
            details[d.split('/')[-1] + '/' + fn] = reason
            continue
        tvo = os.path.join(OUTDIR, fn.replace('.c', '.trvo'))
        rc = subprocess.run(['/Users/zesheng/ai-lab/trirv/cc/bin/trirv_cc', pp, tvo], capture_output=True, text=True)
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
