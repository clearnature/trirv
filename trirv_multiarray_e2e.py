"""TriRV 多维数组 + 初始化列表 端到端验证。

覆盖：二维/三维数组读写与遍历、二维数组形参首维退化、char 二维数组、
结构体数组、初始化列表（一维/多维、嵌套/扁平、struct/struct 数组、
部分初始化补零、标量花括号），与 malloc 混用。编译后在 VM 运行。

注：类型串维度按 C 顺序 [行][列]，ty_elem 取最左维；下标链逐层退化。
"""
import trirv_cc_backend as cc
import trirv_isa as isa
import trirv_vm as vm

S = isa.to_signed_word
LIB = open("trirv_libc.c").read()

CASES = [
    ("二维下标读写",
     "int main(){ int a[2][3]; a[1][2]=42; return a[1][2]; }", 42, False),
    ("二维遍历求和",
     "int main(){ int a[2][3]; int i=0; int j=0; "
     "while(i<2){ j=0; while(j<3){ a[i][j]=i*10+j; j=j+1; } i=i+1; } "
     "int s=0; i=0; while(i<2){ j=0; while(j<3){ s=s+a[i][j]; j=j+1; } i=i+1; } return s; }", 36, False),
    ("三维下标",
     "int main(){ int a[2][2][2]; a[1][1][1]=99; return a[1][1][1]; }", 99, False),
    ("二维初始化-嵌套",
     "int main(){ int a[2][3]={{1,2,3},{4,5,6}}; return a[0][0]+a[1][2]; }", 7, False),
    ("二维初始化-扁平",
     "int main(){ int a[2][3]={1,2,3,4,5,6}; return a[1][2]; }", 6, False),
    ("一维初始化列表",
     "int main(){ int a[5]={10,20,30}; return a[0]+a[2]+a[4]; }", 40, False),
    ("char 数组初始化",
     "int main(){ char s[5]={72,73,0}; return s[0]+s[1]+s[2]; }", 145, False),
    ("char 二维数组",
     "int main(){ char g[2][3]={{65,66,67},{68,69,70}}; return g[0][0]+g[1][2]; }", 135, False),
    ("部分初始化补零",
     "int main(){ int a[5]={7,8}; return a[0]+a[1]+a[2]+a[4]; }", 15, False),
    ("标量花括号初始化",
     "int main(){ int x={42}; return x; }", 42, False),
    ("struct 初始化列表",
     "struct P{int x;int y;}; int main(){ struct P p={3,4}; return p.x+p.y; }", 7, False),
    ("struct 数组初始化",
     "struct P{int x;int y;}; int main(){ struct P a[2]={{1,2},{3,4}}; return a[0].x+a[1].y; }", 5, False),
    ("三维初始化列表",
     "int main(){ int a[2][2][2]={{{1,2},{3,4}},{{5,6},{7,8}}}; return a[1][1][1]; }", 8, False),
    ("二维形参首维退化",
     "int sum(int a[][3]){ int s=0; int j=0; while(j<3){ s=s+a[0][j]+a[1][j]; j=j+1; } return s; } "
     "int main(){ int m[2][3]={{1,2,3},{4,5,6}}; return sum(m); }", 21, False),
    ("二维形参与 malloc 混用",
     "int main(){ int m[2][2]={{1,2},{3,4}}; int *h=malloc(36); "
     "h[0]=m[0][0]; h[1]=m[1][1]; return h[0]+h[1]; }", 5, True),
]


def compile_run(src, steps=400000):
    prog, labels, txt, strs = cc.compile_c(src)
    for i, w in enumerate(prog):
        assert isa.decode(w)["inst"] is not None, f"pc={i} 非法指令"
    cpu = vm.VM(prog, mem_init=dict(strs or {}))
    cpu.run(max_steps=steps)
    return S(cpu.regs[10]), cpu


def main():
    print("=== TriRV 多维数组 + 初始化列表 端到端 ===")
    passed = 0
    for name, csrc, expect, need_lib in CASES:
        src = (LIB + "\n" + csrc) if need_lib else csrc
        got, cpu = compile_run(src)
        good = got == expect
        print(f"  [{'PASS' if good else 'FAIL'}] {name}: {got} (期望 {expect})")
        passed += good
    print("-" * 64)
    print(f"总计通过 {passed}/{len(CASES)}")
    assert passed == len(CASES), "存在失败用例"
    print("全部通过 ✓ 多维数组与初始化列表工作正常")


if __name__ == "__main__":
    main()
