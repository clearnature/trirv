#!/usr/bin/env python3
"""bench_bitops —— 量"在三进制机器上做二进制位运算"到底要付多少条指令。

背景：TriRV 的寄存器是 45 trit 的三进制数，**里面没有二进制位**。
wasm 的 i32 位运算要的是 32 个二进制位，所以每次都得做一次进制展开：

    and/or/xor  ：32 次「比较 2^k + 条件减法」把两个操作数拆成位（无除法）
    shl/shr     ：乘除 2^k，各一次 MUL/DIV
    clz/ctz/pop ：同一条走查，换累加方式

本脚本给三个数：
    ① 单条位运算的**静态指令数**（相对恒等函数多出来的部分）
    ② 位运算密集循环的**每轮动态步数**
    ③ 对照：如果 ISA 保留"二进制视图"指令（当前 VM 用宿主 ALU 实现），只要几条
"""
import re
import subprocess
import time

import trirv_asm
import trirv_wasm
import trirv_wasm_e2e as T

N = 20_000
FNV_OFF = 16_777_619
FNV_BASIS = 2166136261


def s32(v):
    v &= 2 ** 32 - 1
    return v - 2 ** 32 if v >= 2 ** 31 else v


def count_insns(asm):
    return sum(1 for ln in asm.splitlines()
               if ln.startswith("    ") and ln.strip() and not ln.strip().endswith(":"))


def body_of(op_bytes, extra_pre=b"", extra_post=b""):
    body = extra_pre + T.LGET(0) + T.LGET(1) + op_bytes + extra_post + T.END
    w = T.wrap1(body, [0, 0])
    asm, segs, _ = trirv_wasm.compile_wasm(w.build(), "run")
    return asm, segs


def main():
    print("① 单条位运算的静态指令数（相对恒等函数 `local.get 0` 多出来的量）\n")

    base_w = T.wrap1(T.LGET(0) + T.END, [0, 0])
    base_asm, _, _ = trirv_wasm.compile_wasm(base_w.build(), "run")
    base = count_insns(base_asm)

    rows = [("i32.add", T.ADD), ("i32.mul", T.MUL),
            ("i32.and", T.BAND), ("i32.or", T.BOR), ("i32.xor", T.BXOR),
            ("i32.shl", T.SHL), ("i32.shr_s", T.SHRS), ("i32.shr_u", T.SHRU),
            ("i32.rotl", T.ROTL), ("i32.rotr", T.ROTR)]
    print(f"{'操作':12s}{'指令数':>8s}{'相对 add':>12s}")
    add_n = None
    for name, op in rows:
        asm, _ = body_of(op)
        n = count_insns(asm) - base
        if name == "i32.add":
            add_n = n
        rel = f"{n / add_n:.0f}x" if add_n else "-"
        print(f"{name:12s}{n:8d}{rel:>12s}")

    print("\n   单操作数指令（clz/ctz/popcnt）：")
    for name, op in (("i32.clz", T.CLZ), ("i32.ctz", T.CTZ), ("i32.popcnt", T.POPCNT)):
        w = T.wrap1(T.LGET(0) + op + T.END, [0])
        asm, _, _ = trirv_wasm.compile_wasm(w.build(), "run")
        print(f"   {name:12s}{count_insns(asm) - base:8d}")

    print(f"\n② 位运算密集循环：h = (h ^ i) * {FNV_OFF}，跑 {N} 轮（C VM）\n")

    body = (T.I32C(s32(FNV_BASIS)) + T.LSET(1) +
            T.I32C(0) + T.LSET(2) +
            T.BLOCK + T.LOOP +
            T.LGET(2) + T.LGET(0) + T.GTU + T.IF + T.BR(2) + T.END +
            T.LGET(1) + T.LGET(2) + T.BXOR + T.I32C(FNV_OFF) + T.MUL + T.LSET(1) +
            T.LGET(2) + T.I32C(1) + T.ADD + T.LSET(2) +
            T.BR(0) + T.END + T.END + T.LGET(1) + T.END)
    w = T.wrap1(body, [N], nlocals=2)
    asm, segs, _ = trirv_wasm.compile_wasm(w.build(), "run")

    trv = "/tmp/bench_bitops.trv"
    trirv_wasm.build_trv(asm, segs, trv)
    best, steps, ex = 1e9, 0, 0
    for _ in range(3):
        t0 = time.perf_counter()
        r = subprocess.run(["./trirv_vm", trv], capture_output=True)
        best = min(best, time.perf_counter() - t0)
        m = re.search(r"步数=(\d+).*?exit=(-?\d+)", r.stderr.decode("utf-8", "replace"))
        steps, ex = int(m.group(1)), int(m.group(2))
    prog, _ = trirv_asm.assemble(asm)

    print(f"静态指令 {len(prog)}    动态步数 {steps:,}    墙钟 {best*1000:.1f}ms    "
          f"单步 {best/steps*1e9:.1f}ns")
    print(f"每轮 {steps/N:.1f} 步（含 1 次 xor + 1 次 mul + 1 次 add + 循环开销）")
    print(f"推导：xor 走查约 {count_insns(body_of(T.BXOR)[0]) - base} 条，"
          f"即每轮约有 {(steps/N):.0f} 步中绝大多数花在拆位上")


if __name__ == "__main__":
    main()
