#!/usr/bin/env python3
"""TF 三进制浮点编解码（P1 参考实现，纯 Python 大整数精确算尾数）。

值 = (-1)^s × (M / 3^(k-1)) × 3^(E - bias),  M ∈ [3^(k-1), 3^k) 为正规化尾数
（k trit 恰好存下 M；最高 trit ∈{1,2}，等价于 base-3 的隐含前导 1，值域 [1,3)）

档位：
  TF32: 1 符号 + 5 阶码 + 14 尾数 = 20 trit, bias=121,  max_exp=3^5-1=242
  TF64: 1 符号 + 10 阶码 + 34 尾数 = 45 trit, bias=29524, max_exp=3^10-1=59048

raw 布局（trit 位从高到低）：
  TF32: [s:1][E:5][M:14]   → raw = s·3^19 + E·3^14 + M
  TF64: [s:1][E:10][M:34]  → raw = s·3^44 + E·3^34 + M

特殊值：
  E=0,            M=0     → ±0      （符号由 s 决定）
  E=max_exp,      M=0     → ±Inf
  E=max_exp,      M≠0     → NaN
  其余                     → 正规/次正规数

穿透模型（立项书 §3，2026-11-20 更正）：
  - binary64 的"64 位"是 IEEE 软件规范，不是 CPU 物理事实；不拿 double 值域当格式天花板
  - 格式的规范表示是 (sign, mant∈[1,3), exp3 整数)：mant 是唯一进宿主 FPU 的量
    （永远装得下），exp3 走整数 ALU（Python int 无上限）。值 = sign × mant × 3^exp3
  - decompose()/compose() 是该表示的可执行定义：任何 raw（哪怕 exp3=29523）都能
    无损拆成有限 mant + 整数 exp3，没有一步需要把 3^exp3 装进 double —— 无穿透缺口
  - encode()/decode() 是 double↔raw 的便利桥：double 值域外的 decode 返回 ±Inf/0，
    那是"单个 double 装不下"的桥接限制，不是格式限制（格式用 decompose 表示全值域）
  - 尾数用整数 M 精确表示，绝不转 float 再乘 3^k（会丢精度）
  - 舍入 round-to-nearest（数学规则，与 base-2 的 ties-to-even 在 tie 点不同，P1 先锁定）
"""
import math


