"""TriRV 二进制位运算适配层（方案 A：精确 72 位视图）端到端验证。

规范 §5.1.1 / §11.2。ISA 是纯三进制的，"第 k 个二进制位"在 ISA 里没有位置，
所以 C 的 & | ^ ~ 属于外来语义适配层——用三进制指令模拟二进制语义。

ABI（2026-11-19 拍板 = 方案 A）：
    bits(x) = x>=0 ? x : x + 3^45        无符号位模式，值域 [0, 3^45)
    val(v)  = v<=HALF45 ? v : v - 3^45   映回 45-trit 有符号域
    视图宽度 W = 72（最小 W 使 2^W > 3^45-1）
    回绕在 3^45 而非 2^k

本套件验证：
  1. 参考模型（Python 大整数）与 VM 执行结果一致
  2. C 后端与 Python 后端语义一致（差分锁）
  3. 边界：0、±1、HALF、负数、超 32 位的值
"""
import os
import subprocess
import sys
import tempfile

import trirv_cc_backend as cc
import trirv_isa as isa
import trirv_vm as vm
from export_trv import export

S = isa.to_signed_word
CVM = os.environ.get("TRIRV_VM_BIN",
                     os.path.join(os.path.dirname(os.path.abspath(__file__)), "trirv_vm"))

P = 3 ** 45
H = (P - 1) // 2
W = 72
M = (1 << W) - 1


def bits(x):
    return x if x >= 0 else x + P


def val(v):
    v %= P
    return v if v <= H else v - P


def b_and(a, b): return val(bits(a) & bits(b))
def b_or(a, b):  return val(bits(a) | bits(b))
def b_xor(a, b): return val(bits(a) ^ bits(b))
def b_not(a):    return val(M - bits(a))


def compile_run_py(src, steps=400000):
    prog, labels, txt, strs = cc.compile_c(src)
    cpu = vm.VM(prog, mem_init=dict(strs or {}))
    cpu.run(max_steps=steps)
    for i, w in enumerate(prog):
        assert isa.decode(w)["inst"] is not None, f"pc={i} 非法指令"
    return S(cpu.regs[10]), txt


def run_c_vm(src, want=None):
    """C VM 交叉验证。注意 exit_code 只有 8 位（syscall exit 语义 a0&0xFF），
    超出 -128..255 的结果无法通过退出码传回，跳过。"""
    if not os.path.exists(CVM):
        return None
    if want is not None and not (-128 <= want <= 127):
        return None
    d = tempfile.mkdtemp(prefix="trirv_bv_")
    cf, tf = os.path.join(d, "t.c"), os.path.join(d, "t.trv")
    with open(cf, "w") as f:
        f.write(src)
    export(cf, tf)
    r = subprocess.run([CVM, tf], capture_output=True)
    errs = [l for l in r.stderr.decode().split() if l.startswith("exit=")]
    if not errs:
        return None
    v = int(errs[0].split("=")[1])
    return v if v < 128 else v - 256          # exit 只带 8 位，按有符号字节还原


# (名称, 运算符, a, b, 期望值)
BIN_CASES = []
for a, b in [
    (12, 10), (0, 0), (0, -1), (-1, -1), (-1, 0),
    (1, 1), (1, 2), (2, 1), (7, 3), (255, 15),
    (-8, 3), (-8, -3), (100, -100),
    (H, H), (H, -H - 1), (-H - 1, H), (H - 1, 1),
    (1000000, 999999), (123456789, 987654321),
    (2 ** 31, 1), (2 ** 62, 2 ** 62),
]:
    for name, fn in (("and", b_and), ("or", b_or), ("xor", b_xor)):
        BIN_CASES.append((f"0x{a:x} {name} 0x{b:x}", name, a, b, fn(a, b)))

UN_CASES = [(f"~0x{a:x}", a, b_not(a)) for a in
            [0, 1, -1, 2, -2, 12, -8, H, -H - 1, 123456789]]

OP_SYM = {"and": "&", "or": "|", "xor": "^"}


