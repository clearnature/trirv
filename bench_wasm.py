#!/usr/bin/env python3
"""bench_wasm —— 量化"Wasm 栈式直译"与"C 寄存器式"在同一台 TriRV 上的代价差。

两段程序做同一件事：sum = 1 + 2 + ... + N。

三个对照，区别只在"要不要付 i32 语义税"：
    · wasm    ：栈式字节码 → trirv_wasm（操作数栈驻留寄存器）
    · C 寄存器：trirv_cc 寄存器式后端，int = 45 trit，**不截断**
    · C i32   ：同上，但手写 mod 2^32 归正，与 wasm 语义等价 → 这才是公平对照

前两者结果必然不同，不是 bug：wasm 的 i32 每次加法回绕到 2^32 以内，
C 的 int 是 45 trit 不截断。C i32 版与 wasm 应当逐位一致。
"""
import re
import subprocess
import time

import trirv_asm
import trirv_wasm
import trirv_wasm_e2e as T

N = 100_000
M32 = 2 ** 32
I31 = 2 ** 31


def wasm_sum(n):
    body = (T.I32C(0) + T.LSET(1) +
            T.I32C(1) + T.LSET(2) +
            T.BLOCK + T.LOOP +
            T.LGET(2) + T.LGET(0) + T.GTU + T.IF + T.BR(2) + T.END +
            T.LGET(1) + T.LGET(2) + T.ADD + T.LSET(1) +
            T.LGET(2) + T.I32C(1) + T.ADD + T.LSET(2) +
            T.BR(0) +
            T.END + T.END + T.LGET(1) + T.END)
    w = T.wrap1(body, [n], nlocals=2)
    asm, segs, _ = trirv_wasm.compile_wasm(w.build(), "run")
    return asm, segs


def run_c_vm(asm, segs, times=3):
    """用 C VM 跑（Python VM 单步 ~5µs，测不出真实差距）。"""
    trv = "/tmp/bench_wasm.trv"
    trirv_wasm.build_trv(asm, segs, trv)
    best, steps, exitc = 1e9, 0, 0
    for _ in range(times):
        t0 = time.perf_counter()
        r = subprocess.run(["./trirv_vm", trv], capture_output=True)
        dt = time.perf_counter() - t0
        m = re.search(r"步数=(\d+).*?exit=(-?\d+)", r.stderr.decode("utf-8", "replace"))
        steps, exitc = int(m.group(1)), int(m.group(2))
        best = min(best, dt)
    prog, _ = trirv_asm.assemble(asm)
    return len(prog), steps, best, exitc


def main():
    print(f"求和 1..{N}（C VM 计时，每项取 3 次最好）\n")

    asm_w, segs_w = wasm_sum(N)
    n_w, st_w, t_w, e_w = run_c_vm(asm_w, segs_w)

    import trirv_cc_backend as cc
    src = "int main(){int s=0;int i=1;while(i<=%d){s=s+i;i=i+1;}return s;}" % N
    prog_c, _, asm_c, segs_c = cc.compile_c(src)
    del prog_c
    n_c, st_c, t_c, e_c = run_c_vm(asm_c, segs_c or {})

    # 公平对照：C 也手写 mod 2^32 归正，语义与 wasm i32 等价
    src_i32 = (
        "int main(){int s=0;int i=1;while(i<=%d){"
        "s=s+i;"
        "if(s>=%d)s=s-%d;"
        "if(s<-%d)s=s+%d;"
        "i=i+1;"
        "if(i>=%d)i=i-%d;"
        "if(i<-%d)i=i+%d;"
        "}return s;}" % (N, I31, M32, I31, M32, I31, M32, I31, M32))
    try:
        prog_i, _, asm_i, segs_i = cc.compile_c(src_i32)
        del prog_i
        n_i, st_i, t_i, e_i = run_c_vm(asm_i, segs_i or {})
    except Exception as ex:
        print(f"C i32 版编译失败：{ex}")
        n_i = st_i = t_i = 0
        e_i = None

    print(f"{'':14s}{'静态指令':>10s}{'动态步数':>12s}{'墙钟':>11s}{'结果':>14s}")
    print(f"{'wasm 直译':14s}{n_w:10d}{st_w:12d}{t_w*1000:10.1f}ms{e_w:14d}")
    print(f"{'C 寄存器(45t)':14s}{n_c:10d}{st_c:12d}{t_c*1000:10.1f}ms{e_c:14d}")
    if st_i:
        print(f"{'C 寄存器(i32)':14s}{n_i:10d}{st_i:12d}{t_i*1000:10.1f}ms{e_i:14d}")

    print(f"\n动态步数比  wasm/C(45t) {st_w/st_c:.2f}x    "
          f"墙钟比 {t_w/t_c:.2f}x    "
          f"单步 {t_w/st_w*1e9:.1f}ns vs {t_c/st_c*1e9:.1f}ns")
    if st_i:
        print(f"公平对照    wasm/C(i32) {st_w/st_i:.2f}x    "
              f"墙钟比 {t_w/t_i:.2f}x    "
              f"单步 {t_i/st_i*1e9:.1f}ns")

    want_w = (N * (N + 1) // 2) % M32
    want_c = N * (N + 1) // 2
    print(f"\n结果核对：wasm 应回绕到 {want_w}（低 8 位 {want_w & 0xFF}），实得 exit={e_w} "
          f"{'OK' if e_w == want_w & 0xFF else 'FAIL'}")
    print(f"          C(45t) 应得 {want_c}（低 8 位 {want_c & 0xFF}），实得 exit={e_c} "
          f"{'OK' if e_c == want_c & 0xFF else 'FAIL'}")
    if st_i:
        print(f"          C(i32) 应与 wasm 同值 {want_w}（低 8 位 {want_w & 0xFF}），"
              f"实得 exit={e_i} {'OK' if e_i == want_w & 0xFF else 'FAIL'}")


if __name__ == "__main__":
    main()
