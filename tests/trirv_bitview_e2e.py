"""TriRV C 位运算/移位契约——三值本征语义（2026-11-22 方言拍板）。

前史：本套件原名「二进制适配层（方案 A：精确 72 位视图）」，验证 2026-11-19
拍板的「用三进制指令模拟 ISO C 二进制位语义」软件层。2026-11-22 方言转向：
C 的 & | ^ ~ << >> 直接定义为本征三值运算（方案 A 位游标退役，见
ternary-isa-design.md §5.1.1 废案条款），本套件整体重铺为本征语义锁。

本征 ABI（逐 trit，raw 域 [0, 3^45)）：
    a & b = TAND = min(ta, tb)       a | b = TOR = max(ta, tb)
    a ^ b = XOR  = (ta + tb) mod 3   —— 不自反：a^a ≠ 0（mod-3 固有）
    ~a    = TNOT = 2 - ta（≡ -1 - a；~0 = -1）
    a << k = SLL  = raw(a)·3^k mod 3^45
    a >> k = SRA  = a / 3^k 向零截断（≡ 除法口径；|x| < 3^45 ⇒ k≥45 恒 0）
             无符号 = SRL = raw(a) ÷ 3^k

本套件验证：
  1. 参考模型（Python 大整数逐 trit）与 VM 执行结果一致（PyVM + C VM 双跑）
  2. 产物直发本征指令（TAND/TOR/XOR/TNOT/SLLI/SRLI/SRAI/SLL/SRL/SRA），
     不得再出现方案 A 位游标形态
  3. 边界：0、±1、HALF、负数、超 32 位的值、k≥45、越界移位量
"""
import os
import re
import subprocess
import sys
import tempfile

import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import trirv_cc_backend as cc
import trirv_isa as isa
import trirv_vm as vm
from export_trv import export

