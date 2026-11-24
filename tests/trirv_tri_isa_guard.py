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
GEN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/isa/gen_tri_isa_h.py")
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
import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import trirv_isa as isa  # noqa: E402

# 真相源自检：96 条 encode→decode 往返
for mn in isa.INST:
    w = isa.encode(mn)
    back = isa.decode(w)
    check("encode→decode 往返 %s" % mn, back["inst"] == mn,
          "dec=%r raw=%#x" % (back["inst"], w))

# ---- ADR-35 规范函数（3b.5）：编码空间全枚举（常绿锁定审计数）----
# 逻辑组合 (opcode 0-26 × funct 0-8 × funct2 0-26) = 6561；定义域判定：
# U/J（opcode 命中即定义，funct/funct2 不参与）| R（(funct,funct2) 精确对）
# | 其余（funct 命中、funct2 任意）。审计数（ADR-35-encoding-space.md §1）：
# defined 2083 / undefined 4478——真相源漂移立刻 FAIL。
uj_ops, fi_map, r_map = set(), {}, {}
for _k, (_o, _f, _f2, _fmt) in isa.INST.items():
    if _fmt in ("U", "J"):
        uj_ops.add(_o)
    elif _fmt == "R":
        r_map.setdefault((_o, _f, _f2), _k)
    else:
        fi_map.setdefault((_o, _f), _k)
n_def = n_undef = 0
undef_words = []
for _op in range(27):
    for _fc in range(9):
        for _f2 in range(27):
            if _op in uj_ops or (_op, _fc, _f2) in r_map or (_op, _fc) in fi_map:
                n_def += 1
            else:
                n_undef += 1
                # trit 权重打包：t0-2=opcode, t7-8=funct, t17-19=funct2（rd/rs1/rs2=0）
                undef_words.append(_op + _fc * 3 ** 7 + _f2 * 3 ** 17)
check("编码空间全枚举 defined=2083", n_def == 2083, str(n_def))
check("编码空间全枚举 undefined=4478", n_undef == 4478, str(n_undef))
# 词级自洽：4478 个未定义逻辑组合的打包词必须全部 decode→None
# （规范函数双口径：逻辑成员判定 ↔ 词级解码互为镜像）
_bad = [w for w in undef_words if isa.decode(w)["inst"] is not None]
check("未定义编码词级自洽（4478 词 decode→None）", not _bad, str(len(_bad)))

if not os.path.exists(HEADER):
    print("tri-isa.h 不存在（%s）——守卫 SKIP" % HEADER)
    print("\ntri-isa ISA 守卫：%d PASS / %d FAIL / 1 SKIP" % (passed, failed))
    sys.exit(0)

r = subprocess.run([sys.executable, GEN, "--check"], capture_output=True, text=True)
check("tri-isa.h ↔ trirv_isa.py 逐字一致", r.returncode == 0,
      (r.stdout + r.stderr).strip()[:400])

print("\ntri-isa ISA 守卫：%d PASS / %d FAIL" % (passed, failed))
sys.exit(1 if failed else 0)
