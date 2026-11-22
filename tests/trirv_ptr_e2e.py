"""TriRV 指针/char/统一内存 端到端验证。

覆盖：&x 取址、*p 解引用读写、char 类型、char* 下标与指针算术、
int* 9-tryte 步长、strlen/strcpy 风格循环。验证后回归既有工具链。
"""
import os
import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import trirv_cc_backend as cc
import trirv_isa as isa
import trirv_vm as vm

S = isa.to_signed_word
MOD = isa.MOD_X
WORD = 9   # int* 步长 9 tryte

CASES = [
    # (名称, C 源码, 期望 a0, 预置 int 内存 {base: [ints]})
    ("&x/*p 往返",       "int main(){ int x=42; int *p=&x; return *p; }", 42, None),
    ("*p 写入变量",      "int main(){ int x=1; int *p=&x; *p=99; return x; }", 99, None),
    ("char 变量读写",    "int main(){ char c=65; return c; }", 65, None),
    ("char* s[0]",       "int main(){ char *s=\"ABC\"; return s[0]; }", 65, None),
    ("char* s[2]",       "int main(){ char *s=\"ABC\"; return s[2]; }", 67, None),
    ("*(s+1) 指针算术",  "int main(){ char *s=\"ABC\"; return *(s+1); }", 66, None),
    ("int* p[2] 步长9",  "int main(){ int *p=500; return p[2]; }", 30, {500: [10, 20, 30]}),
    ("*(int*+1) 步长9",  "int main(){ int *p=500; return *(p+1); }", 20, {500: [10, 20, 30]}),
    ("char* p[i] 写读",  "int main(){ char *p=900; p[0]=88; p[1]=89; return p[0]+p[1]; }", 177, None),
    ("strlen 循环",      "int main(){ char *s=\"HELLO\"; int n=0; while(s[n]!=0){ n=n+1; } return n; }", 5, None),
    ("strcpy 后 dst[1]", "int main(){ char *src=\"HI\"; char *dst=800; int i=0; while(src[i]!=0){ dst[i]=src[i]; i=i+1; } dst[i]=0; return dst[1]; }", 73, None),
    ("指针差 p-q",       "int main(){ int a=0; int *p=&a; int *q=&a; return p-q; }", 0, None),
]

def compile_run(src, word_mem=None, steps=200000):
    prog, labels, txt, strs = cc.compile_c(src)
    cpu = vm.VM(prog, mem_init=dict(strs or {}))
    if word_mem:
        for base, vals in word_mem.items():
            for i, v in enumerate(vals):
                cpu._store_word(base + i * WORD, v % MOD)
    cpu.run(max_steps=steps)
    # 校验每条指令可解码
    for i, w in enumerate(prog):
        assert isa.decode(w)["inst"] is not None, f"pc={i} 非法指令"
    return S(cpu.regs[10]), cpu

def main():
    print("=== TriRV 指针/char/统一内存 专项 ===")
    passed = 0
    for name, src, expect, wm in CASES:
        got, cpu = compile_run(src, wm)
        ok = got == expect
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {got} (期望 {expect})")
        if name == "strcpy 后 dst[1]":
            print(f"         dst 内容 = {bytes(cpu.mem[800:803])!r}")
        if name == "char* p[i] 写读":
            print(f"         mem[900:902] = {list(cpu.mem[900:902])}")
        passed += ok
    print("-" * 60)
    print(f"专项通过 {passed}/{len(CASES)}")
    assert passed == len(CASES), "存在失败用例"
    print("全部通过 ✓ 指针/char/统一内存工作正常")

if __name__ == "__main__":
    main()
