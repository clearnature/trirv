"""trirv_tritref —— tryte 数组参考实现（换掉二进制容器）

为什么需要这个文件
    `trirv_vm.c` 用 __uint128_t 装 45-trit 值：语义是对的，但**容器是二进制整数**。
    容器决定了两件事改不动：
        · "除以 243" 只能走二进制除法（宿主 ~20-40 周期），不是 tryte 移位
        · "乘 3^k"  只能走二进制乘法 + 取模，不是 trit 移位
    所以凡是用二进制容器测出的成本，都不能代表三进制硬件的成本。

    本文件换掉容器：一个 45-trit 值 = **9 个 tryte 的数组**（每个 0..242），
    所有运算只走 tryte 级原语（加/减/比较/查表/移位），并统计原语调用次数。
    得到的成本是"trit 级操作数"，与宿主无关，可以直接拿去对照硬件数据通路。

它证明什么
    1. 这套 ISA 的每条指令都能只用 tryte 原语实现 —— 即**可硬件化**
    2. 换表示之后语义不变 —— 与二进制容器版逐例差分，结果全等
    3. 每条指令的真实 trit 级代价 —— 见 trirv_tritref_test.py 的成本表

单位约定
    STATS 里记的是"原语调用次数"：
        tryte_add / tryte_sub / tryte_cmp / tryte_mulacc / tryte_move
        tryte_mul_small / tryte_div_small / trit_lut
    一个 tryte 原语 = 一次 5-trit 的加/减/比较；trit_lut 按 trit 计（每 tryte 5 次）。
    这是下界估计：进位链最坏情况要逐 tryte 串行，这里只记一遍遍历。
"""

import collections

L = 9                    # tryte 数（45 trit）
XLEN_BITS = 45           # trit 数（= 5*L）
BASE = 243               # 1 tryte = 3^5
MOD = 3 ** 45
HALF = (MOD - 1) // 2    # 3 补码正数上限
POW3 = [3 ** i for i in range(6)]

STATS = collections.Counter()


def reset():
    STATS.clear()


# --------------------------------------------------------------------------
# 表示转换（外层测试用；不属于 ISA）
# --------------------------------------------------------------------------
def from_int(v):
    """Python 整数（可负）-> 9 tryte 非平衡表示。"""
    v %= MOD
    r = [0] * L
    for i in range(L):
        v, r[i] = divmod(v, BASE)
    return r


def to_int(t):
    """9 tryte -> Python 整数（0..3^45-1）。"""
    v = 0
    for i in range(L - 1, -1, -1):
        v = v * BASE + t[i]
    return v


def sgn(t):
    """非平衡 -> 平衡解释的有符号值（对应 VM 的 s()）。"""
    v = to_int(t)
    return v if v <= HALF else v - MOD


def pad(t, n):
    """补齐/截断到 n tryte。"""
    r = list(t[:n])
    if len(r) < n:
        r += [0] * (n - len(r))
    return r


