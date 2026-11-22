#!/usr/bin/env python3
"""trirv_cc_py_bitop_e2e —— Python 参考后端 vs C 后端的位运算/移位差分。

同一份 C 源码用 trirv_cc.py（Python 参考后端，TRV_CC=py）编译，
PyVM 执行，退出码对齐方案 A（72-bit 位视图）语义——锁定两套后端一致。

覆盖：& | ^ ~（NOT 方案 A 语义：~0 != -1）<< >>（常数移位/变量移位/
无符号右移/负数右移）、组合表达式。负数操作数的期望值取方案 A
参考模型真值（mod 3^45 回绕与二进制直觉不同，如 -6&3=1 而非 2）。
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
    ("AND 基础", "int main(){ return (12 & 10); }", 8),
    ("OR 基础", "int main(){ return (12 | 10); }", 14),
    ("XOR 基础", "int main(){ return (12 ^ 10); }", 6),
    ("NOT 方案A（~0 != -1）", "int main(){ return (~0 == -1); }", 0),
    ("NOT 非零", "int main(){ return (~5 < 0); }", 1),
    ("SHL 常数", "int main(){ return (10 << 3); }", 80),
    ("SHL 大移位（8bit 退出码内）", "int main(){ return (1 << 7); }", 128),
    ("SHR 常数无符号", "int main(){ unsigned u=200; return u >> 3; }", 25),
    ("SHR 常数负数", "int main(){ return (-24 >> 2) == -6; }", 1),
    ("变量移位", "int main(){ int i=3; return (10 << i); }", 80),
    ("变量右移", "int main(){ int i=2; unsigned u=200; return u >> i; }", 50),
    ("组合 a&b|c", "int main(){ return ((12 & 10) | 1); }", 9),
    ("负数 AND（方案 A 真值）", "int main(){ return (-6 & 3); }", 1),
    ("负数 OR（方案 A 真值）", "int main(){ return (-6 | 3) == -4; }", 1),
    ("XOR 对合", "int main(){ int x=42; int y=x^123; return (y^123)==x; }", 1),
]

for name, src, want in CASES:
    got = run_py_backend(src)
    check(f"pycc/{name}", got, want)

print(f"\nPython 后端位运算差分：{passed} PASS / {failed} FAIL")
sys.exit(1 if failed else 0)
