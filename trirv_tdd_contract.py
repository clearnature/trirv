#!/usr/bin/env python3
"""TriRV 契约测试（TDD：先写规格断言，再让实现满足）。

与 `*_e2e.py` 的分工：

    e2e      端到端跑程序、比对结果   —— 回答"能不能用"
    本套件    逐条断言字段/边界/性质   —— 回答"为什么不会静默算错"

这里的每条断言都对应一次真实事故或一个已知陷阱：

  §1 寄存器字段契约     de7c53a：C VM 解码器仍是 3-trit 字段，x27..x31 全解错，
                        x0..x26 恰好正确 —— 差分测试发现不了，因为没有程序用到 s11。
  §2 寄存器执行契约     同上。只有 x27..x31 **真正参与运算**时，错解码才会显形。
  §3 立即数范围契约     B/J 型曾静默 %2187 / %1594323（已修）；I/S/U 型字面量
                        还留着同一个洞：ADDI x1,x0,5000 静默变成 626。
  §4 汇编器边界契约     负数走 3 补码回绕、正数是位模式、CSR 名映射、标签越界。
  §5 后端 STRICT 契约   README 的"验证陷阱"：非 STRICT 下失败用例静默回退 Python，
                        12/14 套全绿的假象就是这么来的。
  §6 大栈帧契约         帧大小 > 1093 必须走 load_imm + ADD，不能 ADDI 直接塞。
  §7 位运算代数律       方案 A（72 位视图）的性质锁，比逐个数值更强。

用法：
    python3 trirv_tdd_contract.py          # 任一断言失败 -> 退出码 1
"""
import os
import struct
import subprocess
import sys
import tempfile

import trirv_asm as asm
import trirv_cc_backend as cc
import trirv_isa as isa
import trirv_vm as vm

HERE = os.path.dirname(os.path.abspath(__file__))
CVM = os.environ.get("TRIRV_VM_BIN", os.path.join(HERE, "trirv_vm"))

PASS = 0
FAIL = 0
FAILED = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILED.append(f"{name}  {detail}".rstrip())
        print(f"  [FAIL] {name} {detail}")
    return cond


def section(title):
    print(f"\n=== {title} ===")


# ---------------------------------------------------------------- 公共工具
IMM7 = 3 ** 7          # 2187
IMM13 = 3 ** 13        # 1594323
LIM7 = (IMM7 - 1) // 2      # 1093
LIM13 = (IMM13 - 1) // 2    # 797161


def run_asm(src, max_steps=500):
    prog, _labels = asm.assemble(src)
    return vm.VM(prog).run(max_steps=max_steps)


def write_trv(prog, path, strings=None):
    buf = bytearray(b"TRV1")
    strings = strings or {}
    buf += struct.pack("<III", 1, len(prog), len(strings))
    for raw in prog:
        buf += struct.pack("<I", raw & 0xFFFFFFFF)
    for addr, data in strings.items():
        buf += struct.pack("<II", addr, len(data)) + bytes(data)
    with open(path, "wb") as f:
        f.write(buf)
    return path


def run_c_vm_prog(prog, strings=None):
    """在 C VM 上跑一段指令序列，返回 8 位退出码（拿不到返回 None）。"""
    if not os.path.exists(CVM):
        return None
    d = tempfile.mkdtemp(prefix="trirv_tdd_")
    tf = os.path.join(d, "t.trv")
    write_trv(prog, tf, strings)
    r = subprocess.run([CVM, tf], capture_output=True)
    for line in r.stderr.decode().split():
        if line.startswith("exit="):
            v = int(line.split("=")[1])
            return v if v < 128 else v - 256
    return None


def compile_run_py(src, steps=400000):
    prog, _l, _t, strs = cc.compile_c(src)
    cpu = vm.VM(prog, mem_init=dict(strs or {})).run(max_steps=steps)
    return isa.to_signed_word(cpu.regs[10]), prog, strs


