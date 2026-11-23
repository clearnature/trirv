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
import trirv_vm as vm

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

    print("\n总计 %d PASS / %d FAIL" % (passed, failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
