#!/usr/bin/env python3
"""trirv_tri_tcc_e2e —— tri-tcc（TinyCC TriRV 后端）端到端契约（T-1）。

被测：~/ai-lab/tinycc 分支 trirv 的 trirv-tcc 交叉编译器（tri-gen.c/tri-link.c）。
管线：C 源 → trirv-tcc(-nostdlib) → TRV1 → 双 VM（PyVM × C VM）执行，
期望值 = C 语义的计算结果（写死在用例里，对齐 trirv_cc e2e 风格）。

覆盖（T-1.1/1.2 验收）：
  - ALU：+ - * / % & | ^ << >> 与全部六种比较（含 unsigned）
  - load/store 宽度：char/short/int、有符号/无符号（LB/LBU/LH/LHU/LW）
  - 控制流：if/else、while、for、递归、多实参调用
  - 全局变量（初始化/读写）、int/char 数组

运行前提：~/ai-lab/tinycc 存在且已构建 trirv-tcc（TINYCC_PORT_PLAN.md T-0.2）；
缺失时全部 SKIP（不阻断全量回归）。
"""
import os
import struct
import subprocess
import sys

import trirv_vm as vm

HERE = os.path.dirname(os.path.abspath(__file__))
TCC = os.path.join(os.path.dirname(HERE), "tinycc", "trirv-tcc")
CVM = os.path.join(HERE, "trirv_vm")

passed = 0
failed = 0
skipped = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
    else:
        failed += 1
        print("FAIL %s: got=%r want=%r" % (name, got, want))