# ---------------------------------------------------------------- §1 寄存器字段
def section_regfile_fields():
    section("§1 寄存器字段契约（32 个寄存器 × 6 种格式 × 三处字段位置）")
    for key, want in (("rd", 4), ("rs1", 4), ("rs2", 4), ("opcode", 3), ("funct2", 3)):
        lo, hi = isa.F[key]
        check(f"字段宽度 {key}", hi - lo + 1 == want, f"实际 {hi - lo + 1} trit")
    check("寄存器数 = 32", isa.NREG == 32, f"实际 {isa.NREG}")

    def rt(mn, **kw):
        d = isa.decode(isa.encode(mn, **kw))
        return d

    bad = 0
    for r in range(32):
        d = rt("ADDI", rd=r, rs1=r, imm=0)
        if not (d["inst"] == "ADDI" and d["rd"] == r and d["rs1"] == r):
            bad += 1
            print(f"  [FAIL] ADDI rd=rs1={r}: 解出 {d['inst']} rd={d['rd']} rs1={d['rs1']}")
        d = rt("ADD", rd=r, rs1=r, rs2=r)
        if not (d["inst"] == "ADD" and (d["rd"], d["rs1"], d["rs2"]) == (r, r, r)):
            bad += 1
            print(f"  [FAIL] ADD r={r}: 解出 rd={d['rd']} rs1={d['rs1']} rs2={d['rs2']}")
        d = rt("SW", rs1=r, rs2=r, imm=0)
        if d["inst"] != "SW" or (d["rs1"], d["rs2"]) != (r, r):
            bad += 1
            print(f"  [FAIL] SW r={r}: 解出 rs1={d['rs1']} rs2={d['rs2']}")
        d = rt("BNE", rs1=r, rs2=r, imm=0)
        if d["inst"] != "BNE" or (d["rs1"], d["rs2"]) != (r, r):
            bad += 1
            print(f"  [FAIL] BNE r={r}: 解出 rs1={d['rs1']} rs2={d['rs2']}")
        d = rt("LUI", rd=r, imm=0)
        if d["inst"] != "LUI" or d["rd"] != r:
            bad += 1
            print(f"  [FAIL] LUI rd={r}: 解出 {d['inst']} rd={d['rd']}")
        d = rt("JAL", rd=r, imm=0)
        if d["inst"] != "JAL" or d["rd"] != r:
            bad += 1
            print(f"  [FAIL] JAL rd={r}: 解出 {d['inst']} rd={d['rd']}")
    # 32 × 6 = 192 项合并成一个断言，避免刷屏
    check("192 项字段往返（rd/rs1/rs2 全 32 值）", bad == 0, f"{bad} 项错")


# ---------------------------------------------------------------- §2 寄存器执行
def section_regfile_exec():
    section("§2 寄存器执行契约（每个寄存器都要能被写、被读）")
    # 注意 x17 就是 a7：ECALL 用它当系统调用号，写完必须恢复成 93（exit），
    # 否则会被当成 syscall 7 执行（实测 a0 拿到的是 -38 = -ENOSYS）。
    bad = 0
    for r in range(1, 32):
        cpu = run_asm(f"ADDI x{r}, zero, 7\n"
                      f"ADDI a5, x{r}, 0\n"      # 读回：rs1 = r
                      f"ADDI a0, a5, 0\n"
                      f"ADDI a7, zero, 93\n"     # a7 = SYS_exit
                      f"ECALL\n")
        if isa.to_signed_word(cpu.regs[10]) != 7:
            bad += 1
            print(f"  [FAIL] x{r}: a0 = {isa.to_signed_word(cpu.regs[10])}，期望 7")
    check("31 个寄存器的 \"写 rd -> 读 rs1\" 往返", bad == 0, f"{bad} 个错")

    src = """
    ADDI x27, zero, 5
    ADDI x28, zero, 6
    ADDI x29, zero, 7
    ADDI x30, zero, 8
    ADDI x31, zero, 9
    ADD  x5, x27, x28
    ADD  x5, x5, x29
    ADD  x5, x5, x30
    ADD  x5, x5, x31
    ADDI a0, x5, 0
    ADDI a7, zero, 93
    ECALL
    """
    prog, _l = asm.assemble(src)
    cpu = vm.VM(prog).run(max_steps=200)
    got = isa.to_signed_word(cpu.regs[10])
    check("x27..x31 参与算术（PyVM）", got == 35, f"得 {got}，期望 35")
    cg = run_c_vm_prog(prog)
    if cg is None:
        print("  [SKIP] C VM 不在，跳过交叉验证")
    else:
        check("x27..x31 参与算术（C VM 独立解码路径）", cg == 35, f"得 {cg}，期望 35")