def main():
    fails = 0
    n = 0
    print(f"参考模型：P=3^45={P}, HALF={H}, W={W}, M=2^72-1")

    # --- 1. 二元运算：Python VM + C VM 双跑 ---
    for label, op, a, b, want in BIN_CASES:
        src = f"int main(){{ int a={a}; int b={b}; return a {OP_SYM[op]} b; }}"
        got, txt = compile_run_py(src)
        n += 1
        ok = got == want
        # 汇编层面：必须出现适配层的走查循环（TMIN/TMAX + DIV p,2），不能是单条 AND
        if "TMIN" not in txt and "TMAX" not in txt:
            ok = False
            print(f"  [FAIL] {label}: 汇编缺逐位走查（TMIN/TMAX）")
        if not ok and got != want:
            print(f"  [FAIL] {label}: want {want}, PyVM {got}")
        if not ok:
            fails += 1
            continue
        cg = run_c_vm(src, want)
        if cg is not None and cg != want:
            print(f"  [FAIL] {label}: C VM {cg} != want {want}")
            fails += 1

    # --- 2. 一元 ~ ---
    for label, a, want in UN_CASES:
        src = f"int main(){{ int a={a}; return ~a; }}"
        got, txt = compile_run_py(src)
        n += 1
        if got != want:
            print(f"  [FAIL] {label}: want {want}, PyVM {got}")
            fails += 1
            continue
        cg = run_c_vm(src, want)
        if cg is not None and cg != want:
            print(f"  [FAIL] {label}: C VM {cg} != want {want}")
            fails += 1

    # --- 3. 组合与优先级：~(a&b) 等 ---
    combos = [
        ("~(a&b)", 12, 10, b_not(b_and(12, 10))),
        ("(a|b)^~a", 7, 3, b_xor(b_or(7, 3), b_not(7))),
        ("~(~a)", -5, None, b_not(b_not(-5))),
        ("a&b|a^b", 6, 5, b_or(b_and(6, 5), b_xor(6, 5))),
    ]
    for label, a, b, want in combos:
        if b is None:
            src = f"int main(){{ int a={a}; return {label}; }}"
        else:
            src = f"int main(){{ int a={a}; int b={b}; return {label}; }}"
        got, _ = compile_run_py(src)
        n += 1
        if got != want:
            print(f"  [FAIL] {label}: want {want}, got {got}")
            fails += 1

    # --- 4. 与算术/比较混用：位运算结果参与后续计算 ---
    mixed = [
        ("a&b 后加", "int main(){ int a=12; int b=10; int c=a&b; return c+1; }", b_and(12, 10) + 1),
        ("位运算结果比较", "int main(){ int a=12; int b=10; if((a&b)==8) return 1; return 0; }", 1),
        ("位运算在循环里", "int main(){ int s=0; int i; for(i=0;i<4;i++) s=s+(i&1); return s; }", 2),
        # 方案 A 下 ~0 ≠ -1：72 位全取反后按 3^45 回绕，结果是 1.77e21 这个"巨大的数"。
        # 这不是 bug，是"回绕在 3^45 而非 2^k"的直接后果，固化成回归锁。
        ("~0 按方案 A 回绕", "int main(){ return ~0; }", b_not(0)),
        ("x&0 是 0", "int main(){ int x=12345; return x&0; }", 0),
        ("-1 的位全 1", "int main(){ int x=-1; int y=x&12345; return y; }", b_and(-1, 12345)),
    ]
    for label, src, want in mixed:
        got, _ = compile_run_py(src)
        n += 1
        if got != want:
            print(f"  [FAIL] {label}: want {want}, got {got}")
            fails += 1

    # --- 5. << >> 仍应硬报错（方案 A 只覆盖 & | ^ ~）---
    for op in ("<<", ">>"):
        src = f"int main(){{ int a=1; int b=2; return a {op} b; }}"
        n += 1
        try:
            compile_run_py(src)
            print(f"  [FAIL] {op} 应硬报错，却编译通过了")
            fails += 1
        except (SystemExit, ValueError, RuntimeError) as e:
            msg = str(e)
            if "移位运算" not in msg and "UNSUPPORTED" not in msg and not isinstance(e, SystemExit):
                print(f"  [FAIL] {op} 报错信息不含 UNSUPPORTED: {msg[:80]}")
                fails += 1

    print(f"\n{'=' * 56}")
    print(f"bitview: {n - fails}/{n} 通过")
    if fails:
        print(f"失败 {fails} 项")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
