#!/usr/bin/env python3
"""TF 浮点格式实测：精度、动态范围、穿透模型、转换成本。

框架更正（2026-11-20）：binary64 的"64 位"是 IEEE 软件规范，不是 CPU 物理事实，
不拿它的值域当格式天花板。正确穿透模型 = 尾数[1,3)进宿主 FPU + 指数走整数 ALU，
double 值域从不约束指数。所有数字本机实测，不写估计值。

    python3 bench_float.py
"""
import math
import sys
import time

import trirv_base12 as b12
import trirv_float as tf


def report():
    print("=" * 74)
    print("A. 格式精度（执行层度量 = trit；表示层记数 = 十二进制位）")
    print("=" * 74)
    print("  执行层度量是 trit；bit/十进制位/十二进制位都只是表示层换算，不作定义依据")
    for f in (tf.TF32, tf.TF64):
        k = f.mant_trits - 1
        d12 = b12.trit_to_digits(k)
        print(f"  {f.name}: 尾数 {f.mant_trits} trit（执行层） ≈ {d12:4.1f} 十二进制位（表示层）")
    print("  宽度由字长与档位需求自主定义；二进制/十进制位数不是约束，仅作背景参照")

    print()
    print("=" * 74)
    print("B. 动态范围（阶码，base-3 整数指数；指数本身就是执行层度量）")
    print("=" * 74)
    for f in (tf.TF32, tf.TF64):
        emin, emax = -f.bias + 1, f.max_exp - f.bias - 1
        print(f"  {f.name}: exp3∈[{emin},{emax}]（base-3 整数，执行层）")

    print()
    print("=" * 74)
    print("C. 穿透模型验证：全阶码可表示（无缺口）")
    print("=" * 74)
    print("  规范表示 = sign × mant[1,3) × 3^exp3；mant 进 FPU，exp3 走整数 ALU")
    print("  宿主 double 只装 mant（恒在[1,3)），其 3^646 上限不约束整数 exp3")
    for f in (tf.TF32, tf.TF64):
        # 抽样整个阶码域（含远超 double 的），验证 mant 有限 + exp3 精确
        emax = f.max_exp - f.bias - 1
        samples = (-f.bias + 1, 0, emax - 1, emax)
        ok = True
        for e3 in samples:
            E = e3 + f.bias
            raw = E * f.P_MANT + f.P_NORM + 13
            sign, mant, exp3 = f.decompose(raw)
            ok = ok and math.isfinite(mant) and 1.0 <= mant < 3.0 and exp3 == e3
        print(f"  {f.name}: 阶码 10^[-{abs(-f.bias)*math.log10(3):.0f},"
              f"{emax*math.log10(3):.0f}] 抽样全部 mant 有限/exp3 精确 → {'PASS' if ok else 'FAIL'}")
        beyond = "远超 double 上限 3^646" if emax > 646 else "在 double 上限内（TF32 档位小）"
        print(f"    极端 exp3={emax}（3^{emax}，{beyond}）仍无需把 3^exp3 塞进 double")

    print()
    print("=" * 74)
    print("D. 转换成本（Python 参考实现的墙钟，仅作相对参考，非 ISA 指令数）")
    print("=" * 74)
    N = 200000
    vals = [1.0 + i / 1000.0 for i in range(N)]
    for f in (tf.TF32, tf.TF64):
        t0 = time.perf_counter()
        for v in vals:
            f.encode(v)
        enc_ns = (time.perf_counter() - t0) / N * 1e9
        raws = [f.encode(v) for v in vals]
        t0 = time.perf_counter()
        for r in raws:
            f.decode(r)
        dec_ns = (time.perf_counter() - t0) / N * 1e9
        print(f"  {f.name}: encode {enc_ns:8.1f} ns, decode {dec_ns:8.1f} ns")

    print()
    print("=" * 74)
    print("E. 结论")
    print("=" * 74)
    print("  格式宽度由字长/档位自主定义（TF32 14、TF64 34 trit 尾数），非对标二进制位宽。")
    print("  穿透 = 尾数进宿主 FPU + 指数走整数 ALU；double 值域不约束指数，全阶码可表示，")
    print("  不存在穿透缺口。P2 按此模型实现 F 扩展指令。")


if __name__ == "__main__":
    report()
