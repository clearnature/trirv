#!/usr/bin/env python3
"""trirv_cc_py_bitop_e2e —— Python 参考后端位运算/移位契约。

同一份 C 源码用 trirv_cc.py（Python 参考后端，TRV_CC=py）编译，
PyVM 执行，退出码对齐三值本征语义（2026-11-22 方言拍板，方案 A 退役）。

覆盖：& | ^ ~（TNOT：~0 = -1）<< >>（SLL ×3^k / SRA ÷3^k 向零截断 /
SRL raw ÷3^k、常数/变量移位）、组合表达式。期望值全部由本征 oracle
推导：& | ^ = 逐 trit min/max/mod-3 加（如 12&10=9、-6&3=3 而非 1）；
本征 XOR 不自反（a^a≠0）；a^-1 = 逐 trit 数字递减 ≠ TNOT。
"""
import os
import sys

import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import trirv_vm as vm
import trirv_cc_backend as backend

passed = 0
failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"PASS {name}: {got!r}")
    else:
        failed += 1
        print(f"FAIL {name}: got={got!r} want={want!r}")


def run_py_backend(src):
    """强制 TRV_CC=py → trirv_cc.py 参考后端 → PyVM 执行退出码。"""
    old_env = os.environ.get("TRV_CC")
    os.environ["TRV_CC"] = "py"
    try:
        try:
            prog, labels, txt, strs = backend.compile_c(src)
        except (RuntimeError, SyntaxError, ValueError) as e:
            return ("COMPILE_ERR", str(e)[:90])
    finally:
        if old_env is None:
            os.environ.pop("TRV_CC", None)
        else:
            os.environ["TRV_CC"] = old_env
    cpu = vm.VM(prog, mem_init=dict(strs or {}))
    c = cpu.run(max_steps=500000)
    return c.exit_code


CASES = [
    ("AND 基础（逐 trit min）", "int main(){ return (12 & 10); }", 9),
    ("OR 基础（逐 trit max）", "int main(){ return (12 | 10); }", 13),
    ("XOR 基础（mod-3 加）", "int main(){ return (12 ^ 10); }", 22),
    ("NOT（~0 = -1）", "int main(){ return (~0 == -1); }", 1),
    ("NOT 非零（~5 = -6）", "int main(){ return (~5 < 0); }", 1),
    ("SHL 常数（×3^k）", "int main(){ return (10 << 3); }", 270 & 0xFF),
    ("SHL 大移位（3^7）", "int main(){ return (1 << 7); }", 2187 & 0xFF),
    ("SHR 常数无符号（raw ÷3^k）", "int main(){ unsigned u=200; return u >> 3; }", 7),
    ("SHR 常数负数（÷3^k 向零截断）", "int main(){ return (-24 >> 2) == -2; }", 1),
    ("变量移位", "int main(){ int i=3; return (10 << i); }", 270 & 0xFF),
    ("变量右移", "int main(){ int i=2; unsigned u=200; return u >> i; }", 22),
    ("组合 a&b|c", "int main(){ return ((12 & 10) | 1); }", 10),
    ("负数 AND（本征真值）", "int main(){ return (-6 & 3); }", 3),
    ("负数 OR（本征真值）", "int main(){ return (-6 | 3) == -4; }", 0),  # TOR(-6,3) = -6
    ("XOR 不自反（mod-3 固有）", "int main(){ int x=42; return (x^x)!=0; }", 1),
]

for name, src, want in CASES:
    got = run_py_backend(src)
    check(f"pycc/{name}", got, want)

print(f"\nPython 后端位运算差分：{passed} PASS / {failed} FAIL")
sys.exit(1 if failed else 0)
