#!/usr/bin/env python3
"""十二进制表示层记数工具（纯记号，不改执行层数值）。

分层（用户 2026-11-20 定调）：
  执行层 = 硬件真正做的运算：二进制门电路 / 三进制门电路。
           CPU 里没有"十进制电路"，`10+10=20` 的那串字符只是记号。
  表示层 = 人看 / 输入 / 记数用的记数法：二进制硬件配十进制，三进制硬件配十二进制。

本模块只做表示层转换（整数↔十二进制字符串、分数记数、记数位换算），
数值本身用 Python int 精确承载，不引入任何执行层语义。

为什么表示层选 12（对标三进制）而非 10（对标二进制）：
  12 能被 3 整除（12 % 3 == 0），10 不能（10 % 3 == 1）。
  ⇒ 十二进制里 1/3 = 0.4 是有限位（十进制 1/3 = 0.333… 无限循环），
    与三进制硬件天然做三等分契合。就像十进制记数适合二进制世界的人读，
    十二进制记数适合三进制世界的人读 —— 进制是记号，不是物理。

用法：
    to12(20)       -> "18"
    from12("18")   -> 20
    frac_to12(1,3) -> "0.4"
"""
import math

# ---- 符号定义（用户 2026-11-20 拍板） ----
# "12" 是十进制进位记法写出的字样（1×10+2），不是十二进制自己的写法。
# 十二进制自己的基数写作 "10"（1×12+0）；字样 "12" 在十二进制里其实是 14。
# 用户拍板：规范数码暂用 ASCII 0-9AB；换字形时只改这里，调用方不动。
RADIX = 12                       # 基数（Python int 承载，这里 12 是给人类看的十进制标签）
RADIX_IN_SELF = "10"             # 同一个基数，用十二进制自己的数码写 = "10"
CANONICAL_DIGITS = "0123456789AB"  # 规范数码（显示层），暂用 ASCII
DIGITS = CANONICAL_DIGITS        # 兼容旧名
DIGIT_VAL = {c: i for i, c in enumerate(CANONICAL_DIGITS)}

SYMBOL_SET = {
    "canonical": CANONICAL_DIGITS,      # 规范数码字形
    "radix_self": RADIX_IN_SELF,        # 基数在本进制的写法
    "radix_decimal_label": RADIX,       # 基数的十进制标签（元层注解，非定义）
    "alias_input": True,                # 输入经别名层归一
    "annotation": "subscript",          # 进制标注方式：数字加下标，如 18₁₂
    "note": "12 是十进制写法；十二进制基数写作 10；字样 12 在十二进制 = 14",
}

# 别名层：规范数码之外的输入写法映射到这里。当前只有大小写折叠；
# 将来若换地支/专用字形，只需在此加 "子"→"0" 等映射，to12/from12 不用改。
INPUT_ALIASES = dict(DIGIT_VAL)
for _c in CANONICAL_DIGITS:
    INPUT_ALIASES[_c.lower()] = DIGIT_VAL[_c]


def normalize(s):
    """输入串 → 规范数码串（别名归一）。非法数码抛 ValueError。"""
    if not isinstance(s, str):
        raise TypeError(f"normalize 只接受 str，得到 {type(s).__name__}")
    out = []
    for c in s:
        if c not in INPUT_ALIASES:
            raise ValueError(f"非法十二进制数码 {c!r}（合法：{CANONICAL_DIGITS}）")
        out.append(CANONICAL_DIGITS[INPUT_ALIASES[c]])
    return "".join(out)


def to12(n):
    """非负整数 → 十二进制字符串（无前缀，数码 0-9AB）。负数无意义，拒绝。"""
    if not isinstance(n, int):
        raise TypeError(f"to12 只接受 int，得到 {type(n).__name__}")
    if n < 0:
        raise ValueError(f"to12 不接受负数：{n}")
    if n == 0:
        return "0"
    out = []
    while n:
        n, r = divmod(n, RADIX)
        out.append(CANONICAL_DIGITS[r])
    return "".join(reversed(out))


def from12(s):
    """十二进制字符串 → 非负整数。经别名层归一；非法数码抛 ValueError。"""
    if not isinstance(s, str):
        raise TypeError(f"from12 只接受 str，得到 {type(s).__name__}")
    if not s:
        raise ValueError("from12 不接受空串")
    v = 0
    for c in normalize(s):
        v = v * RADIX + DIGIT_VAL[c]
    return v


