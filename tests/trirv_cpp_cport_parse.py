#!/usr/bin/env python3
"""cpp C 移植 parse 差分套件（0.1.4）。63 语料 parse 结果恒等
（Python cpp_parser.parse vs tricpp selftest-parse）：ok/双拒（parse
类错误）对齐；SemaError 层不涉 parse（c ok 为预期）。"""
import glob
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSRC = os.path.join(ROOT, "cpp", "c")
sys.path.insert(0, os.path.join(ROOT, "cpp", "src"))
from cpp_parser import parse, ParseError  # noqa: E402


def build():
    out = os.path.join(tempfile.gettempdir(), f"tricpp_{os.getpid()}")
    srcs = sorted(glob.glob(os.path.join(CSRC, "*.c")))
    r = subprocess.run(["gcc", "-std=gnu11", "-O2", "-I", CSRC, "-o", out] + srcs,
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        print(f"tricpp 构建失败: {r.stderr[:300]}")
        sys.exit(1)
    return out


def main():
    binp = build()
    ok_n = parity_n = fails = 0
    for f in sorted(glob.glob(os.path.join(ROOT, "cpp", "tests", "*.cpp"))):
        src = open(f).read()
        cf = os.path.join(tempfile.gettempdir(), "parse_case.cpp")
        open(cf, "w").write(src)
        py_ok = True
        try:
            parse(src)
        except ParseError:
            py_ok = False
        except Exception:
            py_ok = True                       # sema 层错误不涉 parse
        r = subprocess.run([binp, "selftest-parse", cf],
                           capture_output=True, text=True, timeout=60,
                           errors="replace")
        c_ok = r.returncode == 0
        if py_ok and c_ok:
            ok_n += 1
        elif not py_ok and not c_ok:
            parity_n += 1
        else:
            fails += 1
            print(f"  FAIL [{os.path.basename(f)}] py_ok={py_ok} "
                  f"c_exit={r.returncode} {r.stderr.strip()[:70]}")
    total = ok_n + parity_n
    print(f"cpp cport parse 契约：{total} PASS / {fails} FAIL")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
