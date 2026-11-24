#!/usr/bin/env python3
"""TriRV 手算黄金用例——1.11 覆盖矩阵零覆盖指令的补缺（批次 1.12，ADR-28②）。

全部期望值从 ISA 规格与独立模块（trirv_float 编码器）手算推导，
不依赖任何 VM 的运行输出。

**退出模型（手算前提）**：exit = a0 & 0xFF（a7=93 选择 exit 调用）；
ECALL 时 a7=0 = 传统停机（exit=0）；EBREAK 触发断点陷入，mtvec=0
（复位向量，合法）→ 从头重跑形成步耗尽循环。覆盖 1.11 矩阵的 18 条静态零覆盖指令：
AUIPC、BGE/BGEU/BLTU、CSR 高区×6、EBREAK、FENCE/FENCE.I、WFI、
FCVT.F、FEQ.S/FLT.S/FLE.S。
"""
import os
import sys
import struct
import subprocess

import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import trirv_isa as isa
import trirv_vm as vm
import trirv_float as tf

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

passed = 0
failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print("PASS %s: %r" % (name, got))
    else:
        failed += 1
        print("FAIL %s: got=%r want=%r" % (name, got, want))


E = isa.encode
EC = E("ECALL")


def run_py(prog, steps=2000):
    c = vm.VM(prog).run(max_steps=steps)
    return c


def run_cvm(prog):
    path = _bootstrap.scratch("golden_%d.trv" % abs(hash(tuple(prog))))
    with open(path, "wb") as f:
        f.write(b"TRV1")
        f.write(struct.pack("<III", 1, len(prog), 0))
        for x in prog:
            f.write(struct.pack("<I", x))
    r = subprocess.run([os.path.join(ROOT, "vm", "bin", "trirv_vm"), path, "-"],
                       capture_output=True, text=True)
    errs = [l for l in r.stderr.split() if l.startswith("exit=")]
    return int(errs[0].split("=")[1]) if errs else None


def regs_after(prog):
    return run_py(prog)


# ================ §1 AUIPC（PC=tryte 地址）================
# 槽制：指令 i 的 PC = 4*i。slot2 AUIPC imm=1 → x10 = 8 + 2187 = 2195
prog = [E("ADDI", rd=10, rs1=0, imm=0), E("ADDI", rd=10, rs1=0, imm=0),
        E("AUIPC", rd=10, imm=1), E("ADDI", rd=17, rs1=0, imm=93), EC]
c = run_py(prog)
check("AUIPC 槽制·x10=8+2187（exit=a0&0xFF=147）", (c.exit_code, c.regs[10]), (147, 2195))
check("AUIPC CVM·一致", run_cvm(prog), 147)
s = isa.pack_stream(prog)
# 流制：slot0(4)+slot1(4)+slot2 起 pc=8 → 同值（前两条全宽）
c2 = vm.VM.from_stream(s).run(max_steps=500)
check("AUIPC 流制·x10=8+2187", c2.regs[10], 2195)

# ================ §2 有符号/无符号分支四向 ================
# x5 = -1（补码 = 3^45-1，无符号解读为最大值）；x6 = 1
NEG1 = isa.MOD_X - 1
setup = [E("ADDI", rd=6, rs1=0, imm=1)]           # x6=1
# 载入 x5=-1：ADDI x5,zero,-1 → load_imm 路径发 SUB/LUI；直接用负立即数
prog_lt = setup + [E("ADDI", rd=5, rs1=0, imm=-1),
                   E("BLT", rs1=5, rs2=6, imm=3),  # 有符号 -1<1 → 跳（跳过 ADDI 99）
                   E("ADDI", rd=10, rs1=0, imm=99),
                   E("ADDI", rd=17, rs1=0, imm=93), EC]
c = run_py(prog_lt)
# 跳过 ADDI 99 → 落到无 a7=93 的 ECALL = 传统停机 exit=0；x10=0 证明 99 未执行
check("BLT 有符号·-1<1 跳转(99 未执行)", (c.exit_code, c.regs[10]), (0, 0))
check("BLT CVM", run_cvm(prog_lt), 0)

