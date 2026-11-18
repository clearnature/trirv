#!/usr/bin/env python3
"""TriRV ISA 完整性回归测试。

覆盖上一轮体检发现的全部缺口：
  A. 编解码往返：INST 全表 encode->decode 自洽（含 TSEXT / EBREAK）
  B. Python VM：9 条补齐指令 + TSEXT + LB/LH 符号扩展 + MULH 负数
  C. C VM 差分对照：同一程序在 Python VM 与 C VM 上结果一致
  D. 汇编器：U/J 13-trit 立即数不再截断

用法：python3 test_isa_completeness.py
"""
import os, struct, subprocess, sys, tempfile
import trirv_isa as isa
from trirv_vm import VM, s

PASS = 0
FAIL = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")

def run_py(insts, init_regs=None, init_mem=None, max_steps=20):
    v = VM(list(insts))
    if init_regs:
        for r, val in init_regs.items():
            v.regs[r] = val % isa.MOD_X
    if init_mem:
        for addr, val in init_mem.items():
            v.mem[addr] = val % 243
    v.run(max_steps=max_steps)
    return v

def sign5(t):   return t if t <= 121 else t - 243
def sign10(t):  return t if t <= (3**10-1)//2 else t - 3**10

e = isa.encode

# ---------------------------------------------------------------- A. 编解码往返
print("\n=== A. 编解码往返（全指令表）===")
rt_ok = rt_bad = 0
for name, (op, fn, f2, fmt) in isa.INST.items():
    # 为每种格式构造一组代表性参数
    if fmt == "R":
        kw = dict(rd=5, rs1=3, rs2=7)
    elif fmt == "I":
        kw = dict(rd=5, rs1=3, imm=11)
    elif fmt in ("S", "B"):
        kw = dict(rs1=3, rs2=7, imm=11)
    else:  # U / J
        kw = dict(rd=5, imm=3000)
    raw = isa.encode(name, **kw)
    d = isa.decode(raw)
    if d["inst"] == name:
        rt_ok += 1
    else:
        rt_bad += 1
        print(f"    往返失败: {name} -> {d['inst']} (trits={d['trits']})")
check(f"全部 {len(isa.INST)} 条指令 encode->decode 自洽", rt_bad == 0,
      f"(ok={rt_ok}, bad={rt_bad})")
check("EBREAK 不再退化为 ECALL", isa.decode(e("EBREAK"))["inst"] == "EBREAK")
check("TSEXT 已在指令表", "TSEXT" in isa.INST and isa.decode(e("TSEXT", rd=1, rs1=2))["inst"] == "TSEXT")

# ---------------------------------------------------------------- B. Python VM 语义
print("\n=== B. Python VM 补齐指令语义 ===")
# SRA (寄存器) / SRAI (立即数): 向零取整
for val, sh, exp in [(-90, 1, -30), (90, 1, 30), (-6, 1, -2)]:
    v = run_py([e("SRA", rd=1, rs1=2, rs2=3), e("ECALL")], {2: val, 3: sh})
    check(f"SRA({val},{sh})={exp}", s(v.regs[1]) == exp, f"got {s(v.regs[1])}")
for val, imm, exp in [(-91, 1, -30), (91, 1, 30)]:
    v = run_py([e("SRAI", rd=1, rs1=2, imm=imm), e("ECALL")], {2: val})
    check(f"SRAI({val},{imm})={exp} (向零)", s(v.regs[1]) == exp, f"got {s(v.regs[1])}")
