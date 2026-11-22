#!/usr/bin/env python3
"""trirv_switch_e2e —— switch/case/default 语法契约（TDD RED->GREEN）。

背景：musl stdlib（atoi/div/qsort 等）依赖 switch 控制流。实现为
N_SWITCH 扁平收集 case/default/语句，gen 期 BEQ 分发链 + 顺序 case 体
（fall-through 天然成立），break 落 switch 末标签。

覆盖六个面（每面独立断言，位图法定位回归）：
  1. 基本匹配 + break
  2. fall-through（无 break 顺序穿透）
  3. default 命中
  4. 无匹配且无 default（落到 switch 之后，不执行任何 case 体）
  5. 负 case 值（case -1:）
  6. 嵌套 switch（内层 break 不穿透外层）
  7. case 顺序乱序（case 3 在 case 1 之前声明）
"""
import os
import struct
import subprocess
import sys
import tempfile

import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import trirv_vm as vm

HERE = os.path.dirname(os.path.abspath(__file__))
CVM = os.environ.get("TRIRV_VM_BIN", os.path.join(HERE, "..", "vm", "bin", "trirv_vm"))
CC = os.path.join(HERE, "..", "cc", "bin", "trirv_cc")

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


def run_py(src, max_steps=5000):
    cf = tempfile.mktemp(suffix=".c")
    tf = tempfile.mktemp(suffix=".trv")
    with open(cf, "w") as f:
        f.write(src)
    r = subprocess.run([CC, cf, tf], capture_output=True, text=True)
    if r.returncode != 0:
        return ("COMPILE_ERR", r.stderr.strip()[:100])
    data = open(tf, "rb").read()
    n_inst, n_seg = struct.unpack_from("<II", data, 8)
    prog = list(struct.unpack_from(f"<{n_inst}I", data, 16))
    off = 16 + 4 * n_inst
    mem = {}
    for _ in range(n_seg):
        addr, ln = struct.unpack_from("<II", data, off)
        off += 8
        mem[addr] = data[off:off + ln]
        off += ln
    c = vm.VM(prog, mem_init=mem).run(max_steps=max_steps)
    return c.exit_code


CASES = [
    # (名称, 源码, 期望退出码)
    ("基本匹配+break",
     "int main(){ int x=1; int r=0; switch(x){ case 1: r=10; break; "
     "case 2: r=20; break; } return r; }", 10),
    ("fall-through",
     "int main(){ int x=2; int r=0; switch(x){ case 1: r=10; break; "
     "case 2: r=20; case 3: r=r+5; break; case 4: r=99; break; } return r; }", 25),
    ("default 命中",
     "int main(){ int x=9; int r=0; switch(x){ case 1: r=10; break; "
     "default: r=99; break; } return r; }", 99),
    ("无匹配无 default",
     "int main(){ int x=7; int r=5; switch(x){ case 1: r=10; break; "
     "case 2: r=20; break; } return r; }", 5),
    ("负 case 值",
     "int main(){ int x=-1; int r=0; switch(x){ case -1: r=42; break; "
     "case 0: r=1; break; } return r; }", 42),
    ("嵌套 switch 内层 break",
     "int main(){ int a=1; int b=2; int r=0; switch(a){ case 1: "
     "switch(b){ case 1: r=1; break; case 2: r=22; break; } r=r+100; break; "
     "case 9: r=999; break; } return r; }", 122),
    ("case 乱序声明",
     "int main(){ int x=3; int r=0; switch(x){ case 3: r=30; break; "
     "case 1: r=10; break; case 2: r=20; break; } return r; }", 30),
    ("switch 后继续执行",
     "int main(){ int x=1; int r=0; switch(x){ case 1: r=7; break; "
     "default: r=9; break; } r=r+1; return r; }", 8),
]

for name, src, want in CASES:
    got = run_py(src)
    check(f"switch/{name}", got, want)

# C VM 一致性抽查
if os.path.exists(CVM):
    cf = tempfile.mktemp(suffix=".c")
    tf = tempfile.mktemp(suffix=".trv")
    with open(cf, "w") as f:
        f.write(CASES[1][1])
    subprocess.run([CC, cf, tf], check=True, capture_output=True)
    r = subprocess.run([CVM, tf], capture_output=True)
    errs = [l for l in r.stderr.decode().split() if l.startswith("exit=")]
    check("switch/CVM 一致", int(errs[0].split("=")[1]), 25)
    os.unlink(cf)
    os.unlink(tf)

print(f"\nswitch 契约：{passed} PASS / {failed} FAIL")
sys.exit(1 if failed else 0)
