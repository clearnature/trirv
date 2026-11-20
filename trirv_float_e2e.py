#!/usr/bin/env python3
"""TF 浮点编解码契约测试（P1，TDD RED → GREEN）。

锁定 base-3 浮点格式的行为：
  值 = (-1)^s × (M / 3^k) × 3^(E - bias),  M∈[3^k, 2·3^k) 为正规化尾数

档位：
  TF32: 1 符号 + 5 阶码 + 14 尾数 = 20 trit, bias=121
  TF64: 1 符号 + 10 阶码 + 34 尾数 = 45 trit, bias=29524

期望值由数学定义独立计算（不调用被测 encode/decode 的内部），
避免"用实现验证实现"。参考实现：trirv_float.py。
"""
import math
import sys

import trirv_float as tf

PASS = 0
FAIL = 0


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"PASS {name}: {got}")
    else:
        FAIL += 1
        print(f"FAIL {name}: got={got} want={want}")


def check_close(name, got, want, rel_tol):
    global PASS, FAIL
    if math.isnan(want):
        ok = math.isnan(got)
    elif math.isinf(want):
        ok = math.isinf(got) and (math.copysign(1, got) == math.copysign(1, want))
    elif want == 0:
        ok = got == 0 and (math.copysign(1, got) == math.copysign(1, want))
    else:
        ok = abs(got - want) / abs(want) <= rel_tol
    if ok:
        PASS += 1
        print(f"PASS {name}: got={got:.12g} want={want:.12g}")
    else:
        FAIL += 1
        print(f"FAIL {name}: got={got!r} want={want!r} rel_tol={rel_tol}")


# ---- 1. 特殊值精确编码/解码 ----
for fmt, name in ((tf.TF32, "TF32"), (tf.TF64, "TF64")):
    r = fmt.encode(0.0)
    check(f"{name} +0 raw", r, fmt.ZERO_POS)
    check(f"{name} +0 decode", fmt.decode(r), 0.0)
    r = fmt.encode(-0.0)
    check(f"{name} -0 raw", r, fmt.ZERO_NEG)
    check(f"{name} -0 sign", math.copysign(1, fmt.decode(r)), -1.0)
    r = fmt.encode(float("inf"))
    check(f"{name} +Inf raw", r, fmt.INF_POS)
    check(f"{name} +Inf decode", fmt.decode(r), float("inf"))
    r = fmt.encode(float("-inf"))
    check(f"{name} -Inf raw", r, fmt.INF_NEG)
    check(f"{name} -Inf decode", fmt.decode(r), float("-inf"))
    r = fmt.encode(float("nan"))
    check(f"{name} NaN is_nan", fmt.is_nan(r), True)
    check(f"{name} NaN decode", math.isnan(fmt.decode(r)), True)
    check(f"{name} NaN 不等于自己", fmt.decode(r) == fmt.decode(r), False)
    check(f"{name} ±0 相等", fmt.decode(fmt.ZERO_POS) == fmt.decode(fmt.ZERO_NEG), True)

# ---- 2. base-3 精确值往返：3 的整数次幂应无损 ----
# 3^e 用尾数 3^k、阶码 e+bias 精确表示
for fmt, name, tol in ((tf.TF32, "TF32", 1e-6), (tf.TF64, "TF64", 1e-14)):
    for e in (0, 1, 2, 5, 10, -1, -3):
        v = 3.0 ** e
        check_close(f"{name} 3^{e} 往返", fmt.decode(fmt.encode(v)), v, tol)

# ---- 3. 代表值往返（double 精度内的近似） ----
# 容差 = 宿主 double 的 1 ULP（TF64 尾数 34 trit≈53.9bit，与 double 同量级，
# 往返必然有 ~1 ULP 误差，这是物理下限不是 bug）；TF32 用其尾数 1 ULP
for fmt, name, tol in ((tf.TF32, "TF32", 1 / 3 ** 13), (tf.TF64, "TF64", 2 ** -52)):
    for v in (1.0, -1.0, 0.5, 1.5, 2.0, 3.75, 1.0 / 3.0, 123.456, -0.001):
        check_close(f"{name} {v} 往返", fmt.decode(fmt.encode(v)), v, tol)

# ---- 4. 正规化不变量：正规数尾数 M ∈ [3^(k-1), 3^k)（最高 trit ∈{1,2}） ----
for fmt, name in ((tf.TF32, "TF32"), (tf.TF64, "TF64")):
    k = fmt.mant_trits
    for v in (1.0, 1.5, 2.9, 100.0, 0.01):
        s, E, M = fmt.parts(fmt.encode(v))
        ok = (3 ** (k - 1) <= M < 3 ** k) and (0 < E < fmt.max_exp)
        check(f"{name} {v} 正规化", ok, True)

# ---- 5. 上溢/下溢（受宿主 double 动态范围约束，见立项书 §7） ----
# TF64 阶码范围 ±29524 远超 double 极限（~3^646），double 全域都在 TF64 范围内，
# 不会触发 TF 层上溢；TF32 阶码 ±121 小于 double，可测到真正的上溢/下溢。
check("TF32 上溢 Inf", math.isinf(tf.TF32.decode(tf.TF32.encode(1e100))), True)
check("TF32 下溢 0", tf.TF32.decode(tf.TF32.encode(1e-100)), 0.0)
# TF64 装得下 double 全域：最大/最小有限 double 往返不失真、不抛异常
check_close("TF64 max double 往返",
            tf.TF64.decode(tf.TF64.encode(sys.float_info.max)),
            sys.float_info.max, 2 ** -52)
check_close("TF64 min double 往返",
            tf.TF64.decode(tf.TF64.encode(sys.float_info.min)),
            sys.float_info.min, 2 ** -52)

# ---- 6. 舍入：round-to-nearest，误差 ≤ 半个尾数 ULP ----
for fmt, name, tol in ((tf.TF32, "TF32", 1 / 3 ** 13), (tf.TF64, "TF64", 2 ** -52)):
    worst = 0.0
    for i in range(1, 200):
        v = 1.0 + i / 200.0
        got = fmt.decode(fmt.encode(v))
        rel = abs(got - v) / v
        worst = max(worst, rel)
    check(f"{name} 200 点舍入误差 ≤ 1 ULP", worst <= tol, True)

# ---- 7. 档位不变量 ----
check("TF32 trit 总数", tf.TF32.total_trits, 20)
check("TF64 trit 总数", tf.TF64.total_trits, 45)
check("TF32 bias", tf.TF32.bias, 121)
check("TF64 bias", tf.TF64.bias, 29524)

print()
print(f"TF 浮点编解码契约：{PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
