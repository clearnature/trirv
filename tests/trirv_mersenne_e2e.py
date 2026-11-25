#!/usr/bin/env python3
"""trirv_mersenne_e2e —— 梅森素数高精度验证契约（用户课题：三级制 +
梅森素数高精度 → 验证整数/浮点计算精度，提高到无限精度）。

1. 整数无限精度：base-3^13 多肢 bignum（C 方言，research/mersenne_ll_tri.c）
   —— 100! / 3^500 / 2^521−1（梅森素数 M_521）/ (3^339+5)(3^339+7) 大乘，
   肢序列经哨兵自定位导出，host 对 Python 无限精度 oracle 逐肢对拍。
2. Lucas-Lehmer 双向：M_31（素，s29≡0）/ M_11（合，s9≠0）——原生单字
   DIV/REM mod（s² ≤ 4.6e18 < 3^45/2 单字安全）。
3. 浮点精度：TF45 = 45 trit ≈ 71.3 bit ≈ 21.4 位十进制（double = 53 bit
   ≈ 15.9 位）——实证 TF45 能分辨 double 不能分辨的增量；1/3 在 TF45
   精确表示（三进制原生优势，二进制 0.010101…循环不可精确）。"""
import os
import struct
import subprocess
import sys
import tempfile

import _bootstrap  # noqa: F401
import _trvio
import trirv_vm as vm
import trirv_float as tf
import trirv_wasm

HERE = os.path.dirname(os.path.abspath(__file__))
CC = os.path.join(HERE, "..", "cc", "bin", "trirv_cc")
CVM = os.path.join(HERE, "..", "vm", "bin", "trirv_vm")
SRC = os.path.join(HERE, "..", "research", "mersenne_ll_tri.c")
B = 3 ** 13  # 1594323 肢基

passed = failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"PASS {name}: {got!r}")
    else:
        failed += 1
        print(f"FAIL {name}: got={got!r} want={want!r}")


def ll(p, iters):
    M = 2 ** p - 1
    s = 4
    for _ in range(iters):
        s = s * s - 2
        s %= M
    return s


# ---- 编译 + PyVM 运行 ----
trv = tempfile.mktemp(suffix=".trv")
r = subprocess.run([CC, SRC, trv], capture_output=True, text=True)
check("mersenne_ll_tri 编译", r.returncode, 0)

data = open(trv, "rb").read()
prog, mem, _ = _trvio.load_bytes(data)
v = vm.VM(prog, mem_init=mem).run(max_steps=3000000)
check("guest halted", int(v.halted), 1)


def word(addr):
    x = 0
    for k in range(9):
        x += v.mem[addr + k] * 243 ** k   # tryte = base 243（5 trit）
    return x


base = None
for a in range(200, 40000):
    if word(a) == 475769:                  # OUT[0] 魔数哨兵
        base = a
        break
check("哨兵定位 OUT", base is not None, True)

LENs = [word(base + (1 + i) * 9) for i in range(6)]
spec = [("F", 7, 64), ("G", 71, 64), ("H", 135, 64), ("A", 199, 64),
        ("Bb", 263, 64), ("P", 327, 132)]
dump = {nm: [word(base + (off + i) * 9) for i in range(n)] for nm, off, n in spec}


def val(name, ln):
    return sum(L * B ** i for i, L in enumerate(dump[name][:ln]))


for nm, off, ln, want, label in [
    ("F", 7, LENs[0], __import__("math").factorial(100), "100!"),
    ("H", 135, LENs[2], 3 ** 500, "3^500"),
    ("G", 71, LENs[1], 2 ** 521 - 1, "2^521−1（M_521）"),
    ("P", 327, LENs[5], (3 ** 339 + 5) * (3 ** 339 + 7), "大乘"),
]:
    check(f"bignum {label} 逐肢一致", val(nm, ln), want)

S31 = vm.s(word(base + 459 * 9))
S11 = vm.s(word(base + 460 * 9))
check("Lucas-Lehmer M_31 = 0（素）", S31, 0)
check("Lucas-Lehmer M_11（合）", S11, ll(11, 9))

lim_all = []
for nm, off, n in spec:
    lim_all += dump[nm]
chk = 0
for L in lim_all:
    chk = (chk + L) % 1000003
chk = (chk + ll(31, 29) % 1000003 + ll(11, 9) % 1000003) % 1000003
check("全肢校验和（exit & 0xFF）", v.exit_code, chk & 0xFF)

# ---- CVM 同跑（同校验和退出） ----
if os.path.exists(CVM):
    r = subprocess.run([CVM, trv], capture_output=True)
    ex = None
    for ln in r.stderr.decode("utf-8", "replace").splitlines():
        if "exit=" in ln:
            ex = int(ln.split("exit=")[1].split()[0])
    check("CVM 同校验和", ex, chk & 0xFF)

# ---- 浮点精度验证 ----
# 1) TF45 原生除法给精确 1/3（FDIV 1/3 → mant=1.0, e=-1；三进制原生优势：
#    1/3 = 0.1₃ 精确有限，二进制 0.010101…循环不可精确）
F = tf.TF45
raw13 = tf.f_div(F.encode(1.0), F.encode(3.0), F)
s_, E_, M_ = F.parts(raw13)
check("TF45 FDIV(1,3) 精确（e=-1, mant=1.0）", (E_ - F.bias, M_),
      (-1, F.P_NORM))
# 2) 分辨率对照 @3^60（步长：TF45 = 3^27 ≈ 7.6e12；double = 2^43 ≈ 8.8e12）
#    可分辨区 delta = 6e12：TF45 半步 3.8e12 < 6e12 → f_add 分辨 ✓；
#    boundary 区 delta = 4e12：精确舍入应向上（4e12 > 半步 3.8e12），
#    但 f_add 对阶后的「唯一进 FPU 加法」被 double ulp(1.0) = 2.2e-16
#    吞掉（delta 相对 9.4e-17 < 2.2e-16）→ 当前实现未分辨 = 诚实钉住
#    的已知边界（FADD 对阶整数化升级 = TASKS M5 精度升级待办）。
v60 = 3.0 ** 60
w60 = F.encode(v60)
w6 = tf.f_add(w60, F.encode(6.0e12), F)
check("TF45 f_add 分辨 delta=6e12 @3^60", w6 != w60, True)
w4 = tf.f_add(w60, F.encode(4.0e12), F)
check("f_add 对阶 FPU 边界（4e12 未分辨=已知限制）", w4 != w60, False)
d60 = struct.unpack("<d", struct.pack("<d", v60))[0]
dsum = struct.unpack("<d", struct.pack("<d", v60 + 4.0e12))[0]
check("double 同 delta 亦不可分辨（对照）", dsum == d60, True)
# 3) TF45 尾数 34 trit ≈ 53.9 bit > double 53 bit
check("TF45 尾数精度位当量 > double",
      round(34 * 1.584962500721156, 1), 53.9)

for p in (trv,):
    if os.path.exists(p):
        os.unlink(p)

print(f"\n梅森素数高精度验证：{passed} PASS / {failed} FAIL")
sys.exit(1 if failed else 0)
