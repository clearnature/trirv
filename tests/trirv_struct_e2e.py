"""TriRV 结构体 + 成员访问 端到端验证（阶段 B）。

覆盖：struct 定义与布局（9 tryte 对齐）、栈上变量、. 与 -> 成员读写、
含 char 混合成员、结构体数组、结构体指针参数、指向结构的指针算术、
与 malloc 联用（手写链表节点）、成员值拷贝。编译到机器码后在 VM 运行。
"""
import trirv_cc_backend as cc
import trirv_isa as isa
import trirv_vm as vm

S = isa.to_signed_word
LIB = open("trirv_libc.c").read()

# (名称, C 源码, 期望 a0, 是否需要 LIB)
CASES = [
    ("结构体成员写读",
     "struct P{int x;int y;}; int main(){ struct P p; p.x=11; p.y=22; return p.x+p.y; }", 33, False),
    ("结构体指针 ->",
     "struct P{int x;int y;}; int main(){ struct P p; struct P *q=&p; q->x=7; q->y=8; return q->x+q->y; }",
     15, False),
    ("含 char 混合成员",
     "struct P{int x;char c;int y;}; int main(){ struct P p; p.x=3; p.c=65; p.y=4; return p.x+p.y+p.c; }",
     72, False),
    ("结构体数组下标成员",
     "struct P{int x;int y;}; int main(){ struct P a[3]; int i=0; "
     "while(i<3){ a[i].x=i; a[i].y=i*2; i=i+1; } return a[2].x+a[2].y; }", 6, False),
    ("结构体指针作参数",
     "struct P{int x;int y;}; int sum(struct P *p){ return p->x+p->y; } "
     "int main(){ struct P s; s.x=4; s.y=6; return sum(&s); }", 10, False),
    ("结构体数组指针算术",
     "struct P{int x;int y;}; int main(){ struct P a[2]; a[0].x=1; a[1].x=2; "
     "struct P *p=a; return p->x+(p+1)->x; }", 3, False),
    ("malloc 结构体链表节点",
     "struct N{int v;struct N*next;}; int main(){ struct N *h=malloc(18); "
     "h->v=5; h->next=0; return h->v; }", 5, True),
    ("成员值拷贝",
     "struct P{int x;int y;}; int main(){ struct P a; struct P b; "
     "a.x=8; a.y=9; b.x=a.x; b.y=a.y; return b.x+b.y; }", 17, False),
    ("嵌套 struct 指针域",
     "struct I{int v;}; struct O{struct I *ip;int k;}; "
     "int main(){ struct I i; i.v=13; struct O o; o.ip=&i; o.k=2; return o.ip->v*o.k; }", 26, False),
    ("结构体多实例隔离",
     "struct P{int x;int y;}; int main(){ struct P a; struct P b; "
     "a.x=1; a.y=2; b.x=10; b.y=20; return a.x+a.y+b.x+b.y; }", 33, False),
]


def compile_run(src, steps=400000):
    prog, labels, txt, strs = cc.compile_c(src)
    for i, w in enumerate(prog):
        assert isa.decode(w)["inst"] is not None, f"pc={i} 非法指令"
    cpu = vm.VM(prog, mem_init=dict(strs or {}))
    cpu.run(max_steps=steps)
    return S(cpu.regs[10]), cpu


def main():
    print("=== TriRV 结构体 + 成员访问 端到端 ===")
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
    print("全部通过 ✓ 结构体类型与成员访问工作正常")


if __name__ == "__main__":
    main()
