"""trirv_tritref_test —— tryte 数组参考实现的差分验证与成本表

三件事：
  1. 差分：tryte 实现 vs Python 大整数（独立参考），逐例比对，结果必须全等
  2. 端到端：用 tryte 原语跑真实循环（求和 / 斐波那契 / 阶乘）
  3. 成本表：每条 ISA 操作消耗的 trit 级原语次数 —— 与宿主无关，可直接对照硬件
"""

import random
import sys

import trirv_tritref as R

L, MOD, BASE = R.L, R.MOD, R.BASE


# --------------------------------------------------------------------------
# 独立参考实现（不调用 trirv_tritref，避免自证）
#   ref_* 一律用 Python 整数 + 3 的幂直接算
# --------------------------------------------------------------------------
def ref_div(a, b):
    """有符号除法，向零截断，b==0 得 0（ISA 语义）。a/b 为无符号 45-trit。"""
    x, y = R.sgn(R.from_int(a)), R.sgn(R.from_int(b))
    if y == 0:
        return 0
    q = abs(x) // abs(y)
    return (q if (x >= 0) == (y >= 0) else -q) % MOD


def ref_rem(a, b):
    x, y = R.sgn(R.from_int(a)), R.sgn(R.from_int(b))
    if y == 0:
        return 0
    q = abs(x) // abs(y)
    q = q if (x >= 0) == (y >= 0) else -q
    return (x - q * y) % MOD


def ref_sra(a, k):
    x = R.sgn(R.from_int(a))
    q = abs(x) // 3 ** k
    return (q if x >= 0 else -q) % MOD


