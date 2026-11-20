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

DIGITS = "0123456789AB"          # 十二进制数码：十进制 10/11 记作 A/B
DIGIT_VAL = {c: i for i, c in enumerate(DIGITS)}


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
        n, r = divmod(n, 12)
        out.append(DIGITS[r])
    return "".join(reversed(out))


def from12(s):
    """十二进制字符串 → 非负整数。大小写不敏感；非法数码抛 ValueError。"""
    if not isinstance(s, str):
        raise TypeError(f"from12 只接受 str，得到 {type(s).__name__}")
    if not s:
        raise ValueError("from12 不接受空串")
    v = 0
    for c in s.upper():
        if c not in DIGIT_VAL:
            raise ValueError(f"非法十二进制数码 {c!r}（合法：{DIGITS}）")
        v = v * 12 + DIGIT_VAL[c]
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
