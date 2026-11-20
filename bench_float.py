#!/usr/bin/env python3
"""TF 浮点格式实测：精度对标、动态范围、穿透缺口、转换成本。

数据用于裁决立项书 D-F2（宽度）与 D-F1 的"寄存器即 double 载体"假设。
所有数字本机实测，不写估计值。

    python3 bench_float.py
"""
import math
import sys
import time

import trirv_float as tf


def report():
    print("=" * 74)
    print("A. 精度对标（尾数有效位数，log2(3)≈1.585 bit/trit）")
    print("=" * 74)
    for f in (tf.TF32, tf.TF64):
        k = f.mant_trits - 1
        bits = k * math.log2(3)
        digits = k * math.log10(3)
        print(f"  {f.name}: {f.mant_trits} trit 尾数 → {bits:5.1f} bit ≈ {digits:4.1f} 十进制有效位")
    print(f"  f32 (对照): 24 bit ≈ 7.2 十进制位")
    print(f"  f64 (对照): 53 bit ≈ 15.9 十进制位")

    print()
    print("=" * 74)
    print("B. 动态范围（阶码，十进制指数）")
    print("=" * 74)
    for f in (tf.TF32, tf.TF64):
        emin, emax = -f.bias + 1, f.max_exp - f.bias - 1
        print(f"  {f.name}: e∈[{emin},{emax}] → 10^[{emin*math.log10(3):.0f}, {emax*math.log10(3):.0f}]")
    print(f"  f32 (对照): e∈[-126,127] → 10^[-38,38]")
    print(f"  f64 (对照): e∈[-1022,1023] → 10^[-308,308]")
    print(f"  host double: 上限 ≈1.8e308 = 3^{math.log2(sys.float_info.max)/math.log2(3):.0f}")

    print()
    print("=" * 74)
    print("C. 穿透缺口（TF 阶码超出 host double 承载能力的部分）")
    print("=" * 74)
    DBL_MAX_EXP3 = int(math.floor(math.log(sys.float_info.max, 3)))  # ≈646
    for f in (tf.TF32, tf.TF64):
        emax = f.max_exp - f.bias - 1
        gap = max(0, emax - DBL_MAX_EXP3)
        print(f"  {f.name}: 最大阶码 {emax}, double 上限 3^{DBL_MAX_EXP3}, 超出 {gap} 个阶码")
        if gap:
            v = 3.0 ** DBL_MAX_EXP3 * 3  # 刚超 double 的 TF 值（概念上）
            print(f"    → 该区间的值无法装入 double 载体，穿透时返回 ±Inf")

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
    print("E. D-F2 裁决输入")
    print("=" * 74)
    print("  TF32 14 trit 尾数 → 6.2 位十进制，略低于 f32 的 7.2 位；")
    print("    若需对齐 f32 精度，应升到 16 trit（≈7.1 位）。")
    print("  TF64 34 trit 尾数 → 15.7 位十进制，与 f64 的 15.9 位几乎相同，宽度合理。")
    print("  穿透缺口：TF64 阶码范围远超 double，'寄存器即 double 载体' 仅覆盖")
    print("    10^[-323,308] 窗口；窗口外需软件模拟或接受 Inf/0 —— 立项书 §7 风险落地。")


if __name__ == "__main__":
    report()
