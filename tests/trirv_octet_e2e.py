#!/usr/bin/env python3
"""trirv_octet_e2e —— 8-bit 视图层（read8/write8）端到端验证。

背景（CHAR_BIT 决策的实验田）：
  musl 全篇假设 char=8-bit（CHAR_BIT==8），octet 值域 0..255；
  TriRV 的 tryte 是 5-trit（0..242），13 个值（243..255）放不下。
  现有 read/write 是"tryte 直通"语义：宿主字节流直接进/出 tryte 内存，
  高位字节在读端被 %243 归一而丢失（实测 0xFF 读回变 12）。

设计（视图层，不动内存地基）：
  内存保持纯三进制（tryte 0..242）。octet 流在 syscall 边界编解码：
    read8(fd, buf, n)   每 octet o 编 2 tryte [lo=o%243, hi=o/243]
    write8(fd, buf, n)  每 2 tryte [lo,hi] 解 1 octet（hi∈{0,1}）
  syscall 号 5000/5001 = TriRV 扩展区（Linux 编号空间之外）。
  这是 musl 移植的对接面：musl 的 read/write 内部换成 read8/write8。

覆盖：
  1. 高位字节无损往返（0xFF/0xFE/0x81 等 243..255 段）
  2. ASCII 混合流往返
  3. 计数正确性（不足 n 时返回实际数）
  4. 旧 read/write 直通语义不回归（既有 e2e 锁定，此处抽查）
  5. 双 VM 一致（PyVM / C VM 同一 .trv）
"""
import os
import struct
import subprocess
import sys
import tempfile

import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import trirv_vm as vm

HERE = os.path.dirname(os.path.abspath(__file__))
CVM = os.environ.get("TRIRV_VM_BIN", os.path.join(HERE, "..", "vm", "bin", "trirv_vm"))
CC = os.path.join(HERE, "..", "cc", "bin", "trirv_cc")

passed = 0
failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"PASS {name}: {got!r}")
    else:
        failed += 1
        print(f"FAIL {name}: got={got!r} want={want!r}")


SRC = "int main(){ char b[1024]; int n = read8(0, b, 512); write8(1, b, n); return n; }\n"
cf = tempfile.mktemp(suffix=".c")
tf = tempfile.mktemp(suffix=".trv")
with open(cf, "w") as f:
    f.write(SRC)
subprocess.run([CC, cf, tf], check=True, capture_output=True)

data = open(tf, "rb").read()
n_inst, n_seg = struct.unpack_from("<II", data, 8)
prog = list(struct.unpack_from(f"<{n_inst}I", data, 16))
off = 16 + 4 * n_inst
mem = {}
for _ in range(n_seg):
    addr, ln = struct.unpack_from("<II", data, off)
    off += 8
    mem[addr] = data[off:off + ln]
    off += ln


def cvm(stdin_bytes):
    r = subprocess.run([CVM, tf, "-"], capture_output=True, input=stdin_bytes)
    errs = [l for l in r.stderr.decode().split() if l.startswith("exit=")]
    return r.stdout, int(errs[0].split("=")[1]) if errs else -1


def pym(stdin_bytes):
    c = vm.VM(prog, mem_init=mem, stdin=stdin_bytes).run(max_steps=100000)
    return bytes(c.output), c.exit_code


# ================ 1. 高位字节无损往返 ================
HIGH_CASES = [
    ("全高位段", bytes([0xFF, 0xFE, 0xFD, 0xFC])),
    ("FF 单个", bytes([0xFF, 0x41])),
    ("混合 ASCII+高位", bytes([0xE4, 0xB8, 0x89, 0x41, 0xFF])),   # UTF-8 前缀
    ("全 256 值遍历", bytes(range(256))),
    ("大块 512B（单次 read8 上限）", bytes((i * 7 + (i >> 8)) % 256 for i in range(512))),
]
for name, payload in HIGH_CASES:
    out, ec = cvm(payload)
    check(f"视图/{name}.CVM", out.hex(), payload.hex())
    out, ec = pym(payload)
    check(f"视图/{name}.PyVM", out.hex(), payload.hex())

# ================ 2. 计数正确性 ================
out, ec = cvm(b"abc")
check("视图/计数返回.CVM", ec, 3)
out, ec = pym(b"abc")
check("视图/计数返回.PyVM", ec, 3)

# ================ 3. 旧 read/write 直通不回归 ================
SRC_OLD = "int main(){ char b[8]; int n = read(0, b, 8); return n; }\n"
cf2 = tempfile.mktemp(suffix=".c")
tf2 = tempfile.mktemp(suffix=".trv")
with open(cf2, "w") as f:
    f.write(SRC_OLD)
subprocess.run([CC, cf2, tf2], check=True, capture_output=True)
r = subprocess.run([CVM, tf2, "-"], capture_output=True, input=b"abcd")
errs = [l for l in r.stderr.decode().split() if l.startswith("exit=")]
check("直通/read 计数不回归", int(errs[0].split("=")[1]), 4)

os.unlink(cf)
os.unlink(tf)
os.unlink(cf2)
os.unlink(tf2)

print(f"\n8-bit 视图层端到端：{passed} PASS / {failed} FAIL")
sys.exit(1 if failed else 0)
