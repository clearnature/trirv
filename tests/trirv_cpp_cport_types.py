#!/usr/bin/env python3
"""cpp C 移植 types ADT 自检套件（0.1.2）。
L8：套件内动态构建 tricpp 产物落 tmp/（无 bin 缓存——cc/bin 假阴性
教训）。TDD：ADT 不变式 7 项 = cpp_types.py ADT 契约的 C 镜像。"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSRC = os.path.join(ROOT, "cpp", "c")


def build_tricpp():
    out = os.path.join(tempfile.gettempdir(), f"tricpp_{os.getpid()}")
    r = subprocess.run(
        ["gcc", "-std=gnu11", "-O2", "-I", CSRC,
         "-o", out, os.path.join(CSRC, "tricpp.c"),
         os.path.join(CSRC, "cpp_types.c")],
        capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        print(f"tricpp 构建失败: {r.stderr[:300]}")
        sys.exit(1)
    return out


def main():
    binp = build_tricpp()
    r = subprocess.run([binp, "selftest-types"], capture_output=True,
                       text=True, timeout=60)
    out = r.stdout + r.stderr
    if r.returncode != 0 or "FAIL" in out.replace("0 FAIL", ""):
        print(out.strip()[:400])
        print("cpp cport types 契约：0 PASS / 1 FAIL")
        sys.exit(1)
    print(out.strip())
    sys.exit(0)


if __name__ == "__main__":
    main()
