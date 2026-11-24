#!/usr/bin/env python3
"""TriRV AMP 端到端契约（T11，2026-11-23）。

多实例协作（mailbox + 共享页窗口），双 VM 同构：
  - syscall：coreid(5010) / msend(5011) / mrecv(5012)
  - 共享窗：地址空间顶部固定 64 tryte（__amp_shared 保留符号， guests 声明
    `int __amp_shared[7];` 编译器绑定 AMP_SHARED_BASE）
  - 调度：单步协作轮转 + 步间窗口同步（顺序一致性，CVM -a N / PyVM run_amp）

契约：
  1. mailbox 往返：manager 发请求、worker 平方回传，双 VM 退出码一致；
  2. 共享页窗口：写-握手-改-握手-读回，双 VM 逐值一致；
  3. 错误路径：无此核 -3 / 邮箱空 -2 / 非 AMP 实例 msend -3、mrecv -2、coreid 0；
  4. 默认档不受影响：-a 未给时行为与既有单实例完全一致（既有 55 套回归守卫）。
"""
import os
import re
import struct
import subprocess
import sys
import tempfile

import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import trirv_isa as isa
import trirv_vm as vm

E = isa.encode                              # 词级构造助手（load_const/程序构建共用）

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CC = os.path.join(ROOT, "cc", "bin", "trirv_cc")
CVM = os.path.join(ROOT, "vm", "bin", "trirv_vm")

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


