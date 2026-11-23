#!/usr/bin/env python3
"""TriRV py 参考后端 fn-ptr 契约（T7b，2026-11-23）。

trirv_cc.py（TRV_CC=py）补齐函数指针三件套：
  1. 声明符 `int (*fp)(int);`（括号包裹 *name + 参数表）
  2. 函数名作值 `fp = inc;`（fn-ptr decay，&sym 装载地址）
  3. 间接调用 `(*fp)(x)` / `(fp)(x)`（JALR）

契约：同一份源码 py 后端与 C 后端（trirv_cc.c）退出码逐项一致——
双后端同构口径（py 是参考实现，不是平行语义）。
"""
import os
import sys

import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import trirv_cc_backend as cc
import trirv_vm as vm

passed = 0
failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print("PASS %s: %r" % (name, got))
    else:
        failed += 1
        print("FAIL %s: got=%r want=%r" % (name, got, want))


def run_py(src):
    os.environ["TRV_CC"] = "py"
    try:
        prog, labels, asmtext, strings = cc.compile_c(src)
    finally:
        os.environ["TRV_CC"] = "c"
    cpu = vm.VM(prog, mem_init=strings or None).run(max_steps=100000)
    return cpu.exit_code


def run_c(src):
    os.environ.pop("TRV_CC", None)
    os.environ["TRV_CC_STRICT"] = "1"    # 契约必须真 C 覆盖——静默回退即假同构
    try:
        prog, labels, asmtext, strings = cc.compile_c(src)
    finally:
        os.environ.pop("TRV_CC_STRICT", None)
    cpu = vm.VM(prog, mem_init=strings or None).run(max_steps=100000)
    return cpu.exit_code


# （记账：C 后端间接调用只认 (*fp)(x) 形态，裸 (fp)(x) 括号形态回退
#   py 已实证——py-only 用例区不进同构契约。）
CASES = [
    ("声明+赋值+(*fp) 调用",
     "int inc(int x){ return x+1; } "
     "int main(){ int (*fp)(int); fp = inc; return (*fp)(41); }", 42),
    ("if/else 条件选函数",
     "int a(int x){ return x+1; } int b(int x){ return x*2; } "
     "int main(){ int (*fp)(int); int c=1; "
     "if(c){ fp=a; } else { fp=b; } return (*fp)(5); }", 6),
    ("fp 重指切换",
     "int a(int x){ return x+1; } int b(int x){ return x*2; } "
     "int main(){ int (*fp)(int); fp=a; int s=(*fp)(10); "
     "fp=b; s=s+(*fp)(10); return s; }", 11 + 20),
    # （记账：声明带初始化 `int (*fp)(int) = f;` C 后端 declarator 尚不支持
    #   ——回退路径已实证；契约用声明后赋值形态锁双后端同构。）
    ("赋值后切换+多参传递",
     "int add(int a,int b){ return a+b; } int mul(int a,int b){ return a*b; } "
     "int main(){ int (*fp)(int,int); fp=add; int s=(*fp)(3,4); "
     "fp=mul; s=s+(*fp)(3,4); return s; }", 7 + 12),
    ("递归函数经 fn-ptr",
     "int fact(int n){ if(n<=1){ return 1; } return n*fact(n-1); } "
     "int main(){ int (*fp)(int); fp = fact; return (*fp)(5); }", 120),
]


PY_ONLY = [
    ("(fp)(x) 括号形态（C 后端暂不支持）",
     "int inc(int x){ return x+1; } "
     "int main(){ int (*fp)(int); fp = inc; return (fp)(41); }", 42),
]


def main():
    for name, src, want in CASES:
        check(name + "·py", run_py(src), want)
        check(name + "·C 同构", run_c(src), want)
    for name, src, want in PY_ONLY:
        check(name + "·py-only", run_py(src), want)
    print("\npy fn-ptr 契约：%d PASS / %d FAIL" % (passed, failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