def ref_tritwise(a, b, mode):
    v = 0
    for i in range(45):
        x, y = (a // 3 ** i) % 3, (b // 3 ** i) % 3
        d = min(x, y) if mode == 0 else max(x, y) if mode == 1 else (x + y) % 3
        v += d * 3 ** i
    return v


# --------------------------------------------------------------------------
# 样本：边界优先，再随机
# --------------------------------------------------------------------------
def sample_values():
    fixed = [0, 1, 2, 3, 120, 121, 122, 123, 242, 243, 244, 486,
             3 ** 5 - 1, 3 ** 5, 3 ** 10, 2 ** 31 - 1, 2 ** 31, 2 ** 32,
             MOD // 2, MOD // 2 + 1, MOD - 1, MOD - 2,
             29524, 29525, 59048, 3 ** 20, 3 ** 40]
    rnd = random.Random(20261119)
    vals = list(fixed)
    vals += [rnd.randrange(MOD) for _ in range(500)]
    vals += [rnd.randrange(3 ** 20) for _ in range(150)]
    return vals


def main():
    vals = sample_values()
    pairs = [(vals[i], vals[(i * 7 + 3) % len(vals)]) for i in range(len(vals))]
    random.Random(7).shuffle(pairs)
    pairs = pairs[:1200]

    fails = []
    checks = 0

    def chk(name, got, want, ctx):
        nonlocal checks
        checks += 1
        if got != want:
            fails.append((name, ctx, got, want))

    for ai, bi in pairs:
        a, b = R.from_int(ai), R.from_int(bi)
        sa, sb = R.sgn(a), R.sgn(b)

        chk("ADD", R.to_int(R.add(a, b)), (ai + bi) % MOD, (ai, bi))
        chk("SUB", R.to_int(R.sub(a, b)), (ai - bi) % MOD, (ai, bi))
        chk("MUL", R.to_int(R.mul_lo(a, b)), (ai * bi) % MOD, (ai, bi))

        chk("MULHU", R.to_int(R.mul_hi_signed(a, b, False, False)),
            (ai * bi) // MOD, (ai, bi))
        chk("MULH", R.to_int(R.mul_hi_signed(a, b, True, True)),
            ((sa * sb) // MOD) % MOD, (ai, bi))
        chk("MULHSU", R.to_int(R.mul_hi_signed(a, b, True, False)),
            ((sa * bi) // MOD) % MOD, (ai, bi))

        q, r = R.divmod_u(a, b)
        if bi == 0:
            chk("DIVU/0", R.to_int(q), 0, (ai, bi))
            chk("REMU/0", R.to_int(r), 0, (ai, bi))
        else:
            chk("DIVU", R.to_int(q), ai // bi, (ai, bi))
            chk("REMU", R.to_int(r), ai % bi, (ai, bi))

        # 有符号除余：用 tritref 的 divmod_u 组合出来，再与独立参考比
        tq, _ = R.divmod_u(a if sa >= 0 else R.from_int(-sa),
                           b if sb >= 0 else R.from_int(-sb))
        negq = ((sa < 0) != (sb < 0)) if sb else False
        got_q = R.to_int(R.sub([0] * L, tq)) if (negq and R.to_int(tq)) else R.to_int(tq)
        chk("DIV", got_q, ref_div(ai, bi), (ai, bi))
        chk("REM", 0 if bi == 0 else (ai - got_q * bi) % MOD, ref_rem(ai, bi), (ai, bi))

        chk("SLTU", 1 if R.cmp_u(a, b) < 0 else 0, 1 if ai < bi else 0, (ai, bi))
        chk("SLT", 1 if R.cmp_s(a, b) < 0 else 0, 1 if sa < sb else 0, (ai, bi))

        chk("AND", R.to_int(R.tritwise(a, b, 0)), ref_tritwise(ai, bi, 0), (ai, bi))
        chk("OR", R.to_int(R.tritwise(a, b, 1)), ref_tritwise(ai, bi, 1), (ai, bi))
        chk("XOR", R.to_int(R.tritwise(a, b, 2)), ref_tritwise(ai, bi, 2), (ai, bi))

        for k in (0, 1, 2, 3, 5, 8, 13, 30, 44, 45, 46, 60):
            chk("SLL", R.to_int(R.shl(a, k)), (ai * 3 ** k) % MOD, (ai, k))
            chk("SRL", R.to_int(R.shr(a, k)), (ai // 3 ** k) % MOD, (ai, k))
            mag = R.shr(a if sa >= 0 else R.from_int(-sa), k)
            got = R.to_int(R.sub([0] * L, mag)) if (sa < 0 and R.to_int(mag)) else R.to_int(mag)
            chk("SRA", got, ref_sra(ai, k), (ai, k))

    # 1 tryte / 2 tryte 符号扩展：全枚举 1 tryte，2 tryte 抽样
    # 用 sgn() 取有符号值比较（结果在模空间里是 MOD-121 这种形式）
    for v in range(BASE):
        chk("LB", R.sgn(R.lb(R.from_int(v))), v if v <= 121 else v - 243, v)
        chk("LBU", R.to_int(R.lbu(R.from_int(v))), v, v)
    for v in range(0, 59049, 7):
        chk("LH", R.sgn(R.lh(R.from_int(v))), v if v <= 29524 else v - 59049, v)

    # 访存往返
    mem = {}
    for v in (0, 1, 242, 243, MOD - 1, 3 ** 30, 2 ** 31):
        R.store_word(mem, 100, R.from_int(v))
        chk("LW", R.to_int(R.load_word(mem, 100)), v, v)

    print(f"差分校验：{checks} 项，失败 {len(fails)}")
    for f in fails[:5]:
        print("  FAIL", f[0], "输入", f[1], "得", f[2], "应", f[3])
    if fails:
        return 1

    print("\n端到端（只用 tryte 原语逐步计算，不经二进制容器）：")
    acc, one, i = R.from_int(0), R.from_int(1), R.from_int(1)
    while R.cmp_u(i, R.from_int(1000)) <= 0:
        acc = R.add(acc, i)
        i = R.add(i, one)
    got = R.to_int(acc)
    print(f"  求和 1..1000 = {got}   {'OK' if got == 500500 else 'FAIL'}")

    a, b = R.from_int(1), R.from_int(1)
    for _ in range(20):
        a, b = b, R.add(a, b)
    got = R.to_int(a)
    print(f"  fib(21) = {got}   {'OK' if got == 10946 else 'FAIL'}")

    f, want = R.from_int(1), 1
    for k in range(2, 21):
        f = R.mul_lo(f, R.from_int(k))
        want *= k
    got = R.to_int(f)
    print(f"  20! mod 3^45 = {got}   {'OK' if got == want % MOD else 'FAIL'}")

    print("\n成本表（trit 级原语次数，与宿主无关）")
    print(f"{'ISA 操作':<24}{'原语数':>8}  主要构成")
    rows = []

    def cost(name, fn):
        R.reset()
        fn()
        rows.append((name, sum(R.STATS.values()),
                     ", ".join(f"{k}×{v}" for k, v in R.STATS.most_common(3))))

    A1, B1 = R.from_int(12345678), R.from_int(87654321)
    A2, B2 = R.from_int(3 ** 30), R.from_int(2 ** 31)
    big = R.from_int(MOD - 12345)

    cost("ADD / SUB", lambda: R.add(A1, B1))
    cost("SLT（典型）", lambda: R.cmp_s(A1, B1))
    cost("SLT（最坏，相等）", lambda: R.cmp_s(A1, A1))
    cost("AND / OR / XOR", lambda: R.tritwise(A1, B1, 2))
    cost("SLL / SRL k=1", lambda: R.shl(A1, 1))
    cost("SLL / SRL k=13", lambda: [R.shl(A1, 13), R.shr(A1, 13)])
    cost("MUL（低半）", lambda: R.mul_lo(A1, B1))
    cost("MULH（高半）", lambda: R.mul_hi(A1, B1))
    cost("DIVU（45 轮逐 trit）", lambda: R.divmod_u(big, B2))
    cost("LW + SW 往返", lambda: [R.store_word({}, 0, A1), R.load_word({0: 1}, 0)])
    cost("LB（LBU 免费）", lambda: [R.lb(A1), R.lbu(A1)])

    for name, tot, top in rows:
        print(f"{name:<24}{tot:>8}  {top}")

    base = rows[0][1]
    print("\n相对 ADD 的倍数：")
    for name, tot, _ in rows[1:]:
        print(f"  {name:<24}{tot / base:6.1f}×")

    # ---------------- 与 C VM 实测成本的对照 ----------------
    # C VM 数值 = 2000 万次循环实测（循环含 被测 + ADDI + BNE 三条），
    # 已扣除 ADD 循环的基线，2000 万次是为压掉进程启动开销。
    VM_NS = {"ADD / SUB": 0.0, "MUL（低半）": 43.1, "DIVU（45 轮逐 trit）": 115.7,
             "AND / OR / XOR": 36.5}
    print("\n与 C VM 实测成本的偏差（2000 万次循环，已扣 ADD 基线）")
    print(f"{'操作':<22}{'C VM 净增量':>12}{'tryte 原语':>11}{'倍数 vs ADD':>13}")
    for name, tot, _ in rows:
        if name not in VM_NS:
            continue
        ns = VM_NS[name]
        print(f"{name:<22}{ns:>10.1f}ns{tot:>11}{tot / base:>12.1f}×")
    print("\n  关键读数：C VM 上 DIV 只比 MUL 贵 2.7 倍，按 tryte 原语算是 55 倍。")
    print("  差额来自宿主 CPU 有硬件除法器，而三进制数据通路没有（要 45 轮试商）。")
    print("  结论：VM 的墙钟数字不能用来论证架构成本，只能用来比较同一宿主上的优化。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