def compile_trv(src):
    cf = tempfile.mktemp(suffix=".c")
    tf = tempfile.mktemp(suffix=".trv")
    with open(cf, "w") as f:
        f.write(src)
    r = subprocess.run([CC, cf, tf], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("编译失败: %s" % (r.stderr or r.stdout)[:300])
    return tf


def load_trv(path):
    import _trvio
    prog, mem, _packed = _trvio.load_any(path)
    return prog, mem


def run_cvm(tf, amp=0):
    args = [CVM] + (["-a", str(amp)] if amp else []) + [tf, "-"]
    r = subprocess.run(args, capture_output=True, input=b"")
    errs = [l for l in r.stderr.decode().split() if l.startswith("exit=")]
    return int(errs[0].split("=")[1]) if errs else -1


def run_py_plain(tf):
    prog, mem = load_trv(tf)
    c = vm.VM(prog, mem_init=mem).run(max_steps=100000)
    return c.exit_code


def run_py_amp(tf, ncores):
    prog, mem = load_trv(tf)
    cores, hub = vm.run_amp(prog, ncores, mem_init=mem, max_steps=200000)
    return cores, hub

# ---- 用例 1：mailbox 往返（manager 发 1..4，worker 逐个平方回传，求和退出）----
MAILBOX_SRC = """
int main(){
  int me=coreid();
  int buf[8];
  int k=1; int sum=0; int n=-1; int c=0;
  if(me==0){
    while(k<=4){
      buf[0]=k;
      msend(1,buf,1);
      n=-1;
      while(n<0){ n=mrecv(1,buf,4); }
      sum=sum+buf[0];
      k=k+1;
    }
    return sum;
  }
  while(c<4){
    n=-1;
    while(n<0){ n=mrecv(0,buf,4); }
    buf[0]=buf[0]*buf[0];
    msend(0,buf,1);
    c=c+1;
  }
  return 0;
}
"""

# ---- 用例 2：共享页窗口（写-握手-加倍-握手-读回）----
SHARED_SRC = """
int __amp_shared[7];
int main(){
  int me=coreid();
  int buf[8]; int n=-1;
  if(me==0){
    __amp_shared[0]=21;
    buf[0]=1;
    msend(1,buf,1);
    n=-1;
    while(n<0){ n=mrecv(1,buf,4); }
    return __amp_shared[0];
  }
  while(n<0){ n=mrecv(0,buf,4); }
  __amp_shared[0]=__amp_shared[0]*2;
  buf[0]=1;
  msend(0,buf,1);
  return 0;
}
"""

# ---- 用例 3：错误路径（单实例即可测）----
NEG_SRC = """
int main(){
  int buf[8];
  int r1=msend(5,buf,1);      /* -3：无此核（单实例仅核 0） */
  int r2=mrecv(0,buf,4);      /* -2：邮箱空 */
  int me=coreid();            /* 0 */
  int r3=msend(0,buf,1);      /* -3：自发自收 */
  return (-r1)*100 + (-r2)*10 + (me+r3);
}
"""


# ---- ADR-23①：mrecvw 阻塞收（worker 不再轮询）----
MRECVW_SRC = """
int main(){
  int me=coreid();
  int buf[8];
  int k=1; int sum=0; int c=0;
  if(me==0){
    while(k<=4){
      buf[0]=k;
      msend(1,buf,1);
      int n=mrecvw(1,buf,4);
      sum=sum+buf[0];
      k=k+1;
    }
    return sum;
  }
  while(c<4){
    int n=mrecvw(0,buf,4);
    buf[0]=buf[0]*buf[0];
    msend(0,buf,1);
    c=c+1;
  }
  return 0;
}
"""

# ---- ADR-23①：全体挂起 = 死锁诚实停机 ----
DEADLOCK_SRC = """
int main(){
  int buf[8];
  int n=mrecvw(0,buf,4);   /* 单核自等：无人可唤醒 → 死锁 */
  return n;
}
"""


def load_const(rd, val):
    """词级常量装载：LUI 高段 + ADDI 低段（LUI 语义两 VM 已锁 rd=imm×3^7，
    §11 契约）。imm7 窗口 ±1093——手写 ADDI 装大立即数会静默回绕（5014→640
    实证坑），必须走两段。hi 取整使 lo 落窗内。"""
    hi = (val + 1093) // 2187
    lo = val - hi * 2187
    if hi == 0:
        return [E("ADDI", rd=rd, rs1=0, imm=lo)]
    return [E("LUI", rd=rd, imm=hi), E("ADDI", rd=rd, rs1=rd, imm=lo)]


def mipi_prog_build(defer_mie):
    """mipi 自发自收（CLINT MSIP 模型）：exit = 计数+10（陷入=11，未陷入=10）。
    defer_mie=True：mipi 先发（MIE=0 挂起保持）、后开 MIE 兑现。"""
    main = [E("ADDI", rd=5, rs1=0, imm=0)]          # 占位：mtvec 值最后回填
    main.append(E("CSRRW", rd=0, rs1=5, imm=773))   # mtvec ← x5
    if not defer_mie:
        main.append(E("CSRRSI", rd=0, rs1=8, imm=768))   # MIE=1
    main.append(E("ADDI", rd=9, rs1=0, imm=0))      # 计数器
    main += load_const(17, 5014)                    # a7 = mipi（两段装载）
    main.append(E("ADDI", rd=10, rs1=0, imm=0))     # dst = 0（self-IPI）
    main.append(E("ECALL"))
    if defer_mie:
        main.append(E("CSRRSI", rd=0, rs1=8, imm=768))   # 此时开 MIE → 步末兑现
    main.append(E("ADDI", rd=10, rs1=9, imm=10))    # mret 回这里
    main += load_const(17, 93)
    main.append(E("ECALL"))
    pad = [E("ADDI", rd=0, rs1=0, imm=0)]
    handler = [E("ADDI", rd=9, rs1=9, imm=1), E("MRET")]
    H = len(main) + len(pad)                        # handler slot
    prog = main + pad + handler
    prog[0] = E("ADDI", rd=5, rs1=0, imm=H * 4)     # 回填 mtvec（tryte 地址）
    return prog


def mhartid_prog():
    """每核：mhartid 高区读 → 投递核号给核 0 → 三连 mrecvw 求和 → exit(6)。
    非 0 核随后全体挂起（死锁收尾）——挂起核不拖累运行核的调度证明。"""
    main = []
    main.append(E("CSRRS.H", rd=9, rs1=0, imm=1673))   # x9 = mhartid（3860=0xF14 高区）
    main.append(E("SW", rs2=9, rs1=0, imm=900))        # mem[900] = 核号
    main += load_const(17, 5011)                       # a7 = msend
    main.append(E("ADDI", rd=10, rs1=0, imm=0))        # dst=0
    main.append(E("ADDI", rd=11, rs1=0, imm=900))      # buf
    main.append(E("ADDI", rd=12, rs1=0, imm=1))        # len
    main.append(E("ECALL"))                            # 核 0 自环 -3，其余投递
    main.append(E("ADDI", rd=15, rs1=0, imm=3))        # 循环计数 = 3
    loop_start = len(main)
    main += load_const(17, 5013)                       # a7 = mrecvw
    main.append(E("ADDI", rd=10, rs1=0, imm=2186))     # src = -1（任意）
    main.append(E("ADDI", rd=11, rs1=0, imm=910))      # buf
    main.append(E("ADDI", rd=12, rs1=0, imm=1))        # max
    main.append(E("ECALL"))                            # 核 0 阻塞等
    main.append(E("LW", rd=13, rs1=0, imm=910))        # 读回核号
    main.append(E("ADD", rd=14, rs1=14, rs2=13))       # 累加
    main.append(E("ADDI", rd=15, rs1=15, imm=2186))    # -1
    main.append(E("BNE", rs1=15, rs2=0, imm=loop_start - len(main)))  # 回 mrecvw
    main.append(E("ADD", rd=10, rs1=14, rs2=0))        # a0 = 1+2+3 = 6
    main += load_const(17, 93)
    main.append(E("ECALL"))                            # 核 0 exit(6)；其余已挂起
    return main


TCC = os.path.join(ROOT, "tinycc", "trirv-tcc")

# ---- ADR-23②：tri-tcc 管线 AMP（__tri_syscall 直走 5010-5013，阻塞收握手）----
TCC_AMP_SRC = """
long __tri_syscall(long n, long a0, long a1, long a2, long a3, long a4, long a5);
long buf[8];
int main(){
  long me = __tri_syscall(5010, 0, 0, 0, 0, 0, 0);          /* coreid */
  if(me==0){
    long sum=0;
    for(long k=1;k<=4;k++){
      buf[0]=k;
      __tri_syscall(5011, 1, (long)buf, 1, 0, 0, 0);        /* msend */
      __tri_syscall(5013, 1, (long)buf, 4, 0, 0, 0);        /* mrecvw 阻塞收 */
      sum=sum+buf[0];
    }
    return (int)sum;
  }
  for(long c=0;c<4;c++){
    __tri_syscall(5013, 0, (long)buf, 4, 0, 0, 0);          /* mrecvw 挂起等 */
    buf[0]=buf[0]*buf[0];
    __tri_syscall(5011, 0, (long)buf, 1, 0, 0, 0);          /* msend */
  }
  return 0;
}
"""


def compile_tcc_amp():
    cf = tempfile.mktemp(suffix=".c")
    tf = tempfile.mktemp(suffix=".trv")
    with open(cf, "w") as f:
        f.write(TCC_AMP_SRC)
    r = subprocess.run([TCC, "-nostdlib", cf, "-o", tf],
                       capture_output=True, text=True, timeout=60)
    return r, tf


def run_py_words(prog, ncores):
    """词级程序 → PyVM AMP（不经编译器）"""
    return vm.run_amp(prog, ncores, max_steps=100000)


def run_cvm_words(prog, amp=1):
    """词级程序 → TRV1 容器 → CVM -a N"""
    import struct as _s
    tf = tempfile.mktemp(suffix=".trv")
    with open(tf, "wb") as f:
        f.write(b"TRV1")
        f.write(_s.pack("<III", 1, len(prog), 0))
        for x in prog:
            f.write(_s.pack("<I", x))
    return run_cvm(tf, amp=amp)


def main():
    # 用例 1：mailbox 往返
    tf = compile_trv(MAILBOX_SRC)
    check("mailbox·PyVM-AMP 双核退出码", [c.exit_code for c in run_py_amp(tf, 2)[0]], [30, 0])
    check("mailbox·CVM-AMP manager 退出码", run_cvm(tf, amp=2), 30)
    # 单实例：coreid=0 走 manager 路径，等一个永不到来的 worker → 步耗尽停机。
    # 契约点是"双 VM 行为一致"（都停在 0），不是具体退出码。
    check("mailbox·PyVM 单实例步耗尽", run_py_plain(tf), 0)
    check("mailbox·CVM 单实例一致", run_cvm(tf), 0)

    # 用例 2：共享页窗口
    tf2 = compile_trv(SHARED_SRC)
    cores2, hub2 = run_py_amp(tf2, 2)
    check("sharedwin·PyVM-AMP 读回加倍值", cores2[0].exit_code, 42)
    check("sharedwin·CVM-AMP 一致", run_cvm(tf2, amp=2), 42)
    # 窗口内容直查：42 必须落在共享窗地址（而非别处全局）——证明保留符号绑定。
    # tryte 词按 243^i 组合（每格 1 tryte，5 trit）。
    word = 0
    for i in range(9):
        word += cores2[0].mem[vm.AMP_SHARED_BASE + i] * (243 ** i)
    check("sharedwin·窗口内容=42 且地址在共享窗", word, 42)

    # 用例 3：错误路径（非 AMP 常规运行）
    tf3 = compile_trv(NEG_SRC)
    # guest 返回 3*100+2*10+(0-3)=317；exit 契约 = &0xFF = 61（双 VM 一致）
    check("errors·PyVM (-3,-2,自环-3,coreid0)→317&0xFF", run_py_plain(tf3), 61)
    check("errors·CVM 一致", run_cvm(tf3), 61)
    prog3, _ = load_trv(tf3)
    c3 = vm.VM(prog3).run(max_steps=5000)
    check("errors·PyVM 默认 VM() ctor amp=None 路径", c3.exit_code, 61)

    # ---- ADR-23①②（批次 4）：mrecvw 5013 / mipi 5014 / mhartid / 4 核 ----

    # 用例 4：mrecvw 阻塞收——worker 挂起等消息（无轮询），msend 唤醒
    tf4 = compile_trv(MRECVW_SRC)
    c4, h4 = run_py_amp(tf4, 2)
    check("mrecvw·PyVM 双核退出码（阻塞收等价轮询）", [c.exit_code for c in c4], [30, 0])
    check("mrecvw·CVM 一致", run_cvm(tf4, amp=2), 30)

    # 用例 5：mrecvw 全体阻塞 = 死锁诚实停机（单核自等）
    tf5 = compile_trv(DEADLOCK_SRC)
    c5, h5 = run_py_amp(tf5, 1)
    check("mrecvw·PyVM 单核自等死锁旗", (h5.deadlocked, [c.exit_code for c in c5]), (True, [0]))
    check("mrecvw·CVM 单核死锁快速停机", run_cvm(tf5, amp=1), 0)

    # 用例 6：mipi 核间中断（CLINT MSIP 模型，自发自收）——词级直构
    # （handler 需 MRET/CSR，C 方言无 CSR 内建；exit = 计数+10，中断发生=11，
    #   若中断未发生则直落 exit 路径 = 10——退路对照内建）
    mipi_prog = mipi_prog_build(defer_mie=False)
    c6, h6 = run_py_words(mipi_prog, 1)
    check("mipi·PyVM 自发自收陷入（exit=计数+10）", (c6[0].exit_code, c6[0].trap_log[-1][0] if c6[0].trap_log else None),
          (11, isa.CAUSE_MSI))
    check("mipi·CVM 一致", run_cvm_words(mipi_prog, amp=1), 11)

    # 用例 6b：MIE=0 时挂起保持——mipi 先发、后开 MIE 才兑现（挂起待使能）
    c6b, _ = run_py_words(mipi_prog_build(defer_mie=True), 1)
    check("mipi·PyVM MIE=0 挂起待使能", c6b[0].exit_code, 11)
    check("mipi·CVM 挂起一致", run_cvm_words(mipi_prog_build(defer_mie=True), amp=1), 11)

    # 用例 7：mhartid（0xF14 高区读）+ 4 核——各核投递核号，核 0 求和 exit=6；
    # 其余核 mrecvw 挂起等永远不来的消息 → 核 0 退出后全体阻塞 = 死锁停机。
    # 同时证明：挂起核不拖累运行核（核 0 在其余挂起期间照常推进）。
    check("mhartid·PyVM 4 核求和（0xF14 高区读=核号）",
          [c.exit_code for c in run_py_words(mhartid_prog(), 4)[0]][:1], [6])
    check("mhartid·CVM -a 4 核 0 一致", run_cvm_words(mhartid_prog(), amp=4), 6)

    # 用例 8：tri-tcc 管线 AMP（ADR-23②）——tri-tcc 产物 TRV1，双 VM 同构
    if os.path.exists(TCC):
        r8, tf8 = compile_tcc_amp()
        if r8.returncode == 0:
            prog8, mem8 = load_trv(tf8)
            c8, _ = vm.run_amp(prog8, 2, mem_init=mem8, max_steps=200000)
            check("tri-tcc AMP·PyVM mrecvw 双核握手", [c.exit_code for c in c8], [30, 0])
            check("tri-tcc AMP·CVM -a 2 一致", run_cvm(tf8, amp=2), 30)
        else:
            check("tri-tcc AMP·编译", "ok", (r8.stderr or "")[:160])
    else:
        print("SKIP tri-tcc AMP（trirv-tcc 未构建）")

    print("\n总计 %d PASS / %d FAIL" % (passed, failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
