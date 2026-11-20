#!/usr/bin/env python3
"""trirv_undeclared_e2e —— 未声明标识符必须编译报错（RED 契约）。

铁律"宁可报错不可错编"：未声明的标识符（变量/数组名）不应静默当零页
地址读写——那会产生无提示的错码（P4 实测踩坑：IN[k] 未声明 → 静默零页
→ 全 ±0 错码）。本契约锁定：含未声明标识符的编译必须失败（退出码≠0），
且报错信息含标识符名。

**已知缺口（XFAIL）**：trirv_cc 尚未实现该检查——未声明标识符静默当零页
地址读写（P4 实测踩坑）。完整实现需在 gen 阶段做，且须先解决
「函数名作值」（fn-ptr decay）语义：fn 名既可能是隐式外部函数（C89 允许，
链接期解析）也可能是函数指针实参——expr_type/gen-load 两条路径都要覆盖。
待办登记于 TERNARY_FLOAT_PROPOSAL.md §5.7 与 run_all_tests XFAIL。
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CC = os.path.join(HERE, "trirv_cc")

passed = 0
failed = 0


def expect_compile_error(name, src, needle):
    global passed, failed
    open('/tmp/und.c', 'w').write(src)
    r = subprocess.run([CC, '/tmp/und.c', '/tmp/und.trv'], capture_output=True, text=True)
    if r.returncode == 0:
        failed += 1
        print("FAIL %s: 编译意外成功（静默零页风险）" % name)
    elif needle.lower() not in r.stderr.lower():
        failed += 1
        print("FAIL %s: 报错但缺标识符名 %r（stderr=%r）" % (name, needle, r.stderr[:120]))
    else:
        passed += 1
        print("PASS %s" % name)


def expect_compile_ok(name, src):
    global passed, failed
    open('/tmp/und.c', 'w').write(src)
    r = subprocess.run([CC, '/tmp/und.c', '/tmp/und.trv'], capture_output=True, text=True)
    if r.returncode != 0:
        failed += 1
        print("FAIL %s: 合法代码被误报（stderr=%r）" % (name, r.stderr[:120]))
    else:
        passed += 1
        print("PASS %s" % name)


cases = [
    ("未声明变量读",   "int main(void){ int x = undecl_a; return 0; }", "undecl_a"),
    ("未声明变量写",   "int main(void){ undecl_b = 5; return 0; }", "undecl_b"),
    ("未声明数组元素", "int main(void){ IN[0] = 1; return 0; }", "IN"),
    ("未声明数组读",   "int main(void){ int x = IN[3]; return 0; }", "IN"),
    ("未声明传参",     "int f(int a);\nint main(void){ return f(undecl_c); }", "undecl_c"),
    ("声明后使用 ✓",   "int IN[4];\nint main(void){ IN[0] = 7; return IN[0]; }", None),
    ("局部变量 ✓",     "int main(void){ int x = 3; return x; }", None),
]
for name, src_code, needle in cases:
    if needle is None:
        expect_compile_ok(name, src_code)
    else:
        expect_compile_error(name, src_code, needle)

print("\n未声明标识符契约：%d PASS / %d FAIL" % (passed, failed))
sys.exit(1 if failed else 0)