# SLLI / SRLI
v = run_py([e("SLLI", rd=1, rs1=2, imm=2), e("ECALL")], {2: 5})
check("SLLI(5,2)=45", s(v.regs[1]) == 45, f"got {s(v.regs[1])}")
v = run_py([e("SRLI", rd=1, rs1=2, imm=1), e("ECALL")], {2: 90})
check("SRLI(90,1)=30", s(v.regs[1]) == 30, f"got {s(v.regs[1])}")
# 分支 BGE / BLTU / BGEU：构造 taken 时跳到 x1=1，not taken 时 x1=0
def branch_test(mn, r1, r2, exp_taken):
    prog = [e(mn, rs1=1, rs2=2, imm=2),          # taken -> pc+2 (跳过下条)
            e("ADDI", rd=3, rs1=0, imm=1),       # not taken 路径: x3=1
            e("ADDI", rd=4, rs1=0, imm=9),       # taken 目标: x4=9
            e("ECALL")]
    v = run_py(prog, {1: r1, 2: r2})
    taken = s(v.regs[3]) == 0
    return taken == exp_taken
check("BGE 有符号 >= 正确", branch_test("BGE", 5, 5, True))
check("BGE 不低于时不跳", branch_test("BGE", 3, 5, False))
check("BLTU 无符号 < 正确", branch_test("BLTU", 0, 2, True))  # 0 < 2
check("BGEU 无符号 >= 正确", branch_test("BGEU", 3, 2, True))
# AUIPC
v = run_py([e("AUIPC", rd=1, imm=3), e("ECALL")])
check("AUIPC(imm=3)=3*3^7", s(v.regs[1]) == 3 * 3**7, f"got {s(v.regs[1])}")
# FENCE 不抛异常
v = run_py([e("FENCE"), e("ADDI", rd=1, rs1=0, imm=7), e("ECALL")])
check("FENCE 作为 NOP 不中断执行", s(v.regs[1]) == 7)
# TSEXT
for raw, exp in [(242, -1), (5, 5), (200, -43)]:
    v = run_py([e("TSEXT", rd=1, rs1=2), e("ECALL")], {2: raw})
    check(f"TSEXT({raw})={exp}", s(v.regs[1]) == exp, f"got {s(v.regs[1])}")
# EBREAK 现在可触发断点陷入（U 态 mtvec 需先设置；这里只验证不抛"未实现指令"）
v = VM([e("EBREAK"), e("ECALL")]); v.priv = 3
try:
    v.run(max_steps=3); ebreak_ok = True
except RuntimeError as ex:
    ebreak_ok = False
check("EBREAK 不再抛未实现异常", ebreak_ok)
# LB/LH 按 ABI 为零扩展（char=tryte=0..242 无符号）；符号扩展由 TSEXT 提供
for raw in (242, 100):
    v = run_py([e("LB", rd=1, rs1=2, imm=0), e("ECALL")], {2: 100}, {100: raw})
    check(f"LB({raw})={raw} 零扩展(char ABI)", v.regs[1] == raw, f"got {v.regs[1]}")
v = run_py([e("LBU", rd=1, rs1=2, imm=0), e("ECALL")], {2: 100}, {100: 242})
check("LBU(242)=242", v.regs[1] == 242)
lo10 = 59048 % 243; hi10 = 59048 // 243  # 3^10-1 = 59048 -> 10-trit 全 2
v = run_py([e("LH", rd=1, rs1=2, imm=0), e("ECALL")], {2: 100}, {100: lo10, 101: hi10})
check("LH 零扩展 = 59048", v.regs[1] == 59048, f"got {v.regs[1]}")
# MULH 负数: (-1)*2 高半 = -1 (floor)
v = run_py([e("MULH", rd=1, rs1=2, rs2=3), e("ECALL")], {2: -1, 3: 2})
check("MULH(-1,2)=-1 (floor)", s(v.regs[1]) == -1, f"got {s(v.regs[1])}")

# ---------------------------------------------------------------- D. 汇编器
print("\n=== D. 汇编器 13-trit 立即数 ===")
import trirv_asm
out, _ = trirv_asm.assemble("LUI x1, 74565")
check("LUI 74565 不截断", isa.decode(out[0])["imm"] == 74565)
out, _ = trirv_asm.assemble("AUIPC x2, -1")
check("AUIPC -1 = 3^13-1", isa.decode(out[0])["imm"] == 3**13 - 1)