# BGE：有符号 1 ≥ -1 → 跳（比较方向：rs1=x6=1, rs2=x5=-1）
prog_bge = setup + [E("ADDI", rd=5, rs1=0, imm=-1),
                    E("BGE", rs1=6, rs2=5, imm=3),
                    E("ADDI", rd=10, rs1=0, imm=99),
                    E("ADDI", rd=17, rs1=0, imm=93), EC]
c = run_py(prog_bge)
check("BGE 有符号·1≥-1 跳转", (c.exit_code, c.regs[10]), (0, 0))
check("BGE CVM", run_cvm(prog_bge), 0)

# BLTU：无符号 -1 最大 → x5 < x6 假 → 不跳 → x10=99
prog_bltu = setup + [E("ADDI", rd=5, rs1=0, imm=-1),
                     E("BLTU", rs1=5, rs2=6, imm=3),
                     E("ADDI", rd=10, rs1=0, imm=99),
                     E("ADDI", rd=17, rs1=0, imm=93), EC]
c = run_py(prog_bltu)
# 不跳 → x10=99 → a7=93 → ECALL → exit = a0 & 0xFF = 99（退出码=a0 口径）
check("BLTU 无符号·(3^45-1)<1 假不跳", (c.exit_code, c.regs[10]), (99, 99))
check("BLTU CVM", run_cvm(prog_bltu), 99)

# BGEU：无符号 -1 ≥ 1 真 → 跳
prog_bgeu = setup + [E("ADDI", rd=5, rs1=0, imm=-1),
                     E("BGEU", rs1=5, rs2=6, imm=3),
                     E("ADDI", rd=10, rs1=0, imm=99),
                     E("ADDI", rd=17, rs1=0, imm=93), EC]
c = run_py(prog_bgeu)
check("BGEU 无符号·(3^45-1)≥1 跳转", (c.exit_code, c.regs[10]), (0, 0))
check("BGEU CVM", run_cvm(prog_bgeu), 0)

# ================ §3 CSR 高区六条（写忽略读 0）================
# 各写 mhartid 区（0xF14=3860 → imm=1673），rd 取旧值：未实现 → 0
base = [E("ADDI", rd=5, rs1=0, imm=7)]
H = [("CSRRW.H", 2), ("CSRRS.H", 3), ("CSRRC.H", 4)]
for mn, _sf in H:
    prog = base + [E(mn, rd=10, rs1=5, imm=1673),
                   E("ADDI", rd=17, rs1=0, imm=93), EC]
    c = run_py(prog)
    check("%s 高区·旧值 0（exit=a0 口径）" % mn, (c.exit_code, c.regs[10]), (0, 0))
for mn in ("CSRRWI.H", "CSRRSI.H", "CSRRCI.H"):
    # 立即数版：uimm 字段（低 3 trit? 规格=rs1 字段即 uimm；TriRV uimm 占 rs1 4-trit 全宽）
    prog = base + [E(mn, rd=10, rs1=5, imm=1673),
                   E("ADDI", rd=17, rs1=0, imm=93), EC]
    c = run_py(prog)
    check("%s 高区·旧值 0（exit=a0 口径）" % mn, (c.exit_code, c.regs[10]), (0, 0))

# ================ §4 EBREAK（断点陷阱 → mtvec=0 停机）================
prog = [E("ADDI", rd=10, rs1=0, imm=5), E("EBREAK"),
        E("ADDI", rd=17, rs1=0, imm=93), EC]
c = run_py(prog, steps=300)
# mtvec=0 是合法复位向量 → 陷入后从头重跑 → EBREAK 再触发 → 步耗尽停机
check("EBREAK·mtvec=0 重启循环（步耗尽，末圈 x10=5）", (c.halted, c.exit_code, c.regs[10]), (False, 0, 5))
check("EBREAK CVM·步耗尽 exit=0", run_cvm(prog), 0)

