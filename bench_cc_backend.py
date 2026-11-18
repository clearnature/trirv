#!/usr/bin/env python3
"""C 编译器 vs Python 栈式编译器 —— 可复现基准。

三组指标：
  1) 编译耗时（CLI 端到端，含进程启动 / 解释器导入）
  2) 生成代码规模（指令条数，越小越好）
  3) 生成代码运行吞吐（同一 VM，比较代码质量）

用法: python3 bench_cc_backend.py [--rounds N]
"""
import os
import statistics
import struct
import subprocess
import sys
import tempfile
import time

import trirv_cc as pycc
import trirv_isa as isa
import trirv_vm as vm

HERE = os.path.dirname(os.path.abspath(__file__))


def gen_scale(nfun):
    """生成 nfun 个函数 + main 的源程序（规模可调）。"""
    lines = [f"int f{i}(int n){{int s=0;int k=1;while(k<=n){{s=s+k*{i + 1};k=k+1;}}return s;}}"
             for i in range(nfun)]
    body = "t=t+f1(i)+f2(i);"
    lines.append("int main(){int t=0;int i=0;while(i<2000){" + body + "i=i+1;}return t;}")
    return "\n".join(lines) + "\n"


PROGRAMS = {
    "小(2 行)": "int sum(int n){int s=0;int i=1;while(i<=n){s=s+i;i=i+1;}return s;}\n"
                "int main(){return sum(10);}\n",
    "中(41 函数)": gen_scale(40),
    "大(201 函数)": gen_scale(200),
}


def cli_compile(bin_, src, rounds):
    """CLI 端到端编译耗时（取最小值）。"""
    tmp = tempfile.mkdtemp()
    cpath = os.path.join(tmp, "a.c")
    with open(cpath, "w") as f:
        f.write(src)
    out = os.path.join(tmp, "a.trv")
    ts = []
    for _ in range(rounds):
        t0 = time.perf_counter()
        r = subprocess.run([*bin_, cpath, out], capture_output=True)
        ts.append((time.perf_counter() - t0) * 1000)
        if r.returncode:
            raise RuntimeError(r.stderr.decode()[:200])
    return min(ts)


def load_trv(path):
    data = open(path, "rb").read()
    _v, n, nseg = struct.unpack_from("<III", data, 4)
    prog = list(struct.unpack_from(f"<{n}I", data, 12))
    return prog


def main():
    rounds = 7
    if "--rounds" in sys.argv:
        rounds = int(sys.argv[sys.argv.index("--rounds") + 1])
    binary = os.path.join(HERE, "trirv_cc")
    py_bin = [sys.executable, os.path.join(HERE, "export_trv.py")]

    print(f"{'源程序':16s}{'Python 编译':>14s}{'C 编译':>12s}{'加速':>8s}"
          f"{'指令(Py)':>11s}{'指令(C)':>9s}{'收缩':>8s}")
    for name, src in PROGRAMS.items():
        tp = cli_compile(py_bin, src, rounds if "小" not in name else 3)
        tc = cli_compile([binary], src, rounds)
        pyp, _, _, _ = pycc.compile_c(src)
        tmp = tempfile.mkdtemp()
        cpath = os.path.join(tmp, "a.c")
        open(cpath, "w").write(src)
        subprocess.run([binary, cpath, os.path.join(tmp, "a.trv")], capture_output=True)
        cp = load_trv(os.path.join(tmp, "a.trv"))
        print(f"{name:16s}{tp:12.1f}ms{tc:10.1f}ms{tp / tc:7.1f}x"
              f"{len(pyp):11d}{len(cp):9d}{len(pyp) / len(cp):7.2f}x")

    # 运行吞吐：同一段热循环，分别用两种编译器产物在 C VM 上跑
    hot = ("int main(){int s=0;int i=1;while(i<=200000){s=s+i*3-i/2;i=i+1;}return s;}\n")
    print("\n运行吞吐（C VM，热循环 20 万次）")
    for label in ("Python 栈式", "C 寄存器式"):
        tmp = tempfile.mkdtemp()
        cpath = os.path.join(tmp, "h.c")
        tpath = os.path.join(tmp, "h.trv")
        open(cpath, "w").write(hot)
        if label.startswith("Python"):
            prog, _l, _t, strs = pycc.compile_c(hot)
            buf = bytearray(b"TRV1") + struct.pack("<III", 1, len(prog), len(strs))
            for w in prog:
                buf += struct.pack("<I", w & 0xFFFFFFFF)
            for addr, data in strs.items():
                buf += struct.pack("<II", addr, len(data)) + bytes(data)
            open(tpath, "wb").write(bytes(buf))
        else:
            subprocess.run([binary, cpath, tpath], capture_output=True)
            prog = load_trv(tpath)
        r = subprocess.run([os.path.join(HERE, "trirv_vm"), tpath],
                           capture_output=True, text=True)
        line = [l for l in r.stderr.splitlines() if "吞吐" in l]
        info = line[0] if line else r.stderr.strip()
        print(f"  {label:12s} 指令 {len(prog):6d}  {info.strip()}")


if __name__ == "__main__":
    main()