# ---------------------------------------------------------------- C. C VM 差分
print("\n=== C. Python VM vs C VM 差分对照 ===")
CVM = "/tmp/trirv_vm_test"
if os.path.exists(CVM):
    def dump_trv(insts, seg=None):
        """生成 TRV1 程序；在末尾放 SW 把 x1..x3 写入结果区，供 -o dump。"""
        raws = list(insts)
        data = b"TRV1" + struct.pack("<III", 1, len(raws), 0)
        for r in raws:
            data += struct.pack("<I", r & 0xFFFFFFFF)
        return data

    # 结果写内存地址 1000（9 tryte/字，可用单条 ADDI 装载）。构造：算出 x1，SW x1,0(x2),ECALL
    RESULT = 1000
    def cvm_case(name, setup, op_insts, expected):
        # setup: (regs dict) ; op 计算到 x1
        prog = []
        for r, val in setup.items():
            prog.append(e("ADDI", rd=r, rs1=0, imm=val % 2187))  # 小数用 ADDI 装载
        prog += op_insts
        prog.append(e("ADDI", rd=2, rs1=0, imm=RESULT % 2187))
        prog.append(e("SW", rs2=1, rs1=2, imm=0))
        prog.append(e("ECALL"))
        # Python 侧
        pv = run_py(prog, max_steps=40)
        py_word = pv._load_word(RESULT)
        # C 侧
        trv = dump_trv(prog)
        with tempfile.NamedTemporaryFile(suffix=".trv", delete=False) as f:
            f.write(trv); path = f.name
        try:
            r = subprocess.run([CVM, "-o", str(RESULT), path], capture_output=True, timeout=10)
            c_bytes = r.stdout[:9]
            c_word = sum(c_bytes[i] * 3**(5*i) for i in range(9)) % isa.MOD_X if len(c_bytes) == 9 else None
        finally:
            os.unlink(path)
        check(f"C/Python 一致: {name}", c_word == py_word == expected % isa.MOD_X,
              f"(py={s(py_word)}, c={s(c_word) if c_word is not None else 'ERR'}, exp={s(expected)})")

    # 用能让 ADDI 装载的小立即数做 setup；SRA(6,1) 等
    # setup x2=6(ADDI), x3=1(ADDI)
    cvm_case("SRA(6,1)=2", {2: 6, 3: 1}, [e("SRA", rd=1, rs1=2, rs2=3)], 2)
    cvm_case("SLLI(5,2)=45", {2: 5}, [e("SLLI", rd=1, rs1=2, imm=2)], 45)
    cvm_case("TSEXT(5)=5", {2: 5}, [e("TSEXT", rd=1, rs1=2)], 5)
    cvm_case("SRLI(90,1)=30", {2: 90}, [e("SRLI", rd=1, rs1=2, imm=1)], 30)
else:
    print(f"  [SKIP] C VM 二进制 {CVM} 不存在，先编译: gcc -O2 -o {CVM} trirv_vm.c")

# ---------------------------------------------------------------- E. 第二轮深度审查修复
print("\n=== E. 第二轮审查修复（编译器大栈帧/大常量/短路 + VM 边界）===")
import trirv_cc as cc

def run_c(src, steps=3000000):
    """C 源码 -> 编译 -> Python VM 执行，返回 (a0有符号值, cpu)。"""
    prog, labels, txt, strs = cc.compile_c(src)
    cpu = VM(prog, mem_init=strs or None).run(max_steps=steps)
    return s(cpu.regs[10]), cpu

# E1. 大栈帧：单个函数局部变量区 > 1093（7-trit 上限）时，prologue 抬栈、
#     访存偏移都不能当 7-trit 立即数，否则踩栈/访错地址。跨调用验证局部保留。
BIG = ("void g(){ int b[200]; int i=0; while(i<200){ b[i]=99; i=i+1; } }\n"
       "int main(){ int a[200]; a[150]=11; g(); return a[150]; }")
