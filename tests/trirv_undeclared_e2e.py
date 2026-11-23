#!/usr/bin/env python3
"""trirv_undeclared_e2e —— 未声明标识符必须编译报错。

铁律"宁可报错不可错编"：未声明的标识符（变量/数组名）不应静默当零页
地址读写——那会产生无提示的错码（P4 实测踩坑：IN[k] 未声明 → 静默零页
→ 全 ±0 错码）。本契约锁定：含未声明标识符的编译必须失败（退出码≠0），
且报错信息含标识符名。

**已实现（2026-11-22，原 XFAIL 转 GREEN）**：trirv_cc gen 阶段四处静默兜底
（N_VAR 读 / addr_of / N_ASSIGN 目的 / 自增）改为硬报错。豁免口径：
- 隐式声明函数调用（C89）：N_CALL 名字直发，不经 N_VAR——链接期
  scan_undef 兜底报 undefined reference；
- 已声明函数名作值（fn-ptr decay）：fn_label_addr 路径保留
  （__libc_start_main(main) 等既有用例依赖）；
- tri-tcc 侧 tccgen.c `'%s' undeclared` 早已达标，不涉本契约。
fn-ptr 完整语义（&f/*f 形态、typedef 函数指针）另行记账，不在此契约。
"""
import os
import subprocess
import sys

import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）

HERE = os.path.dirname(os.path.abspath(__file__))
CC = os.path.join(HERE, "..", "cc", "bin", "trirv_cc")
UNDECL_C = _bootstrap.scratch("und.c")       # 草稿落仓库 tmp/（按进程隔离），不写系统 /tmp
UNDECL_TRV = _bootstrap.scratch("und.trv")

passed = 0
failed = 0


def expect_compile_error(name, src, needle):
    global passed, failed
    open(UNDECL_C, 'w').write(src)
    r = subprocess.run([CC, UNDECL_C, UNDECL_TRV], capture_output=True, text=True)
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
    open(UNDECL_C, 'w').write(src)
    r = subprocess.run([CC, UNDECL_C, UNDECL_TRV], capture_output=True, text=True)
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
    # 2026-11-22 闸门补强（GREEN 后新锁的三个兜底点）
    ("未声明赋值目的", "int main(void){ int x = 1; x = undecl_d; return x; }", "undecl_d"),
    ("未声明自增",     "int main(void){ undecl_e++; return 0; }", "undecl_e"),
    ("未声明取址",     "int main(void){ int *p = &undecl_f; return 0; }", "undecl_f"),
    # 豁免口径锁定：
    # ① 隐式声明调用的拒绝来自【链接层】（undefined reference），gen 层不得
    #    误报 undeclared identifier——单文件可执行未解析引用本就应拒绝；
    # ② 已声明函数名作值（裸名传参，__libc_start_main(main) 形态）不报错。
    ("隐式声明调用→链接层拒", "int main(void){ return ext_fn(3); }", "undefined reference"),
    ("函数名作值 ✓",   "int take(int f){ return f!=0; }\nint g(void){ return 1; }\n"
                       "int main(void){ return take(g); }", None),
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
