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
# log_12(3) ≈ 0.442114；34 trit 是 TF45 尾数宽度
ratio = math.log(3) / math.log(12)
check("trit→十二进制位比例", round(b12.trit_to_digits(1), 6), round(ratio, 6))
check("34 trit 换算", round(b12.trit_to_digits(34), 2), round(34 * ratio, 2))

# ---- 9. 符号定义与基数自指（用户 2026-11-20 定调） ----
# "12" 是十进制进位记法写出的字样；十二进制自己的基数写作 "10"。
# 用户拍板：规范数码暂用 ASCII 0-9AB，但必须显式定义符号集 + 留别名层。
check("符号集定义存在", isinstance(b12.SYMBOL_SET, dict), True)
check("符号集 canonical", b12.SYMBOL_SET["canonical"], "0123456789AB")
check("符号集 radix_self", b12.SYMBOL_SET["radix_self"], "10")
check("符号集 十进制标签", b12.SYMBOL_SET["radix_decimal_label"], 12)
check("符号集 启用别名输入", b12.SYMBOL_SET["alias_input"], True)
check("基数数值", b12.RADIX, 12)
check("基数在本进制的写法", b12.RADIX_IN_SELF, "10")
check("to12(基数)=10", b12.to12(b12.RADIX), "10")
check("from12('10')=基数", b12.from12("10"), 12)
# 决定性证据：字样 "12" 在十二进制 = 1×12+2 = 14，不是基数
check("字样'12'在十二进制=14", b12.from12("12"), 14)
check("十进制10在十二进制=A", b12.to12(10), "A")
# ASCII 别名归一层（将来换地支/专用字形只改映射，不动 to12/from12）
check("规范数码表", b12.CANONICAL_DIGITS, "0123456789AB")
check("别名归一 aB→AB", b12.normalize("aB"), "AB")
check("别名归一 10→10", b12.normalize("10"), "10")
check("别名归一后解码", b12.from12("aB"), b12.from12("AB"))

# ---- 10. 数字加下标标注进制（用户 2026-11-20 定调） ----
# 表示 = 数码串 + 进制下标；下标是元层注解，标明这串数码按哪个基数解读。
check("符号集 标注方式", b12.SYMBOL_SET["annotation"], "subscript")
check("基数下标 12", b12.radix_subscript(12), "₁₂")
check("基数下标 10", b12.radix_subscript(10), "₁₀")
check("基数下标 3",  b12.radix_subscript(3),  "₃")
check("带下标表示 20₁₂ = 18₁₂", b12.labelled(20), "18₁₂")
check("带下标表示 10₁₀", b12.labelled(10, base=10), "10₁₀")
check("带下标表示 20₁₀", b12.labelled(20, base=10), "20₁₀")
check("解析 18₁₂ = 20", b12.parse_labelled("18₁₂"), 20)
check("解析 20₁₀ = 20", b12.parse_labelled("20₁₀"), 20)
check("解析 101₁₀ = 101", b12.parse_labelled("101₁₀"), 101)
check("带下标 round-trip", b12.parse_labelled(b12.labelled(59048)), 59048)
for bad_lbl in ("18", "18₁₃", "1x₁₂", "₁₂"):
    try:
        b12.parse_labelled(bad_lbl)
        rejected = False
    except ValueError:
        rejected = True
    check(f"拒绝非法标注 {bad_lbl!r}", rejected, True)

print()
print(f"十二进制表示层契约：{PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
