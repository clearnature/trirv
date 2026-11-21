#!/usr/bin/env python3
"""trirv_fuzz —— 指令级随机差分 fuzz：Python VM vs C VM。

思路：在一个程序里批量生成随机 ALU 操作，每条结果 STORE 到连续结果区，
最后 write(1, BUF, N*8) 输出全部 8-tryte word 的 tryte 字节。
两边 VM 捕获 stdout 对比字节流；不一致时二分定位到具体 case。

覆盖手工测试难以穷举的组合：MULH 系列 floor 语义（负积低位非零）、
DIV/REM 符号与除零、SLL/SRL/SRA 大移位与负数、SLT/SLTU 边界、
三值逻辑 TMIX/TSEXT 等，以及所有 I 型立即数指令。

用法：
    python3 trirv_fuzz.py [种子] [case数]
默认 seed=0, N=1500。C VM 二进制取 ./trirv_vm（可用 TRIRV_VM_BIN 覆盖）。
"""
import os, sys, struct, subprocess, tempfile, random
import trirv_isa as isa
from trirv_vm import VM, s

_HERE = os.path.dirname(os.path.abspath(__file__))
CVM = os.environ.get("TRIRV_VM_BIN", os.path.join(_HERE, "trirv_vm"))
BUF = 1000                     # 结果区起始地址（<1093，单 ADDI 可装载）
MOD3_7 = 3 ** 7                # 2187

e = isa.encode

# ---------------------------------------------------------------- 装载 helper
def _load20(rd, val):
    """装载幅值 < 3^20 的有符号值到 rd：LUI(imm13*3^7) + ADDI(7-trit)。"""
    out = []
    hi = val // MOD3_7
    lo = val % MOD3_7
    if lo > (MOD3_7 - 1) // 2:
        hi += 1
        lo -= MOD3_7
    out.append(e("LUI", rd=rd, imm=hi % (3**13)))
    if lo:
        out.append(e("ADDI", rd=rd, rs1=rd, imm=lo % MOD3_7))
    return out

