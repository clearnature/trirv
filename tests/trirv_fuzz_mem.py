#!/usr/bin/env python3
"""trirv_fuzz_mem —— 访存指令差分 fuzz：Python VM vs C VM。

覆盖 ALU fuzz 未涉及的访存语义：
  - SW/SH/SB 写入与 LW/LB/LBU/LH/LHU 读回的 tryte 序、零扩展、半字合成
  - 地址计算 = 有符号 base + 7-trit sign-extend imm（含负偏移、越界）
  - 越界地址读回 0 / 写入被忽略（两版内存边界一致性）
  - 写入后紧跟读取的 store-to-load 转发

做法：每个 case 先向 scratch 区写一组已知 tryte 模式，再从多种地址读回，
把读回 word 写入结果区；Python/C 两边对结果区字节流做对比。

用法：python3 trirv_fuzz_mem.py [种子] [case数]
"""
import os, sys, struct, subprocess, tempfile, random
import os
import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import trirv_isa as isa
from trirv_vm import VM, s

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根（重组后上跳一层）
CVM = os.environ.get("TRIRV_VM_BIN", os.path.join(R, "vm", "bin", "trirv_vm"))
BUF = 1000                     # 结果区
SCRATCH = 5000                 # 暂存区（远离 BUF 与 sp=4096 起步的栈）
MEMSZ = 1 << 20
e = isa.encode
MOD3_7 = 3 ** 7
HX = isa.HALF_X

def emit_load(rd, val):
    """任意有符号 45-trit 值装载（3×15-trit 分段，复用与 ALU fuzz 相同方案）。"""
    out = []
    word = val % isa.MOD_X
    a = word // (3**30); b = (word // (3**15)) % (3**15); c = word % (3**15)
    def load20(r, v):
        o = []; hi = v // MOD3_7; lo = v % MOD3_7
        if lo > (MOD3_7 - 1)//2: hi += 1; lo -= MOD3_7
        o.append(e("LUI", rd=r, imm=hi % (3**13)))
        if lo: o.append(e("ADDI", rd=r, rs1=r, imm=lo % MOD3_7))
        return o
    out += load20(rd, a)
    out.append(e("SLLI", rd=rd, rs1=rd, imm=15))
    out += load20(26, b); out.append(e("ADD", rd=rd, rs1=rd, rs2=26))
    out.append(e("SLLI", rd=rd, rs1=rd, imm=15))
    out += load20(26, c); out.append(e("ADD", rd=rd, rs1=rd, rs2=26))
    return out

STORES = ["SW", "SH", "SB"]
LOADS  = ["LW", "LB", "LBU", "LH", "LHU"]
AMOS   = ["AMOADD","AMOSWAP","AMOAND","AMOOR","AMOXOR","AMOMIN","AMOMAX"]

def gen_atomic_case(rng, idx):
    """A 扩展序列：初始化内存 -> LRW 建保留 -> SCW/AMO -> 读回最终值。
    结果区写 2 个 word：操作返回值 + 内存最终值。"""
    insts = []
    insts += emit_load(5, SCRATCH)                  # x5 = 地址
    init = rng.randint(0, isa.MOD_X - 1)
    insts += emit_load(6, init)
    insts.append(e("SW", rs2=6, rs1=5, imm=0))      # 内存初始
    src = rng.choice([rng.randint(0, isa.MOD_X-1), rng.randint(-HX, HX)])
    insts += emit_load(6, src)                      # x6 = 源值
    mode = rng.randrange(3)
    if mode == 0:      # 纯 LRW/SCW：返回 LR 读值(x7) + SC 状态(x8)
        insts.append(e("LRW", rd=7, rs1=5, rs2=0))
        insts.append(e("SCW", rd=8, rs1=5, rs2=6))
        desc = f"LRW/SCW(init={init},src={s(src)})"
    elif mode == 1:    # AMO：返回 old(x7) + 内存新值(x8)
        op = rng.choice(AMOS)
        insts.append(e(op, rd=7, rs1=5, rs2=6))
        insts.append(e("LW", rd=8, rs1=5, imm=0))
        desc = f"{op}(init={init},src={s(src)})"
    else:              # LRW -> 普通 SW 干扰 -> SCW（应失败返回 1）
        insts.append(e("LRW", rd=7, rs1=5, rs2=0))
        insts += emit_load(9, rng.randint(0, isa.MOD_X-1))
        insts.append(e("SW", rs2=9, rs1=5, imm=0))  # 干扰写，破坏保留
        insts.append(e("SCW", rd=8, rs1=5, rs2=6))
        desc = f"LRW/SW干扰/SCW(init={init})"
    return insts, desc, 2