def frac_to12(num, den, max_places=12):
    """分数 num/den → 十二进制小数字符串（仅有限位；无限位按 max_places 截断）。

    12 = 2^2 × 3，故分母只含因子 2/3 时有限位 —— 这正是 1/3 在十二进制
    是 0.4（有限）、在十进制是 0.333…（无限）的原因。
    """
    if den == 0:
        raise ValueError("分母不能为 0")
    neg = (num < 0) != (den < 0)
    num, den = abs(num), abs(den)
    integer, rem = divmod(num, den)
    if rem == 0:
        return ("-" if neg else "") + to12(integer)
    out = []
    for _ in range(max_places):
        rem *= 12
        digit, rem = divmod(rem, den)
        out.append(DIGITS[digit])
        if rem == 0:
            break
    s = to12(integer) + "." + "".join(out)
    return ("-" if neg else "") + s


def trit_to_digits(n_trits):
    """执行层 n 个 trit 的信息量换算成表示层十二进制位数：n × log_12(3)。"""
    return n_trits * math.log(3) / math.log(12)


def bit_to_digits(n_bits):
    """执行层 n 个 bit 的信息量换算成表示层十进制位数：n × log_10(2)。"""
    return n_bits * math.log(2) / math.log(10)


# ---- 进制下标标注：数字 + 下标，标明按哪个基数解读（用户 2026-11-20 定调） ----
# 下标是元层注解（人类读的标签），用十进制 Unicode 下标字形书写——这是
# 注解本身的记法，不是被注解数值的一部分，不违反"表示层自主定义"。
_SUBSCRIPT = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")
_UNSUBSCRIPT = {v: k for k, v in zip("0123456789", "₀₁₂₃₄₅₆₇₈₉")}
_SUPPORTED = {2, 3, 10, 12}       # 执行层 2/3，表示层 10/12


def _digits_for(base):
    """给定基数的数码表（0-9AB 覆盖到 12；更大的档位按需扩）。"""
    if base not in _SUPPORTED:
        raise ValueError(f"未支持的基数 {base}（支持：{sorted(_SUPPORTED)}）")
    return CANONICAL_DIGITS[:base]


def radix_subscript(base):
    """基数的十进制标签 → Unicode 下标注解，如 12 → '₁₂'。"""
    if base not in _SUPPORTED:
        raise ValueError(f"未支持的基数 {base}（支持：{sorted(_SUPPORTED)}）")
    return str(base).translate(_SUBSCRIPT)


def labelled(n, base=RADIX):
    """非负整数 → 带进制下标的表示，如 labelled(20)= '18₁₂'、labelled(20,10)='20₁₀'。"""
    if not isinstance(n, int) or n < 0:
        raise ValueError(f"labelled 只接受非负整数，得到 {n!r}")
    digits = _digits_for(base)
    if n == 0:
        body = "0"
    else:
        out = []
        while n:
            n, r = divmod(n, base)
            out.append(digits[r])
        body = "".join(reversed(out))
    return body + radix_subscript(base)


def parse_labelled(s):
    """带下标的表示 → 非负整数。下标必须是支持基数且数码合法，否则 ValueError。"""
    if not isinstance(s, str):
        raise TypeError(f"parse_labelled 只接受 str，得到 {type(s).__name__}")
    if not s:
        raise ValueError("parse_labelled 不接受空串")
    body, sub = [], []
    i = 0
    while i < len(s) and s[i] not in _UNSUBSCRIPT:      # 数码体
        body.append(s[i])
        i += 1
    while i < len(s) and s[i] in _UNSUBSCRIPT:          # 下标注解
        sub.append(_UNSUBSCRIPT[s[i]])
        i += 1
    if i != len(s) or not body or not sub:
        raise ValueError(f"非法标注 {s!r}（应为 数码+下标，如 18₁₂）")
    base = int("".join(sub))
    digits = _digits_for(base)
    v = 0
    for c in normalize("".join(body)):
        if c not in digits[:base]:
            raise ValueError(f"数码 {c!r} 不属于基数 {base}（合法：{digits[:base]}）")
        v = v * base + DIGIT_VAL[c]
    return v
