"""TriRV 变参函数 + printf 端到端验证。

覆盖：va_list 变参机制（va_start/va_arg/va_end）、多类型变参
(int/char*/char)、变参与普通函数/sret/struct 传参组合、字符字面量、
基于 va_list 实现的 printf 族（%d/%s/%c/%%、负数、零、puts）。

关键约定：变参函数 prologue 转储 a0-a7 到 __argsave 区；va_start 指向
第 N 个参数（N=固定参数个数）后的首个变参；本 ABI 所有参数等宽 9 tryte，
va_arg 按字读取并递增。变参总数上限 8（含固定参数）。
"""
import trirv_cc_backend as cc
import trirv_isa as isa
import trirv_vm as vm

S = isa.to_signed_word
STDIO = open("trirv_stdio.c").read()
LIBC = open("trirv_libc.c").read()

# (名称, C 源码, 期望 a0, 期望输出字符串 或 None, 是否需要 STDIO/LIBC)
CASES = [
    ("变参求和",
     "int sumall(int n, ...){ va_list ap; va_start(ap); int s=0; int i=0; "
     "while(i<n){ s=s+va_arg(ap,int); i=i+1; } va_end(ap); return s; } "
     "int main(){ return sumall(3, 10, 20, 30); }", 60, None, ""),
    ("多类型变参",
     "int mix(int n,...){ va_list ap; va_start(ap); int a=va_arg(ap,int); "
     "char *s=va_arg(ap,char*); int b=va_arg(ap,int); va_end(ap); "
     "return a+s[0]+b; } int main(){ return mix(3, 5, \"Z\", 7); }", 102, None, ""),
    ("变参 char 指针",
     "int firststr(char *tag, ...){ va_list ap; va_start(ap); "
     "char *s=va_arg(ap,char*); va_end(ap); return s[0]; } "
     "int main(){ return firststr(\"hi\",\"ABC\"); }", 65, None, ""),
    ("字符字面量",
     "int main(){ return 'A'; }", 65, None, ""),
    ("printf %d",
     "int main(){ return printf(\"Hi=%d!\", 42); }", 6, "Hi=42!", "stdio"),
    ("printf 混合格式",
     "int main(){ return printf(\"%s/%d/%c/%d%%\", \"ok\", 7, 65, 3); }",
     9, "ok/7/A/3%", "stdio"),
    ("printf 负数",
     "int main(){ return printf(\"neg=%d\", 0-123); }", 8, "neg=-123", "stdio"),
    ("printf 零",
     "int main(){ return printf(\"z=%d\", 0); }", 3, "z=0", "stdio"),
    ("printf 纯字符串",
     "int main(){ return printf(\"plain text\"); }", 10, "plain text", "stdio"),
    ("puts 换行",
     "int main(){ return puts(\"hello\"); }", 6, "hello" + chr(10), "stdio"),
    ("printf 多参数",
     "int main(){ return printf(\"%d%d%d%d%d\", 1,2,3,4,5); }",
     5, "12345", "stdio"),
    ("变参与 malloc 混用",
     "int show(int n,...){ va_list ap; va_start(ap); int s=0; int i=0; "
     "while(i<n){ s=s+va_arg(ap,int); i=i+1; } va_end(ap); return s; } "
     "int main(){ int *p=malloc(18); p[0]=40; p[1]=2; "
     "return show(2, p[0], p[1]); }", 42, None, "libc"),
]


def compile_run(src, steps=600000):
    prog, labels, txt, strs = cc.compile_c(src)
    for i, w in enumerate(prog):
        assert isa.decode(w)["inst"] is not None, f"pc={i} 非法指令"
    cpu = vm.VM(prog, mem_init=dict(strs or {}))
    cpu.run(max_steps=steps)
    return S(cpu.regs[10]), bytes(cpu.output).decode("latin1")


def main():
    print("=== TriRV 变参函数 + printf 端到端 ===")
    passed = 0
    for name, csrc, expect, expect_out, dep in CASES:
        src = csrc
        if dep == "stdio":
            src = STDIO + "\n" + csrc
        elif dep == "libc":
            src = LIBC + "\n" + csrc
        got, out = compile_run(src)
        good = got == expect and (expect_out is None or out == expect_out)
        detail = f"ret={got}" + (f" out={out!r}" if expect_out is not None else "")
        print(f"  [{'PASS' if good else 'FAIL'}] {name}: {detail}")
        passed += good
    print("-" * 64)
    print(f"总计通过 {passed}/{len(CASES)}")
    assert passed == len(CASES), "存在失败用例"
    print("全部通过 ✓ 变参机制与 printf 工作正常")


if __name__ == "__main__":
    main()