class FloatFormat:
    def __init__(self, name, exp_trits, mant_trits, bias):
        self.name = name
        self.sign_trits = 1
        self.exp_trits = exp_trits
        self.mant_trits = mant_trits
        self.total_trits = 1 + exp_trits + mant_trits
        self.bias = bias
        self.max_exp = 3 ** exp_trits - 1
        self.P_MANT = 3 ** mant_trits          # 3^k（尾数字段容量，M ∈ [3^(k-1), 3^k)）
        self.P_NORM = 3 ** (mant_trits - 1)    # 3^(k-1)，归一化分母
        self.P_SIGN = 3 ** (exp_trits + mant_trits)  # 符号位权重
        # 特殊值 raw（符号 0/1）
        self.ZERO_POS = 0
        self.ZERO_NEG = self.P_SIGN
        self.INF_POS = self.max_exp * self.P_MANT
        self.INF_NEG = self.P_SIGN + self.max_exp * self.P_MANT
        self.NAN = self.max_exp * self.P_MANT + 1

    def parts(self, raw):
        """拆成 (s, E, M) 三元组（ trit 整数）。"""
        s = raw // self.P_SIGN
        rest = raw % self.P_SIGN
        E = rest // self.P_MANT
        M = rest % self.P_MANT
        return s, E, M

    def is_nan(self, raw):
        s, E, M = self.parts(raw)
        return E == self.max_exp and M != 0

    def is_inf(self, raw):
        s, E, M = self.parts(raw)
        return E == self.max_exp and M == 0

    def decompose(self, raw):
        """raw → (sign, mant, exp3)：格式的规范表示，穿透模型的核心。

        sign ∈ {-1, +1}；mant ∈ [1,3) 的 host double（唯一进 FPU 的量，永远装得下）；
        exp3 是 base-3 整数指数（走整数 ALU，Python int 无上限）。
        值 = sign × mant × 3^exp3。

        特殊值：Inf → (sign, None, 'inf')；NaN → (sign, None, 'nan')；
        ±0 → (sign, 1.0, None) 由 sign 区分（调用方看 exp3 is None 判断零）。

        关键：任何正规 raw（哪怕 exp3=29523，3^exp3 远超 double）都能拆成有限 mant
        + 整数 exp3，全程不把 3^exp3 塞进 double —— 这就是"无穿透缺口"的证明。
        """
        s, E, M = self.parts(raw)
        sign = -1 if s else 1
        if E == self.max_exp:
            return (sign, None, "nan" if M != 0 else "inf")
        if E == 0 and M == 0:
            return (sign, 1.0, None)              # ±0，靠 sign 区分
        mant = M / self.P_NORM                    # ∈ [1,3)，有限 double
        exp3 = E - self.bias                      # 整数，无 double 范围约束
        return (sign, mant, exp3)

    def compose(self, sign, mant, exp3):
        """(sign, mant∈[1,3), exp3 整数) → raw，decompose 的逆（格式精度内）。

        mant 回量化到 M = round(mant × 3^(k-1))，全程 Python 整数精确算，
        不经过 3^exp3 的浮点乘方 —— exp3 多大都不溢出 double。
        """
        if exp3 == "inf":
            return self.INF_NEG if sign < 0 else self.INF_POS
        if exp3 == "nan":
            return self.NAN
        if exp3 is None:
            return self.ZERO_NEG if sign < 0 else self.ZERO_POS
        mi, e2 = _double_to_int_pow2(mant)
        M = _round_mantissa(mi, e2, 0, self.mant_trits - 1)  # round(mant×3^(k-1))
        E = exp3 + self.bias
        if M >= self.P_MANT:                      # 舍入进位到 3^k
            M //= 3
            E += 1
        if E >= self.max_exp:
            return self.INF_NEG if sign < 0 else self.INF_POS
        if E <= 0:
            return self.ZERO_NEG if sign < 0 else self.ZERO_POS
        s = 1 if sign < 0 else 0
        return s * self.P_SIGN + E * self.P_MANT + M

    def decode(self, raw):
        """便利桥：raw → 单个 host double。

        注意这只是"装进一个 double"的便利接口：double 值域外返回 ±Inf/0。
        完整格式值（含 double 装不下的大阶码）请用 decompose()，它是规范表示。
        """
        s, E, M = self.parts(raw)
        sign = -1.0 if s else 1.0
        if E == self.max_exp:
            if M == 0:
                return sign * float("inf")
            return float("nan")
        if E == 0 and M == 0:
            return sign * 0.0
        # 值 = (M / 3^(k-1)) × 3^(E-bias)
        # 分步算，先算 M/3^(k-1)（double），再乘 3^(E-bias)
        mant = M / self.P_NORM                       # ∈ [0, 3)
        exp = E - self.bias
        try:
            scale = 3.0 ** exp                       # double 承载能力上限 ~3^646
        except OverflowError:
            return sign * float("inf")
        if scale == 0.0:                             # 下溢
            return sign * 0.0
        v = mant * scale
        if math.isinf(v):
            return sign * float("inf")
        return sign * v

    def encode(self, value):
        """host double → raw。round-to-nearest，正规化到 M∈[3^k, 2·3^k)。"""
        if math.isnan(value):
            return self.NAN
        if math.isinf(value):
            return self.INF_NEG if value < 0 else self.INF_POS
        if value == 0.0:
            return self.ZERO_NEG if math.copysign(1, value) < 0 else self.ZERO_POS

        s = 1 if value < 0 else 0
        a = abs(value)

        # 把 double 精确拆成 mant_int × 2^exp2（mant_int 是整数，无精度损失）
        mant_int, exp2 = _double_to_int_pow2(a)

        # 求 e 使 3^e <= a < 3^(e+1)：用整数位长估计 + 精确比较修正
        # log2(a) ≈ (mant_int.bit_length()-1 + exp2) + frac，e ≈ log2(a)/log2(3)
        approx_bits = (mant_int.bit_length() - 1) + exp2
        e = int(math.floor(approx_bits / math.log2(3)))
        while e > -700 and _cmp_a_pow3(mant_int, exp2, e) < 0:
            e -= 1
        while _cmp_a_pow3(mant_int, exp2, e + 1) >= 0:
            e += 1

        E = e + self.bias
        if E >= self.max_exp:                        # 上溢
            return self.INF_NEG if s else self.INF_POS
        if E <= 0:                                   # 下溢 → 0（次正规实现留 P2）
            return self.ZERO_NEG if s else self.ZERO_POS

        # M = round(a × 3^(k-1-e)) = round(mant_int × 2^exp2 × 3^(k-1-e))，整数精确
        # 目标 M ∈ [3^(k-1), 3^k)，对应归一化分数 ∈ [1,3)
        M = _round_mantissa(mant_int, exp2, e, self.mant_trits - 1)
        if M >= self.P_MANT:                         # 舍入进位：M 到 3^k，右移一位、阶码 +1
            M //= 3
            E += 1
            if E >= self.max_exp:
                return self.INF_NEG if s else self.INF_POS
        if M < self.P_NORM:                          # 舍入下溢到归一化下界
            M = self.P_NORM

        return s * self.P_SIGN + E * self.P_MANT + M