# ---------------------------------------------------------------- §3 立即数范围
def section_imm_range():
    section("§3 立即数范围契约（越界必须报错，绝不静默取模）")
    for tok in ("-1093", "-1", "1", "0", "1093", "2186", "0x80"):
        try:
            out, _l = asm.assemble(f"ADDI x1, x0, {tok}")
            want = int(tok, 0) % IMM7
            got = isa.decode(out[0])["imm"]
            check(f"7-trit 接受 {tok}", got == want, f"解出 imm={got}，期望 {want}")
        except Exception as e:
            check(f"7-trit 接受 {tok}", False, f"却报错 {e}")

    for tok in ("2187", "2188", "5000", "-1094", "-2188", "999999"):
        try:
            out, _l = asm.assemble(f"ADDI x1, x0, {tok}")
            check(f"7-trit 拒绝 {tok}", False,
                  f"却静默接受，解出 imm={isa.decode(out[0])['imm']}")
        except (ValueError, SystemExit):
            check(f"7-trit 拒绝 {tok}", True)

    for tok in ("-797161", "0", "797161", "1594322"):
        try:
            out, _l = asm.assemble(f"LUI x1, {tok}")
            want = int(tok, 0) % IMM13
            got = isa.decode(out[0])["imm"]
            check(f"13-trit 接受 {tok}", got == want, f"解出 imm={got}，期望 {want}")
        except Exception as e:
            check(f"13-trit 接受 {tok}", False, f"却报错 {e}")

    for tok in ("1594323", "1600000", "-797162"):
        try:
            out, _l = asm.assemble(f"LUI x1, {tok}")
            check(f"13-trit 拒绝 {tok}", False,
                  f"却静默接受，解出 imm={isa.decode(out[0])['imm']}")
        except (ValueError, SystemExit):
            check(f"13-trit 拒绝 {tok}", True)


# ---------------------------------------------------------------- §4 汇编器边界
def section_asm_edges():
    section("§4 汇编器边界契约")
    out, _l = asm.assemble("ADDI x1, x0, -1")
    check("负数 -1 -> 7-trit 3 补码 2186", isa.decode(out[0])["imm"] == 2186,
          f"实际 {isa.decode(out[0])['imm']}")
    out, _l = asm.assemble("SW x1, -1(x2)")
    check("S 型负偏移 -1 -> 2186（4+3 trit 拆分）", isa.decode(out[0])["imm"] == 2186,
          f"实际 {isa.decode(out[0])['imm']}")
    if "mstatus" in isa.CSR:
        out, _l = asm.assemble("CSRRW x1, x2, mstatus")
        check("CSR 名 -> 地址", isa.decode(out[0])["imm"] == isa.CSR["mstatus"],
              f"实际 {isa.decode(out[0])['imm']}")

    # B 型偏移 ±1093 条指令；越界必须报错（静默 % 2187 会跳到貌似合法的错位置）
    src = "BNE x1, x2, far\n" + "ADDI x3, x3, 0\n" * 1200 + "far:\nECALL\n"
    try:
        asm.assemble(src)
        check("B 型偏移越界报错", False, "却静默通过")
    except (ValueError, SystemExit):
        check("B 型偏移越界报错", True)

    # J 型 ±797161；用 8 万条指令构造越界（构造量大，单独计时）
    src = "JAL x1, far\n" + "ADDI x3, x3, 0\n" * 800000 + "far:\nECALL\n"
    try:
        asm.assemble(src)
        check("J 型偏移越界报错", False, "却静默通过")
    except (ValueError, SystemExit):
        check("J 型偏移越界报错", True)


