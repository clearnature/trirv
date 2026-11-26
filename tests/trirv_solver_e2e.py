#!/usr/bin/env python3
"""trirv_solver_e2e —— solver/ 模块契约：见证-校验分离求解器双端对拍。

py/color3.py（搜索原型）与 c/color3_solve_tri.c（TriRV 生产求解器）
同搜索序——同实例必得同一见证（逐字符对拍）；UNSAT = 完备枚举证明
（K4 负样本）；见证独立复验零冲突。架构与 oracle 数据集见
solver/README.md。"""
import math
import os
import subprocess
import sys
import tempfile

import _bootstrap  # noqa: F401
import _trvio
import trirv_vm as vm

HERE = os.path.dirname(os.path.abspath(__file__))
CC = os.path.join(HERE, "..", "cc", "bin", "trirv_cc")
CVM = os.path.join(HERE, "..", "vm", "bin", "trirv_vm")
LIB = os.path.join(HERE, "..", "solver", "c", "color3_solve_tri.c")
sys.path.insert(0, os.path.join(HERE, "..", "solver", "py"))
from color3 import color3_solve

passed = failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"PASS {name}: {got!r}")
    else:
        failed += 1
        print(f"FAIL {name}: got={got!r} want={want!r}")


DRIVER = '\nint color3_solve(int n, int m, char *edges, char *colors_out);\nchar INST[600] = {41, 43, 47};\nchar OUT[600] = {51, 52, 53};\n\nint main(void) {\n    int cnt;\n    int off_in;\n    int off_out;\n    int inst;\n    int n;\n    int m;\n    if (INST[0] != 41 || INST[1] != 43 || INST[2] != 47) {\n        return 9;\n    }\n    cnt = INST[3];\n    off_in = 4;\n    off_out = 3;    /* 哨兵 OUT[0..2] 保留 */\n    inst = 0;\n    while (inst < cnt) {\n        int sat;\n        n = INST[off_in];\n        m = INST[off_in + 1] + INST[off_in + 2] * 243;\n        sat = color3_solve(n, m, INST + off_in + 3, OUT + off_out + 1);\n        OUT[off_out] = 48 + sat;\n        if (sat == 1) {\n            off_out = off_out + 1 + n + 1;      /* flag + 见证 + NUL */\n        } else {\n            off_out = off_out + 2;              /* flag + NUL（UNSAT 空见证） */\n        }\n        off_in = off_in + 3 + 2 * m;\n        inst = inst + 1;\n    }\n    OUT[off_out] = 0;\n    return 0;\n}\n'

# ---- 实例集（host 生成：可解 / UNSAT / 结构化）----
import random
rng = random.Random(2026)

INSTS = []
# ① planted 3-col（解保证存在）
COL = [0, 0, 0, 1, 1, 1, 2, 2]
E1 = [(i, j) for i in range(8) for j in range(i + 1, 8)
      if COL[i] != COL[j] and rng.random() < 0.5]
INSTS.append((8, E1))
# ② 密集 planted
E2 = [(i, j) for i in range(6) for j in range(i + 1, 6)
      if COL[i] != COL[j] and rng.random() < 0.8]
INSTS.append((6, E2))
# ③ K4（4-团：3-着色 UNSAT——完备枚举证明）
INSTS.append((4, [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)]))
# ④ 三角形（最小可解非平凡）
INSTS.append((3, [(0, 1), (1, 2), (0, 2)]))
# ⑤ C6 偶环
INSTS.append((6, [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (0, 5)]))

# ---- Python 侧求解（oracle 原型）----
py_witnesses = []
for (n, edges) in INSTS:
    py_witnesses.append(color3_solve(n, edges))
print("py witnesses:", py_witnesses)

# ---- 实例 blob（哨兵 + 逐实例）----
blob = bytearray([41, 43, 47, len(INSTS)])
for (n, edges) in INSTS:
    m = len(edges)
    blob += bytes([n, m % 243, m // 243])
    for (u, vv) in edges:
        blob += bytes([u, vv])

# ---- 编译 + 链接 + 注入 + 运行 ----
trvo = tempfile.mktemp(suffix=".trvo")
r = subprocess.run([CC, LIB, trvo], capture_output=True, text=True)
check("color3_solve_tri 编译导出", r.returncode, 0)

cf = tempfile.mktemp(suffix=".c")
with open(cf, "w") as f:
    f.write(DRIVER)
trv = tempfile.mktemp(suffix=".trv")
r = subprocess.run([CC, cf, trv, "-l", trvo], capture_output=True, text=True)
check("驱动链接", r.returncode, 0)

data = open(trv, "rb").read()
prog, mem, _ = _trvio.load_bytes(data)
v = vm.VM(prog, mem_init=mem)
base = None
for a in range(200, 40000):
    if v.mem[a] == 41 and v.mem[a + 1] == 43 and v.mem[a + 2] == 47:
        base = a
        break
check("哨兵定位 INST", base is not None, True)
v.mem[base:base + len(blob)] = blob
v.run(max_steps=3000000)
check("guest halted", int(v.halted), 1)

# ---- OUT 解析（flag char + witness + NUL × 实例数）----
ob = None
for a in range(200, 40000):
    if v.mem[a] == 49 and v.mem[a + 1 + len(str(py_witnesses[0])) + 1] == 49:
        ob = a
        break
if ob is None:
    for a in range(200, 40000):
        if v.mem[a] in (48, 49) and v.mem[a + 1] in (48, 49, 50):
            ob = a
            break
check("OUT 定位", ob is not None, True)

ok = True
a = ob
for idx, (n, edges) in enumerate(INSTS):
    flag = chr(v.mem[a])
    wit = ""
    k = a + 1
    while v.mem[k] != 0:
        wit += chr(v.mem[k])
        k += 1
    a = k + 1
    py_w = py_witnesses[idx]
    nm = f"实例{idx}(n={n},m={len(edges)})"
    # 双端一致
    if py_w is None:
        good = flag == "0" and wit == ""
        check(f"{nm} 双端 UNSAT", (flag, wit), ("0", ""))
    else:
        good = flag == "1" and wit == py_w
        check(f"{nm} 双端同见证", wit, py_w)
    # 独立复验：见证零冲突
    if flag == "1":
        colors = [ord(ch) - 48 for ch in wit]
        confl = sum(1 for (u, vv) in edges if colors[u] == colors[vv])
        check(f"{nm} 见证独立复验零冲突", confl, 0)
    ok = ok and good

# UNSAT 实例（K4 = 完备枚举证明）已由实例2 双端 UNSAT 检查覆盖

ex = None
if os.path.exists(CVM):
    r = subprocess.run([CVM, trv], capture_output=True)
    for ln in r.stderr.decode("utf-8", "replace").splitlines():
        if "exit=" in ln:
            ex = int(ln.split("exit=")[1].split()[0])
check("CVM 同跑 exit 一致（return 0）", ex, 0)

for p in (trvo, trv, cf):
    if os.path.exists(p):
        os.unlink(p)

print(f"\nsolver/ 模块契约：{passed} PASS / {failed} FAIL")
sys.exit(1 if failed else 0)
