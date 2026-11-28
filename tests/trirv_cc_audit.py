#!/usr/bin/env python3
"""cc 双编译器回审套件——audit_cc.py 错误路径/边界（44 用例×2 编译器）
+ 语义差分四路（24 组 cc×tcc）。已知差异白名单：static-local（cc 拒
tcc 收——能力不对称文档化）、cc silent 面（寄存器式编译器检查深度，
文档化）。口径：crash=FAIL；语义分歧=FAIL。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    r = subprocess.run([sys.executable, "-I",
                        os.path.join(ROOT, "tools", "audit_cc.py")],
                       capture_output=True, text=True, timeout=600,
                       errors="replace")
    out = r.stdout + r.stderr
    bad = []
    for ln in out.splitlines():
        if "<-- 异常" in ln or "<-- 分歧" in ln:
            # static-local 已知差异豁免
            if "static-local" in ln:
                continue
            bad.append(ln.strip())
    n_cases = 0
    for ln in out.splitlines():
        if ln.strip().startswith("[") and "cc=" in ln:
            n_cases += 1
    if bad:
        for ln in bad:
            print(f"FAIL {ln}")
        print(f"cc 双编译器回审契约：{n_cases - len(bad)} PASS / {len(bad)} FAIL")
        sys.exit(1)
    print(f"cc 双编译器回审契约：{n_cases} PASS / 0 FAIL")
    sys.exit(0)


if __name__ == "__main__":
    main()