got, _ = run_c(BIG)
check("大栈帧：跨调用后 a[150] 仍为 11", got == 11, f"got {got}")

# E2. 大常量 >= 3^20：分段装载，覆盖到 3^45-1
for n in (3**20, 3**20-1, 3**21, 10**12, 3**40, 3**44):
    got, _ = run_c(f"int main(){{ return {n}; }}")
    check(f"大常量 {n} 装载正确", got == s(n % isa.MOD_X), f"got {got}, exp {s(n % isa.MOD_X)}")

# E3. 短路求值：左边定结果时不求值右边（右边函数有副作用）
PRE = "int f(int*p){ *p=*p+1; return 1; } "
for expr, exp in [("0 && f(&side)", 0), ("1 || f(&side)", 1),
                  ("1 && f(&side)", 11), ("0 || f(&side)", 11)]:
    src = PRE + f"int main(){{ int side; int r; side=0; r=({expr}); return side*10+r; }}"
    got, _ = run_c(src)
    check(f"短路求值 {expr} (f 副作用={exp//10})", got == exp, f"got {got}, exp {exp}")

# E4. VM 边界（Python 侧）：负移位、负 PC、write 负 count
for op in ("SLLI", "SRLI", "SRAI"):
    v = run_py([e("ADDI", rd=2, rs1=0, imm=5), e(op, rd=1, rs1=2, imm=2186), e("ECALL")])
    check(f"{op} 负移位立即数 -> 0（无符号解释）", v.regs[1] == 0, f"got {v.regs[1]}")
v = run_py([e("ADDI", rd=1, rs1=0, imm=2186), e("JALR", rd=0, rs1=1, imm=0),
            e("ADDI", rd=10, rs1=0, imm=99), e("ECALL")])
check("负 PC（JALR->-1）停机且后续指令不执行", v.halted and v.regs[10] == 0,
      f"halted={v.halted}, x10={v.regs[10]}")
v = run_py([e("ADDI", rd=10, rs1=0, imm=1), e("ADDI", rd=12, rs1=0, imm=2186),
            e("ADDI", rd=17, rs1=0, imm=64), e("ECALL")])
check("write(count=-1) 返回 -EINVAL", s(v.regs[10]) == -22, f"got {s(v.regs[10])}")

# E5. C VM 差分：负移位与 Python 一致（CVM 二进制存在时）
CVM2 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trirv_vm")
if not os.path.exists(CVM2):
    CVM2 = CVM  # 回退到 /tmp/trirv_vm_test
if os.path.exists(CVM2):
    def _dump_trv(insts):
        return b"TRV1" + struct.pack("<III", 1, len(insts), 0) + \
               b"".join(struct.pack("<I", r & 0xFFFFFFFF) for r in insts)
    for op in ("SLLI", "SRLI", "SRAI"):
        prog = [e("ADDI", rd=2, rs1=0, imm=5), e(op, rd=1, rs1=2, imm=2186),
                e("ADDI", rd=3, rs1=0, imm=1000), e("SW", rs2=1, rs1=3, imm=0), e("ECALL")]
        pv = run_py(prog, max_steps=40)
        pyw = pv._load_word(1000)
        with tempfile.NamedTemporaryFile(suffix=".trv", delete=False) as f:
            f.write(_dump_trv(prog)); path = f.name
        try:
            r = subprocess.run([CVM2, "-o", "1000", path], capture_output=True, timeout=5)
            cb = r.stdout[:9]
            cw = sum(cb[i]*3**(5*i) for i in range(9)) % isa.MOD_X if len(cb) == 9 else None
        finally:
            os.unlink(path)
        check(f"C/Python 一致: {op} 负移位 -> 0", cw == pyw == 0,
              f"(py={pyw}, c={cw})")