S = isa.to_signed_word
CVM = os.environ.get("TRIRV_VM_BIN",
                     os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vm", "bin", "trirv_vm"))

P = 3 ** 45
H = (P - 1) // 2


def raw(x):
    return x % P


def val(v):
    v %= P
    return v if v <= H else v - P


def _trits(x):
    x = raw(x)
    ts = []
    for _ in range(45):
        ts.append(x % 3)
        x //= 3
    return ts


def _from_trits(ts):
    r = 0
    for i, t in enumerate(ts):
        r += t * 3 ** i
    return r


def t_and(a, b):
    ta, tb = _trits(a), _trits(b)
    return val(_from_trits([x if x < y else y for x, y in zip(ta, tb)]))


def t_or(a, b):
    ta, tb = _trits(a), _trits(b)
    return val(_from_trits([x if x > y else y for x, y in zip(ta, tb)]))


def t_xor(a, b):
    ta, tb = _trits(a), _trits(b)
    return val(_from_trits([(x + y) % 3 for x, y in zip(ta, tb)]))


def t_not(a):
    return val(P - 1 - raw(a))          # TNOT ≡ -1 - a（全字精确）


# ---- 三值移位参考模型 ----
def t_shl(a, k):  return val(raw(a) * 3 ** k % P)     # ×3^k，模 3^45 回绕
def t_shru(a, k): return val(raw(a) // 3 ** k)        # 无符号：raw 除
def t_shrs(a, k):                                     # 有符号：向零截断（≡ a/3^k）
    q = abs(a) // 3 ** k
    return -q if a < 0 else q


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
    for name, fn in (("and", t_and), ("or", t_or), ("xor", t_xor)):
        BIN_CASES.append((f"{a} {name} {b}", name, a, b, fn(a, b)))

UN_CASES = [(f"~{a}", a, t_not(a)) for a in
            [0, 1, -1, 2, -2, 12, -8, H, -H - 1, 123456789]]

OP_SYM = {"and": "&", "or": "|", "xor": "^"}
OP_INS = {"and": "TAND", "or": "TOR", "xor": "XOR"}


def main():
    fails = 0
    n = 0
    print(f"参考模型：P=3^45={P}, HALF={H}（三值本征：min/max/mod-3/TNOT/SLL 系）")

    # --- 1. 二元运算：Python VM + C VM 双跑 ---
    for label, op, a, b, want in BIN_CASES:
        src = f"int main(){{ int a={a}; int b={b}; return a {OP_SYM[op]} b; }}"
        got, txt = compile_run_py(src)
        n += 1
        ok = got == want
        # 产物锁：必须直发本征指令（TAND/TOR/XOR），方案 A 位游标（TMIN/TMAX
        # 走查 + DIVU /2 循环）不得再现
        if OP_INS[op] not in txt:
            ok = False
            print(f"  [FAIL] {label}: 汇编缺本征直发（{OP_INS[op]}）")
        if "TMIN" in txt or "TMAX" in txt:
            ok = False
            print(f"  [FAIL] {label}: 汇编出现方案 A 位游标残留（TMIN/TMAX）")
        if not ok and got != want:
            print(f"  [FAIL] {label}: want {want}, PyVM {got}")
        if not ok:
            fails += 1
            continue
        cg = run_c_vm(src, want)
        if cg is not None and cg != want:
            print(f"  [FAIL] {label}: C VM {cg} != want {want}")
            fails += 1

    # --- 2. 一元 ~（TNOT 直发） ---
    for label, a, want in UN_CASES:
        src = f"int main(){{ int a={a}; return ~a; }}"
        got, txt = compile_run_py(src)
        n += 1
        if got != want:
            print(f"  [FAIL] {label}: want {want}, PyVM {got}")
            fails += 1
            continue
        if "TNOT" not in txt:
            print(f"  [FAIL] {label}: 汇编缺 TNOT 直发")
            fails += 1
            continue
        cg = run_c_vm(src, want)
        if cg is not None and cg != want:
            print(f"  [FAIL] {label}: C VM {cg} != want {want}")
            fails += 1

    # --- 3. 组合与优先级：~(a&b) 等（min/max/2-t 是完整 De Morgan 代数） ---
    combos = [
        ("~(a&b)", 12, 10, t_not(t_and(12, 10))),
        ("(a|b)^~a", 7, 3, t_xor(t_or(7, 3), t_not(7))),
        ("~(~a)", -5, None, t_not(t_not(-5))),
        ("a&b|a^b", 6, 5, t_or(t_and(6, 5), t_xor(6, 5))),
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
    loop_and = 0
    for i in range(4):
        loop_and += t_and(i, 1)
    mixed = [
        ("a&b 后加", "int main(){ int a=12; int b=10; int c=a&b; return c+1; }", t_and(12, 10) + 1),
        # 逐 trit min 不清零：12&10 = 9 ≠ 8，等式不成立 → 0
        ("位运算结果比较", "int main(){ int a=12; int b=10; if((a&b)==8) return 1; return 0; }", 0),
        ("位运算在循环里", "int main(){ int s=0; int i; for(i=0;i<4;i++) s=s+(i&1); return s; }", loop_and),
        # 本征 TNOT(0) = 全 trit 2 = -1（方案 A 时代此例锁 ~0≠-1，语义反转后易向）
        ("TNOT(0) = -1", "int main(){ return ~0; }", -1),
        ("x&0 是 0", "int main(){ int x=12345; return x&0; }", 0),
        ("-1 的 trit 全 2：min(2,t)=t 恒等", "int main(){ int x=-1; int y=x&12345; return y; }", t_and(-1, 12345)),
    ]
    for label, src, want in mixed:
        got, _ = compile_run_py(src)
        n += 1
        if got != want:
            print(f"  [FAIL] {label}: want {want}, got {got}")
            fails += 1

    # --- 5. 三值移位 << >> ---
    # << = ×3^k（SLLI）；>> 无符号 = raw ÷3^k（SRLI）；>> 有符号 = ÷3^k 向零截断
    # （SRAI，≡ 除法口径——历史坑：早期直译 SLL 得 ×3 被当 bug，本征化后 ×3^k
    # 恰是定义；k≥45 商恒 0）。k∈[1,44] 必须出现 SLLI/SRLI/SRAI；k=0 只有搬移；
    # k≥45 是 ADDI zero（无移位指令）。
    CONST_AS = (0, 1, -1, -8, 123456789, H)
    CONST_KS = (0, 1, 3, 11, 45, 71)
    for a in CONST_AS:
        for k in CONST_KS:
            for op, fn, ty in (("<<", t_shl, "int"), (">>", t_shru, "unsigned"), (">>", t_shrs, "int")):
                want = fn(a, k)
                src = f"int main(){{ {ty} a={a}; return a {op} {k}; }}"
                got, txt = compile_run_py(src)
                n += 1
                ok = got == want
                if 1 <= k <= 44:
                    must = {"<<": "SLLI", ">>u": "SRLI", ">>s": "SRAI"}[
                        op + ("" if op == "<<" else ("u" if ty == "unsigned" else "s"))]
                    if must not in txt:
                        ok = False
                        print(f"  [FAIL] {ty} {a} {op} {k}: 汇编缺本征移位 {must}")
                if not ok:
                    print(f"  [FAIL] {ty} {a} {op} {k}: want {want}, PyVM {got}")
                    fails += 1
                    continue
                cg = run_c_vm(src, want)
                if cg is not None and cg != want:
                    print(f"  [FAIL] {ty} {a} {op} {k}: C VM {cg} != {want}")
                    fails += 1

    # 变量移位量：单条原生 SLL/SRL/SRA（本征化后无需任何工作寄存器）
    for a in (1, -8, 123456789):
        for k in (0, 1, 5, 40, 71):
            for op, fn, ty in (("<<", t_shl, "int"), (">>", t_shru, "unsigned"), (">>", t_shrs, "int")):
                want = fn(a, k)
                src = f"int main(){{ {ty} a={a}; int n={k}; return a {op} n; }}"
                got, _ = compile_run_py(src)
                n += 1
                if got != want:
                    print(f"  [FAIL] {ty} {a} {op} n({k}): want {want}, got {got}")
                    fails += 1

    # 定点回归：本征口径的对照形态（含与除法的 ≡ 关系）
    fixed = [
        ("x<<1 是 ×3 不是 ×2（方案 A 时代此为 bug 形态）",
         "int main(){ int a=10; return a<<1; }", 30, "SLLI"),
        ("无符号 >>1 走 SRLI（raw ÷3）", "int main(){ unsigned a=10; return a>>1; }", 3, "SRLI"),
        ("有符号 >>1 是 ÷3 向零截断", "int main(){ int a=-7; return a>>1; }", -2, "SRAI"),  # trunc(-7/3)=-2（floor 会给 -3）
        ("右移 ≡ 除法对照：-7>>1 ≡ -7/3", "int main(){ int a=-7; return a/3; }", -2, None),
        ("1<<n 当 3 的幂", "int main(){ int n=4; return (1<<n)-1; }", 80, None),
        ("移位参与循环累加", "int main(){ int s=0; int i; for(i=0;i<5;i++) s=s+(1<<i); return s; }", 121, None),
        ("移位与位运算混用", "int main(){ int a=12; return (a<<1)&0xFF; }", t_and(36, 255), None),
    ]
    for label, src, want, must in fixed:
        got, txt = compile_run_py(src)
        n += 1
        if got != want:
            print(f"  [FAIL] {label}: want {want}, got {got}")
            fails += 1
            continue
        if must and must not in txt:
            print(f"  [FAIL] {label}: 汇编缺 {must}")
            fails += 1
            continue
        cg = run_c_vm(src, want)
        if cg is not None and cg != want:
            print(f"  [FAIL] {label}: C VM {cg} != {want}")
            fails += 1

    # --- 6. 必须仍然拒绝的移位形态（负移位量在 mod 3^7 下会静默巨移） ---
    for label, src in (("移位量为负", "int main(){ int a=1; return a << -1; }"),
                       ("移位量过大", "int main(){ int a=1; return a >> 99999; }")):
        n += 1
        try:
            compile_run_py(src)
            print(f"  [FAIL] {label}: 应硬报错，却编译通过了")
            fails += 1
        except (SystemExit, ValueError, RuntimeError) as e:
            if isinstance(e, SystemExit):
                continue                       # 汇编器/编译器 exit(2) 也算拒绝
            msg = str(e)
            if "UNSUPPORTED" not in msg:
                print(f"  [FAIL] {label}: 报错信息不含 UNSUPPORTED: {msg[:80]}")
                fails += 1

    print(f"\n{'=' * 56}")
    print(f"bitview: {n - fails}/{n} 通过")
    if fails:
        print(f"失败 {fails} 项")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
