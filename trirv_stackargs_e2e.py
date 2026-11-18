#!/usr/bin/env python3
"""TriRV 栈参数传递 ABI 端到端测试。

验证第 9+ 参数走栈块、va_arg 跨寄存器/栈边界、sret+多参数、
struct 按值传参与多参数混合。全部回归保持全绿。
"""
import os
import trirv_cc_backend as cc
import trirv_isa as isa
import trirv_vm as vm

S = isa.to_signed_word
_HERE = os.path.dirname(os.path.abspath(__file__))   # 可移植：相对脚本目录，原硬编码 /workspace
LIB = open(os.path.join(_HERE, "trirv_stdio.c")).read()


def run(src, steps=900000):
    prog, lbl, txt, strs = cc.compile_c(src)
    for i, w in enumerate(prog):
        assert isa.decode(w)["inst"] is not None, f"pc={i} 非法指令"
    cpu = vm.VM(prog, mem_init=dict(strs or {}))
    cpu.run(max_steps=steps)
    return S(cpu.regs[10]), bytes(cpu.output).decode("latin1"), cpu


# (名称, 源码, 期望返回值, 期望输出)
CASES = [
    ("9参数求和",
     "int f(int a,int b,int c,int d,int e,int f,int g,int h,int i)"
     "{return a+b+c+d+e+f+g+h+i;} int main(){return f(1,2,3,4,5,6,7,8,9);}",
     45, ""),

    ("10参数混合",
     "int f(int a,int b,int c,int d,int e,int f,int g,int h,int i,int j)"
     "{return a*100+j*10+i;} int main(){return f(1,2,3,4,5,6,7,8,9,10);}",
     209, ""),

    ("12参数求和",
     "int f(int a,int b,int c,int d,int e,int f,int g,int h,int i,int j,int k,int l)"
     "{return a+b+c+d+e+f+g+h+i+j+k+l;} int main(){return f(1,2,3,4,5,6,7,8,9,10,11,12);}",
     78, ""),

    ("8参数仍走寄存器",
     "int f(int a,int b,int c,int d,int e,int f,int g,int h)"
     "{return a+b+c+d+e+f+g+h;} int main(){return f(1,2,3,4,5,6,7,8);}",
     36, ""),

    ("跨边界变参(1固定+8变参)",
     "int sum(int n,...){va_list ap;va_start(ap);int s=0;int i=0;"
     "while(i<n){s=s+va_arg(ap,int);i=i+1;}va_end(ap);return s;} "
     "int main(){return sum(8,1,2,3,4,5,6,7,8);}",
     36, ""),

    ("10个变参",
     "int sum(int n,...){va_list ap;va_start(ap);int s=0;int i=0;"
     "while(i<n){s=s+va_arg(ap,int);i=i+1;}va_end(ap);return s;} "
     "int main(){return sum(10,1,2,3,4,5,6,7,8,9,10);}",
     55, ""),

    ("printf 9参数",
     "int main(){return printf(\"%d%d%d%d%d%d%d%d%d\",1,2,3,4,5,6,7,8,9);}",
     9, "123456789"),

    ("printf 混合超8参数",
     "int main(){return printf(\"%s/%d/%d/%d/%d/%d/%d/%d\",\"A\",1,2,3,4,5,6,7);}",
     15, "A/1/2/3/4/5/6/7"),

    ("sret+多参数",
     "struct P{int x;int y;}; struct P mk(int a,int b,int c,int d,int e,int f,int g,int h,int i)"
     "{struct P p;p.x=i;p.y=a+h;return p;} "
     "int main(){struct P s=mk(1,2,3,4,5,6,7,8,9);return s.x*10+s.y;}",
     99, ""),

    ("struct按值传参+多参数",
     "struct P{int x;}; int add(struct P p,int a,int b,int c,int d,int e,int f,int g,int h)"
     "{return p.x+a+b+c+d+e+f+g+h;} "
     "int main(){struct P s;s.x=100;return add(s,1,2,3,4,5,6,7,8);}",
     136, ""),

    ("printf 负数超8参数",
     "int main(){return printf(\"%d,%d,%d,%d,%d,%d,%d,%d,%d\",0-1,0-2,0-3,0-4,0-5,0-6,0-7,0-8,0-9);}",
     26, "-1,-2,-3,-4,-5,-6,-7,-8,-9"),
]


def main():
    passed = 0
    for name, body, er, eo in CASES:
        src = LIB + "\n" + body if "printf" in body or "puts" in body else body
        try:
            g, out, _ = run(src)
            ok = (g == er and out == eo)
        except Exception as ex:
            ok = False
            g, out = f"{type(ex).__name__}: {ex}", ""
        tag = "PASS" if ok else "FAIL"
        print(f"{tag} {name}: ret={g} out={out!r} | 期望 ret={er} out={eo!r}")
        if ok:
            passed += 1
    print(f"\n总计 {passed}/{len(CASES)} 通过")
    if passed == len(CASES):
        print("全部通过 ✓ 栈参数传递 ABI 工作正常")
    else:
        raise SystemExit("存在失败用例")


if __name__ == "__main__":
    main()
