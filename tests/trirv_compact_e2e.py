#!/usr/bin/env python3
"""TriRV TC 压缩发射端到端契约（T10d，2026-11-23）。

TRV_COMPACT=1 时 trirv_cc 在编码期（asm_line fmt==1/2 检查点）把五类
高频形态压缩成 15-trit TC 指令（C-ADDI/C-LW/C-SW/C-BEQ/C-BNE）。

契约三条：
  1. 语义等价：压缩档与全宽档在同一程序上 PyVM/CVM 退出码与输出逐项一致；
  2. 压缩确实发生：窗口形态用例（叶子函数驻留局部自增、驻留变量真值分支）
     TC 槽计数 > 0，按子格式分类报告（T10e 码长测算的数据种子）；
  3. 门控干净：TRV_COMPACT 未设时 TC 槽 = 0（默认行为不变）。
"""
import os
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


def load_trv(path):
    """解析 .trv：指令槽 + 数据段（与 trirv_arch_e2e 同法）。"""
    data = open(path, "rb").read()
    n_inst, n_seg = struct.unpack_from("<II", data, 8)
    prog = list(struct.unpack_from("<%dI" % n_inst, data, 16))
    off = 16 + 4 * n_inst
    mem = {}
    for _ in range(n_seg):
        addr, ln = struct.unpack_from("<II", data, off)
        off += 8
        mem[addr] = data[off:off + ln]
        off += ln
    return prog, mem


def compile_trv(src, compact):
    cf = tempfile.mktemp(suffix=".c")
    tf = tempfile.mktemp(suffix=".trv")
    with open(cf, "w") as f:
        f.write(src)
    env = dict(os.environ)
    env.pop("TRV_COMPACT", None)
    if compact:
        env["TRV_COMPACT"] = "1"
    r = subprocess.run([CC, cf, tf], capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise RuntimeError("编译失败(compact=%s): %s" % (compact, (r.stderr or r.stdout)[:300]))
    return tf


def tc_stats(prog):
    """统计 TC 槽：首 tryte %27==23 → TC；子格式 = t3..t4。"""
    stats = {"total": 0, "sf": {}}
    for w in prog:
        if (w % 243) % 27 == 23:
            stats["total"] += 1
            sf = (w // 27) % 9
            stats["sf"][sf] = stats["sf"].get(sf, 0) + 1
    return stats


def run_py(prog, mem):
    c = vm.VM(prog, mem_init=mem).run(max_steps=200000)
    return c.exit_code, bytes(c.output)


def run_cvm(tf):
    r = subprocess.run([CVM, tf, "-"], capture_output=True, input=b"")
    errs = [l for l in r.stderr.decode().split() if l.startswith("exit=")]
    return int(errs[0].split("=")[1]) if errs else -1


# (名字, 源码, 期望退出码, 压缩档最少 TC 槽)
# 窗口形态说明：叶子函数里非 spilled 局部/形参驻留 a0-a5（TC 窗口），
# `x = x + K` 复合赋值就地发 ADDI rd,rd → C-ADDI；
# `while(v)` 真值分支发 BEQ v,zero → C-BEQ。
CASES = [
    ("自增循环·驻留局部",
     "int main(){ int x=5; int y=0; while(y<3){ x=x+2; y=y+1; } return x; }",
     11, 2),
    ("驻留参数算术链",
     "int f(int a){ int x=a; x=x+7; x=x+3; return x; } int main(){ return f(5); }",
     15, 2),
    ("真值分支·驻留变量",
     "int f(int a){ int s=0; while(a){ s=s+a; a=a-1; } return s; } int main(){ return f(5); }",
     15, 1),
    ("if 真值分支",
     "int main(){ int a=2; int b=0; if(a){ b=b+1; } if(b){ b=b+2; } return b; }",
     3, 1),
    ("调用与递归（全宽为主）",
     "int fib(int n){ if(n<2){ return n; } return fib(n-1)+fib(n-2); } int main(){ return fib(10); }",
     55, 0),
    ("数组遍历（混合窗口）",
     "int g[8]; int main(){ int i=0; int s=0; while(i<8){ g[i]=i*3; i=i+1; } "
     "while(i>0){ i=i-1; s=s+g[i]; } return s; }",
     84, 1),
    ("多函数窗口交错",
     "int inc3(int v){ v=v+3; return v; } "
     "int main(){ int a=inc3(4); a=a+10; int b=inc3(a); return b; }",
     20, 1),
]


def main():
    for name, src, want, tc_min in CASES:
        tf_full = compile_trv(src, compact=False)
        tf_cpt = compile_trv(src, compact=True)
        prog_full, mem_full = load_trv(tf_full)
        prog_cpt, mem_cpt = load_trv(tf_cpt)

        # 契约 3：门控干净——默认档无 TC 槽
        st_full = tc_stats(prog_full)
        check(name + "·默认档TC=0", st_full["total"], 0)

        # 契约 1：语义等价（PyVM 双档 + CVM 压缩档）
        py_full = run_py(prog_full, mem_full)
        py_cpt = run_py(prog_cpt, mem_cpt)
        cvm_cpt = run_cvm(tf_cpt)
        check(name + "·PyVM等价", py_cpt[0], py_full[0])
        check(name + "·PyVM输出等价", py_cpt[1], py_full[1])
        check(name + "·PyVM期望", py_cpt[0], want)
        check(name + "·CVM一致", cvm_cpt, want)

        # 契约 2：压缩确实发生（窗口形态用例）
        st_cpt = tc_stats(prog_cpt)
        check(name + "·TC槽>=%d" % tc_min, min(st_cpt["total"], 10 ** 9) >= tc_min, True)
        print("     TC 统计: %d 槽 子格式%s（总槽 %d）"
              % (st_cpt["total"], sorted(st_cpt["sf"].items()), len(prog_cpt)))

    print("\n总计 %d PASS / %d FAIL" % (passed, failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