TMP = 26
SEG = 15                      # 每段 15 trit（< 3^15，_load20 可容纳）
def emit_load(rd, val):
    """把任意有符号 45-trit 值 val 装到 rd（补码 word 语义）。
    拆 3 段各 15 trit：rd = ((a<<15)+b)<<15 + c；每段走 _load20。"""
    word = val % isa.MOD_X
    a = word // (3**30)
    b = (word // (3**15)) % (3**15)
    c = word % (3**15)
    out = _load20(rd, a)
    out.append(e("SLLI", rd=rd, rs1=rd, imm=SEG))
    out += _load20(TMP, b)
    out.append(e("ADD", rd=rd, rs1=rd, rs2=TMP))
    out.append(e("SLLI", rd=rd, rs1=rd, imm=SEG))
    out += _load20(TMP, c)
    out.append(e("ADD", rd=rd, rs1=rd, rs2=TMP))
    return out

# 有意思的操作数池：覆盖 0、±1、7-trit/13-trit 边界、3 的幂、全 45-trit 极值
HX = isa.HALF_X
EDGE = [HX, -HX, HX - 1, -HX + 1,          # 3 补码正负上限（对称，无 INT_MIN/-1 溢出）
        3**44, -(3**44), 3**30, -(3**30),
        isa.MOD_X - 1, 1 - isa.MOD_X]       # word 全 1 / 全 2

def operand(rng):
    kind = rng.randrange(8)
    if kind == 0:
        return rng.choice([0, 1, -1, 2, -2, 3, -3, 5, -5, 7, -7, 13, -13])
    if kind == 1:
        return rng.randint(-1093, 1093)              # 7-trit 有符号边界
    if kind == 2:
        return rng.choice([3**6, -(3**6), 3**7, -(3**7), 3**8, -(3**8)])
    if kind == 3:
        return rng.randint(-(3**13), 3**13)          # 13-trit 区
    if kind == 4:
        return rng.choice([364, 365, 1092, 1093, 2186, -2186])
    if kind == 5:
        return rng.choice(EDGE)                      # 全幅极值：验证软件除法/乘法等价性
    if kind == 6:
        return rng.randint(-HX, HX)                  # 全 45-trit 随机
    return rng.randint(-(3**10), 3**10)

# (助记符, 是否 R 型双源, 是否单源)
R_BIN = ["ADD","SUB","SLL","SRL","SRA","SLT","SLTU","XOR","OR","AND",
         "MUL","MULH","MULHSU","MULHU","DIV","DIVU","REM","REMU",
         "TMIN","TMAX","TAND","TOR"]
R_UN  = ["TNOT","TNEG","TSEXT","TPACK","TUNPACK"]
I_IMM = ["ADDI","SLTI","SLTIU","XORI","ORI","ANDI","SLLI","SRLI","SRAI"]

def gen_case(rng, idx):
    """生成一个 case 的指令列表 + 人类可读描述。结果写入 x7。"""
    desc = None
    pick = rng.randrange(4)
    t = R_BIN[rng.randrange(len(R_BIN))]
    if pick == 0:
        a, b = operand(rng), operand(rng)
        insts = emit_load(5, a) + emit_load(6, b)
        insts.append(e(t, rd=7, rs1=5, rs2=6))
        desc = f"{t}({a},{b})"
    elif pick == 1:
        a = operand(rng)
        t = R_UN[rng.randrange(len(R_UN))]
        insts = emit_load(5, a) + [e(t, rd=7, rs1=5)]
        desc = f"{t}({a})"
    elif pick == 2:
        a = operand(rng)
        t = I_IMM[rng.randrange(len(I_IMM))]
        imm = rng.randrange(3**7)
        insts = emit_load(5, a) + [e(t, rd=7, rs1=5, imm=imm)]
        desc = f"{t}({a},imm={imm})"
    else:
        # 移位量刻意取 0..60 及 3^7-1 边界，触发 sh>=45 与溢出路径
        a = operand(rng)
        sh = rng.choice([0, 1, 2, 7, 13, 44, 45, 46, 63, 2186, rng.randrange(3**7)])
        t = rng.choice(["SLL","SRL","SRA"])
        insts = emit_load(5, a) + emit_load(6, sh) + [e(t, rd=7, rs1=5, rs2=6)]
        desc = f"{t}({a},sh={sh})"
    return insts, desc

def build_program(cases):
    prog = []
    # x3 = BUF（结果写指针）
    prog += emit_load(3, BUF)
    for insts, _desc in cases:
        prog += insts
        prog.append(e("SW", rs2=7, rs1=3, imm=0))   # *x3 = x7
        prog.append(e("ADDI", rd=3, rs1=3, imm=9))  # x3 += 9
    # write(1, BUF, N*9)
    prog += emit_load(10, 1)
    prog += emit_load(11, BUF)
    prog += emit_load(12, len(cases) * 9)
    prog.append(e("ADDI", rd=17, rs1=0, imm=64))
    prog.append(e("ECALL"))
    prog.append(e("ADDI", rd=17, rs1=0, imm=93))    # exit(0)
    prog.append(e("ECALL"))
    return prog

def make_trv(prog):
    data = b"TRV1" + struct.pack("<III", 1, len(prog), 0)
    for r in prog:
        data += struct.pack("<I", r & 0xFFFFFFFF)
    return data

def run_py(prog, max_steps):
    vm = VM(prog)
    vm.run(max_steps=max_steps)
    return bytes(vm.output)

def run_c(prog):
    with tempfile.NamedTemporaryFile(suffix=".trv", delete=False) as f:
        f.write(make_trv(prog)); path = f.name
    try:
        r = subprocess.run([CVM, path], capture_output=True, timeout=30)
        return r.stdout
    finally:
        os.unlink(path)

def word_of(chunk):
    return sum(chunk[i] * 3**(5*i) for i in range(9)) % isa.MOD_X

def main():
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    N = int(sys.argv[2]) if len(sys.argv) > 2 else 1500
    if not os.path.exists(CVM):
        print(f"[SKIP] C VM 不存在: {CVM}")
        return 2
    rng = random.Random(seed)
    cases = [gen_case(rng, i) for i in range(N)]
    prog = build_program(cases)
    max_steps = len(prog) * 4 + 1000

    py_out = run_py(prog, max_steps)
    c_out = run_c(prog)

    ok = (py_out == c_out)
    print(f"seed={seed} cases={N} inst={len(prog)} py_bytes={len(py_out)} c_bytes={len(c_out)} "
          f"{'MATCH' if ok else 'MISMATCH'}")
    if ok:
        return 0

    # 逐 case 定位差异
    bad = 0
    n = min(len(py_out) // 9, len(c_out) // 9, N)
    for i in range(n):
        pc = py_out[i*9:i*9+9]; cc_ = c_out[i*9:i*9+9]
        if pc != cc_:
            bad += 1
            if bad <= 20:
                print(f"  DIFF case#{i}: {cases[i][1]}  py={s(word_of(pc))} c={s(word_of(cc_))} "
                      f"pybytes={list(pc)} cbytes={list(cc_)}")
    if len(py_out) != len(c_out):
        print(f"  LENGTH DIFF py={len(py_out)} c={len(c_out)}")
    print(f"  共 {bad} 个 case 不一致")
    return 1

if __name__ == "__main__":
    sys.exit(main())
