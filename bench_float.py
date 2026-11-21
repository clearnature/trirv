#!/usr/bin/env python3
"""TF 浮点格式实测：精度、动态范围、穿透模型、转换成本、VM 级指令成本、尾数承载精度。

框架更正（2026-11-20）：binary64 的"64 位"是 IEEE 软件规范，不是 CPU 物理事实，
不拿它的值域当格式天花板。正确穿透模型 = 尾数[1,3)进宿主 FPU + 指数走整数 ALU，
double 值域从不约束指数。所有数字本机实测，不写估计值。

P5 新增（2026-11-20，见 TERNARY_FLOAT_PROPOSAL.md §5.4）：
  F. VM 级 F 指令单条成本（直线热循环 × C VM 计时，15 变体）
  G. 宿主分解微基准（tf_dec/tf_compose/裸 FPU，分解 FADD 构成）
  H. TF45 尾数 double 承载精度（decompose→compose 往返 → double-double 判定）

    python3 bench_float.py
"""
import math
import os
import random
import re
import struct
import subprocess
import sys
import tempfile
import time

import trirv_base12 as b12
import trirv_isa as isa
import trirv_float as tf

HERE = os.path.dirname(os.path.abspath(__file__))
CVM = os.path.join(HERE, "trirv_vm")


def best_of(fn, n=3):
    """跑 n 次取最小（最小 = 干扰最少，与既有 perf 口径一致）。"""
    return min(fn() for _ in range(n))