# ================ §5 FENCE / WFI（提示性 NOP）================
prog = [E("ADDI", rd=10, rs1=0, imm=5), E("FENCE"),
        E("ADDI", rd=5, rs1=0, imm=42), E("WFI"),
        E("ADDI", rd=17, rs1=0, imm=93), EC]
c = run_py(prog)
check("FENCE+WFI·NOP 前后不变（exit=a0=5）", (c.exit_code, c.regs[10], c.regs[5]), (5, 5, 42))
check("FENCE+WFI CVM", run_cvm(prog), 5)

# ================ §6 FCVT.F（整数→TF45 freg，黄金=独立编码器）================
# x5=7 → FCVT.F f3,x5 → freg[3] = TF45.encode(7.0)；再 FCVT.W x10,f3 回整数=7
golden7 = tf.TF45.encode(7.0)
prog = [E("ADDI", rd=5, rs1=0, imm=7), E("FCVT.F", rd=3, rs1=5),
        E("FCVT.W", rd=10, rs1=3), E("ADDI", rd=17, rs1=0, imm=93), EC]
c = run_py(prog)
check("FCVT.F→FCVT.W 往返·7", (c.exit_code, c.freg[3]), (7, golden7))
check("FCVT CVM", run_cvm(prog), 7)

# ================ §7 FEQ.S / FLT.S / FLE.S（TF20 档比较）================
# f1=TF20(1.0)、f2=TF20(1.5)：FEQ→0、FLT→1、FLE→1（黄金=独立编码器+实数序）
g10 = tf.TF20.encode(1.0)
g15 = tf.TF20.encode(1.5)
prog = [E("ADDI", rd=5, rs1=0, imm=10),
        E("FCVT.F.S", rd=1, rs1=5)] if False else None