def _double_to_int_pow2(a):
    """a (正 double) → (mant_int, exp2) 使 a == mant_int × 2^exp2，精确无损失。"""
    m, p = math.frexp(a)                             # a = m×2^p, m∈[0.5,1)
    # m × 2^53 必为整数（double 有效位 ≤53）
    mant_int = int(m * (1 << 53))
    return mant_int, p - 53


def _cmp_a_pow3(mant_int, exp2, e):
    """比较 mant_int×2^exp2 与 3^e：返回 <0 / 0 / >0，全程整数精确。

    e 可能为负（3^e = 1/3^|e|），此时两边同乘 3^|e| 化为整数比较：
      mant_int × 2^exp2 × 3^|e|  vs  1
    再把 2^exp2 移到对侧（exp2<0 时左乘 2^(-exp2)）即可全整数。
    """
    lhs = mant_int
    rhs = 1
    if e >= 0:
        rhs = 3 ** e
    else:
        lhs *= 3 ** (-e)
    if exp2 >= 0:
        lhs <<= exp2
    else:
        rhs <<= (-exp2)
    return (lhs > rhs) - (lhs < rhs)


def _round_mantissa(mant_int, exp2, e, k):
    """round(mant_int × 2^exp2 × 3^(k-e))，整数精确，round-to-nearest。

    调用方传 k=mant_trits-1，结果落在 [3^(k), 3^(k+1))（调用方处理边界进位）。
    """
    shift = k - e
    if shift >= 0:
        num = mant_int * (3 ** shift)
        if exp2 >= 0:
            return num << exp2
        den = 1 << (-exp2)
    else:
        num = mant_int
        den = (3 ** (-shift))
        if exp2 >= 0:
            num = num << exp2
        else:
            den = den << (-exp2)
    # round-to-nearest：floor(num/den + 1/2)
    q, r = divmod(num, den)
    if 2 * r >= den:
        q += 1
    return q


TF32 = FloatFormat("TF32", exp_trits=5, mant_trits=14, bias=121)
TF64 = FloatFormat("TF64", exp_trits=10, mant_trits=34, bias=29524)

FORMATS = {"TF32": TF32, "TF64": TF64}


# ===========================================================================
# F 扩展算术（P2）——分离表示 (sign, mant∈[1,3), exp3) 上的穿透运算。
# 这是两套 VM 共享的语义定义（C 版照此移植，差分测试锁定一致）。
# 核心：进 FPU 的只有 mant（恒在 [1,3)）与"指数差值"（≤ EXP_GAP，恒在窗口内）；
#       指数本身是整数，永远不进 double —— 无穿透缺口。
# ===========================================================================