# ---------------------------------------------------------------- §5 后端契约
def section_backend_strict():
    section("§5 后端契约（STRICT 必须抛错，非 STRICT 必须回退且记录）")
    # struct 按值传参：C 后端明确 UNSUPPORTED，Python 后端能编 —— 正好当探针
    src = ("struct P{int x;int y;}; int f(struct P p){return p.x+p.y;}"
           " int main(){struct P p;p.x=1;p.y=2;return f(p);}")

    old = os.environ.get("TRV_CC_STRICT")
    os.environ["TRV_CC_STRICT"] = "1"
    try:
        cc.compile_c(src)
        check("STRICT=1 时不支持的语义必须抛错", False, "却编译通过了")
    except Exception:
        check("STRICT=1 时不支持的语义必须抛错", True)

    # 切回非 STRICT：本项要验的正是"静默回退"，所以必须显式清掉外部环境里的 STRICT
    # （在 run_all_tests.py 里整轮都是 STRICT=1，不清就会把回退当失败）。
    os.environ.pop("TRV_CC_STRICT", None)
    try:
        cc.compile_c(src)
        check("非 STRICT 时回退 Python 且记录后端", cc.LAST_BACKEND == "py",
              f"实际后端 {cc.LAST_BACKEND}")
    finally:
        if old is None:
            os.environ.pop("TRV_CC_STRICT", None)
        else:
            os.environ["TRV_CC_STRICT"] = old


# ---------------------------------------------------------------- §6 大栈帧
def section_big_frame():
    section("§6 大栈帧契约（帧大小 > 1093 必须走 load_imm）")
    cases = [
        ("400 int 数组", "int main(){int a[400];a[0]=3;a[399]=4;return a[0]+a[399];}", 7),
        ("300 int 结构体", "struct B{int a[300];}; int main(){struct B b;b.a[0]=5;b.a[299]=6;return b.a[0]+b.a[299];}", 11),
        ("函数内 200 int", "int g(int n){int a[200];a[0]=n;return a[0]*2;} int main(){return g(11);}", 22),
    ]
    for name, src, want in cases:
        got, prog, strs = compile_run_py(src)
        check(f"{name}（PyVM）", got == want, f"得 {got}，期望 {want}")
        if want <= 127:
            cg = run_c_vm_prog(prog, strs)
            if cg is not None:
                check(f"{name}（C VM）", cg == want, f"得 {cg}，期望 {want}")


# ---------------------------------------------------------------- §7 位运算代数律
P45 = 3 ** 45
HALF45 = (P45 - 1) // 2
M72 = (1 << 72) - 1


def _bits(x): return x if x >= 0 else x + P45
def _val(v):
    v %= P45
    return v if v <= HALF45 else v - P45


def b_and(a, b): return _val(_bits(a) & _bits(b))
def b_or(a, b):  return _val(_bits(a) | _bits(b))
def b_xor(a, b): return _val(_bits(a) ^ _bits(b))
def b_not(a):    return _val(M72 - _bits(a))


