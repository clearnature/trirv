#!/usr/bin/env python3
"""C VM 除法路径微基准 —— 实测 divmod_word 快路径 vs 72 轮恢复除法。

为什么需要：README §六.3 曾写"DIV ~150ns、优化方向乘法逆元"，但那是旧口径。
本脚本一次性裁决：
  - 默认快路径（宿主 128 位整数除法）：当前实现
  - 72 轮恢复除法（TRIRV_NO_HW_DIV，三值电路参考）：divmod_word_nodiv
  - 逐 tryte 分块（每轮 5 bit，修正有界）：作为候选方案对照

编译技巧：#define main → 规避 trirv_vm.c 的 main 冲突，直接复用内部函数。

    python3 bench_div.py
"""
import os
import subprocess
import sys
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))

C_SRC = r'''
#define main trirv_vm_unused_main
#include "TRIRV_VM_PATH"
#undef main

static volatile __int128_t sink;

static double bench_fast(int n) {
    __int128_t a = 1234567890123LL, b = 987654321LL;
    for (int i = 0; i < 1000; i++) { __int128_t q, r; divmod_word(a+i, b+(i&7), &q, &r); sink += q + r; }
    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (int i = 0; i < n; i++) {
        __int128_t q, r;
        divmod_word(a + i, b + (i & 7), &q, &r);
        sink += q ^ r;
    }
    clock_gettime(CLOCK_MONOTONIC, &t1);
    return ((t1.tv_sec-t0.tv_sec)+(t1.tv_nsec-t0.tv_nsec)/1e9)/n*1e9;
}

static double bench_nodiv(int n) {
    __int128_t a = 1234567890123LL, b = 987654321LL;
    for (int i = 0; i < 100; i++) { __int128_t q, r; divmod_word_nodiv(a+i, b+(i&7), &q, &r); sink += q + r; }
    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (int i = 0; i < n; i++) {
        __int128_t q, r;
        divmod_word_nodiv(a + i, b + (i & 7), &q, &r);
        sink += q ^ r;
    }
    clock_gettime(CLOCK_MONOTONIC, &t1);
    return ((t1.tv_sec-t0.tv_sec)+(t1.tv_nsec-t0.tv_nsec)/1e9)/n*1e9;
}

int main(void) {
    init_consts();
    double fast = bench_fast(20000000);
    double slow = bench_nodiv(2000000);
    printf("divmod_word 快路径(宿主128位除法)  : %8.2f ns/op\n", fast);
    printf("divmod_word_nodiv(72轮逐位恢复)    : %8.2f ns/op\n", slow);
    printf("加速比                              : %8.1fx\n", slow / fast);
    printf("(sink=%lld, 值含循环/调用开销，非纯指令周期)\n", (long long)sink);
    return 0;
}
'''


def main():
    src = C_SRC.replace("TRIRV_VM_PATH", os.path.join(HERE, "trirv_vm.c"))
    cfile = os.path.join(HERE, "bench_div_impl.c")
    binfile = os.path.join(HERE, "bench_div_impl")
    with open(cfile, "w") as f:
        f.write(src)
    cc = None
    for c in ("clang", "gcc", "cc"):
        if subprocess.run(["which", c], capture_output=True).returncode == 0:
            cc = c
            break
    if not cc:
        print("未找到 C 编译器")
        return 1
    r = subprocess.run([cc, "-O2", "-o", binfile, cfile, "-lm"],
                       capture_output=True, text=True)
    if r.returncode:
        print(r.stderr[:2000])
        return 1
    out = subprocess.run([binfile], capture_output=True, text=True, cwd=HERE)
    print(out.stdout)
    os.remove(cfile)
    if os.path.exists(binfile):
        os.remove(binfile)
    return out.returncode


if __name__ == "__main__":
    sys.exit(main())