EXP_GAP = 40                       # 对阶最大跨度：3^40 > 2^53，超过即小项 < 1ulp 可丢弃
_POW3_NEG = tuple(3.0 ** (-k) for k in range(EXP_GAP + 1))


def _dec(raw, fmt):
    """raw → 规范分类 (kind, sign, mant, exp3)。
    kind ∈ {'num','zero','inf','nan'}；num 时 mant∈[1,3)、exp3 整数。"""
    sign, mant, exp3 = fmt.decompose(raw)
    if exp3 == "nan":
        return ("nan", sign, None, None)
    if exp3 == "inf":
        return ("inf", sign, None, None)
    if exp3 is None:
        return ("zero", sign, None, None)
    return ("num", sign, mant, exp3)


def _scale_down(mant, k):
    """mant × 3^(-k)，k≥0 整数；k 超阈值返回 0（小于 1ulp，可忽略）。"""
    if k >= EXP_GAP:
        return 0.0
    return mant * _POW3_NEG[k]


def _norm(sign, mant, exp3, fmt):
    """把 mant 正规化到 [1,3)，再 compose 回 raw（含上溢/下溢边界）。"""
    if mant == 0.0:
        return fmt.ZERO_NEG if sign < 0 else fmt.ZERO_POS
    while mant < 1.0:
        mant *= 3.0
        exp3 -= 1
    while mant >= 3.0:
        mant /= 3.0
        exp3 += 1
    return fmt.compose(sign, mant, exp3)


def f_add(raw_a, raw_b, fmt, subtract=False):
    """分离表示加法（subtract=True 即 a-b）。穿透：对阶差值进 FPU，指数走整数。"""
    ka, sa, ma, ea = _dec(raw_a, fmt)
    kb, sb, mb, eb = _dec(raw_b, fmt)
    if subtract:
        sb = -sb
    if ka == "nan" or kb == "nan":
        return fmt.NAN
    if ka == "inf":
        if kb == "inf" and sa != sb:
            return fmt.NAN                       # (+inf)+(-inf)
        return fmt.INF_NEG if sa < 0 else fmt.INF_POS
    if kb == "inf":
        return fmt.INF_NEG if sb < 0 else fmt.INF_POS
    if ka == "zero" and kb == "zero":
        return fmt.ZERO_NEG if (sa < 0 and sb < 0) else fmt.ZERO_POS
    if ka == "zero":
        return fmt.compose(sb, 1.0, eb)          # 0 + b = b（保 b 的符号/值）
    if kb == "zero":
        return fmt.compose(sa, 1.0, ea)
    E = max(ea, eb)                              # 对到较大指数
    va = sa * ma * _scale_down(1.0, E - ea)
    vb = sb * mb * _scale_down(1.0, E - eb)
    t = va + vb                                  # 唯一进 FPU 的加法，|t|<6
    if t == 0.0:
        return fmt.ZERO_POS
    return _norm(1 if t > 0 else -1, abs(t), E, fmt)


def f_mul(raw_a, raw_b, fmt):
    """分离表示乘法：mant 相乘进 FPU，指数整数相加。"""
    ka, sa, ma, ea = _dec(raw_a, fmt)
    kb, sb, mb, eb = _dec(raw_b, fmt)
    sign = sa * sb
    if ka == "nan" or kb == "nan":
        return fmt.NAN
    if (ka == "inf" and kb == "zero") or (kb == "inf" and ka == "zero"):
        return fmt.NAN
    if ka == "inf" or kb == "inf":
        return fmt.INF_NEG if sign < 0 else fmt.INF_POS
    if ka == "zero" or kb == "zero":
        return fmt.ZERO_NEG if sign < 0 else fmt.ZERO_POS
    return _norm(sign, ma * mb, ea + eb, fmt)


