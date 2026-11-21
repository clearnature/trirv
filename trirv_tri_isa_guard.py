#!/usr/bin/env python3
"""trirv_tri_isa_guard —— T-1.8 ISA 一致性守卫（tri-isa.h ↔ trirv_isa.py）。

tri-isa.h 头注释自称"由 trirv_isa.py 自动生成"，但生成脚本曾长期不存在
（笔记 §4.5：声称不可复核、trirv_isa.py 演进后漂移无人发现）。本套件把
gen_tri_isa_h.py --check 注册为常绿门控：tinycc 头文件必须与 ISA 真相源
逐字一致，任何一方演进不同步立刻 FAIL。

额外做 96 条 encode→decode 往返自检（真相源自身健康）。
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GEN = os.path.join(HERE, "gen_tri_isa_h.py")
HEADER = os.path.normpath(os.path.join(HERE, "..", "tinycc", "tri-isa.h"))

passed = 0
failed = 0


def check(name, ok, note=""):
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
        print("FAIL %s%s" % (name, ("：" + note) if note else ""))


sys.path.insert(0, HERE)
import trirv_isa as isa  # noqa: E402

# 真相源自检：96 条 encode→decode 往返
for mn in isa.INST:
    w = isa.encode(mn)
    back = isa.decode(w)
    check("encode→decode 往返 %s" % mn, back["inst"] == mn,
          "dec=%r raw=%#x" % (back["inst"], w))

if not os.path.exists(HEADER):
    print("tri-isa.h 不存在（%s）——守卫 SKIP" % HEADER)
    print("\ntri-isa ISA 守卫：%d PASS / %d FAIL / 1 SKIP" % (passed, failed))
    sys.exit(0)

r = subprocess.run([sys.executable, GEN, "--check"], capture_output=True, text=True)
check("tri-isa.h ↔ trirv_isa.py 逐字一致", r.returncode == 0,
      (r.stdout + r.stderr).strip()[:400])

print("\ntri-isa ISA 守卫：%d PASS / %d FAIL" % (passed, failed))
sys.exit(1 if failed else 0)
