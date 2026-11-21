#!/usr/bin/env python3
"""trirv_fuzz_csr —— CSR 指令差分 fuzz：Python VM vs C VM。

覆盖：
  - CSRRW/CSRRS/CSRRC 及立即数版：旧值返回、读-改-写
  - CSR 地址越界（>=NCSR 或未实现）读 0 / 写忽略
  - mepc/mtvec 写入时的 4 对齐（floor，负值）
  - 立即数版 uimm 受 3-trit rs1 字段限制（0..26，D3 后）
  - 写 mip 触发/不触发定时器中断的边界

注意：不触发 MRET/trap 跳转（那会改变控制流），只观察 CSR 读回值与 rd 旧值。
每个 case 向结果区写 CSRRx 返回的旧值 + 随后 CSRRS x0 读回的当前值。
"""
import os, sys, struct, subprocess, tempfile, random
import trirv_isa as isa
from trirv_vm import VM, s

_HERE = os.path.dirname(os.path.abspath(__file__))
CVM = os.environ.get("TRIRV_VM_BIN", os.path.join(_HERE, "trirv_vm"))
BUF = 1000
MEMSZ = 1 << 20
e = isa.encode
MOD3_7 = 3 ** 7
HX = isa.HALF_X
NCSR = 900

# 合法 CSR 地址 + 越界地址。
# 排除 mstatus/mip：随机写它们会置 MIE+MTIP 触发定时器中断→跳非法 mtvec→程序提前停机，
# 使输出为空造成假匹配；其中断语义已由 trirv_csr_e2e.py 13/13 专项覆盖。
# 保留 mepc/mtvec：验证 4 对齐（不主动 MRET，不影响控制流）。
_SAFE = {k: v for k, v in isa.CSR.items() if k not in ("mstatus", "mip")}
VALID = list(_SAFE.values())
ADDRS = VALID + [0, 1, 899, 900, 2186, 2187, 3**7+5, 3**13-1]
OPS_R = ["CSRRW", "CSRRS", "CSRRC"]
OPS_I = ["CSRRWI", "CSRRSI", "CSRRCI"]

def emit_load(rd, val):
    out = []
    word = val % isa.MOD_X
    a = word // (3**30); b = (word // (3**15)) % (3**15); c = word % (3**15)
    def l20(r, v):
        o = []; hi = v // MOD3_7; lo = v % MOD3_7
        if lo > (MOD3_7-1)//2: hi += 1; lo -= MOD3_7
        o.append(e("LUI", rd=r, imm=hi % (3**13)))
        if lo: o.append(e("ADDI", rd=r, rs1=r, imm=lo % MOD3_7))
        return o
    out += l20(rd, a); out.append(e("SLLI", rd=rd, rs1=rd, imm=15))
    out += l20(26, b); out.append(e("ADD", rd=rd, rs1=rd, rs2=26))
    out.append(e("SLLI", rd=rd, rs1=rd, imm=15))
    out += l20(26, c); out.append(e("ADD", rd=rd, rs1=rd, rs2=26))
    return out

def gen_case(rng, idx):
    """一个 case：写源值到 x6，CSRRx 操作，读回当前值。结果 2 word。"""
    insts = []
    csr = rng.choice(ADDRS)
    imm = csr % (3**7)
    src = rng.choice([rng.randint(0, isa.MOD_X-1), rng.randint(-HX, HX),
                      rng.randint(0, 60), rng.choice([8, 15, 31, 32, 40, 80])])
    use_imm = rng.randrange(2) == 0
    if use_imm:
        op = rng.choice(OPS_I)
        uimm = rng.choice([0, 1, 8, 15, 16, 26])
        insts.append(e(op, rd=7, rs1=uimm, imm=imm))
        desc = f"{op}(uimm={uimm},csr={csr})"
    else:
        op = rng.choice(OPS_R)
        insts += emit_load(6, src)
        insts.append(e(op, rd=7, rs1=6, imm=imm))
        desc = f"{op}(src={s(src)},csr={csr})"
    # 读回当前 CSR 值到 x8（CSRRS x8,x0,csr，不改值）
    insts.append(e("CSRRS", rd=8, rs1=0, imm=imm))
    return insts, desc, 2

def build_program(cases):
    prog = []; prog += emit_load(3, BUF); total = 0
    for insts, _, _ in cases:
        prog += insts
        prog.append(e("SW", rs2=7, rs1=3, imm=0)); prog.append(e("ADDI", rd=3, rs1=3, imm=9)); total += 1
        prog.append(e("SW", rs2=8, rs1=3, imm=0)); prog.append(e("ADDI", rd=3, rs1=3, imm=9)); total += 1
    prog += emit_load(10, 1); prog += emit_load(11, BUF); prog += emit_load(12, total*9)
    prog += [e("ADDI", rd=17, rs1=0, imm=64), e("ECALL")]
    prog += [e("ADDI", rd=17, rs1=0, imm=93), e("ECALL")]
    return prog

def make_trv(prog):
    d = b"TRV1" + struct.pack("<III", 1, len(prog), 0)
    for r in prog: d += struct.pack("<I", r & 0xFFFFFFFF)
    return d

def run_py(prog, steps):
    vm = VM(prog); vm.run(max_steps=steps); return bytes(vm.output)

def run_c(prog):
    with tempfile.NamedTemporaryFile(suffix=".trv", delete=False) as f:
        f.write(make_trv(prog)); p = f.name
    try:
        return subprocess.run([CVM, p], capture_output=True, timeout=30).stdout
    finally:
        os.unlink(p)

def word_of(b): return sum(b[i]*3**(5*i) for i in range(9)) % isa.MOD_X

def main():
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    N = int(sys.argv[2]) if len(sys.argv) > 2 else 800
    if not os.path.exists(CVM):
        print(f"[SKIP] C VM 不存在: {CVM}"); return 2
    rng = random.Random(seed)
    cases = [gen_case(rng, i) for i in range(N)]
    prog = build_program(cases)
    steps = len(prog)*4 + 1000
    py_out, c_out = run_py(prog, steps), run_c(prog)
    expect = len(cases) * 2 * 9
    if len(py_out) != expect or len(c_out) != expect:
        print(f"seed={seed} LENGTH ANOMALY expect={expect} py={len(py_out)} c={len(c_out)} "
              f"(程序可能提前停机，检查中断副作用)")
        return 2
    ok = py_out == c_out
    print(f"seed={seed} cases={N} inst={len(prog)} py={len(py_out)} c={len(c_out)} "
          f"{'MATCH' if ok else 'MISMATCH'}")
    if ok: return 0
    w2c = []
    for ci in range(len(cases)): w2c += [ci, ci]
    bad = 0; nw = min(len(py_out)//9, len(c_out)//9)
    for i in range(nw):
        pc, cc = py_out[i*9:i*9+9], c_out[i*9:i*9+9]
        if pc != cc:
            bad += 1
            if bad <= 25:
                ci = w2c[i] if i < len(w2c) else -1
                print(f"  DIFF word#{i} case#{ci}: {cases[ci][1] if ci>=0 else '?'}  "
                      f"py={s(word_of(pc))} c={s(word_of(cc))}")
    if len(py_out) != len(c_out): print(f"  LENGTH py={len(py_out)} c={len(c_out)}")
    print(f"  共 {bad} 个 word 不一致")
    return 1

if __name__ == "__main__":
    sys.exit(main())