def f_div(raw_a, raw_b, fmt):
    """分离表示除法：mant 相除进 FPU，指数整数相减。"""
    ka, sa, ma, ea = _dec(raw_a, fmt)
    kb, sb, mb, eb = _dec(raw_b, fmt)
    sign = sa * sb
    if ka == "nan" or kb == "nan":
        return fmt.NAN
    if (ka == "inf" and kb == "inf") or (ka == "zero" and kb == "zero"):
        return fmt.NAN
    if ka == "inf":
        return fmt.INF_NEG if sign < 0 else fmt.INF_POS
    if kb == "inf":
        return fmt.ZERO_NEG if sign < 0 else fmt.ZERO_POS
    if kb == "zero":                             # 有限 / 0 → Inf（符号为积）
        return fmt.INF_NEG if sign < 0 else fmt.INF_POS
    if ka == "zero":
        return fmt.ZERO_NEG if sign < 0 else fmt.ZERO_POS
    return _norm(sign, ma / mb, ea - eb, fmt)


def f_sqrt(raw, fmt):
    """分离表示平方根：mant 走宿主 sqrt，指数整数折半（奇偶各归一）。"""
    k, s, m, e = _dec(raw, fmt)
    if k == "nan":
        return fmt.NAN
    if k == "inf":
        return fmt.INF_POS if s > 0 else fmt.NAN
    if k == "zero":
        return raw                              # ±0 的 sqrt 仍是 ±0
    if s < 0:
        return fmt.NAN
    if e % 2 == 0:
        return _norm(1, math.sqrt(m), e // 2, fmt)
    return _norm(1, math.sqrt(3.0 * m), (e - 1) // 2, fmt)


def f_cmp(raw_a, raw_b, fmt, op):
    """比较：op ∈ {'eq','lt','le'} → 1/0。NaN 比较全为 0（除 eq 也 0）。"""
    ka, sa, ma, ea = _dec(raw_a, fmt)
    kb, sb, mb, eb = _dec(raw_b, fmt)
    if ka == "nan" or kb == "nan":
        return 0
    # 精确比较：按 (符号, 指数, 尾数) 三元组
    def cmp_num(x, y):
        # x,y 为 (sign, mant, exp)；mant∈[1,3)
        if x[0] != y[0]:
            return 1 if x[0] > y[0] else -1
        # 同号：比较 m×3^e，用 log 判定，接近时精确比对
        lx = math.log(x[1]) + x[2] * math.log(3)
        ly = math.log(y[1]) + y[2] * math.log(3)
        if abs(lx - ly) > 1e-9:
            return 1 if lx > ly else -1
        # 极接近：指数相同比 mant，否则用整数幂精确比
        if x[2] == y[2]:
            return (x[1] > y[1]) - (x[1] < y[1])
        # m1×3^e1 vs m2×3^e2 → 移到一个共同指数（差值小才到这里）
        E = max(x[2], y[2])
        vx = x[1] * _scale_down(1.0, E - x[2])
        vy = y[1] * _scale_down(1.0, E - y[2])
        c = (vx > vy) - (vx < vy)
        return c if x[0] > 0 else -c
    # 分类比较
    order = {"zero": 1, "num": 2, "inf": 3}
    if ka != kb:
        # 负 inf < 负 num < 负 zero < 正 zero < 正 num < 正 inf
        def full(t):
            k, s, m, e = t
            if k == "zero":
                base = 0
            elif k == "inf":
                base = 3 if s > 0 else -3
            else:
                base = 2 if s > 0 else -2
            return base
        fa, fb = full((ka, sa, ma, ea)), full((kb, sb, mb, eb))
        if fa != fb:
            c = (fa > fb) - (fa < fb)
        else:
            c = cmp_num((sa, ma, ea), (sb, mb, eb)) if ka == "num" and kb == "num" else 0
    elif ka == "num":
        c = cmp_num((sa, ma, ea), (sb, mb, eb))
    elif ka == "inf":
        c = (sa > sb) - (sa < sb)
    else:  # zero
        c = 0
    if op == "eq":
        return 1 if c == 0 else 0
    if op == "lt":
        return 1 if c < 0 else 0
    return 1 if c <= 0 else 0
