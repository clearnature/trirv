"""TriRV C 编译器端到端验证。

流程: C 源码 -> trirv_cc 编译 -> trirv_asm 汇编 -> trirv_isa 机器码 -> trirv_vm 执行
覆盖: 算术/比较/逻辑、while/for/if、break/continue、函数、递归。
"""
import trirv_cc_backend as cc
import trirv_isa as isa
import trirv_vm as vm

S = isa.to_signed_word


def compile_run(src, steps=300000, stdin=b""):
    prog, labels, asmtext, strings = cc.compile_c(src)
    cpu = vm.VM(prog, mem_init=strings or None, stdin=stdin).run(max_steps=steps)
    # 校验每条指令都能解码
    for i, w in enumerate(prog):
        assert isa.decode(w)["inst"] is not None, f"pc={i} 非法指令"
    return S(cpu.regs[10]), cpu


CASES = [
    ("算术 + - * / %", "int main(){ return 7*3 + 8/2 - 4; }", 21),
    ("比较运算", "int main(){ return (3<5) + (5<=5)*2 + (7==7)*4 + (8!=8); }", 7),
    ("逻辑 && || !", "int main(){ return (1&&1) + (0||1)*2 + (!0)*4; }", 7),
    ("while 累加", "int main(){ int s=0; int i=1; while(i<=10){ s=s+i; i=i+1; } return s; }", 55),
    ("for 累加", "int main(){ int s=0; int i=0; for(i=1;i<=10;i=i+1){ s=s+i; } return s; }", 55),
    ("if/else", "int main(){ int x=7; int r; if(x>5){ r=100; } else { r=0; } return r; }", 100),
    ("break/continue",
     "int main(){ int s=0; int i=0; for(i=0;i<10;i=i+1){ if(i%2==0){continue;} if(i>6){break;} s=s+i; } return s; }",
     1 + 3 + 5),
    ("函数参数", "int add(int a,int b){ return a+b; } int main(){ return add(20,22); }", 42),
    ("递归阶乘", "int fact(int n){ if(n<=1){return 1;} return n*fact(n-1); } int main(){ return fact(6); }", 720),
    ("递归斐波那契", "int fib(int n){ if(n<2){return n;} return fib(n-1)+fib(n-2); } int main(){ return fib(12); }", 144),
]


def main():
    print("=" * 60)
    print("TriRV C 编译器端到端验证")
    print("C 源码 -> 编译器 -> 汇编器 -> 机器码 -> VM 执行")
    print("=" * 60)
    passed = 0
    for name, src, expect in CASES:
        try:
            got, cpu = compile_run(src)
            ok = got == expect
            mark = "PASS" if ok else "FAIL"
            print(f"  [{mark}] {name:16s} = {got:5d} (期望 {expect})  steps={cpu.steps}")
            if ok:
                passed += 1
            else:
                assert False
        except Exception as e:
            print(f"  [FAIL] {name:16s} 异常: {e}")
    print("-" * 60)
    print(f"基础用例通过 {passed}/{len(CASES)}")
    passed += test_syscalls()

    # 展示一个完整编译产物（阶乘）作为工具链能力样本
    demo = "int fact(int n){ if(n<=1){return 1;} return n*fact(n-1); } int main(){ return fact(5); }"
    prog, labels, txt, strs = cc.compile_c(demo)
    print(f"\n编译样本 fact(5): 生成 {len(prog)} 条 20-trit 指令，符号表 {labels}")
    print("前 12 条汇编:")
    for line in [l for l in txt.splitlines() if l.strip()][:12]:
        print("   " + line.strip())
    total = len(CASES) + 4
    print(f"\n总计通过 {passed}/{total}")
    assert passed == total, "存在失败用例"
    print("全部通过 ✓ 工具链已具备编译 C + syscall 的 libc 地基能力")


def test_syscalls():
    """syscall ABI 专项：write 输出、read 输入、exit 退出码、返回值。"""
    print("-" * 60)
    print("syscall ABI 专项:")
    ok_count = 0

    # 1) write(1, "Hello, TriRV!\n", 14) + return 42
    src = 'int main(){ write(1, "Hello, TriRV!\\n", 14); return 42; }'
    got, cpu = compile_run(src)
    out = bytes(cpu.output)
    p1 = out == b"Hello, TriRV!\n" and got == 42 and cpu.exit_code == 42
    print(f"  [{'PASS' if p1 else 'FAIL'}] write 输出={out!r} 返回值={got} 退出码={cpu.exit_code}")
    ok_count += p1

    # 2) write 返回写入字节数
    src2 = 'int main(){ return write(1, "ABCD", 4); }'
    got2, cpu2 = compile_run(src2)
    p2 = got2 == 4 and bytes(cpu2.output) == b"ABCD"
    print(f"  [{'PASS' if p2 else 'FAIL'}] write 返回值={got2} (期望4) 输出={bytes(cpu2.output)!r}")
    ok_count += p2

    # 3) read 从 stdin 读取 + write 回显
    src3 = 'int main(){ int n = read(0, 200, 5); write(1, 200, n); return n; }'
    got3, cpu3 = compile_run(src3, stdin=b"XYZ\n")
    p3 = got3 == 4 and bytes(cpu3.output) == b"XYZ\n"
    print(f"  [{'PASS' if p3 else 'FAIL'}] read 读取={got3} 回显={bytes(cpu3.output)!r}")
    ok_count += p3

    # 4) exit(code) 中途停机：exit(9) 后的 return 不执行
    src4 = 'int main(){ write(1, "X", 1); exit(9); return 0; }'
    got4, cpu4 = compile_run(src4)
    p4 = cpu4.exit_code == 9 and bytes(cpu4.output) == b"X"
    print(f"  [{'PASS' if p4 else 'FAIL'}] exit 退出码={cpu4.exit_code} (期望9) 输出={bytes(cpu4.output)!r}")
    ok_count += p4

    assert ok_count == 4, "syscall 用例未全过"
    return ok_count


if __name__ == "__main__":
    main()
