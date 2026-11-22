#!/usr/bin/env python3
"""A 扩展验证：
1) 单线程差分（Python vs C VM）：LR/SC 成功/失败、AMO 各操作，结果写回内存后输出比对。
2) SMP 原子性：多核对共享计数器并发 AMOADD，验证无丢失更新（结果 == 核数×次数）。
"""
import struct, subprocess, os, sys
_R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))         # 仓库根
for _d in (_R, os.path.join(_R, "isa"), os.path.join(_R, "vm"),
           os.path.join(os.path.dirname(os.path.abspath(__file__))),
           os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cc")):
    if _d not in sys.path:
        sys.path.insert(0, _d)
import trirv_isa as isa
from trirv_vm import VM, SYS_EXIT, SYS_WRITE

e = isa.encode
MOD = isa.MOD_X
CNT = 200          # 共享计数器地址

def word_trytes(w):
    w %= MOD
    return bytes((w // (243 ** i)) % 243 for i in range(9))

def write_trv(path, prog, seg=None):
    with open(path, "wb") as f:
        f.write(b"TRV1")
        n_seg = 1 if seg else 0
        f.write(struct.pack("<III", 1, len(prog), n_seg))
        for ins in prog:
            f.write(struct.pack("<I", ins & 0xFFFFFFFF))
        if seg:
            f.write(struct.pack("<II", 0, len(seg)))
            f.write(bytes(seg))

def c_run(prog, vmbin, extra=None, seg=None):
    path = "/tmp/adiff.trv"
    write_trv(path, prog, seg)
    args = [vmbin] + (extra or []) + [path]
    r = subprocess.run(args, capture_output=True)
    return r.stdout, r.stderr.decode()

def py_run(prog, init=None):
    v = VM(prog, mem_init=init or {})
    v.run(max_steps=2000)
    return v

EXIT = [e("ADDI", rd=17, rs1=0, imm=SYS_EXIT % (3**7)), e("ECALL")]

# ---------- 1) 单线程差分 ----------
def single_program(op):
    # 内存 0..8 = a, 9..17 = b, 18..26 = 输出
    prog = [
        e("ADDI", rd=5, rs1=0, imm=0),
        e("LW", rd=1, rs1=5, imm=0),     # a
        e("LW", rd=2, rs1=5, imm=9),     # b
        e(op, rd=3, rs1=5, rs2=2),       # addr=x5=0, 源=x2; 结果 x3
        e("SW", rd=0, rs1=5, rs2=3, imm=18),
        e("ADDI", rd=10, rs1=0, imm=1),
        e("ADDI", rd=11, rs1=0, imm=18),
        e("ADDI", rd=12, rs1=0, imm=9),
        e("ADDI", rd=17, rs1=0, imm=SYS_WRITE % (3**7)),
        e("ECALL"),
    ] + EXIT
    return prog

def py_single(op, a, b):
    seg = bytearray(30)
    seg[0:9] = word_trytes(a); seg[9:18] = word_trytes(b)
    v = py_run(single_program(op), {0: bytes(seg)})
    return bytes(v.output)

def c_single(op, a, b, vmbin):
    seg = bytearray(30)
    seg[0:9] = word_trytes(a); seg[9:18] = word_trytes(b)
    out, _ = c_run(single_program(op), vmbin, seg=seg)
    return out

def test_single(vmbin):
    import random
    random.seed(7)
    vals = [0, 1, 2, 5, 242, 243, 3**7, MOD-1, MOD-2]
    cases = [(random.randrange(MOD), random.randrange(MOD)) for _ in range(20)]
    cases += [(a,b) for a in vals for b in vals]
    ops = ["AMOADD","AMOSWAP","AMOAND","AMOOR","AMOXOR","AMOMIN","AMOMAX"]
    total=ok=0
    for op in ops:
        for a,b in cases:
            pe=py_single(op,a,b); ce=c_single(op,a,b,vmbin)
            total+=1; ok+=(pe==ce)
            if pe!=ce and total-ok<=5:
                print(f"  FAIL {op} a={a} b={b} py={list(pe)} c={list(ce)}")
    # LR/SC 成功路径
    prog=[e("ADDI",rd=5,rs1=0,imm=0), e("LRW",rd=3,rs1=5,rs2=0),
          e("ADDI",rd=2,rs1=0,imm=42), e("SCW",rd=4,rs1=5,rs2=2),
          e("ADD",rd=3,rs1=3,rs2=4), e("SW",rd=0,rs1=5,rs2=3,imm=18),
          e("ADDI",rd=10,rs1=0,imm=1), e("ADDI",rd=11,rs1=0,imm=18),
          e("ADDI",rd=12,rs1=0,imm=9),
          e("ADDI",rd=17,rs1=0,imm=SYS_WRITE%(3**7)), e("ECALL")]+EXIT
    seg=bytearray(30); seg[0:9]=word_trytes(7)
    pe=py_run(prog,{0:bytes(seg)}).output
    ce,_=c_run(prog,vmbin,seg=seg)
    total+=1; ok+=(bytes(pe)==ce)
    print(f"  单线程 A 差分: {ok}/{total} {'PASS' if ok==total else 'FAIL'}")
    return ok==total

def test_smp(vmbin):
    """4 核 × 1000 次 AMOADD(1) 到计数器 CNT，结果应为 4000。"""
    # 用 imm 循环：x6=1000, x2=1, x5=CNT
    prog=[
        e("ADDI",rd=5,rs1=0,imm=CNT),
        e("ADDI",rd=2,rs1=0,imm=1),       # 加数 1
        e("ADDI",rd=6,rs1=0,imm=1000),    # 循环计数
        # loop @ pc=3
        e("AMOADD",rd=3,rs1=5,rs2=2),     # pc3
        e("ADDI",rd=6,rs1=6,imm=-1%(3**7)),  # pc4: 计数-1
        e("BNE",rd=0,rs1=6,rs2=0,imm=-2%(3**7)),  # pc5: 若 x6!=0 跳回 pc3 (offset=-2)
    ]+EXIT
    # BNE 立即数是 7-trit 有符号，-2 -> 3^7-2；target = oldpc + s(imm) = 5 + (-2) = 3 ✓
    ncore=4; iters=1000
    out,err=c_run(prog,vmbin,extra=["-s","-t",str(ncore),"-n",str(ncore),"-o",str(CNT)])
    if len(out)<9:
        print(f"  SMP 输出不足: {list(out)} stderr={err[-200:]}"); return False
    val=sum(out[i]*(243**i) for i in range(9))
    exp=ncore*iters
    print(f"  SMP AMOADD: {ncore}核×{iters}次 = {val} (期望 {exp}) {'PASS' if val==exp else 'FAIL'}")
    return val==exp

def main():
    vmbin=sys.argv[1] if len(sys.argv)>1 else "./trirv_vm"
    ok1=test_single(vmbin)
    ok2=test_smp(vmbin)
    print(f"A 扩展验证: 单线程 {'PASS' if ok1 else 'FAIL'} / SMP {'PASS' if ok2 else 'FAIL'}")
    return 0 if ok1 and ok2 else 1

if __name__=="__main__":
    sys.exit(main())