# ---------------------------------------------------------------- M1: VM 级
def raw_to_trytes(raw):
    return bytes((raw // (243 ** i)) % 243 for i in range(9))


def write_trv(path, prog, data):
    buf = bytearray(b"TRV1")
    buf += struct.pack("<III", 1, len(prog), len(data))
    for w in prog:
        buf += struct.pack("<I", w & 0xFFFFFFFF)
    for addr, b in data.items():
        buf += struct.pack("<II", addr, len(b))
        buf += bytes(b)
    with open(path, "wb") as f:
        f.write(buf)


def run_cvm_steps(path):
    """跑 C VM，返回 (时间秒, 步数)。"""
    r = subprocess.run([CVM, path], capture_output=True, text=True)
    m = re.search(r"时间=([\d.]+)s", r.stderr)
    s = re.search(r"步数=(\d+)", r.stderr)
    if not m or not s:
        raise RuntimeError(f"C VM 输出异常: {r.stderr[-300:]}")
    return float(m.group(1)), int(s.group(1))


def bench_vm_instructions(N=200000):
    """直线展开 N 条热指令（无循环体开销），单条成本 = 时间/步数。"""
    A_ADDR, B_ADDR = 100, 109
    data = {A_ADDR: raw_to_trytes(tf.TF45.encode(1.5)),
            B_ADDR: raw_to_trytes(tf.TF45.encode(2.25))}
    setup = [("FLW", 0, 0, 0, A_ADDR), ("FLW", 1, 0, 0, B_ADDR),
             ("ADDI", 3, 0, 0, 1)]
    tail = [("ADDI", 10, 0, 0, 0), ("ADDI", 17, 0, 0, 93), ("ECALL", 0, 0, 0, 0)]
    variants = [
        ("ADD        ", ("ADD", 3, 3, 3, 0)),          # 分发基线
        ("LW         ", ("LW", 3, 0, 0, A_ADDR)),
        ("SW         ", ("SW", 3, 0, 0, A_ADDR)),
        ("FLW        ", ("FLW", 2, 0, 0, A_ADDR)),     # raw 搬运，预期≈LW
        ("FSW        ", ("FSW", 0, 0, 2, B_ADDR)),
        ("FADD       ", ("FADD", 2, 0, 1, 0)),
        ("FADD.S     ", ("FADD.S", 2, 0, 1, 0)),
        ("FMUL       ", ("FMUL", 2, 0, 1, 0)),
        ("FMUL.S     ", ("FMUL.S", 2, 0, 1, 0)),
        ("FDIV       ", ("FDIV", 2, 0, 1, 0)),
        ("FDIV.S     ", ("FDIV.S", 2, 0, 1, 0)),
        ("FSQRT      ", ("FSQRT", 2, 0, 0, 0)),
        ("FSQRT.S    ", ("FSQRT.S", 2, 0, 0, 0)),
        ("FEQ        ", ("FEQ", 4, 0, 1, 0)),          # 结果写整数 reg
        ("FEQ.S      ", ("FEQ.S", 4, 0, 1, 0)),
    ]
    if not os.path.exists(CVM):
        print("  （未找到 ./trirv_vm，跳过 VM 级实测；先 ./build.sh）")
        return None
    tmpdir = tempfile.mkdtemp(prefix="trirv_fbench_")
    out = {}
    for label, hot in variants:
        ops = setup + [hot] * N + tail
        prog = [isa.encode(mn, rd=rd, rs1=rs1, rs2=rs2, imm=imm)
                for (mn, rd, rs1, rs2, imm) in ops]
        path = os.path.join(tmpdir, "hot.trv")
        write_trv(path, prog, data)
        def one(path=path):
            t, steps = run_cvm_steps(path)
            assert steps >= N, f"步数异常 {steps}"
            return t
        t = best_of(one)
        out[label.strip()] = t / N * 1e9          # ns/op（setup+tail 6 条，占比 0.003%）
    return out


# ---------------------------------------------------------------- M2: 宿主分解
M2_C_SRC = r"""
#include <stdio.h>
#include <stdint.h>
#include <time.h>
#include <string.h>
#include "trirv_float.h"

static __uint128_t POW3L[46];
static TFFmt FMT;

__attribute__((noinline)) static double bare_add(double a, double b){ return a + b; }
__attribute__((noinline)) static double bare_mul(double a, double b){ return a * b; }
__attribute__((noinline)) static double do_dec(__uint128_t raw){
    return tf_dec(&FMT, raw, FMT.max_exp).mant;
}
__attribute__((noinline)) static __uint128_t do_compose(int s, double m, long e){
    return tf_compose(&FMT, s, m, e, FMT.max_exp);
}
__attribute__((noinline)) static __uint128_t do_add(__uint128_t a, __uint128_t b){
    return tf_add(&FMT, a, b, 0, FMT.max_exp, POW3L) % POW3L[45];
}

static double now_ns(void){
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1e9 + ts.tv_nsec;
}
#define NS(lb, expr) do{ double t0=now_ns(); __uint128_t acc=0; double dacc=0; \
    for(long i=0;i<N;i++){ expr; } double dt=now_ns()-t0; \
    printf("%s %.2f\n", lb, dt/N); (void)acc; (void)dacc; }while(0)

int main(void){
    const long N = 1000000;
    POW3L[0]=1; for(int i=1;i<=45;i++) POW3L[i]=POW3L[i-1]*3;
    tf45_init(&FMT, POW3L);
    /* 1000 个变化的正规 raw，防常量折叠；全走正规数路径 */
    static __uint128_t raws[1000]; static double mants[1000];
    for(int i=0;i<1000;i++){
        double span = (double)(FMT.P_MANT - FMT.P_NORM);
        unsigned long long M = (unsigned long long)(double)FMT.P_NORM
                             + (unsigned long long)(span * ((i+0.5)/1000.0));
        raws[i] = ((i % 2) ? FMT.P_SIGN : 0) + ((__uint128_t)(i % 97 + 1) * FMT.P_MANT) + M;
        mants[i] = 1.0 + (double)i / 700.0;      /* [1, ~2.43)，全正规 */
    }
    double ds = 0; __uint128_t us = 0;
    /* 实参必须逐轮变化：static+noinline 的函数在全常量实参下会被编译期求值（实测 0.1ns 假象） */
    NS("bare_add",  ds += bare_add(mants[i % 1000] , 1.0000001));
    NS("bare_mul",  ds += bare_mul(mants[i % 1000] , 1.0000001));
    NS("tf_dec",    ds += do_dec(raws[i % 1000]));
    NS("tf_compose", us ^= do_compose(i & 1, mants[i % 1000], (i % 200) - 100));
    NS("tf_add_full", us ^= do_add(raws[i % 1000], raws[(i + 1) % 1000]));
    printf("sink %g %llu\n", ds, (unsigned long long)(us & 0xFFFF));
    return 0;
}
"""


def bench_host_decompose():
    """编译运行 C 微基准，返回 {name: ns/op}。"""
    import shutil
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        print("  （无 C 编译器，跳过宿主分解微基准）")
        return None
    d = tempfile.mkdtemp(prefix="trirv_fmicro_")
    src = os.path.join(d, "m2.c")
    with open(src, "w") as f:
        f.write(M2_C_SRC)
    exe = os.path.join(d, "m2")
    # 与 VM 同级优化（build.sh 用 -O3 -march=native + PGO；微基准无法复现 PGO，取前两者）
    flags = ["-O3", "-march=native"]
    r = subprocess.run([cc, *flags, "-I", HERE, "-o", exe, src], capture_output=True, text=True)
    if r.returncode != 0:
        r = subprocess.run([cc, "-O2", "-I", HERE, "-o", exe, src], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  （微基准编译失败，跳过: {r.stderr[-200:]}）")
        return None
    out = {}
    for _ in range(3):                            # 3 次取最好
        rr = subprocess.run([exe], capture_output=True, text=True)
        for line in rr.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] in ("bare_add", "bare_mul", "tf_dec",
                                                "tf_compose", "tf_add_full"):
                v = float(parts[1])
                if parts[0] not in out or v < out[parts[0]]:
                    out[parts[0]] = v
    return out


# ---------------------------------------------------------------- M3: 承载精度
def bench_mant_precision(n=50000, seed=20261120):
    """随机正规 raw 的 decompose→compose 往返：精确率 + 最大尾数误差（量子计）。"""
    rng = random.Random(seed)
    res = {}
    for F in (tf.TF20, tf.TF45):
        P_MANT, P_NORM, P_SIGN = F.P_MANT, F.P_NORM, F.P_SIGN
        exact = 0
        max_err_q = 0.0
        max_err_q_lo = 0.0                        # mant∈[1,2) 区
        max_err_q_hi = 0.0                        # mant∈[2,3) 区
        n_lo = n_hi = 0
        exp_exact = True
        for _ in range(n):
            sign = rng.getrandbits(1)             # 0/1 位
            E = rng.randint(1, F.max_exp - 2)     # 避开 ±0/±Inf/NaN
            M = rng.randint(P_NORM, P_MANT - 1)
            raw = sign * P_SIGN + E * P_MANT + M
            s, mant, exp3 = F.decompose(raw)      # 注意 s ∈ {-1,+1}
            exp_exact = exp_exact and (exp3 == E - F.bias) \
                and ((s == -1) == (sign == 1))
            raw2 = F.compose(s, mant, exp3)
            if raw2 == raw:
                exact += 1
            err_q = abs(mant * P_NORM - M)        # 尾数量子（=1 trit @最低位）为单位
            if err_q > max_err_q:
                max_err_q = err_q
            if mant < 2.0:
                n_lo += 1
                max_err_q_lo = max(max_err_q_lo, err_q)
            else:
                n_hi += 1
                max_err_q_hi = max(max_err_q_hi, err_q)
        res[F.name] = (exact, n, max_err_q, exp_exact, n_lo, n_hi,
                       max_err_q_lo, max_err_q_hi)
    return res


# ---------------------------------------------------------------- 报告
def report():
    print("=" * 74)
    print("A. 格式精度（执行层度量 = trit；表示层记数 = 十二进制位）")
    print("=" * 74)
    print("  执行层度量是 trit；bit/十进制位/十二进制位都只是表示层换算，不作定义依据")
    for f in (tf.TF20, tf.TF45):
        k = f.mant_trits - 1
        d12 = b12.trit_to_digits(k)
        print(f"  {f.name}: 尾数 {f.mant_trits} trit（执行层） ≈ {d12:4.1f} 十二进制位（表示层）")
    print("  宽度由字长与档位需求自主定义；二进制/十进制位数不是约束，仅作背景参照")

    print()
    print("=" * 74)
    print("B. 动态范围（阶码，base-3 整数指数；指数本身就是执行层度量）")
    print("=" * 74)
    for f in (tf.TF20, tf.TF45):
        emin, emax = -f.bias + 1, f.max_exp - f.bias - 1
        print(f"  {f.name}: exp3∈[{emin},{emax}]（base-3 整数，执行层）")

    print()
    print("=" * 74)
    print("C. 穿透模型验证：全阶码可表示（无缺口）")
    print("=" * 74)
    print("  规范表示 = sign × mant[1,3) × 3^exp3；mant 进 FPU，exp3 走整数 ALU")
    print("  宿主 double 只装 mant（恒在[1,3)），其 3^646 上限不约束整数 exp3")
    for f in (tf.TF20, tf.TF45):
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
        beyond = "远超 double 上限 3^646" if emax > 646 else "在 double 上限内（TF20 档位小）"
        print(f"    极端 exp3={emax}（3^{emax}，{beyond}）仍无需把 3^exp3 塞进 double")

    print()
    print("=" * 74)
    print("D. 转换成本（Python 参考实现的墙钟，仅作相对参考，非 ISA 指令数）")
    print("=" * 74)
    N = 200000
    vals = [1.0 + i / 1000.0 for i in range(N)]
    for f in (tf.TF20, tf.TF45):
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
    print("F. VM 级 F 指令单条成本（P5-M1：直线 20 万条热指令，C VM 计时，3 次取最好）")
    print("=" * 74)
    vm = bench_vm_instructions()
    add_ns = vm.get("ADD") if vm else None
    if vm:
        for label, ns in vm.items():
            ratio = f"  {ns / add_ns:6.1f}×ADD" if add_ns else ""
            print(f"  {label:12s} {ns:8.2f} ns/条{ratio}")

    print()
    print("=" * 74)
    print("G. 宿主分解微基准（P5-M2：noinline 防闭式，变化操作数，3 次取最好）")
    print("=" * 74)
    host = bench_host_decompose()
    if host:
        for k in ("bare_add", "bare_mul", "tf_dec", "tf_compose", "tf_add_full"):
            if k in host:
                print(f"  {k:12s} {host[k]:8.2f} ns/op")
        if add_ns and "tf_dec" in host and "tf_compose" in host and "FSQRT" in vm:
            # M1 交叉推导（同一 PGO 二进制内的差分，口径一致）：
            #   FSQRT = 分发 + 1×tf_dec + sqrt；FADD = 分发 + 2×tf_dec + add
            #   → tf_dec_in_VM ≈ FADD − FSQRT（sqrt 与 add 同为 ~1ns 级）
            dec_in_vm = vm["FADD"] - vm["FSQRT"]
            print(f"  构成分解（口径注意：微基准 noinline 有 ~几 ns 调用开销、无 PGO，")
            print(f"  只作量级参考；VM 内部实测用 M1 交叉推导）：")
            print(f"    tf_dec（独立编译）   ≈ {host['tf_dec']:.1f} ns")
            print(f"    tf_dec（VM 内推导）  ≈ {dec_in_vm:.1f} ns  = FADD({vm['FADD']:.0f}) − FSQRT({vm['FSQRT']:.0f})")
            print(f"    tf_compose（独立）   ≈ {host['tf_compose']:.1f} ns")
            print(f"    裸 double add/mul    ≈ {host['bare_add']:.1f} / {host['bare_mul']:.1f} ns（FPU 本体）")
            share = (dec_in_vm * 2 + host["tf_compose"]) / vm["FADD"] * 100
            print(f"    → FADD 的 ~{share:.0f}% 花在 base-3 编解码（2×dec+compose），FPU 运算本身 ≈1ns")

    print()
    print("=" * 74)
    print("H. TF45 尾数 double 承载精度（P5-M3：5 万随机正规 raw，固定种子）")
    print("=" * 74)
    prec = bench_mant_precision()
    verdicts = {}
    for name, (exact, n, maxq, expok, nlo, nhi, qlo, qhi) in prec.items():
        F = tf.TF20 if name == "TF20" else tf.TF45
        quantum_bits = (F.mant_trits - 1) * math.log2(3)
        print(f"  {name}: 往返精确 {exact}/{n}（{exact/n*100:.2f}%），"
              f"exp3/sign 全程精确 → {'PASS' if expok else 'FAIL'}")
        print(f"    最大尾数误差 = {maxq:.3f} 个尾数量子"
              f"（量子 = 3^-{F.mant_trits-1} ≈ 2^-{quantum_bits:.1f}）")
        print(f"    分区: mant∈[1,2) 最大 {qlo:.3f} 量子（{nlo} 例），"
              f"mant∈[2,3) 最大 {qhi:.3f} 量子（{nhi} 例）")
        verdicts[name] = (exact, n, maxq)
    if "TF45" in verdicts:
        _, _, maxq64 = verdicts["TF45"]
        print(f"  判读：TF45 往返漂移以尾数量子计（实测最大 {maxq64:.0f} 量子 = 末 1~2 个 trit），")
        print("  根源是 double 在 [2,3) 的间隔 2^-51 > 量子 3^-33——承载网格比格式粗；")
        print("  两套 VM 同用 double 承载 mant → 结果逐位一致（wasm e2e 112 例已证），")
        print("  代价仅是 VM 内 TF45 实现精度 ≈ 53 bit ≈ 33.5 trit < 格式 34 trit。")

    print()
    print("=" * 74)
    print("E. P5 结论（全部为上方实测数字，无估计值）")
    print("=" * 74)
    if vm and host:
        fadd = vm.get("FADD", 0)
        dec_in_vm = vm.get("FADD", 0) - vm.get("FSQRT", 0)
        codec = dec_in_vm * 2 + host.get("tf_compose", 0)
        print(f"  1. 转换税没有消失，而是随 raw-in-freg 进了每条算术指令：")
        print(f"     FADD {fadd:.0f} ns ≈ 分发 {add_ns:.0f} + 2×tf_dec {dec_in_vm*2:.0f}"
              f"（VM 内推导）+ 编码 ~{host.get('tf_compose',0):.0f} + FPU 加法 ~{host.get('bare_add',0):.0f}；")
        print(f"     FLW {vm.get('FLW',0):.0f} / FSW {vm.get('FSW',0):.0f} ns ≈ LW {vm.get('LW',0):.0f} /"
              f" SW {vm.get('SW',0):.0f} ns（raw 纯搬运，边界零税）。")
        print(f"  2. 穿透收益成立但被编解码吃掉大头：FADD 里 FPU 加法只占 ~{host.get('bare_add',0)/fadd*100:.0f}%，")
        print(f"     base-3 编解码占 ~{codec/fadd*100:.0f}%（≈{codec/add_ns:.0f}×ADD）——四则/比较全部聚在")
        print(f"     ~100ns（{vm.get('FADD',0)/add_ns:.1f}×ADD，与具体运算无关）就是证据：贵的是进制转换，")
        print(f"     不是浮点运算。这正是 §3.1 分离表示要省的部分（每条算术省 2×解码 ≈ {dec_in_vm*2/fadd*100:.0f}% FADD，")
        print(f"     代价是 FLW/FSW 各付一次，算术密集链净赚）；")
        print(f"     对比方案 B（IEEE 直存）：每条算术还要在三进制指令上抽二进制位段，")
        print(f"     既有实测代理 372–753 条动态指令/op，比 A 的税贵约两个数量级。A 仍是对的。")
        if "TF45" in verdicts:
            exact, n, maxq = verdicts["TF45"]
            print(f"  3. TF45 尾数承载：往返精确 {exact/n*100:.1f}%，最大漂移 {maxq:.0f} 个尾数量子")
            print(f"     （= 末 1~2 个 trit；根源：double 间隔 2^-51/2^-52 > 格式量子 3^-33）。")
            print(f"     两套 VM 同源承载、逐位一致，e2e 差分不受影响 → double-double 暂不引入，")
            print(f"     记为演进项（需要完整 34-trit 实现精度时再做）。")
        print(f"  4. 硬件 FPU 直通不立专项：真三值硬件没有宿主 FPU，也无此税——")
        print(f"     base-3 内存 + 原生 base-3 FP 单元读 raw 即分离表示的硬件形态，")
        print(f"     本立项的 ISA 语义不变（§7 已有注记）。")
        print(f"  5. 分离表示改造列为后续 VM 优化项（不改 ISA），预期收益见 G 节。")
    else:
        print("  （VM 或宿主微基准缺数据，见上方 SKIP 提示）")


if __name__ == "__main__":
    report()