else:
    print(f"  [SKIP] C VM 二进制不存在，无法做边界差分")

print("\n=== F. PC 按 tryte 编址（指令 i 地址 = 4*i）===")
# F1. JAL 链接地址 = old_pc + 4（下一条 tryte 地址）
v = run_py([e("JAL", rd=1, imm=1), e("ECALL")], max_steps=5)
check("JAL 链接地址=4(tryte)", v.regs[1] == 4, f"ra={v.regs[1]}")
# F2. 分支目标 = old_pc + 4*imm（+2 槽 -> +8 tryte，跳过 99）
v = run_py([e("ADDI", rd=1, rs1=0, imm=1), e("BEQ", rs1=1, rs2=0, imm=2),
            e("ADDI", rd=10, rs1=0, imm=99), e("ADDI", rd=10, rs1=0, imm=7), e("ECALL")],
           max_steps=10)
check("分支 +2 槽跳到 +8 tryte", s(v.regs[10]) == 7, f"x10={s(v.regs[10])}")
# F3. AUIPC 基于 tryte PC（在槽1=地址4 执行，imm=1 -> 4+3^7）
v = run_py([e("ADDI", rd=0, rs1=0, imm=0), e("AUIPC", rd=1, imm=1), e("ECALL")], max_steps=5)
check("AUIPC 基于 tryte PC", s(v.regs[1]) == 4 + 3**7, f"x1={s(v.regs[1])}")
# F4. 负 JALR 目标向下对齐（floor），非法 PC 停机而非回绕到 0
v = run_py([e("ADDI", rd=1, rs1=0, imm=2186), e("JALR", rd=0, rs1=1, imm=0),
            e("ADDI", rd=10, rs1=0, imm=99), e("ECALL")], max_steps=20)
check("负 JALR 停机不回绕", v.halted and v.regs[10] == 0,
      f"halted={v.halted} x10={s(v.regs[10])}")
# F5. mepc/mtvec 存 tryte 地址：非法指令在槽2 -> mepc=8；mtvec=槽3 地址 12
v = run_py([e("ADDI", rd=1, rs1=0, imm=12), e("CSRRW", rd=0, rs1=1, imm=isa.CSR["mtvec"]),
            26, e("MRET")], max_steps=10)   # 槽3: MRET 返回 mepc=8 -> 又踩非法指令, 但 trap 已记录
if v.trap_log:
    check("mepc 存 tryte 地址(8)", v.csr[isa.CSR["mepc"]] == 8, f"mepc={v.csr[isa.CSR['mepc']]}")
    check("mtvec tryte 地址(12)跳转", v.trap_log[0][2] == 12, f"to={v.trap_log[0][2]}")
else:
    check("非法指令触发 trap", False, "trap_log 空")
# F6. C/Python 差分：JAL/分支/负JALR 两版一致
CVM3 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trirv_vm")
if not os.path.exists(CVM3):
    CVM3 = CVM
if os.path.exists(CVM3):
    def _pc_diff(prog, slot):
        # 把寄存器 x10 存到 mem[1000] 供 C VM dump；slot=存 x10 前已完成的跳转
        pv = run_py(prog, max_steps=60)
        pyw = pv._load_word(1000)
        data = b"TRV1" + struct.pack("<III", 1, len(prog), 0) + \
               b"".join(struct.pack("<I", r & 0xFFFFFFFF) for r in prog)
        with tempfile.NamedTemporaryFile(suffix=".trv", delete=False) as f:
            f.write(data); path = f.name
        try:
            r = subprocess.run([CVM3, "-o", "1000", path], capture_output=True, timeout=5)
            cb = r.stdout[:9]
            cw = sum(cb[i]*3**(5*i) for i in range(9)) % isa.MOD_X if len(cb) == 9 else None
        finally:
            os.unlink(path)
        return pyw, cw
    # 分支 +2 槽: x10=7 存 mem[1000]
    prog = [e("ADDI", rd=1, rs1=0, imm=1), e("BEQ", rs1=1, rs2=0, imm=2),
            e("ADDI", rd=10, rs1=0, imm=99), e("ADDI", rd=10, rs1=0, imm=7),
            e("ADDI", rd=3, rs1=0, imm=1000), e("SW", rs2=10, rs1=3, imm=0), e("ECALL")]
    pyw, cw = _pc_diff(prog, 4)
    check("C/Python 一致: tryte 分支", pyw == cw == 7, f"(py={pyw}, c={cw})")