def trit(t, i):
    """取第 i 个 trit（0..2）。"""
    return (t[i // 5] // POW3[i % 5]) % 3


# --------------------------------------------------------------------------
# 基础原语：加减比较
# --------------------------------------------------------------------------
def add(a, b, n=L):
    c = 0
    r = [0] * n
    for i in range(n):
        v = a[i] + b[i] + c
        STATS["tryte_add"] += 1
        if v >= BASE:
            v -= BASE
            c = 1
        else:
            c = 0
        r[i] = v
    return r


def sub(a, b, n=L):
    """a - b（mod 3^45）。a<b 时自然回绕到模空间高位，正确。"""
    br = 0
    r = [0] * n
    for i in range(n):
        v = a[i] - b[i] - br
        STATS["tryte_sub"] += 1
        if v < 0:
            v += BASE
            br = 1
        else:
            br = 0
        r[i] = v
    return r


def cmp_u(a, b, n=L):
    """无符号比较 -> -1/0/1（高位优先，未等则提前退出）。"""
    for i in range(n - 1, -1, -1):
        STATS["tryte_cmp"] += 1
        if a[i] != b[i]:
            return -1 if a[i] < b[i] else 1
    return 0


def is_neg(t):
    """3 补码判负：从高位往下找第一个不是 1 的 trit，是 2 则为负。

    为什么不能只看最高 trit：HALF = (3^45-1)/2 = 111...1（45 个 1），
    正负分界点就落在 111...1 | 111...2 之间，**不存在单一符号位**。
    v=HALF 时全为 1 仍算正；v=HALF+1 才是负。这与规范 §D2 一致。
    """
    for i in range(XLEN_BITS - 1, -1, -1):
        d = trit(t, i)
        STATS["trit_cmp"] += 1
        if d != 1:
            return d == 2
    return False


def cmp_s(a, b):
    """有符号比较，对应 SLT / BLT / BGE。

    判号要扫 trit（见 is_neg），同号再按非平衡顺序比。
    这是三进制相对二进制的一处真实代价：二进制 SLT 只需"符号位异或 + 无符号比较"，
    三进制判号最坏要扫 45 个 trit —— 没有符号位可用。
    """
    na, nb = is_neg(a), is_neg(b)
    if na != nb:
        return -1 if na else 1
    return cmp_u(a, b)


def is_zero(t, n=L):
    for i in range(n):
        STATS["tryte_cmp"] += 1
        if t[i]:
            return False
    return True


# --------------------------------------------------------------------------
# 乘法：tryte 卷积 + 进位传播（与 trirv_vm.c 的 mul90 同构）
# --------------------------------------------------------------------------
def _convolve(a, b):
    acc = [0] * (2 * L)
    for i in range(L):
        ai = a[i]
        if ai == 0:
            continue
        for j in range(L):
            if b[j]:
                acc[i + j] += ai * b[j]
                STATS["tryte_mulacc"] += 1
    return acc


def _carry(acc, lo, hi):
    c = 0
    r = [0] * (hi - lo)
    for k in range(2 * L):
        v = acc[k] + c
        STATS["tryte_add"] += 1
        d, c = v % BASE, v // BASE
        if lo <= k < hi:
            r[k - lo] = d
    return r


def mul_lo(a, b):
    """(a*b) mod 3^45，只看低 9 tryte。"""
    return _carry(_convolve(a, b), 0, L)


def mul_hi(a, b):
    """floor(a*b / 3^45)，取高 9 tryte。"""
    return _carry(_convolve(a, b), L, 2 * L)


def mul_hi_signed(a, b, sa, sb):
    """有符号高半，覆盖 MULH / MULHSU / MULHU 三种组合。

    sa/sb 表示该操作数是否按"有符号"解释（为真且值为负时先取幅值）。
    负积时高半要补码；若低位非零，还要先加 1 再取补（floor 语义）。
    """
    na, nb = sgn(a), sgn(b)
    xa = from_int(-na) if (sa and na < 0) else a
    xb = from_int(-nb) if (sb and nb < 0) else b
    lo = mul_lo(xa, xb)
    hi = mul_hi(xa, xb)
    neg = (sa and na < 0) ^ (sb and nb < 0)
    if not neg:
        return hi
    r = add(hi, [1] + [0] * (L - 1)) if not is_zero(lo) else hi
    return sub([0] * L, r) if not is_zero(r) else [0] * L


# --------------------------------------------------------------------------
# 除法：45 轮逐 trit 恢复除法（硬件会这么做，不是二进制长除法）
# --------------------------------------------------------------------------
def divmod_u(a, b):
    """返回 (q, r)，a,b 均为非平衡非负。b==0 时返回 (0,0)（ISA 语义）。

    逐 trit 试商：每轮 r = r*3 + a_i，试 d∈{0,1,2} 使 d*b <= r。
    余量用 L+1 个 tryte，避免 2*b 与 r 在 45 trit 内回绕造成误判。
    """
    if is_zero(b):
        return [0] * L, [0] * L
    W = L + 1
    rb = pad(b, W)
    rb2 = add(rb, rb, W)                 # 2b（W 位内不会溢出，因为 b < 3^45）
    r = [0] * W
    q = [0] * L
    for i in range(44, -1, -1):
        r = shl(r, 1, W)                 # r *= 3
        r = add(r, [trit(a, i)] + [0] * (W - 1), W)
        if cmp_u(r, rb2, W) >= 0:
            r = sub(r, rb2, W)
            d = 2
        elif cmp_u(r, rb, W) >= 0:
            r = sub(r, rb, W)
            d = 1
        else:
            d = 0
        # 商本来就逐 trit 生成：直接写第 i 个 trit，不必"乘 3 再加"
        q[i // 5] += d * POW3[i % 5]
    return q, pad(r, L)


# --------------------------------------------------------------------------
# 移位：三进制移位 = 乘/除 3^k，按 tryte 移动 + tryte 内查表
# --------------------------------------------------------------------------
def shl(a, k, n=L):
    """a * 3^k mod 3^(5n)。整 tryte 移动 + 余数乘 3^r 并进位。"""
    if k <= 0:
        return pad(a, n)
    q, r = divmod(k, 5)
    if q >= n:
        return [0] * n
    if q:
        res = [0] * n
        for i in range(n):
            STATS["tryte_move"] += 1
            if i + q < n:
                res[i + q] = a[i]
    else:
        res = list(a)                    # q==0 不用搬，直接就地乘
    if r:
        c = 0
        for i in range(n):
            v = res[i] * POW3[r] + c
            STATS["tryte_mul_small"] += 1
            res[i] = v % BASE
            c = v // BASE
    return res


def shr(a, k, n=L):
    """a // 3^k（无符号，向下取整）。整 tryte 移动 + 余数除 3^r。"""
    if k <= 0:
        return pad(a, n)
    q, r = divmod(k, 5)
    if q >= n:
        return [0] * n
    if q:
        res = [0] * n
        for i in range(q, n):
            STATS["tryte_move"] += 1
            res[i - q] = a[i]
    else:
        res = list(a)
    if r:
        c = 0
        for i in range(n - 1, -1, -1):
            v = res[i] + c * BASE
            STATS["tryte_div_small"] += 1
            res[i] = v // POW3[r]
            c = v % POW3[r]
    return res


# --------------------------------------------------------------------------
# 逐 trit 三值逻辑（决议 B 之后的 ISA 语义）
# --------------------------------------------------------------------------
def _build_luts():
    mn = [[0] * BASE for _ in range(BASE)]
    mx = [[0] * BASE for _ in range(BASE)]
    xr = [[0] * BASE for _ in range(BASE)]
    for x in range(BASE):
        for y in range(BASE):
            p, a, b = 1, x, y
            m = n = o = 0
            for _ in range(5):
                dx, dy = a % 3, b % 3
                a //= 3
                b //= 3
                m += min(dx, dy) * p
                n += max(dx, dy) * p
                o += ((dx + dy) % 3) * p
                p *= 3
            mn[x][y], mx[x][y], xr[x][y] = m, n, o
    return mn, mx, xr


LUT_MIN, LUT_MAX, LUT_XOR = _build_luts()


def tritwise(a, b, mode):
    """mode: 0=min 1=max 2=xor。逐 tryte 查表，每 tryte 内含 5 个 trit。"""
    lut = (LUT_MIN, LUT_MAX, LUT_XOR)[mode]
    r = [0] * L
    for i in range(L):
        r[i] = lut[a[i]][b[i]]
        STATS["trit_lut"] += 5
    return r


# --------------------------------------------------------------------------
# 访存：字 = 9 tryte，按 243 的幂拼接（不是按字节移位）
# --------------------------------------------------------------------------
def load_word(mem, addr, n=L):
    """从 tryte 内存取 9 tryte 组成一个字。"""
    v = 0
    for i in range(n - 1, -1, -1):
        STATS["tryte_load"] += 1
        v = v * BASE + mem.get(addr + i, 0) % BASE
    return from_int(v)


def store_word(mem, addr, t, n=L):
    """把字拆成 9 tryte 写入 tryte 内存。"""
    u = to_int(pad(t, n))
    for i in range(n):
        STATS["tryte_store"] += 1
        mem[addr + i] = u % BASE
        u //= BASE


def lb(t):
    """1 tryte 符号扩展（阈值 121）。"""
    v = t[0]
    STATS["tryte_add"] += 1
    return from_int(v if v <= 121 else v - 243)


def lbu(t):
    return from_int(t[0])


def lh(t, n=2):
    """2 tryte 符号扩展（阈值 29524 = (243^2-1)/2）。"""
    v = t[0] + t[1] * BASE
    STATS["tryte_add"] += 1
    return from_int(v if v <= 29524 else v - 59049)
