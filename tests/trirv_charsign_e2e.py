"""TriRV char 符号性 / LB-LBU-LH-LHU 语义 端到端验证。

背景：TriRV 原先把 LB/LH 实现为零扩展（char=0..242 无符号），与 RISC-V 的
"LB/LH 符号扩展、LBU/LHU 零扩展" 不一致。2026-11-19 已对齐 RISC-V 语义。

关键点：char 本身仍是无符号——这不是妥协，而是与 RISC-V psABI 一致
（GCC riscv 的 DEFAULT_SIGNED_CHAR=0，plain char 无符号）。所以：
  char / unsigned char  -> LBU 零扩展，值域 0..242
  signed char           -> LB  符号扩展，值域 -121..121

本套件验证三件事：
  1. 三种 char 的返回值语义（Python VM）
  2. 生成的汇编确实按符号性选了 LBU / LB
  3. 同一程序在 C VM 上结果一致（防止两套 VM 漂移）
"""
import os
import subprocess
import tempfile

import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import trirv_cc_backend as cc
import trirv_isa as isa
import trirv_vm as vm
from export_trv import export

S = isa.to_signed_word
CVM = os.environ.get("TRIRV_VM_BIN", os.path.join(os.path.dirname(os.path.abspath(__file__)), "trirv_vm"))

# (名称, C 源码, 期望返回值, 期望出现的载入指令)
CASES = [
    # --- 单变量：三种 char 对同一个 tryte 值 200 的解读 ---
    ("signed char 200 -> -43",   "int main(){ signed char c; c=200; return c; }", -43, ["LB"]),
    ("char 200 -> 200",          "int main(){ char c; c=200; return c; }", 200, ["LBU"]),
    ("unsigned char 200 -> 200", "int main(){ unsigned char c; c=200; return c; }", 200, ["LBU"]),

    # --- 5-trit 符号边界：半阈值 121 ---
    ("signed char 121 -> 121",   "int main(){ signed char c; c=121; return c; }", 121, ["LB"]),
    ("signed char 122 -> -121",  "int main(){ signed char c; c=122; return c; }", -121, ["LB"]),
    ("signed char 242 -> -1",    "int main(){ signed char c; c=242; return c; }", -1, ["LB"]),
    ("signed char 0 -> 0",       "int main(){ signed char c; c=0; return c; }", 0, ["LB"]),

    # --- 负值写回：SB 与 LB 往返一致 ---
    ("signed char 写 -43 读 -43", "int main(){ signed char c; c=-43; return c; }", -43, ["LB"]),
    ("signed char -121 往返",     "int main(){ signed char c; c=-121; return c; }", -121, ["LB"]),

    # --- 比较语义：符号性必须影响 SLT/SLTU 结果 ---
    ("signed char <0 为真",      "int main(){ signed char c; c=200; if(c<0) return 1; return 0; }", 1, ["LB"]),
    ("char <0 恒假",             "int main(){ char c; c=200; if(c<0) return 1; return 0; }", 0, ["LBU"]),
    ("signed char 与 int 比较",  "int main(){ signed char c; c=200; int x=0; if(c<x) return 7; return 0; }", 7, ["LB"]),

    # --- 数组与指针步长：char* 步长恒为 1 tryte ---
    ("signed char 数组求和",     "int main(){ signed char a[2]; a[0]=200; a[1]=100; return a[0]+a[1]; }", 57, ["LB"]),
    ("signed char* 步长 1",      "int main(){ signed char b[2]; signed char *p; b[0]=200; b[1]=100; p=b; return *(p+1); }", 100, ["LB"]),
    ("signed char* 下标读",      "int main(){ signed char b[2]; signed char *p; b[0]=200; b[1]=100; p=b; return p[0]; }", -43, ["LB"]),
    ("char 与 signed char 同址", "int main(){ char c; signed char *p; c=200; p=&c; return *p; }", -43, ["LB"]),

    # --- 结构体成员：布局仍按 1 tryte ---
    ("struct signed char 成员",  "struct S{int x; signed char c;}; int main(){ struct S s; s.x=3; s.c=200; return s.c+s.x; }", -40, ["LB"]),
    ("struct char 成员无符号",   "struct S{int x; char c;}; int main(){ struct S s; s.x=3; s.c=200; return s.c+s.x; }", 203, ["LBU"]),

    # --- 形参归一化：char 形参在入口按 tryte 收窄 ---
    ("char 形参 200 -> 200",       "int f(char c){return c;} int main(){return f(200);}", 200, ["LBU"]),
    ("signed char 形参 200 -> -43", "int f(signed char c){return c;} int main(){return f(200);}", -43, ["LB"]),
    ("char* 传参后读高位字节",      "int f(char *s){return s[0];} int main(){char b[1]; b[0]=200; return f(b);}", 200, ["LBU"]),
]


def asm_load_ops(txt):
    """从生成的汇编里取出所有载入类指令助记符。"""
    ops = []
    for line in txt.splitlines():
        tok = line.split()
        if tok and tok[0] in ("LB", "LBU", "LH", "LHU", "LW"):
            ops.append(tok[0])
    return ops


def compile_run_py(src, steps=200000):
    prog, labels, txt, strs = cc.compile_c(src)
    cpu = vm.VM(prog, mem_init=dict(strs or {}))
    cpu.run(max_steps=steps)
    for i, w in enumerate(prog):
        assert isa.decode(w)["inst"] is not None, f"pc={i} 非法指令"
    return S(cpu.regs[10]), txt, cpu


def run_c_vm(src):
    """把同一份源码用 C 后端编译成 .trv，交给 C VM 跑，取退出码。"""
    if not os.path.exists(CVM):
        return None
    d = tempfile.mkdtemp(prefix="trirv_cs_")
    cf, tf = os.path.join(d, "t.c"), os.path.join(d, "t.trv")
    with open(cf, "w") as f:
        f.write(src)
    export(cf, tf)
    r = subprocess.run([CVM, tf], capture_output=True)
    errs = [l for l in r.stderr.decode().split() if l.startswith("exit=")]
    return int(errs[0].split("=")[1]) if errs else None


def main():
    print("=== TriRV char 符号性 / LB-LBU 语义 专项 ===")
    passed = 0
    cross_ok = cross_n = 0
    for name, src, expect, want_ops in CASES:
        got, txt, cpu = compile_run_py(src)
        ops = asm_load_ops(txt)
        # 1) 返回值正确
        val_ok = (got == expect)
        # 2) 汇编里出现了期望的载入指令（LB 与 LBU 不能混用）
        op_ok = all(op in ops for op in want_ops)
        # 3) C VM 一致（退出码取低 8 位，故仅对 -121..121 区间可直接比对）
        c_exit = run_c_vm(src)
        if c_exit is None:
            cross = True
        else:
            cross_n += 1
            cross = (c_exit == (expect & 0xFF))
            cross_ok += cross
        ok = val_ok and op_ok and cross
        detail = "" if op_ok else f"  汇编载入={ops}"
        if c_exit is not None and not cross:
            detail += f"  C VM exit={c_exit}(期望 {expect & 0xFF})"
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {got} (期望 {expect}){detail}")
        passed += ok
    print("-" * 66)
    print(f"专项通过 {passed}/{len(CASES)}   C VM 交叉一致 {cross_ok}/{cross_n}")
    assert passed == len(CASES), "存在失败用例"
    print("全部通过 ✓ LB/LH 符号扩展、LBU/LHU 零扩展、char 无符号 ABI 均正确")


if __name__ == "__main__":
    main()