# (name, c_source, expected_exit&0xFF)
CASES = [
    ("常量返回", "int main(void){ return 42; }", 42),

    # ---- T-1.2 ALU ----
    ("加", "int main(void){ int a=1234; int b=567; return a+b; }", (1234+567)&0xFF),
    ("减", "int main(void){ int a=1000; int b=234; return a-b; }", (1000-234)&0xFF),
    ("乘", "int main(void){ int a=123; int b=456; return a*b; }", (123*456)&0xFF),
    ("除", "int main(void){ int a=100000; int b=7; return a/b; }", (100000//7)&0xFF),
    ("模", "int main(void){ int a=100000; int b=7; return a%b; }", (100000%7)&0xFF),
    # 位运算（变量版）：TriRV AND/OR/XOR 是逐 trit 语义（ISA §5.1 决议 B），
    # C 二进制语义需方案 A 适配层（T-1.3 未做）——本套件锁定当前逐 trit 直译语义。
    # 常量版会被 tccgen 前端以宿主二进制语义折叠（2|1→3），测不到生成代码。
    ("与min", "int main(void){ int a=6; int b=4; return a & b; }", 3),   # min(20₃,11₃)=10₃=3
    ("或max", "int main(void){ int a=2; int b=1; return a | b; }", 2),   # max(2,1)=2
    ("异或mod3", "int main(void){ int a=3; int b=1; return a ^ b; }", 4), # 10₃^01₃=11₃=4
    # 256 & 0xFF = 0（exit 低 8 位）
    ("左移", "int main(void){ return 1 << 8; }", 0),
    ("逻辑右移", "int main(void){ unsigned u = 0x80000000u; return (int)(u >> 31); }", 1),
    ("算术右移", "int main(void){ int a = -64; return a >> 3; }", (-64 >> 3) & 0xFF),
    ("负数", "int main(void){ return -100; }", (-100) & 0xFF),

    # ---- 比较（真=1） ----
    ("相等真", "int main(void){ return 3 == 3; }", 1),
    ("相等假", "int main(void){ return 3 == 4; }", 0),
    ("不等", "int main(void){ return 3 != 4; }", 1),
    ("小于", "int main(void){ return 3 < 4; }", 1),
    ("大于等于", "int main(void){ return 4 >= 4; }", 1),
    ("无符号比较", "int main(void){ unsigned a = 3000000000u; return a > 5; }", 1),

    # ---- T-1.1 load/store 宽度 ----
    ("char 有符号负", "int main(void){ signed char c = -5; return c; }", (-5)&0xFF),
    ("char 无符号回绕", "int main(void){ unsigned char c = 200; return c; }", 200),
    ("short 宽度", "int main(void){ short s = 12345; return s; }", 12345&0xFF),
    ("short 负", "int main(void){ short s = -1000; return s; }", (-1000)&0xFF),
    ("char 数组读写", "int main(void){ char b[4]; b[0]=65; b[1]=66; b[3]=68; return b[0]+b[1]+b[3]-b[2]; }", (65+66+68)&0xFF),
    ("short 数组", "int main(void){ short a[3]; a[0]=1000; a[1]=2000; a[2]=a[0]+a[1]; return a[2]; }", 3000&0xFF),
    ("int 数组", "int main(void){ int a[4]; int i=0; while(i<4){ a[i]=i*i; i=i+1; } return a[1]+a[2]+a[3]; }", 14),

    # ---- 控制流 ----
    ("while 求和", "int main(void){ int i=0; int s=0; while(i<3){ s=s+i; i=i+1; } return s; }", 3),
    ("for 求和", "int main(void){ int s=0; int i; for(i=1;i<=20;i=i+1) s=s+i; return s; }", 210),
    ("if-else 双路径", "int main(void){ int x=5; if(x>3) return 1; else return 2; }", 1),
    ("递归 fib10", "int fib(int n){ if(n<2) return n; return fib(n-1)+fib(n-2); } int main(void){ return fib(10); }", 55),
    ("三实参", "int add3(int a,int b,int c){ return a+b+c; } int main(void){ return add3(100,20,3); }", 123),
    ("深实参调用", "int f8(int a,int b,int c,int d,int e,int f,int g,int h){ return a+b+c+d+e+f+g+h; } int main(void){ return f8(1,2,3,4,5,6,7,8); }", 36),

    # ---- 全局 ----
    ("全局初始化读写", "int g = 7; int main(void){ g = g + 1; return g; }", 8),
    ("全局跨函数", "int g = 100; void bump(void){ g = g + 5; } int main(void){ bump(); bump(); return g; }", 110),
    ("全局数组", "int ga[3] = {10, 20, 30}; int main(void){ return ga[0]+ga[1]+ga[2]; }", 60),
    ("char* 字符串", "char *m = \"AB\"; int main(void){ return m[0]+m[1]; }", (65+66)&0xFF),

    # ---- 组合 ----
    ("表达式嵌套", "int main(void){ return (1+2)*(3+4)-(5-3); }", 19),
    ("混合宽度", "int main(void){ char c = 10; short s = 1000; int i = 100000; return c + s + (i/1000); }", (10+1000+100)&0xFF),
]


def load_trv(path):
    data = open(path, "rb").read()
    n_inst, n_seg = struct.unpack_from("<II", data, 8)
    prog = list(struct.unpack_from("<%dI" % n_inst, data, 16))
    off = 16 + 4 * n_inst
    mem = {}
    for _ in range(n_seg):
        addr, ln = struct.unpack_from("<II", data, off)
        off += 8
        mem[addr] = list(data[off:off + ln])
        off += ln
    return prog, mem


def main():
    global skipped
    if not os.path.exists(TCC):
        print("tri-tcc 未构建（%s 不存在）—— 全部 SKIP" % TCC)
        print("\ntri-tcc 契约：0 PASS / 0 FAIL / %d SKIP" % (len(CASES) * 2))
        sys.exit(0)

    for name, src, want in CASES:
        cpath = "/tmp/tri_e2e_%s.c" % abs(hash(name))
        tpath = "/tmp/tri_e2e_%s.trv" % abs(hash(name))
        with open(cpath, "w") as f:
            f.write(src)
        r = subprocess.run([TCC, "-nostdlib", cpath, "-o", tpath],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            check("%s [编译]" % name, r.stderr.strip()[:120], "")
            continue
        # C VM
        rr = subprocess.run([CVM, tpath], capture_output=True, text=True, timeout=60)
        ce = rr.stderr.split("exit=")[-1].split()[0] if "exit=" in rr.stderr else "?"
        check("%s [CVM]" % name, int(ce) & 0xFF if ce != "?" else "?", want)
        # PyVM
        prog, mem = load_trv(tpath)
        c = vm.VM(prog, mem_init=mem).run(max_steps=50000000)
        check("%s [PyVM]" % name, c.exit_code & 0xFF, want)

    print("\ntri-tcc 契约：%d PASS / %d FAIL / %d 用例" % (passed, failed, len(CASES)))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