def section_bitlaws():
    section("§7 位运算代数律（方案 A：72 位视图 / 回绕在 3^45）")
    a, b, c = 123456789, -987654321, HALF45

    # --- 7.1 成立的律（每条都是单个运算或对合，不跨运算组合）---
    holds = [
        ("幂等 a&a==a",   f"int main(){{int a={a};return a&a;}}", b_and(a, a)),
        ("幂等 a|a==a",   f"int main(){{int a={a};return a|a;}}", b_or(a, a)),
        ("消去 a^a==0",   f"int main(){{int a={a};return a^a;}}", 0),
        ("零元 a&0==0",   f"int main(){{int a={a};return a&0;}}", 0),
        ("单位元 a|0==a", f"int main(){{int a={a};return a|0;}}", a),
        ("对合 ~~a==a",   f"int main(){{int a={a};return ~~a;}}", a),
        ("交换 a&b==b&a",
         f"int main(){{int a={a};int b={b};return (a&b)==(b&a);}}", 1),
        ("交换 a^b==b^a",
         f"int main(){{int a={a};int b={b};return (a^b)==(b^a);}}", 1),
    ]
    for name, src, want in holds:
        got, _p, _s = compile_run_py(src)
        check(name, got == want, f"得 {got}，期望 {want}")

    # --- 7.2 不成立的律（方案 A 的定义后果，锁成回归的事实）---
    # 原因：每次运算的结果都按 3^45 回绕（emit_bitview_out），72 位视图**不是闭合的**。
    # 单次运算语义清晰，但组合表达式的结果依赖求值顺序 —— 这与"位运算是纯函数"的
    # 直觉相反，必须显式写下来，否则下一个人会把它当 bug 去"修"。
    breaks = [
        ("德摩根 ~(a&b) != ~a|~b",
         f"int main(){{int a={a};int b={b};return ~(a&b)==(~a|~b);}}"),
        ("德摩根 ~(a|b) != ~a&~b",
         f"int main(){{int a={a};int b={b};return ~(a|b)==(~a&~b);}}"),
        ("结合 (a|b)|c != a|(b|c)",
         f"int main(){{int a={a};int b={b};int c={c};return ((a|b)|c)==(a|(b|c));}}"),
        ("分配 a&(b|c) != (a&b)|(a&c)",
         f"int main(){{int a={a};int b={b};int c={c};return a&(b|c)==((a&b)|(a&c));}}"),
    ]
    for name, src in breaks:
        got, _p, _s = compile_run_py(src)
        check(name + "（回绕导致，非 bug）", got == 0, f"得 {got}，若为 1 说明语义变了")

    # --- 7.3 同一条链上左结合的参考模型对拍 ---
    # 参考模型必须**逐步回绕**，才与编译器一致；一次性算完再回绕是错的。
    chain = [
        ("(a&b)&c", b_and(b_and(a, b), c)),
        ("(a|b)|c", b_or(b_or(a, b), c)),
        ("(a^b)^c", b_xor(b_xor(a, b), c)),
    ]
    for expr, want in chain:
        got, _p, _s = compile_run_py(
            f"int main(){{int a={a};int b={b};int c={c};return {expr};}}")
        check(f"逐步回绕对拍 {expr}", got == want, f"得 {got}，期望 {want}")

    # --- 7.4 随机对（固定种子，可复现）：与参考模型逐个对拍 ---
    import random
    rng = random.Random(20261119)
    pairs = []
    for _ in range(8):
        pairs.append((rng.randrange(-HALF45, HALF45), rng.randrange(-HALF45, HALF45)))
    bad = 0
    for x, y in pairs:
        for sym, fn in (("&", b_and), ("|", b_or), ("^", b_xor)):
            got, _p, _s = compile_run_py(f"int main(){{int a={x};int b={y};return a{sym}b;}}")
            if got != fn(x, y):
                bad += 1
                print(f"  [FAIL] {x} {sym} {y}: 得 {got}，期望 {fn(x, y)}")
    check("24 组随机对拍（& | ^）", bad == 0, f"{bad} 组错")


# ---------------------------------------------------------------- main
def main():
    print("TriRV 契约测试（TDD）")
    section_regfile_fields()
    section_regfile_exec()
    section_imm_range()
    section_asm_edges()
    section_backend_strict()
    section_big_frame()
    section_bitlaws()

    print("\n" + "=" * 60)
    print(f"契约测试：{PASS}/{PASS + FAIL} 通过")
    if FAIL:
        print(f"失败 {FAIL} 项：")
        for f in FAILED:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