# ---------------------------------------------------------------- G. ISA 决策固化一致性
print("\n=== G. ISA 设计决策固化（§9 D1–D5 已拍板）===")
D = getattr(isa, "ISA_DECISIONS", {})
check("决策表 D1–D5 齐全", set(D) == {"D1", "D2", "D3", "D4", "D5"}, f"keys={sorted(D)}")
check("每项决策含理由与被否决方案",
      all(d.get("reason") and d.get("rejected") for d in D.values()))
# D1：字长 20 trit 恰好填满 4 tryte 容器
check("D1 字长决策 = 实现 W = 20", D.get("D1", {}).get("word_trits") == isa.W == 20)
check("D1 20 trit 恰好填满 4 tryte 容器", isa.W == 4 * 5)
check("D1 否决依据: 3^21 != 2^32（无精确 32-bit 等效）", 3**21 != 2**32)
# D2：显式 3 补码符号扩展
check("D2 方案 = 显式 3 补码规整", "3's-complement" in D.get("D2", {}).get("scheme", ""))
check("D2 全 2 的 7-trit 立即数符号扩展为 -1",
      isa.sign_extend_imm(3**7 - 1) == (-1) % isa.MOD_X)
check("D2 正数符号扩展保持原值", isa.sign_extend_imm(5) == 5)
# D3：32 寄存器 / 4-trit 字段
import trirv_asm as _asm
check("D3 寄存器数决策 = 汇编器唯一寄存器号 = 27",
      D.get("D3", {}).get("regs") == len(set(_asm.REG.values())) == isa.NREG == 27)
check("D3 字段宽度决策 = 实现 rd 字段 3 trit",
      D.get("D3", {}).get("field_trits") == isa.F["rd"][1] - isa.F["rd"][0] + 1 == 3)
check("D3 立即数布局未动: I=13..19 / S,B=3..6+17..19 / U,J=7..19",
      isa.F["rs2"] == (13, 15) and isa.F["rs1"] == (9, 11) and isa.F["rd"] == (3, 5))
check("D3 ABI 别名保留: t0-t6/a0-a7/s0 均 < 27",
      all(_asm.REG[k] < 27 for k in
          ["t0","t1","t2","t3","t4","t5","t6","a0","a7","s0","sp","ra","zero"]))
# D4：I 型 7-trit / U 型 13-trit 立即数
check("D4 I 型立即数决策 = 实现 7 trit",
      D.get("D4", {}).get("i_imm_trits") == 7 and isa.decode(e("ADDI", imm=3**7-1))["imm"] == 3**7-1)
check("D4 U 型立即数决策 = 实现 13 trit",
      D.get("D4", {}).get("u_imm_trits") == 13 and isa.decode(e("LUI", imm=3**13-1))["imm"] == 3**13-1)
# D5：45-trit 数据字长（LP64 不适用于三进制硬件，见 §9）
check("D5 字长决策 = 实现 XLEN = 45", D.get("D5", {}).get("xlen") == isa.XLEN == 45)
import trirv_cc as _cc
check("D5 字长 = 9 tryte (long=pointer 同宽)", _cc.WORD_TRYTES == 9 == isa.XLEN // 5)

print(f"\n=== 总结: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
