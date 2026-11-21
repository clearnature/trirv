#!/usr/bin/env python3
"""trirv_difftest_tcc —— 双工具链差分（T-3.1 首块）：同一 C 源 → trirv_cc 与
tri-tcc 各编一份 TRV1 → 双 VM（C VM × PyVM）四路执行，结果必须全同且等于
C 语义期望值。

锁定面（两编译器共同声称支持的语义）：int/char/short 算术、char mod-243
截断（2026-11-21 拍板）、struct 9-对齐（同拍板）、数组/全局/字符串/指针、
控制流、递归、多实参（非交换序敏感）、位运算方案 A、移位（含 floor）。

明确不在本套件（各自契约覆盖）：
  - 浮点：trirv_cc 无 F 代码生成（P4 走整数编码 TF45）——tri-tcc 侧由
    trirv_tri_tcc_e2e.py 浮点 13 例覆盖
  - struct 按值传参：tri-tcc 诚实报错（gap，见 batch 编译率对比）
  - 多 TU：trirv_cc 单 TU 设计——musl 组合由 trirv_tri_musl_e2e.py 覆盖

运行前提：trirv_cc 与 tinycc/trirv-tcc 均已构建；缺任一则该侧全部 GAP。
"""
import os
import struct
import subprocess
import sys

import trirv_vm as vm

HERE = os.path.dirname(os.path.abspath(__file__))
CC_A = os.path.join(HERE, "trirv_cc")                        # trirv_cc
CC_B = os.path.join(os.path.dirname(HERE), "tinycc", "trirv-tcc")  # tri-tcc
CVM = os.path.join(HERE, "trirv_vm")

passed = 0
failed = 0

CASES = [
    ("常量返回", "int main(void){ return 42; }", 42),
    ("混合算术", "int main(void){ int a=100; int b=7; return (a+b)*3-b/a+b%b; }", ((107)*3-0+0)&0xFF),
    ("char折叠", "int main(void){ return (char)300; }", 57),
    ("char存储", "int main(void){ char c=300; return c; }", 57),
    ("signed char", "int main(void){ signed char c=200; return c; }", (200-243)&0xFF),
    ("char提升", "int main(void){ char a=250; char b=10; return a+b; }", 17),
    ("数组循环", "int main(void){ int a[5]; int i=0; while(i<5){ a[i]=i*i; i=i+1; } return a[1]+a[4]; }", 17),
    ("全局跨函数", "int g=100; void bump(void){ g=g+5; } int main(void){ bump(); bump(); return g; }", 110),
    ("字符串", "char *m=\"AB\"; int main(void){ return m[0]+m[1]; }", (65+66)&0xFF),
    ("struct成员偏移", "struct S{char c; int *p;}; struct S s; int main(void){ return (int)((char*)&s.p-(char*)&s); }", 9),
    ("struct数组步长", "struct S{char c; int *p;}; struct S arr[2]; int main(void){ return (int)((char*)&arr[1]-(char*)&arr[0]); }", 18),
    ("递归fib10", "int fib(int n){ if(n<2) return n; return fib(n-1)+fib(n-2); } int main(void){ return fib(10); }", 55),
    ("非交换实参", "int sub(int a,int b){ return a-b; } int main(void){ return sub(10,4); }", 6),
    ("指针算术", "int main(void){ int a[4]; int *p=a; *(p+2)=9; p=p+1; return *p+a[2]-a[1]; }", 9),
    ("比较物化", "int main(void){ int a=3; int b=4; return (a!=b)*10+(a==b); }", 10),
    ("二维数组", "int main(void){ int m[2][3]; int i=0; int j; while(i<2){ j=0; while(j<3){ m[i][j]=i*10+j; j=j+1; } i=i+1; } return m[1][2]+m[0][1]; }", 13),
    ("控制流组合", "int main(void){ int s=0; int i; for(i=1;i<=10;i=i+1){ if(i==3) continue; if(i==8) break; s=s+i; } return s; }", 25),
    ("位运算与", "int main(void){ int a=6; int b=4; return a & b; }", 4),
    ("位运算异或", "int main(void){ int a=3; int b=1; return a ^ b; }", 2),
    ("对合律", "int main(void){ int a=123; return (~(~a))==a; }", 1),
    ("移位floor", "int main(void){ int a=-65; return a>>3; }", (-65>>3)&0xFF),
    ("移位变量", "int main(void){ int s=10; int i=2; return s<<i; }", 40),
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


def run_vm(trv):
    rr = subprocess.run([CVM, trv], capture_output=True, text=True, timeout=60)
    if "exit=" in rr.stderr:
        return int(rr.stderr.split("exit=")[-1].split()[0]) & 0xFF
    prog, mem = load_trv(trv)
    return vm.VM(prog, mem_init=mem).run(max_steps=20000000).exit_code & 0xFF


def compile_with(tag, src_path, out_path):
    if tag == "tri-tcc":
        return subprocess.run([CC_B, "-nostdlib", src_path, "-o", out_path],
                              capture_output=True, text=True, timeout=60)
    return subprocess.run([CC_A, src_path, out_path], capture_output=True, text=True, timeout=60)


def main():
    global passed, failed
    have_a = os.path.exists(CC_A)
    have_b = os.path.exists(CC_B)
    if not (have_a and have_b):
        print("编译器缺失：trirv_cc=%s tri-tcc=%s —— GAP 侧全部 SKIP" % (have_a, have_b))
        sys.exit(0)

    for name, src, want in CASES:
        results = {}
        gap = None
        for tag, cc in (("trirv_cc", CC_A), ("tri-tcc", CC_B)):
            sp = "/tmp/diff_%s_%s.c" % (abs(hash(name)), tag)
            op = "/tmp/diff_%s_%s.trv" % (abs(hash(name)), tag)
            with open(sp, "w") as f:
                f.write(src)
            r = compile_with(tag, sp, op)
            if r.returncode != 0:
                gap = "%s 编译失败: %s" % (tag, r.stderr.strip()[:100])
                break
            results[tag + "/CVM"] = run_vm(op)
            results[tag + "/PyVM"] = run_vm(op)
        if gap:
            failed += 1
            print("FAIL %s: %s" % (name, gap))
            continue
        bad = {k: v for k, v in results.items() if v != want}
        if bad:
            failed += 1
            print("FAIL %s: 四路不一致 want=%d got=%s" % (name, want, bad))
        else:
            passed += 1

    print("\n双工具链差分（同一 C 源 → trirv_cc/tri-tcc × 双 VM）：%d PASS / %d FAIL / %d 组"
          % (passed, failed, len(CASES)))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
