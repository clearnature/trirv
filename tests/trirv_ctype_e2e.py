#!/usr/bin/env python3
"""trirv_ctype_e2e —— ADR-38 M3：musl src/ctype 15 文件 → ctype_tri.c
查表重写的差分契约。

上游是二进制惯用法（isalpha 的 (unsigned)c|32、tolower 的 c|32、isascii
的 !(c&~0x7f)），三值方言下编译过但静默算错（ADR-38 障碍分类 C 类首案；
见 docs/C-TERNARY-PITFALLS.md）。本契约：ctype_tri.c（3^k 旗标查表）
经 trirv_cc 编译链接，guest 写 15 函数 × c=0..127 全值 + 越界探针
（-5/200 → isX 全 0）入 char 矩阵，host 逐格对 Python oracle（musl
ASCII 语义）。"""
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
SRC = os.path.join(HERE, "..", "libc-musl", "musl_tri", "ctype_tri.c")

passed = failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"PASS {name}: {got!r}")
    else:
        failed += 1
        print(f"FAIL {name}: got={got!r} want={want!r}")


def isx(c, fn):
    if fn == "alnum": return 32 <= c < 127 and chr(c).isalnum()
    if fn == "alpha": return 32 <= c < 127 and chr(c).isalpha()
    if fn == "ascii": return 0 <= c < 128
    if fn == "blank": return c in (32, 9)
    if fn == "cntrl": return c < 32 or c == 127
    if fn == "digit": return 48 <= c <= 57
    if fn == "graph": return 33 <= c <= 126
    if fn == "lower": return 97 <= c <= 122
    if fn == "print": return 32 <= c <= 126
    if fn == "punct": return 33 <= c <= 126 and not chr(c).isalnum()
    if fn == "space": return c in (32, 9, 10, 11, 12, 13)
    if fn == "upper": return 65 <= c <= 90
    if fn == "xdigit": return 48 <= c <= 57 or 65 <= c <= 70 or 97 <= c <= 102
    return None


def low(c): return c + 32 if 65 <= c <= 90 else c
def up(c): return c - 32 if 97 <= c <= 122 else c

FNS = ["alnum", "alpha", "ascii", "blank", "cntrl", "digit", "graph",
       "lower", "print", "punct", "space", "upper", "xdigit"]

# ---- 编译 ctype_tri.c → TRVO ----
trvo = tempfile.mktemp(suffix=".trvo")
r = subprocess.run([CC, SRC, trvo], capture_output=True, text=True)
check("ctype_tri 编译导出", r.returncode, 0)

# ---- 差分矩阵测试程序（全局 char 数组 + 魔数哨兵自定位）----
FNS_DECLARE = " ".join(f"int is{f}(int);" for f in FNS) + " int tolower(int); int toupper(int);"
blocks = []
for i, f in enumerate(FNS):
    g = "is" + f
    blocks.append(
        f"    row = {i};\n"
        f"    for (c = 0; c < 128; c++) M[row * 140 + c] = {g}(c);\n"
        f"    M[row * 140 + 128] = {g}(-5);\n"
        f"    M[row * 140 + 129] = {g}(200);")
body = "\n".join(blocks)
TEST_SRC = f"""{FNS_DECLARE}

char M[2110];
int row;
int c;

int main(void) {{
    M[2100] = 41;
    M[2101] = 43;
    M[2102] = 47;
{body}
    row = 13;
    for (c = 0; c < 128; c++) M[row * 140 + c] = tolower(c);
    row = 14;
    for (c = 0; c < 128; c++) M[row * 140 + c] = toupper(c);
    return 0;
}}
"""
cf = tempfile.mktemp(suffix=".c")
with open(cf, "w") as f:
    f.write(TEST_SRC)
trv = tempfile.mktemp(suffix=".trv")
r = subprocess.run([CC, cf, trv, "-l", trvo], capture_output=True, text=True)
check("矩阵程序链接", r.returncode, 0)

data = open(trv, "rb").read()
prog, mem, _ = _trvio.load_bytes(data)
v = vm.VM(prog, mem_init=mem).run(max_steps=2000000)
check("矩阵执行(PyVM) halted", int(v.halted), 1)

base = None
for a in range(200, len(v.mem) - 20):
    if v.mem[a] == 41 and v.mem[a + 1] == 43 and v.mem[a + 2] == 47:
        base = a - 2100
        break
check("哨兵定位 M", base is not None, True)

bad = []
for i, fn in enumerate(FNS):
    for c in range(128):
        want = 1 if isx(c, fn) else 0
        got = v.mem[base + i * 140 + c]
        if got != want:
            bad.append((fn, c, got, want))
    # 越界探针：guest 存在 col 128（-5）/ col 129（200），读映射勿用 c 当偏移
    for c, col in ((-5, 128), (200, 129)):
        got = v.mem[base + i * 140 + col]
        if got != 0:
            bad.append((fn, c, got, 0))
check("isX 差分矩阵 13×130", len(bad), 0)
for fn, c, got, want in bad[:6]:
    print(f"  MISMATCH {fn}({c}): got={got} want={want}")

for c in range(128):
    g = v.mem[base + 13 * 140 + c]
    if g != low(c):
        bad.append(("tolower", c, g, low(c)))
    g = v.mem[base + 14 * 140 + c]
    if g != up(c):
        bad.append(("toupper", c, g, up(c)))
check("tolower/toupper 映射 128×2", len(bad) - len([b for b in bad if b[0] in FNS]), 0)
for fn, c, got, want in bad[-6:]:
    if fn in ("tolower", "toupper"):
        print(f"  MISMATCH {fn}({c}): got={got} want={want}")

check("spot isdigit('2')", v.mem[base + 5 * 140 + 50], 1)
check("spot tolower('A')", v.mem[base + 13 * 140 + 65], 97)
check("spot toupper('a')", v.mem[base + 14 * 140 + 97], 65)
check("spot ispunct('!')", v.mem[base + 9 * 140 + 33], 1)
check("spot isxdigit('g')", v.mem[base + 12 * 140 + 103], 0)

# ---- CVM 同跑 ----
if os.path.exists(CVM):
    r = subprocess.run([CVM, trv], capture_output=True)
    ex = None
    for ln in r.stderr.decode("utf-8", "replace").splitlines():
        if "exit=" in ln:
            ex = int(ln.split("exit=")[1].split()[0])
    check("矩阵执行(CVM) exit 0", ex, 0)

for p in (trvo, trv, cf):
    if os.path.exists(p):
        os.unlink(p)

print(f"\nctype M3 契约：{passed} PASS / {failed} FAIL")
sys.exit(1 if failed else 0)
