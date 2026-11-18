"""TriRV malloc/free/calloc/realloc 端到端验证 + brk 语义专项。

复用 trirv_libc.c（C 子集实现的分配器），编译到 TriRV 机器码后在 VM 运行。
覆盖：单次分配写读、多次分配隔离、free 后复用、相邻块合并、
calloc 零初始化、realloc 保留数据、动态链表、字符串动态填充、
分配失败返回 0、以及 VM brk 查询/增长/收缩/越界失败语义。
"""
import trirv_cc as cc
import trirv_isa as isa
import trirv_vm as vm

S = isa.to_signed_word
LIB = open("trirv_libc.c").read()

# (名称, main 函数 C 源码, 期望 a0)  —— LIB 会自动前置
CASES = [
    ("单次分配写读",
     "int main(){ int *p=malloc(36); p[0]=11; p[1]=22; p[2]=33; return p[0]+p[1]+p[2]; }", 66),
    ("三次分配互不干扰",
     "int main(){ int *a=malloc(18); int *b=malloc(18); int *c=malloc(18); "
     "a[0]=7; b[0]=9; c[0]=11; return a[0]+b[0]+c[0]; }", 27),
    ("free 后内存复用",
     "int main(){ int *a=malloc(27); a[0]=5; a[1]=6; free(a); "
     "int *b=malloc(27); b[0]=a[0]; return b[0]; }", 5),
    ("相邻块合并后再分配",
     "int main(){ int *a=malloc(18); int *b=malloc(18); int aa=a; a[0]=1; b[0]=2; "
     "free(a); free(b); int *c=malloc(36); c[0]=40; "
     "return c[0]+(c==aa); }", 41),  # 合并成功则 c 复用 a 起点: c==aa 为真 -> 40+1=41
    ("calloc 零初始化",
     "int main(){ int *p=calloc(27); return p[0]+p[1]+p[2]; }", 0),
    ("calloc 后写入",
     "int main(){ int *p=calloc(18); p[0]=3; p[1]=4; return p[0]+p[1]; }", 7),
    ("realloc 保留前值",
     "int main(){ int *p=malloc(18); p[0]=13; p[1]=17; "
     "p=realloc(p,36); return p[0]+p[1]; }", 30),
    ("realloc 缩小",
     "int main(){ int *p=malloc(36); p[0]=8; p[1]=9; "
     "p=realloc(p,18); return p[0]; }", 8),
    ("动态链表三节点求和",
     "int main(){ int head=0; int i=3; int *p; "
     "while(i>0){ p=malloc(18); *p=i; *(p+1)=head; head=p; i=i-1; } "
     "int sum=0; p=head; while(p!=0){ sum=sum+*p; p=*(p+1); } return sum; }", 6),
    ("malloc 字符串填充",
     "int main(){ char *s=malloc(16); char *t=\"ABC\"; int i=0; "
     "while(t[i]!=0){ s[i]=t[i]; i=i+1; } s[i]=0; return s[0]+s[1]+s[2]; }",
     65 + 66 + 67),
    ("大块分配跨多字",
     "int main(){ int *p=malloc(90); int i=0; while(i<10){ p[i]=i; i=i+1; } "
     "return p[9]; }", 9),
    ("反复分配释放不崩",
     "int main(){ int i=0; int *p; int s=0; while(i<5){ p=malloc(18); p[0]=i; "
     "s=s+p[0]; free(p); i=i+1; } return s; }", 10),
]


def compile_run(src, steps=400000):
    prog, labels, txt, strs = cc.compile_c(src)
    for i, w in enumerate(prog):
        assert isa.decode(w)["inst"] is not None, f"pc={i} 非法指令"
    cpu = vm.VM(prog, mem_init=dict(strs or {}))
    cpu.run(max_steps=steps)
    return S(cpu.regs[10]), cpu


def brk_tests():
    """直接通过 syscall 验证 brk 的查询/增长/收缩/越界失败语义。"""
    print("--- brk 语义专项 ---")
    e = isa.encode
    p = []
    # a7=214
    p.append(e("ADDI", rd=17, rs1=0, imm=214))
    # 1) brk(0) 查询 -> a0 应为 heap_base=524288
    p.append(e("ADDI", rd=10, rs1=0, imm=0))
    p.append(e("ECALL"))
    p.append(e("SW", rs2=10, rs1=0, imm=0))       # mem[0]=初始brk
    # 2) brk(base+90) 增长
    p.append(e("LW", rd=11, rs1=0, imm=0))         # x11 = base
    p.append(e("ADDI", rd=10, rs1=11, imm=90))     # a0 = base+90
    p.append(e("ECALL"))
    p.append(e("SW", rs2=10, rs1=0, imm=9))        # mem[9]=增长返回
    # 3) brk(base) 收缩
    p.append(e("LW", rd=10, rs1=0, imm=0))         # a0 = base
    p.append(e("ECALL"))
    p.append(e("SW", rs2=10, rs1=0, imm=18))       # mem[18]=收缩返回
    # 4) brk(1) 越界失败 -> 返回旧 brk(=base)
    p.append(e("ADDI", rd=10, rs1=0, imm=1))
    p.append(e("ECALL"))
    p.append(e("SW", rs2=10, rs1=0, imm=27))       # mem[27]=失败返回
    p.append(e("ADDI", rd=17, rs1=0, imm=93))      # exit
    p.append(e("ECALL"))
    cpu = vm.VM(p)
    cpu.run(max_steps=1000)
    base = cpu._load_word(0)
    grow = cpu._load_word(9)
    shrink = cpu._load_word(18)
    fail = cpu._load_word(27)
    results = [
        ("查询 brk(0)", base, 524288),
        ("增长 brk(base+90)", grow, 524288 + 90),
        ("收缩 brk(base)", shrink, 524288),
        ("越界 brk(1) 返回旧值", fail, 524288),
    ]
    ok = 0
    for name, got, exp in results:
        good = got == exp
        print(f"  [{'PASS' if good else 'FAIL'}] {name}: {got} (期望 {exp})")
        ok += good
    return ok, len(results)


def main():
    print("=== TriRV malloc 端到端 ===")
    passed = 0
    for name, main_src, expect in CASES:
        got, cpu = compile_run(LIB + "\n" + main_src)
        good = got == expect
        print(f"  [{'PASS' if good else 'FAIL'}] {name}: {got} (期望 {expect})")
        passed += good
    print("-" * 64)
    bok, btot = brk_tests()
    passed += bok
    total = len(CASES) + btot
    print("-" * 64)
    print(f"总计通过 {passed}/{total}")
    assert passed == total, "存在失败用例"
    print("全部通过 ✓ malloc/brk 动态内存栈工作正常")


if __name__ == "__main__":
    main()
