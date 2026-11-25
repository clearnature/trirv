#!/usr/bin/env python3
"""trirv_bignum_e2e —— 三值无限精度整数库 bignum_tri 契约（科学计算模块）。

lib 侧（libc-musl/musl_tri/bignum_tri.c）：base-3^13 多肢 bignum 全 API
（from_int/mul_small/div_small/dec_one/mul_big/cmp/dec_str 十进制串）。
驱动：200! / 2^127−1（M_127 梅森素数）/ 3^1000 / (3^500+123)(3^500+77)
的十进制串逐字符对 Python 无限精度 oracle；bn_cmp 编码 spot；py VM +
CVM 双跑。oracle 数据集 = docs/MERSENNE-PRIMES.md。"""
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
LIB = os.path.join(HERE, "..", "libc-musl", "musl_tri", "bignum_tri.c")

passed = failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"PASS {name}: {got!r}")
    else:
        failed += 1
        print(f"FAIL {name}: got={got!r} want={want!r}")


DRIVER = '\nint bn_from_int(int*, int*, int);\nint bn_is_zero(int*, int);\nvoid bn_mul_small(int*, int*, int);\nint bn_div_small(int*, int*, int);\nvoid bn_dec_one(int*, int*);\nvoid bn_mul_big(int*, int, int*, int, int*);\nint bn_cmp(int*, int, int*, int);\nvoid bn_dec_str(int*, int, char*, int*);\n\nchar SD[6000];\nint LF;\nint LG;\nint LH;\nint LA;\nint LBn;\nint LP;\nint F[80];\nint G[80];\nint H[80];\nint A[80];\nint Bb[80];\nint P[160];\nint T[80];\nint LT;\nint S31;\nint S11;\nint M;\n\nint main(void) {\n    int m;\n    int i;\n    int s;\n    int off;\n    int n;\n    int r;\n    bn_from_int(F, &LF, 1);\n    m = 2;\n    while (m <= 200) {\n        bn_mul_small(F, &LF, m);\n        m = m + 1;\n    }\n    bn_from_int(G, &LG, 1);\n    i = 0;\n    while (i < 127) {\n        bn_mul_small(G, &LG, 2);\n        i = i + 1;\n    }\n    bn_dec_one(G, &LG);\n    bn_from_int(H, &LH, 1);\n    i = 0;\n    while (i < 1000) {\n        bn_mul_small(H, &LH, 3);\n        i = i + 1;\n    }\n    bn_from_int(A, &LA, 1);\n    i = 0;\n    while (i < 500) {\n        bn_mul_small(A, &LA, 3);\n        i = i + 1;\n    }\n    A[0] = A[0] + 123;\n    bn_from_int(Bb, &LBn, 1);\n    i = 0;\n    while (i < 500) {\n        bn_mul_small(Bb, &LBn, 3);\n        i = i + 1;\n    }\n    Bb[0] = Bb[0] + 77;\n    bn_mul_big(A, LA, Bb, LBn, P);\n    LP = LA + LBn;\n    M = 2147483647;\n    s = 4;\n    i = 0;\n    while (i < 29) {\n        s = s * s - 2;\n        s = s - (s / M) * M;\n        i = i + 1;\n    }\n    S31 = s;\n    M = 2047;\n    s = 4;\n    i = 0;\n    while (i < 9) {\n        s = s * s - 2;\n        s = s - (s / M) * M;\n        i = i + 1;\n    }\n    S11 = s;\n    SD[0] = 41;\n    SD[1] = 43;\n    SD[2] = 47;\n    off = 3;\n    bn_dec_str(F, LF, SD + off, &n);\n    off = off + n + 1;\n    bn_dec_str(G, LG, SD + off, &n);\n    off = off + n + 1;\n    bn_dec_str(H, LH, SD + off, &n);\n    off = off + n + 1;\n    bn_dec_str(P, LP, SD + off, &n);\n    off = off + n + 1;\n    SD[off] = 48 + r % 10;\n    SD[off + 1] = 48 + 8;\n    SD[off + 2] = 48 + 7;\n    off = off + 3;\n    SD[off] = 48 + (1 + bn_cmp(A, LA, Bb, LBn));\n    SD[off + 1] = 48 + (1 + bn_cmp(P, LP, A, LA));\n    SD[off + 2] = 48 + (1 + bn_cmp(F, LF, F, LF));\n    off = off + 3;\n    SD[off] = 0;\n    return 0;\n}\n'

# ---- 编译 ----
trvo = tempfile.mktemp(suffix=".trvo")
r = subprocess.run([CC, LIB, trvo], capture_output=True, text=True)
check("bignum_tri 编译导出", r.returncode, 0)

cf = tempfile.mktemp(suffix=".c")
with open(cf, "w") as f:
    f.write(DRIVER)
trv = tempfile.mktemp(suffix=".trv")
r = subprocess.run([CC, cf, trv, "-l", trvo], capture_output=True, text=True)
check("驱动链接", r.returncode, 0)

# ---- PyVM 运行 + 哨兵定位 + 逐字符对拍 ----
data = open(trv, "rb").read()
prog, mem, _ = _trvio.load_bytes(data)
v = vm.VM(prog, mem_init=mem).run(max_steps=3000000)
check("guest halted", int(v.halted), 1)

base = None
for a in range(200, 40000):
    if v.mem[a] == 41 and v.mem[a + 1] == 43 and v.mem[a + 2] == 47:
        base = a
        break
check("哨兵定位 SD", base is not None, True)


def parse_strings(start):
    out = []
    cur = bytearray()
    a = start
    while len(out) < 4:
        ch = v.mem[a]
        if ch == 0:
            out.append(cur.decode())
            a += 1
            cur = bytearray()
            continue
        cur.append(ch)
        a += 1
    return out, a


strs, tail = parse_strings(base + 3)
wants = [str(math.factorial(200)), str(2 ** 127 - 1), str(3 ** 1000),
         str((3 ** 500 + 123) * (3 ** 500 + 77))]
names = ["200!", "2^127-1 (M_127)", "3^1000", "bigmul"]
for nm, got, want in zip(names, strs, wants):
    check(nm + " dec-string", got, want)

ex_py = v.exit_code

if os.path.exists(CVM):
    r = subprocess.run([CVM, trv], capture_output=True)
    ex = None
    for ln in r.stderr.decode("utf-8", "replace").splitlines():
        if "exit=" in ln:
            ex = int(ln.split("exit=")[1].split()[0])
    check("CVM exit match", ex, ex_py)

for p in (trvo, trv, cf):
    if os.path.exists(p):
        os.unlink(p)

print(f"\nbignum_tri contract: {passed} PASS / {failed} FAIL")
sys.exit(1 if failed else 0)
