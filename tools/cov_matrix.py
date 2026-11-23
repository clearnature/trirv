#!/usr/bin/env python3
"""指令覆盖矩阵（批次 1.11）：助记符 × 测试/库源 静态扫描。快照见 docs/COVERAGE_MATRIX.md。"""
import os, re, sys, glob
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "isa"))
import trirv_isa as isa
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(HERE)
scanned={}
for pat in ("tests/*.py","libc-musl/musl_tri/*.c","cc/trirv_crt.c","cc/trirv_firmware.c"):
    for path in sorted(glob.glob(os.path.join(ROOT,pat))):
        scanned[os.path.basename(path)]=open(path,encoding="utf-8",errors="ignore").read()
zero=[]
for inst in sorted(isa.INST.keys()):
    hits=[n for n,t in scanned.items() if re.search(r'(?<![A-Za-z0-9_.])'+re.escape(inst)+r'(?![A-Za-z0-9_.])',t)]
    print("%-10s %d  %s"%(inst,len(hits),",".join(sorted(hits)[:4])))
    if not hits: zero.append(inst)
print("\n静态零覆盖 %d/%d：%s（手算补缺=tests/trirv_golden_e2e.py）"%(len(zero),len(isa.INST),",".join(zero)))
