#!/usr/bin/env python3
"""trirv_strtol_tri_e2e —— musl_tri/strtol_tri.c 契约。

语义（C99 strtol）：跳过前导空白；可选 +/-；base 0 自动判定
（0x/0X 十六进制、前导 0 八进制、其余十进制）；base 2..36；
返回解析值；end 写入停止位置；溢出钳制到 int 极值（TriRV 等宽
映射后 LONG_MAX/MIN 语义）；无有效数字返回 0 且 end=nptr。
"""
import os
import subprocess
import sys
import tempfile

import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import _trvio
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


TEST_SRC = """
int strtol(const char *n, char **end, int base);

int main(void) {
    int f = 0;
    char bbuf[8];
    char *e = bbuf;
    if (strtol("123", &e, 10) != 123) f += 1;
    if (e[0] != 0) f += 2;
    if (strtol("  -456x", &e, 10) != -456) f += 4;
    if (e[0] != 'x') f += 8;
    if (strtol("+789", &e, 10) != 789) f += 16;
    if (strtol("ff", &e, 16) != 255) f += 32;
    if (strtol("0x1f tail", &e, 16) != 31) f += 64;
    if (e[0] != ' ') f += 128;
    if (strtol("0x10", &e, 0) != 16) f += 256;
    if (strtol("017", &e, 0) != 15) f += 512;
    if (strtol("42", &e, 0) != 42) f += 1024;
    if (strtol("1010", &e, 2) != 10) f += 2048;
    if (strtol("99999999999", &e, 10) != 2147483647) f += 4096;
    if (strtol("-99999999999", &e, 10) != -2147483648) f += 8192;
    if (strtol("zzz", &e, 10) != 0) f += 16384;
    if (e[0] != 'z') f += 32768;
    if (strtol("   ", &e, 10) != 0) f += 65536;
    return f;
}
"""

tri_c = os.path.join(HERE, "..", "libc-musl", "musl_tri", "strtol_tri.c")
trvo = tempfile.mktemp(suffix=".trvo")
r = subprocess.run([CC, tri_c, trvo], capture_output=True, text=True)
check("strtol_tri 编译", r.returncode, 0)

trv = tempfile.mktemp(suffix=".trv")
cf = tempfile.mktemp(suffix=".c")
with open(cf, "w") as f:
    f.write(TEST_SRC)
r = subprocess.run([CC, cf, trv, "-l", trvo], capture_output=True, text=True)
if r.returncode != 0:
    print(f"  [link rc={r.returncode}] {r.stderr.strip()[:150]}")
check("strtol/测试程序链接", r.returncode, 0)

data = open(trv, "rb").read()
prog, mem, _packed = _trvio.load_bytes(data)
c = vm.VM(prog, mem_init=mem).run(max_steps=100000)
BITNAMES = {1: "base10", 2: "end NUL", 4: "负数+空白", 8: "end x",
            16: "正号", 32: "base16", 64: "0x前缀", 128: "end 空格",
            256: "auto 0x", 512: "auto 八进制", 1024: "auto 十进制",
            2048: "base2", 4096: "溢出钳上限", 8192: "溢出钳下限",
            16384: "坏输入零", 32768: "end=n", 65536: "纯空白"}
check("strtol/行为位图(PyVM)", c.exit_code, 0)
for bit, nm in BITNAMES.items():
    if c.exit_code & bit:
        print(f"  FAIL: {nm}")

if os.path.exists(CVM):
    r2 = subprocess.run([CVM, trv], capture_output=True)
    errs = [l for l in r2.stderr.decode().split() if l.startswith("exit=")]
    check("strtol/行为位图(CVM)", int(errs[0].split("=")[1]), 0)

for p in (trvo, trv, cf):
    if os.path.exists(p):
        os.unlink(p)

print(f"\nstrtol 三进制重写契约：{passed} PASS / {failed} FAIL")
sys.exit(1 if failed else 0)
