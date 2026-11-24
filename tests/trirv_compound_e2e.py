#!/usr/bin/env python3
"""trirv_compound_e2e —— 复合字面量 (TYPE){init} 契约（TDD）。

C99 复合字面量：`(div_t){ num/den, num%den }` —— musl stdlib 的
div/ldiv/lldiv/imaxdiv 全用此形态返回 struct。

覆盖三个语境 + struct 语义：
  1. return 语境：`return (T){a,b};`（配合 struct 按值返回/sret）
  2. 初始化语境：`struct T p = (T){1,2};`
  3. 实参语境：`f((T){3,4});`
  4. 成员读取：结果 .quot/.rem 正确
  5. 负值成员（-17/5 → quot=-3 rem=-2，C 截断语义）
  6. 嵌套表达式成员：`(T){x+1, x*2}`
"""
import os
import subprocess
import sys
import tempfile

import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import _trvio
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
        return ("COMPILE_ERR", r.stderr.strip()[:120])
    data = open(tf, "rb").read()
    prog, mem, _packed = _trvio.load_bytes(data)
    c = vm.VM(prog, mem_init=mem).run(max_steps=max_steps)
    return c.exit_code


CASES = [
    ("return 语境 + 成员读取",
     "struct D { int quot; int rem; };\n"
     "struct D mydiv(int num, int den) { return (struct D){ num/den, num%den }; }\n"
     "int main(){ struct D d = mydiv(17, 5); return d.quot*10 + d.rem; }", 32),
    ("负值截断语义",
     "struct D { int quot; int rem; };\n"
     "struct D mydiv(int num, int den) { return (struct D){ num/den, num%den }; }\n"
     "int main(){ struct D d = mydiv(-17, 5); return d.quot*10 + (d.rem+2); }",
     # -17/5=-3(截断), -17%5=-2 → (-3)*10 + (-2+2)= -30 → 45-trit 补码 & 0xFF
     (-30) & 0xFF),
    ("初始化语境",
     "struct P { int x; int y; };\n"
     "int main(){ struct P p = (struct P){ 3, 4 }; return p.x*10 + p.y; }", 34),
    ("实参语境",
     "struct P { int x; int y; };\n"
     "int sum(struct P p) { return p.x + p.y; }\n"
     "int main(){ return sum((struct P){ 30, 12 }); }", 42),
    ("嵌套表达式成员",
     "struct P { int x; int y; };\n"
     "int main(){ int x=5; struct P p = (struct P){ x+1, x*2 }; return p.x*10 + p.y; }",
     (6*10 + 10) & 0xFF),
]

for name, src, want in CASES:
    got = run_py(src)
    check(f"compound/{name}", got, want)

# C VM 一致性抽查（第一个用例）
if os.path.exists(CVM):
    cf = tempfile.mktemp(suffix=".c")
    tf = tempfile.mktemp(suffix=".trv")
    with open(cf, "w") as f:
        f.write(CASES[0][1])
    r = subprocess.run([CC, cf, tf], capture_output=True, text=True)
    if r.returncode == 0:
        r2 = subprocess.run([CVM, tf], capture_output=True)
        errs = [l for l in r2.stderr.decode().split() if l.startswith("exit=")]
        check("compound/CVM 一致", int(errs[0].split("=")[1]), 32)
    else:
        check("compound/CVM 编译", r.returncode, 0)
    os.unlink(cf)
    os.unlink(tf)

print(f"\n复合字面量契约：{passed} PASS / {failed} FAIL")
sys.exit(1 if failed else 0)
