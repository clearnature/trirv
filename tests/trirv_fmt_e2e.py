"""TriRV unsigned 类型 + printf 无符号格式端到端验证。

覆盖：
  - unsigned 除法/取模（DIVU/REMU）：对 word > HALF_X（有符号为负、无符号为大正数）按无符号解释
  - unsigned 关系比较（SLTU）
  - unsigned 与 signed 对照（同一 word 两种解释给出不同结果）
  - printf %u / %o / %x / %X / %d 混合，含多位数、零、大 word
  - 编译器 unsigned 与 unsigned int 两种写法等价

关键约定：unsigned 与 int 同为 9 tryte 整字，仅除法/取模/关系比较改用无符号指令；
三补码 word 天然是 mod 3^45 的非负表示，故 %x 等直接按 word 位型展开即可。
"""
import os
import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import trirv_cc_backend as cc
import trirv_isa as isa
import trirv_vm as vm

S = isa.to_signed_word
MOD = isa.MOD_X
STDIO = open(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/cc/trirv_stdio.c").read()

# (名称, C 源码, 期望返回 或 None, 期望输出 或 None, 需要 stdio)
CASES = [
    # --- unsigned 算术/比较（不依赖 stdio）---
    ("unsigned 取模 7%3", "int main(){ unsigned a=7; return a%3; }", 1, None, False),
    ("unsigned 除法 100/7", "int main(){ unsigned a=100; return a/7; }", 14, None, False),
    ("unsigned int 等价写法", "int main(){ unsigned int a=7; return a%3; }", 1, None, False),
    ("unsigned 大 word 比较 >5", "int main(){ unsigned a=0-1; return a>5; }", 1, None, False),
    ("signed 同 word 比较 >5(对照)", "int main(){ int a=0-1; return a>5; }", 0, None, False),
    ("unsigned 加法回绕", "int main(){ unsigned a=0-1; unsigned b=a+1; return b; }", 0, None, False),
    ("unsigned 大 word 除 2", "int main(){ unsigned a=0-1; return a/2; }", S((MOD - 1) // 2), None, False),
    # --- printf 无符号格式 ---
    ("printf %x 两位", "int main(){ return printf(\"x=%x\", 255); }", 4, "x=ff", True),
    ("printf %X 大写", "int main(){ return printf(\"X=%X\", 48879); }", 6, "X=BEEF", True),
    ("printf %o 八进制", "int main(){ return printf(\"o=%o\", 8); }", 4, "o=10", True),
    ("printf %u 十进制", "int main(){ return printf(\"u=%u\", 42); }", 4, "u=42", True),
    ("printf %d 负数(对照)", "int main(){ return printf(\"d=%d\", 0-123); }", 6, "d=-123", True),
    ("printf %x 零", "int main(){ return printf(\"z=%x\", 0); }", 3, "z=0", True),
    ("printf 混合格式", "int main(){ return printf(\"%x/%u/%o/%d\", 16, 10, 9, 0-7); }",
     11, "10/10/11/-7", True),
    ("printf 大 word 按 %x", "int main(){ return printf(\"%x\", 0-1); }", 18, None, True),
]


def compile_run(src, steps=3000000):
    prog, labels, txt, strs = cc.compile_c(src)
    for i, w in enumerate(prog):
        assert isa.decode(w)["inst"] is not None, f"pc={i} 非法指令"
    cpu = vm.VM(prog, mem_init=dict(strs or {}))
    cpu.run(max_steps=steps)
    return S(cpu.regs[10]), bytes(cpu.output).decode("latin1")


def main():
    print("=== TriRV unsigned 类型 + printf 无符号格式端到端 ===")
    passed = 0
    for name, csrc, expect, expect_out, need_stdio in CASES:
        src = (STDIO + "\n" + csrc) if need_stdio else csrc
        ret, out = compile_run(src)
        good = (expect is None or ret == expect) and (expect_out is None or out == expect_out)
        detail = f"ret={ret}" + (f" out={out!r}" if expect_out is not None else "")
        print(f"  [{'PASS' if good else 'FAIL'}] {name}: {detail}")
        passed += good
    print("-" * 68)
    print(f"总计通过 {passed}/{len(CASES)}")
    assert passed == len(CASES), "存在失败用例"
    print("全部通过 ✓ unsigned 语义与 printf 无符号格式工作正常")


if __name__ == "__main__":
    main()
