"""TriRV 一维数组端到端验证（阶段 A）。

覆盖：int/char 数组声明与下标读写、循环填充/求和、数组名退化为指针、
形参数组退化、数组传址修改、多数组栈帧共存、char 数组当字符串、
数组与 malloc 混用。全部编译到 TriRV 机器码后在 VM 运行。
"""
import trirv_cc_backend as cc
import trirv_isa as isa
import trirv_vm as vm

S = isa.to_signed_word
LIB = open("trirv_libc.c").read()

# (名称, C 源码, 期望 a0)；带 LIB 前缀的用例在 src 内置
CASES = [
    ("数组下标写读",
     "int main(){ int a[5]; a[0]=11; a[1]=22; a[2]=33; return a[0]+a[1]+a[2]; }", 66),
    ("数组循环填充求和",
     "int main(){ int a[5]; int i=0; while(i<5){ a[i]=i; i=i+1; } "
     "int s=0; i=0; while(i<5){ s=s+a[i]; i=i+1; } return s; }", 10),
    ("char 数组下标",
     "int main(){ char b[4]; b[0]=65; b[1]=66; b[2]=67; return b[0]+b[2]; }", 132),
    ("数组名退化为指针",
     "int main(){ int a[3]; a[0]=7; a[1]=8; int *p=a; return p[0]+*(p+1); }", 15),
    ("指针遍历数组",
     "int main(){ int a[4]; int i=0; while(i<4){ a[i]=i+1; i=i+1; } "
     "int *p=a; int s=0; i=0; while(i<4){ s=s+p[i]; i=i+1; } return s; }", 10),
    ("形参数组求和",
     "int sum(int a[], int n){ int s=0; int i=0; while(i<n){ s=s+a[i]; i=i+1; } return s; } "
     "int main(){ int x[4]; x[0]=1; x[1]=2; x[2]=3; x[3]=4; return sum(x,4); }", 10),
    ("数组传址被修改",
     "int inc(int a[], int n){ int i=0; while(i<n){ a[i]=a[i]+1; i=i+1; } return 0; } "
     "int main(){ int x[3]; x[0]=10; x[1]=20; x[2]=30; inc(x,3); "
     "return x[0]+x[1]+x[2]; }", 63),
    ("两数组栈帧共存",
     "int main(){ int a[3]; int b[3]; int i=0; while(i<3){ a[i]=i+1; b[i]=10+i; i=i+1; } "
     "return a[2]+b[2]; }", 15),
    ("char 数组当字符串",
     "int main(){ char s[4]; s[0]=72; s[1]=73; s[2]=0; int n=0; "
     "while(s[n]!=0){ n=n+1; } return n; }", 2),
    ("数组与 malloc 混用",
     "int main(){ int stk[3]; stk[0]=5; stk[1]=6; stk[2]=7; "
     "int *h=malloc(27); h[0]=stk[0]; h[1]=stk[1]; h[2]=stk[2]; "
     "return h[0]+h[1]+h[2]; }", 18, True),
    ("数组逆序写入",
     "int main(){ int a[5]; int i=0; while(i<5){ a[4-i]=i; i=i+1; } "
     "return a[0]+a[4]; }", 4),
]

# 带 LIB 前缀的用例标记：三元组扩展为四元组
CASES = [c if len(c) == 4 else (c[0], c[1], c[2], False) for c in CASES]


def compile_run(src, steps=400000):
    prog, labels, txt, strs = cc.compile_c(src)
    for i, w in enumerate(prog):
        assert isa.decode(w)["inst"] is not None, f"pc={i} 非法指令"
    cpu = vm.VM(prog, mem_init=dict(strs or {}))
    cpu.run(max_steps=steps)
    return S(cpu.regs[10]), cpu


def main():
    print("=== TriRV 一维数组 端到端 ===")
    passed = 0
    for name, main_src, expect, need_lib in CASES:
        src = (LIB + "\n" + main_src) if need_lib else main_src
        got, cpu = compile_run(src)
        good = got == expect
        print(f"  [{'PASS' if good else 'FAIL'}] {name}: {got} (期望 {expect})")
        passed += good
    print("-" * 64)
    print(f"总计通过 {passed}/{len(CASES)}")
    assert passed == len(CASES), "存在失败用例"
    print("全部通过 ✓ 一维数组类型工作正常")


if __name__ == "__main__":
    main()
