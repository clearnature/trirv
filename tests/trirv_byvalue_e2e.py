"""TriRV 结构体按值传参 + sret 返回 端到端验证。

覆盖：struct 按值传参（被调方逐字拷入局部副本、修改隔离）、
struct 返回（sret 隐藏返回缓冲）、struct 赋值/初始化拷贝、
struct 数组元素按值传、参数+返回组合、返回再传参、含 char 成员的
整块拷贝。编译到机器码后在 VM 运行。

关键约定：struct 类型表达式求值结果 = 结构体首地址；
按值传参 = 调用方压源地址、被调方 prologue 逐字拷入本地；
sret = 调用方栈帧预留缓冲、a0 传缓冲地址、被调方 return 逐字拷入。
"""
import os
import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import trirv_cc_backend as cc
import trirv_isa as isa
import trirv_vm as vm

S = isa.to_signed_word

# (名称, C 源码, 期望 a0)
CASES = [
    ("按值传参修改隔离",
     "struct P{int x;}; int bump(struct P p){ p.x=p.x+100; return p.x; } "
     "int main(){ struct P s; s.x=7; int r=bump(s); return s.x*10+r; }", 177),
    ("struct 返回 sret",
     "struct P{int x;int y;}; struct P mk(int a,int b){ struct P p; p.x=a; p.y=b; return p; } "
     "int main(){ struct P s=mk(3,4); return s.x*10+s.y; }", 34),
    ("返回值按值隔离",
     "struct P{int x;}; struct P mk(int a){ struct P p; p.x=a; return p; } "
     "int main(){ struct P s=mk(9); s.x=1; struct P t=mk(2); return s.x*10+t.x; }", 12),
    ("struct 赋值整块拷贝",
     "struct P{int x;int y;}; int main(){ struct P a; a.x=5; a.y=6; "
     "struct P b; b=a; b.x=99; return a.x*10+b.y; }", 56),
    ("参数+返回组合",
     "struct P{int x;int y;}; "
     "struct P add(struct P a,struct P b){ struct P r; r.x=a.x+b.x; r.y=a.y+b.y; return r; } "
     "int main(){ struct P p; p.x=1; p.y=2; struct P q; q.x=10; q.y=20; "
     "struct P s=add(p,q); return s.x*100+s.y; }", 1122),
    ("struct 数组元素按值传",
     "struct P{int x;}; int get(struct P p){ return p.x; } "
     "int main(){ struct P a[3]; a[0].x=4; a[1].x=5; a[2].x=6; "
     "return get(a[0])*100+get(a[1])*10+get(a[2]); }", 456),
    ("多成员含 char 整块拷贝",
     "struct P{int x;char c;int y;}; struct P mk(int a){ struct P p; p.x=a; p.c=65; p.y=a*2; return p; } "
     "int main(){ struct P s=mk(5); return s.x+s.y+s.c; }", 80),
    ("返回再传参",
     "struct P{int x;}; struct P mk(int a){ struct P p; p.x=a; return p; } "
     "int twice(struct P p){ return p.x*2; } int main(){ return twice(mk(21)); }", 42),
    ("返回赋给已存在变量",
     "struct P{int x;int y;}; struct P mk(int a,int b){ struct P p; p.x=a; p.y=b; return p; } "
     "int main(){ struct P s; s.x=1; s.y=1; s=mk(8,9); return s.x*10+s.y; }", 89),
    ("嵌套 struct 字段按值传",
     "struct I{int v;}; struct O{struct I in;int k;}; "
     "int get(struct I q){ return q.v; } "
     "int main(){ struct O o; o.in.v=33; o.k=2; return get(o.in)*o.k; }", 66),
    ("sret 后成员读取",
     "struct P{int x;int y;}; struct P mk(){ struct P p; p.x=12; p.y=34; return p; } "
     "int main(){ struct P s=mk(); return s.x*100+s.y; }", 1234),
    ("多实例各自隔离",
     "struct P{int x;}; struct P mk(int a){ struct P p; p.x=a; return p; } "
     "int main(){ struct P a=mk(1); struct P b=mk(2); struct P c=mk(3); "
     "return a.x*100+b.x*10+c.x; }", 123),
    # f().x 链式成员访问：结构体右值（sret 缓冲）直接取成员
    ("f().x 链式读",
     "struct P{int x;int y;}; struct P mk(){ struct P p; p.x=12; p.y=34; return p; } "
     "int main(){ return mk().x; }", 12),
    ("f().y 第二成员",
     "struct P{int x;int y;}; struct P mk(){ struct P p; p.x=12; p.y=34; return p; } "
     "int main(){ return mk().y; }", 34),
    ("两次调用 f().x*10+f().y",
     "struct P{int x;int y;}; struct P mk(){ struct P p; p.x=12; p.y=34; return p; } "
     "int main(){ return mk().x*10+mk().y; }", 154),
    ("带参数 f(a).x",
     "struct P{int x;}; struct P mk(int a){ struct P p; p.x=a; return p; } "
     "int main(){ return mk(9).x; }", 9),
    ("嵌套 f().in.v",
     "struct I{int v;}; struct O{struct I in;int k;}; "
     "struct O mk(){ struct O o; o.in.v=33; o.k=2; return o; } "
     "int main(){ return mk().in.v; }", 33),
]


def compile_run(src, steps=400000):
    prog, labels, txt, strs = cc.compile_c(src)
    for i, w in enumerate(prog):
        assert isa.decode(w)["inst"] is not None, f"pc={i} 非法指令"
    cpu = vm.VM(prog, mem_init=dict(strs or {}))
    cpu.run(max_steps=steps)
    return S(cpu.regs[10]), cpu


def main():
    print("=== TriRV 结构体按值传参 + sret 返回 端到端 ===")
    passed = 0
    for name, csrc, expect in CASES:
        got, cpu = compile_run(csrc)
        good = got == expect
        print(f"  [{'PASS' if good else 'FAIL'}] {name}: {got} (期望 {expect})")
        passed += good
    print("-" * 64)
    print(f"总计通过 {passed}/{len(CASES)}")
    assert passed == len(CASES), "存在失败用例"
    print("全部通过 ✓ struct 按值传参与 sret 返回工作正常")


if __name__ == "__main__":
    main()