def gen_case(rng, idx):
    """写一个 word 到 scratch[off]，再从 scratch[off2] 用各宽度读回 x7。
    返回 (insts, desc, nout)。"""
    insts = []
    insts += emit_load(5, SCRATCH)
    val = rng.randint(0, isa.MOD_X - 1) if rng.randrange(2) else rng.randint(0, 3**10)
    insts += emit_load(6, val)
    woff = rng.choice([0, 1, 2, 5, 8, 9, 17, 24]) if rng.randrange(5) else rng.choice([-1, -9, 30, MEMSZ])
    st = rng.choice(STORES)
    insts.append(e(st, rs2=6, rs1=5, imm=woff % MOD3_7))
    loff = woff if rng.randrange(2) else rng.choice([0, 1, 2, 8, 9, 17, 24, -1, MEMSZ+5])
    ld = rng.choice(LOADS)
    insts.append(e(ld, rd=7, rs1=5, imm=loff % MOD3_7))
    desc = f"{st}(off={s(isa.sign_extend_imm(woff%MOD3_7))})->{ld}(off={s(isa.sign_extend_imm(loff%MOD3_7))}) v={val}"
    return insts, desc, 1

def build_program(cases):
    prog = []
    prog += emit_load(3, BUF)
    total_words = 0
    for insts, _, nout in cases:
        prog += insts
        prog.append(e("SW", rs2=7, rs1=3, imm=0))
        prog.append(e("ADDI", rd=3, rs1=3, imm=9))
        total_words += 1
        if nout == 2:
            prog.append(e("SW", rs2=8, rs1=3, imm=0))
            prog.append(e("ADDI", rd=3, rs1=3, imm=9))
            total_words += 1
    prog += emit_load(10, 1)
    prog += emit_load(11, BUF)
    prog += emit_load(12, total_words * 9)
    prog.append(e("ADDI", rd=17, rs1=0, imm=64)); prog.append(e("ECALL"))
    prog.append(e("ADDI", rd=17, rs1=0, imm=93));  prog.append(e("ECALL"))
    return prog

def make_trv(prog):
    data = b"TRV1" + struct.pack("<III", 1, len(prog), 0)
    for r in prog: data += struct.pack("<I", r & 0xFFFFFFFF)
    return data

def run_py(prog, steps):
    vm = VM(prog); vm.run(max_steps=steps); return bytes(vm.output)

def run_c(prog):
    with tempfile.NamedTemporaryFile(suffix=".trv", delete=False) as f:
        f.write(make_trv(prog)); path = f.name
    try:
        return subprocess.run([CVM, path], capture_output=True, timeout=30).stdout
    finally:
        os.unlink(path)

def word_of(b): return sum(b[i]*3**(5*i) for i in range(9)) % isa.MOD_X

def main():
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    N = int(sys.argv[2]) if len(sys.argv) > 2 else 1000
    if not os.path.exists(CVM):
        print(f"[SKIP] C VM 不存在: {CVM}"); return 2
    rng = random.Random(seed)
    cases = []
    for i in range(N):
        if rng.randrange(4) == 0:              # 25% 原子序列
            cases.append(gen_atomic_case(rng, i))
        else:
            cases.append(gen_case(rng, i))
    prog = build_program(cases)
    steps = len(prog) * 4 + 1000
    py_out, c_out = run_py(prog, steps), run_c(prog)
    ok = py_out == c_out
    print(f"seed={seed} cases={N} inst={len(prog)} py={len(py_out)} c={len(c_out)} "
          f"{'MATCH' if ok else 'MISMATCH'}")
    if ok: return 0
    # 逐 word 定位，并映射回 case 描述
    w2c = []
    for ci, (_,_,nout) in enumerate(cases):
        w2c += [ci] * nout
    bad = 0
    nw = min(len(py_out)//9, len(c_out)//9)
    for i in range(nw):
        pc, cc = py_out[i*9:i*9+9], c_out[i*9:i*9+9]
        if pc != cc:
            bad += 1
            if bad <= 20:
                ci = w2c[i] if i < len(w2c) else -1
                desc = cases[ci][1] if ci >= 0 else "?"
                print(f"  DIFF word#{i} case#{ci}: {desc}  py={s(word_of(pc))} c={s(word_of(cc))}")
    if len(py_out) != len(c_out): print(f"  LENGTH py={len(py_out)} c={len(c_out)}")
    print(f"  共 {bad} 个 word 不一致")
    return 1

if __name__ == "__main__":
    sys.exit(main())
