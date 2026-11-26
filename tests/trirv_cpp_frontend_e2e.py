#!/usr/bin/env python3
"""trirv_cpp_frontend_e2e —— C++ 前端 sema 管线契约（五层架构，v0.0.8 起）。

管线：cpp/transpiler.py（lexer→parser→sema→lower→emit）→ trirv_cc → 双 VM。
行为断言 7 + N8 拒绝 2；双 VM 一致 + N8 精确匹配（要么对、要么报错）。

sema 验收标准（用户拍板）：Type{kind: VALUE|REF, is_const} 驱动全部
引用/const/mangling 决策；_ZNK/RK 喂点逐文件核对（test09 产物即样张）。
"""
import os
import subprocess
import sys

import _bootstrap  # noqa: F401
import _trvio
import trirv_vm as vm

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CPP = os.path.join(ROOT, "cpp", "transpiler.py")
CC = os.path.join(ROOT, "cc", "bin", "trirv_cc")
CVM = os.path.join(ROOT, "vm", "bin", "trirv_vm")
TMP = os.path.join(ROOT, "tmp")

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


# (文件, 期望 exit)——exit 通道 8 位 POSIX 截断（vm/trirv_vm.py:515），期望值 <256
BEHAVIOR = [
    ("test01_counter.cpp", 2),
    ("test02_vector.cpp", 11),
    ("test03_inherit.cpp", 5),
    ("test04_ref.cpp", 99),
    ("test05_refparam.cpp", 25),
    ("test06_const.cpp", 19),
    ("test07_overload.cpp", 160),
    ("test09_sema.cpp", 70),
    ("test08b_lifetime.cpp", 7),     # 0.0.9 曾拒绝，0.0.10 物化转正
    ("test10_lifetime.cpp", 56),
    ("test11_default.cpp", 69),
    ("test12_freefunc.cpp", 33),
    ("test13_namespaces.cpp", 44),
    ("test14_externc.cpp", 25),
    ("test15_new.cpp", 57),          # 需链 libc（malloc/free）
    ("test16_operators.cpp", 122),
    ("test17_ctor_expr.cpp", 44),
    ("test18_struct_assign.cpp", 136),
    ("test19_dtor.cpp", 28),         # 需链 libc（new/delete）
    ("test20_static.cpp", 9),
    ("test21_fwd.cpp", 17),
]
# (文件, stderr 必含码)——拒绝性用例（C++ 法：非常量左值引用不延寿）
REJECT = [
    ("test08a_n8_ref.cpp", "N8"),
]

os.makedirs(TMP, exist_ok=True)

# libc trvo（malloc/free——test15 new/delete 依赖；只对标注用例链接）
LIBC_Tinyin = None
LIBC_SRC = os.path.join(ROOT, "cc", "trirv_libc.c")
LIBC_TVO = os.path.join(TMP, "trirv_libc.trvo")
_needs_libc = any(f in ("test15_new.cpp", "test19_dtor.cpp")
                  for f, _w in BEHAVIOR)
if _needs_libc and not os.path.exists(LIBC_TVO):
    r = subprocess.run([CC, LIBC_SRC, LIBC_TVO], capture_output=True, text=True)
    assert r.returncode == 0, f"libc trvo 构建失败: {r.stderr[:120]}"
LIBC_LINK = ["-l", LIBC_TVO] if _needs_libc else []

for fn, want in BEHAVIOR:
    src = os.path.join(ROOT, "cpp", "tests", fn)
    c_out = os.path.join(TMP, fn.replace(".cpp", ".c"))
    trv = os.path.join(TMP, fn.replace(".cpp", ".trv"))
    r = subprocess.run([sys.executable, CPP, src, c_out],
                       capture_output=True, text=True)
    if r.returncode != 0:
        check(f"{fn} 降级", f"exit={r.returncode} {r.stderr[:80]}", "exit=0")
        continue
    r = subprocess.run([CC, c_out, trv] + (LIBC_LINK if fn in ("test15_new.cpp", "test19_dtor.cpp") else []),
                       capture_output=True, text=True)
    if r.returncode != 0:
        check(f"{fn} trirv_cc", r.stderr[:80], "exit=0")
        continue
    prog, mem, _ = _trvio.load_bytes(open(trv, "rb").read())
    c = vm.VM(prog, mem_init=mem).run(max_steps=50000)
    check(f"{fn} PyVM", c.exit_code, want)
    if os.path.exists(CVM):
        r = subprocess.run([CVM, trv], capture_output=True)
        ex = None
        for ln in r.stderr.decode("utf-8", "replace").splitlines():
            if "exit=" in ln:
                ex = int(ln.split("exit=")[1].split()[0])
        check(f"{fn} CVM", ex, want)

for fn, code in REJECT:
    src = os.path.join(ROOT, "cpp", "tests", fn)
    c_out = os.path.join(TMP, fn.replace(".cpp", ".c"))
    r = subprocess.run([sys.executable, CPP, src, c_out],
                       capture_output=True, text=True)
    check(f"{fn} 拒绝退出", r.returncode != 0, True)
    check(f"{fn} 错误码含 {code}", code in r.stderr, True)

# ---- 类型层 ADT 不变式（sov-math 强类型纪律：非法状态不可表达） ----
sys.path.insert(0, os.path.join(ROOT, "cpp", "src"))
from cpp_types import (TInt, TVoid, TClass, TRef, tint, tvoid, tcls, tref,
                       TypeError_, type_name)   # noqa: E402

try:
    tref(tref(tint()))
    check("ADT: ref-of-ref 拒绝", False, True)
except TypeError_:
    check("ADT: ref-of-ref 拒绝", True, True)
try:
    TVoid(True)                                # TVoid 无 is_const 形参
    check("ADT: const void 不可表达", False, True)
except TypeError:
    check("ADT: const void 不可表达", True, True)
r = tref(tint(True))
try:
    r.is_const = False
    check("ADT: ref 顶层 const 不可赋值", False, True)
except TypeError_:
    check("ADT: ref 顶层 const 不可赋值", True, True)
check("ADT: ref const 派生自 pointee", r.is_const, True)
try:
    tcls("")
    check("ADT: 匿名类拒绝", False, True)
except TypeError_:
    check("ADT: 匿名类拒绝", True, True)
check("ADT: kind 投影（边界层）", type_name(tref(tcls("Box"))), "Box")
check("ADT: 相等性（结构）", tint(True) == tint(True)
      and tint(True) != tint(False) and tref(tint()) == tref(tint()), True)

print(f"\ncpp 前端 sema 管线契约：{passed} PASS / {failed} FAIL")
sys.exit(1 if failed else 0)
