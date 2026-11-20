#!/usr/bin/env python3
"""十二进制表示层工具契约测试（TDD RED → GREEN）。

分层（用户 2026-11-20 定调）：
  执行层 = 硬件真正做的运算：二进制门 / 三进制门
  表示层 = 人看的记数法：    十进制 / 十二进制

本模块只做表示层记数转换，不改任何执行层数值。期望值由数学定义独立算出，
不调用被测函数自身（避免"用实现验证实现"）。
"""
import math
import sys

import trirv_base12 as b12

PASS = 0
FAIL = 0


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"PASS {name}: {got}")
    else:
        FAIL += 1
        print(f"FAIL {name}: got={got!r} want={want!r}")


# ---- 1. 数码表：十二进制用 0-9 A B ----
check("数码表长度", len(b12.DIGITS), 12)
check("数码 A=10", b12.DIGITS[10], "A")
check("数码 B=11", b12.DIGITS[11], "B")

# ---- 2. 小值编码（期望值手算，不依赖被测函数） ----
for n, want in ((0, "0"), (1, "1"), (9, "9"), (10, "A"), (11, "B"),
                (12, "10"), (13, "11"), (20, "18"), (24, "20"),
                (143, "BB"), (144, "100")):
    check(f"to12({n})", b12.to12(n), want)

# ---- 3. 解码（含大小写、数码 A/B） ----
for s, want in (("0", 0), ("A", 10), ("B", 11), ("10", 12),
                ("18", 20), ("20", 24), ("BB", 143), ("100", 144),
                ("b", 11), ("aB", 131)):
    check(f"from12({s!r})", b12.from12(s), want)

# ---- 4. round-trip：含 trit 幂与 TF 阶码边界 ----
vals = list(range(0, 2000)) + [3 ** k for k in range(20)] + [29523, 59048, 2 ** 53]
bad = [n for n in vals if b12.from12(b12.to12(n)) != n]
check("round-trip 错误数", len(bad), 0)

# ---- 5. 非法输入必须拒绝（表示层的健壮性） ----
for bad_s in ("", "C", "12C", "1-2", " 10", "10 "):
    try:
        b12.from12(bad_s)
        rejected = False
    except ValueError:
        rejected = True
    check(f"from12 拒绝 {bad_s!r}", rejected, True)
for bad_n in (-1, -12):
    try:
        b12.to12(bad_n)
        rejected = False
    except ValueError:
        rejected = True
    check(f"to12 拒绝 {bad_n}", rejected, True)

# ---- 6. 分数记数：12 能被 3 整除 → 1/3 有限位，这是选 12 的数学理由 ----
# 1/3 在十二进制 = 0.4（因为 4/12 = 1/3）
check("1/3 十二进制有限位", b12.frac_to12(1, 3), "0.4")
# 1/2 = 0.6；1/4 = 0.3；1/6 = 0.2（都是有限位）
check("1/2 十二进制", b12.frac_to12(1, 2), "0.6")
check("1/4 十二进制", b12.frac_to12(1, 4), "0.3")
check("1/6 十二进制", b12.frac_to12(1, 6), "0.2")

# ---- 7. 表示层对照：同一个值在三种记数法下的写法 ----
# 期望值独立手算：十进制20 = 十二进制18 = 三进制202
check("对照 (20,12)", b12.to12(20), "18")
check("对照 12 能被 3 整除", 12 % 3, 0)
check("对照 10 不能被 3 整除", 10 % 3, 1)

# ---- 8. 记数位换算（执行层 trit → 表示层十二进制位） ----
# log_12(3) ≈ 0.442114；34 trit 是 TF64 尾数宽度
ratio = math.log(3) / math.log(12)
check("trit→十二进制位比例", round(b12.trit_to_digits(1), 6), round(ratio, 6))
check("34 trit 换算", round(b12.trit_to_digits(34), 2), round(34 * ratio, 2))

print()
print(f"十二进制表示层契约：{PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