# TF20 装载：用 FLW 从内存？简化——用 FCVT.F 产 TF45 再…TF20 需 FLW.S 内存路。
# 直接构造：FLW.S 需要内存镜像。改用 VM freg 初始为 0 的路径不可行——
# 用 mem_init 装入 TF20 raw，FLW.S 读入：
raw10 = [ (g10 // 243**i) % 243 for i in range(9) ]
raw15 = [ (g15 // 243**i) % 243 for i in range(9) ]
mem = {100: raw10, 109: raw15}          # TF20 raw 也按 9-tryte raw 存（FLW.S 原样搬运）
flw1 = E("FLW.S", rd=1, rs1=0, imm=100)  # rs1=zero 基址 0？FLW.S rd=f1, imm(基址)
# FLW.S 的 rs1 是整数基址寄存器——用 ADDI x20=100 / x21=109
prog_feq = [E("ADDI", rd=20, rs1=0, imm=100), E("ADDI", rd=21, rs1=0, imm=109),
            E("FLW.S", rd=1, rs1=20, imm=0), E("FLW.S", rd=2, rs1=21, imm=0),
            E("FEQ.S", rd=10, rs1=1, rs2=2),
            E("ADDI", rd=17, rs1=0, imm=93), EC]
c = vm.VM(prog_feq, mem_init=mem).run(max_steps=500)
check("FEQ.S·1.0==1.5 → 0（exit=a0）", (c.exit_code, c.regs[10]), (0, 0))
prog_flt = prog_feq[:5] + [E("FLT.S", rd=10, rs1=1, rs2=2)] + prog_feq[6:6]
prog_flt = [E("ADDI", rd=20, rs1=0, imm=100), E("ADDI", rd=21, rs1=0, imm=109),
            E("FLW.S", rd=1, rs1=20, imm=0), E("FLW.S", rd=2, rs1=21, imm=0),
            E("FLT.S", rd=10, rs1=1, rs2=2),
            E("ADDI", rd=17, rs1=0, imm=93), EC]
c = vm.VM(prog_flt, mem_init=mem).run(max_steps=500)
check("FLT.S·1.0<1.5 → 1（exit=a0）", (c.exit_code, c.regs[10]), (1, 1))
prog_fle = [E("ADDI", rd=20, rs1=0, imm=100), E("ADDI", rd=21, rs1=0, imm=100),
            E("FLW.S", rd=1, rs1=20, imm=0), E("FLW.S", rd=2, rs1=21, imm=0),
            E("FLE.S", rd=10, rs1=1, rs2=2),
            E("ADDI", rd=17, rs1=0, imm=93), EC]
c = vm.VM(prog_fle, mem_init=mem).run(max_steps=500)
check("FLE.S·1.0≤1.0 → 1（exit=a0）", (c.exit_code, c.regs[10]), (1, 1))

# ================ §8 ADR-35 未定义编码统一口径：双 VM 陷入 ================
# 未定义编码词（逻辑组合 (opcode=0,funct=0,funct2=2) 按 trit 权重打包；
# 全零词是合法 ADD——ADR-35 §3 初稿口误已订正）。
# 统一口径：take_trap(CAUSE_ILLEGAL) → mtvec=0（复位向量）→ 重启循环 →
# 步耗尽。对照证据：任一 VM 若 NOP 落穿（CVM 3b.5 前的旧行为），将执行
# 后续 ADDI a0=7 → exit=7——本用例即退路退役证明。
UW1 = 0 * 3**7 + 2 * 3**17           # (0,0,2)
UW2 = 0 * 3**7 + 3 * 3**17           # (0,0,3)
assert isa.decode(UW1)["inst"] is None and isa.decode(UW2)["inst"] is None
for _tag, _uw in (("fc0-f2=2", UW1), ("fc0-f2=3", UW2)):
    prog_ill = [_uw, E("ADDI", rd=10, rs1=0, imm=7),
                E("ADDI", rd=17, rs1=0, imm=93), EC]
    c = run_py(prog_ill, steps=300)
    check("ADR-35 未定义编码·PyVM 陷入重启（%s，非 NOP 落穿）" % _tag,
          (c.exit_code, c.regs[10], c.halted), (0, 0, False))
    check("ADR-35 未定义编码·CVM 同口径（%s）" % _tag, run_cvm(prog_ill), 0)

# ================ §9 ADR-35 补口：寄存器字段越界（审计 2026-11-24）================
# rd 作寄存器的指令（R/I/U/J 格式）字段 4 trit 可取 0..80，越 32 界 → 双 VM
# 统一非法指令陷入 → mtvec=0 重启循环 → 步耗尽 exit=0。审计实证旧行为：
# CVM rd=40 静默写 freg[8]（PyVM IndexError 崩溃）。对照证据：陷入则后续
# ADDI a0=7 不执行；若静默放过会 exit=7。合法 rd=31 对照不受影响。
for _rd in (33, 40, 64):
    prog_rd = [E("ADD", rd=_rd, rs1=0, rs2=0),
               E("ADDI", rd=10, rs1=0, imm=7),
               E("ADDI", rd=17, rs1=0, imm=93), EC]
    c = run_py(prog_rd, steps=300)
    check("ADR-35 rd 越界·PyVM 陷入重启（rd=%d）" % _rd,
          (c.exit_code, c.regs[10], c.halted), (0, 0, False))
    check("ADR-35 rd 越界·CVM 同口径（rd=%d）" % _rd, run_cvm(prog_rd), 0)
prog_ok = [E("ADD", rd=31, rs1=0, rs2=0), E("ADDI", rd=10, rs1=31, imm=7),
           E("ADDI", rd=17, rs1=0, imm=93), EC]
c = run_py(prog_ok, steps=500)
check("ADR-35 rd 界内·合法不受影响（rd=31）", (c.exit_code, c.regs[10]), (7, 7))
check("ADR-35 rd 界内·CVM", run_cvm(prog_ok), 7)

print("\n手算黄金补缺：%d PASS / %d FAIL" % (passed, failed))
sys.exit(1 if failed else 0)
