#!/usr/bin/env python3
"""TF 浮点编解码契约测试（P1，TDD RED → GREEN）。

锁定 base-3 浮点格式的行为：
  值 = (-1)^s × (M / 3^k) × 3^(E - bias),  M∈[3^k, 2·3^k) 为正规化尾数

档位：
  TF20: 1 符号 + 5 阶码 + 14 尾数 = 20 trit, bias=121
  TF45: 1 符号 + 10 阶码 + 34 尾数 = 45 trit, bias=29524

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
for fmt, name in ((tf.TF20, "TF20"), (tf.TF45, "TF45")):
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
for fmt, name, tol in ((tf.TF20, "TF20", 1e-6), (tf.TF45, "TF45", 1e-14)):
    for e in (0, 1, 2, 5, 10, -1, -3):
        v = 3.0 ** e
        check_close(f"{name} 3^{e} 往返", fmt.decode(fmt.encode(v)), v, tol)

# ---- 3. 代表值往返（double 精度内的近似） ----
# 容差 = 宿主 double 的 1 ULP（TF45 尾数 34 trit≈53.9bit，与 double 同量级，
# 往返必然有 ~1 ULP 误差，这是物理下限不是 bug）；TF20 用其尾数 1 ULP
for fmt, name, tol in ((tf.TF20, "TF20", 1 / 3 ** 13), (tf.TF45, "TF45", 2 ** -52)):
    for v in (1.0, -1.0, 0.5, 1.5, 2.0, 3.75, 1.0 / 3.0, 123.456, -0.001):
        check_close(f"{name} {v} 往返", fmt.decode(fmt.encode(v)), v, tol)

# ---- 4. 正规化不变量：正规数尾数 M ∈ [3^(k-1), 3^k)（最高 trit ∈{1,2}） ----
for fmt, name in ((tf.TF20, "TF20"), (tf.TF45, "TF45")):
    k = fmt.mant_trits
    for v in (1.0, 1.5, 2.9, 100.0, 0.01):
        s, E, M = fmt.parts(fmt.encode(v))
        ok = (3 ** (k - 1) <= M < 3 ** k) and (0 < E < fmt.max_exp)
        check(f"{name} {v} 正规化", ok, True)

# ---- 5. 上溢/下溢（受宿主 double 动态范围约束，见立项书 §7） ----
# TF45 阶码范围 ±29524 远超 double 极限（~3^646），double 全域都在 TF45 范围内，
# 不会触发 TF 层上溢；TF20 阶码 ±121 小于 double，可测到真正的上溢/下溢。
check("TF20 上溢 Inf", math.isinf(tf.TF20.decode(tf.TF20.encode(1e100))), True)
check("TF20 下溢 0", tf.TF20.decode(tf.TF20.encode(1e-100)), 0.0)
# TF45 装得下 double 全域：最大/最小有限 double 往返不失真、不抛异常
check_close("TF45 max double 往返",
            tf.TF45.decode(tf.TF45.encode(sys.float_info.max)),
            sys.float_info.max, 2 ** -52)
check_close("TF45 min double 往返",
            tf.TF45.decode(tf.TF45.encode(sys.float_info.min)),
            sys.float_info.min, 2 ** -52)

# ---- 6. 舍入：round-to-nearest，误差 ≤ 半个尾数 ULP ----
for fmt, name, tol in ((tf.TF20, "TF20", 1 / 3 ** 13), (tf.TF45, "TF45", 2 ** -52)):
    worst = 0.0
    for i in range(1, 200):
        v = 1.0 + i / 200.0
        got = fmt.decode(fmt.encode(v))
        rel = abs(got - v) / v
        worst = max(worst, rel)
    check(f"{name} 200 点舍入误差 ≤ 1 ULP", worst <= tol, True)

# ---- 7. 档位不变量 ----
check("TF20 trit 总数", tf.TF20.total_trits, 20)
check("TF45 trit 总数", tf.TF45.total_trits, 45)
check("TF20 bias", tf.TF20.bias, 121)
check("TF45 bias", tf.TF45.bias, 29524)

# ---- 8. 尾数/指数分离穿透：无"穿透缺口"（2026-11-20 框架更正） ----
# binary64 的 64 位是 IEEE 软件规范，不是 CPU 物理事实，不能当格式天花板。
# 正确模型 = 尾数 mant∈[1,3) 进 FPU + 指数 exp3 走整数 ALU，double 值域不约束指数。
# 构造极端阶码 raw（直接按字段拼，绕开 encode 的 double 输入），验证：
#   (a) decompose 给出有限 mant 与整数 exp3，哪怕 3^exp3 远超 double（无缺口）
#   (b) exp3 精确保住 —— 这是穿透模型成立的关键（范围由整数指数承载）
#   (c) compose 回去：TF20 逐位相等；TF45 尾数 34trit>double 53bit 有 ~1ulp 误差，
#       但等价值与阶码仍精确（穿透代价，属实现层账）
for fmt, name, exact in ((tf.TF20, "TF20", True), (tf.TF45, "TF45", False)):
    for E in (1, 2, fmt.bias - 1, fmt.bias, fmt.bias + 1,
              fmt.max_exp - 2, fmt.max_exp - 1):
        M = fmt.P_NORM + 13
        raw = E * fmt.P_MANT + M
        sign, mant, exp3 = fmt.decompose(raw)
        check(f"{name} E={E} mant 有限", math.isfinite(mant), True)
        check(f"{name} E={E} mant∈[1,3)", (1.0 <= mant < 3.0), True)
        check(f"{name} E={E} exp3 精确", exp3, E - fmt.bias)
        back = fmt.compose(sign, mant, exp3)
        if exact:
            check(f"{name} E={E} decompose→compose 逐位", back, raw)
        else:
            _, E2, _ = fmt.parts(back)
            check(f"{name} E={E} 往返阶码精确保住", E2, E)

# 极端：TF45 最大正规阶码，3^29523 —— 远超 double 上限 3^646
sign, mant, exp3 = tf.TF45.decompose((tf.TF45.max_exp - 1) * tf.TF45.P_MANT + tf.TF45.P_NORM)
check("TF45 极端阶码 mant 仍有限", math.isfinite(mant), True)
check("TF45 极端阶码 exp3=29523", exp3, tf.TF45.max_exp - 1 - tf.TF45.bias)

# 特殊值 decompose/compose 往返
for fmt, name in ((tf.TF20, "TF20"), (tf.TF45, "TF45")):
    for label, raw in (("+0", fmt.ZERO_POS), ("-0", fmt.ZERO_NEG),
                       ("+Inf", fmt.INF_POS), ("-Inf", fmt.INF_NEG), ("NaN", fmt.NAN)):
        sign, mant, exp3 = fmt.decompose(raw)
        back = fmt.compose(sign, mant, exp3)
        if label == "NaN":
            check(f"{name} {label} 分离往返仍 NaN", fmt.is_nan(back), True)
        else:
            check(f"{name} {label} 分离往返", back, raw)

print()
print(f"TF 浮点编解码契约：{PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
