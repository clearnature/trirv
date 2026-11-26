#!/usr/bin/env python3
"""trirv_witness_e2e —— 见证运行时 v1 契约（可见证性 = 产品要求）。

witness-sat 形态：昂贵求解产「结果+见证」，独立廉价校验器验见证。
本契约 = 对外能力演示：host 侧 Python 求解器产出着色见证 + 哈希
见证 → guest（TriRV 双 VM）独立校验。ABI 见 docs/WITNESS.md。"""
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
LIB = os.path.join(HERE, "..", "libc-musl", "musl_tri", "witness_tri.c")

passed = failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"PASS {name}: {got!r}")
    else:
        failed += 1
        print(f"FAIL {name}: got={got!r} want={want!r}")


DRIVER = '\nint w_coloring_check(int n, char *edges, int m, char *colors);\nint w_ll_cert(int p, int claim);\nint w_hash_check(char *data, int len, int claim);\n\nchar WBLOB[128] = {41, 43, 47};\nint CLAIMH[4] = {77777, 88888, 99999, 0};\nint RES[12] = {91, 92, 93};\n\nint main(void) {\n    int n;\n    int m;\n    int r0;\n    int r0b;\n    int r1;\n    int r2;\n    int r3;\n    int r4;\n    if (WBLOB[0] != 41 || WBLOB[1] != 43 || WBLOB[2] != 47) {\n        RES[3] = 9;\n        return 9;\n    }\n    n = WBLOB[3];\n    m = WBLOB[4] + WBLOB[5] * 243;\n    r0 = w_coloring_check(n, WBLOB + 6, m, WBLOB + 6 + 2 * m);\n    WBLOB[6 + 2 * m] = (WBLOB[6 + 2 * m] + 1) % 3;\n    r0b = w_coloring_check(n, WBLOB + 6, m, WBLOB + 6 + 2 * m);\n    WBLOB[6 + 2 * m] = (WBLOB[6 + 2 * m] + 2) % 3;\n    r1 = w_ll_cert(31, 0);\n    r2 = w_ll_cert(11, 1736);\n    r3 = w_ll_cert(31, 1);\n    r4 = w_hash_check(WBLOB + 6, m * 2, CLAIMH[3]);\n    RES[3] = r0;\n    RES[4] = r0b;\n    RES[5] = r1;\n    RES[6] = r2;\n    RES[7] = r3;\n    RES[8] = r4;\n    return 0;\n}\n'

# ---- 外部求解器（host Python）：planted 3-col 产出着色见证 ----
import random
rng = random.Random(2026)
N = 8
COL = [0, 0, 0, 1, 1, 1, 2, 2]
EDGES = [(i, j) for i in range(N) for j in range(i + 1, N)
         if COL[i] != COL[j] and rng.random() < 0.5]
M = len(EDGES)
blob = bytearray([41, 43, 47, N, M % 243, M // 243])
for (u, vv) in EDGES:
    blob += bytes([u, vv])
blob += bytes(COL)

# ---- 哈希见证（host 独立计算：三值 FNV mod 3^45）----
MOD = 3 ** 45
def t3xor(a, b):
    out = 0
    pw = 1
    for _ in range(45):
        out += ((a % 3) + (b % 3)) % 3 * pw
        a //= 3
        b //= 3
        pw *= 3
    return out

edge_bytes = bytes(v for pair in EDGES for v in pair)
h = 2166136261
for byte in edge_bytes:
    h = t3xor(h, byte)
    h = (h * 16777619) % MOD
claim_bytes = [(h // 243 ** k) % 243 for k in range(9)]

# ---- 编译 + 链接 ----
trvo = tempfile.mktemp(suffix=".trvo")
r = subprocess.run([CC, LIB, trvo], capture_output=True, text=True)
check("witness_tri 编译导出", r.returncode, 0)

cf = tempfile.mktemp(suffix=".c")
with open(cf, "w") as f:
    f.write(DRIVER)
trv = tempfile.mktemp(suffix=".trv")
r = subprocess.run([CC, cf, trv, "-l", trvo], capture_output=True, text=True)
check("驱动链接", r.returncode, 0)

# ---- 载入 + 注入外部见证 blob（mem poke，load 后 run 前）----
data = open(trv, "rb").read()
prog, mem, _ = _trvio.load_bytes(data)
v = vm.VM(prog, mem_init=mem)
base = None
for a in range(200, 40000):
    if v.mem[a] == 41 and v.mem[a + 1] == 43 and v.mem[a + 2] == 47:
        base = a
        break
check("哨兵定位 WBLOB", base is not None, True)
v.mem[base:base + len(blob)] = blob
# CLAIMH 哨兵定位（int 字 77777/88888/99999，9-tryte 步距）
def word(a):
    x = 0
    for k in range(9):
        x += v.mem[a + k] * 243 ** k
    return x
cb = None
for a in range(200, 40000):
    if word(a) == 77777 and word(a + 9) == 88888 and word(a + 18) == 99999:
        cb = a + 27
        break
check("哨兵定位 CLAIMH", cb is not None, True)
for k in range(9):
    v.mem[cb + k] = claim_bytes[k]

v.run(max_steps=3000000)
check("guest halted", int(v.halted), 1)
persist = all(v.mem[base + k] == blob[k] for k in range(len(blob)))
check("poke 持久化（blob 注入存活）", int(persist), 1)

# ---- 结果断言（RES[3..8] = r0 r0b r1 r2 r3 r4）----
# RES 哨兵自定位（int 数组 = 9-tryte word 步距）
def word2(a):
    x = 0
    for k in range(9):
        x += v.mem[a + k] * 243 ** k
    return x
rbase = None
for a in range(200, 40000):
    if word2(a) == 91 and word2(a + 9) == 92 and word2(a + 18) == 93:
        rbase = a
        break
check("哨兵定位 RES", rbase is not None, True)
res = [word2(rbase + (3 + i) * 9) for i in range(6)]
check("W1 有效着色见证 → 0", res[0], 0)
check("W1 篡改一色 → 1（独立拒绝）", res[1], 1)
check("W2 LL M_31 素证书（claim=0）→ 0", res[2], 0)
check("W2 LL M_11 合证书（claim=1736）→ 0", res[3], 0)
check("W2 错误声明 → 2（拒绝）", res[4], 2)
check("W4 哈希见证（host 独立算 claim）→ 0", res[5], 0)

# ---- CVM 同跑 ----
if os.path.exists(CVM):
    r = subprocess.run([CVM, trv], capture_output=True)
    ex = None
    for ln in r.stderr.decode("utf-8", "replace").splitlines():
        if "exit=" in ln:
            ex = int(ln.split("exit=")[1].split()[0])
    check("CVM 同跑 exit 一致（return 0）", ex, 0)

for p in (trvo, trv, cf):
    if os.path.exists(p):
        os.unlink(p)

print(f"\n见证运行时契约：{passed} PASS / {failed} FAIL")
sys.exit(1 if failed else 0)
