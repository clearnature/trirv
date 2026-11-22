#!/usr/bin/env python3
"""trirv_musl_real_e2e —— 真 musl 源码编译试点（里程碑验证）。

源：musl 1.2.5 官方源码（/Users/zesheng/ai-lab/trirv/third_party/musl，原文件拷贝至 musl_src/），
经 preprocess_musl.py 预处理（#include 丢弃、#ifdef __GNUC__ 取 #else 纯 C
回退分支、对象式 #define 展开、size_t/uint*_t → int、restrict/attribute 剥离）。

编译流程（与真 musl 移植同一管线）：
  musl/xxx.c → preprocess → trirv_cc -o xxx.trvo → 链接进测试程序 → 双 VM 执行

覆盖（对真 musl 源码的行为断言，不是对 TriRV 手写仿品）：
  1. strcmp：相等=0、'abc'<'abd' 负、反向正（C 语义 <0/>0 判断）
  2. strncmp：前缀相等、长度内比较
  3. strlen：非零/空串
  4. abs：负/正
  5. memset：头/中/尾字节填充
  6. memcpy：内容拷贝 + NUL 保留
  7. 集成场景：strstr 朴素实现（strncmp+strlen 协作）、memset+memcpy+strcmp 链
  8. 双 VM 一致

为让真 musl 源码编译而补齐的编译器能力（副产品，全部有独立用例锁定）：
  for 逗号 step（i++,r++）/ 空语句 ";" / for-init 声明（分号归 for）/
  后缀 ++/--（返回旧值）/ 前缀 ++/-- / const unsigned 任意顺序 /
  声明符基类型隔离（int a,*b 的 b 是 int* 不是 int**）/ N_CAST 类型传播
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
MUSL_SRC = os.path.join(HERE, "..", "libc-musl", "musl_src")
PP = os.path.join(HERE, "..", "libc-musl", "preprocess_musl.py")

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


def preprocess(src_path):
    out = tempfile.mktemp(suffix=".c")
    r = subprocess.run([sys.executable, PP, src_path], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"preprocess fail: {r.stderr[:200]}")
    with open(out, "w") as f:
        f.write(r.stdout)
    return out


def compile_trvo(c_src, trvo_out):
    r = subprocess.run([CC, c_src, trvo_out], capture_output=True, text=True)
    return r.returncode, r.stderr


def load_trv(path):
    data = open(path, "rb").read()
    n_inst, n_seg = struct.unpack_from("<II", data, 8)
    prog = list(struct.unpack_from(f"<{n_inst}I", data, 16))
    off = 16 + 4 * n_inst
    mem = {}
    for _ in range(n_seg):
        addr, ln = struct.unpack_from("<II", data, off)
        off += 8
        mem[addr] = data[off:off + ln]
        off += ln
    return prog, mem


# ================ 编译全部真 musl 试点文件 ================
FILES = ["strcmp.c", "strncmp.c", "strlen.c", "abs.c", "memset.c", "memcpy.c"]
trvos = []
for fn in FILES:
    src = os.path.join(MUSL_SRC, fn)
    pp = preprocess(src)
    tvo = tempfile.mktemp(suffix=".trvo")
    rc, err = compile_trvo(pp, tvo)
    check(f"真musl/{fn} 编译导出", rc, 0)
    trvos.append(tvo)

# ================ 行为断言（位图） ================
TEST_SRC = """
int strcmp(const char *l, const char *r);
int strncmp(const char *l, const char *r, int n);
int strlen(const char *s);
int abs(int a);
void *memset(void *d, int c, int n);
void *memcpy(void *d, const void *s, int n);
int main(void) {
    int f = 0;
    if (strcmp("abc", "abc") != 0) f += 1;
    if (strcmp("abc", "abd") >= 0) f += 2;
    if (strcmp("abd", "abc") <= 0) f += 4;
    if (strncmp("abcxx", "abcy", 3) != 0) f += 8;
    if (strncmp("abd", "abc", 3) <= 0) f += 16;
    if (strlen("hello") != 5) f += 32;
    if (strlen("") != 0) f += 64;
    if (abs(-7) != 7) f += 128;
    if (abs(3) != 3) f += 256;
    char b1[16];
    memset(b1, 65, 4);
    if (b1[0] != 65) f += 512;
    if (b1[3] != 65) f += 1024;
    if (b1[4] != 0) f += 2048;
    char b2[16];
    memcpy(b2, "xyz", 4);
    if (b2[0] != 120) f += 4096;
    if (b2[2] != 122) f += 8192;
    if (b2[3] != 0) f += 16384;
    return f;
}
"""
trv = tempfile.mktemp(suffix=".trv")
cf = tempfile.mktemp(suffix=".c")
with open(cf, "w") as f:
    f.write(TEST_SRC)
args = [CC, cf, trv]
for t in trvos:
    args += ["-l", t]
r = subprocess.run(args, capture_output=True, text=True)
check("真musl/测试程序链接", r.returncode, 0)

prog, mem = load_trv(trv)
c = vm.VM(prog, mem_init=mem).run(max_steps=20000)
BITNAMES = {1: "strcmp相等", 2: "strcmp负", 4: "strcmp正", 8: "strncmp前缀",
            16: "strncmp大小", 32: "strlen", 64: "strlen空串", 128: "abs负",
            256: "abs正", 512: "memset头", 1024: "memset尾", 2048: "memset界外",
            4096: "memcpy头", 8192: "memcpy中", 16384: "memcpy NUL"}
check("真musl/行为位图", c.exit_code, 0)
for bit, nm in BITNAMES.items():
    if c.exit_code & bit:
        print(f"  FAIL: {nm}")

# ================ 集成场景（musl 函数协作） ================
INT_SRC = """
int strcmp(const char *l, const char *r);
int strncmp(const char *l, const char *r, int n);
int strlen(const char *s);
void *memset(void *d, int c, int n);
void *memcpy(void *d, const void *s, int n);
const char *strstr_nm(const char *hay, const char *needle) {
    int nl = strlen(needle);
    if (nl == 0) return hay;
    int hl = strlen(hay);
    for (int i = 0; i + nl <= hl; i++) {
        if (strncmp(hay + i, needle, nl) == 0) return hay + i;
    }
    return 0;
}
int main(void) {
    int fails = 0;
    const char *hit = strstr_nm("hello trirv world", "trirv");
    if (hit == 0) fails++;
    else if (strncmp(hit, "trirv", 5) != 0) fails++;
    if (strstr_nm("hello world", "xyz") != 0) fails++;
    char buf[32];
    memset(buf, 0, 32);
    memcpy(buf, "musl-on-trirv", 14);
    if (strcmp(buf, "musl-on-trirv") != 0) fails++;
    if (strlen(buf) != 13) fails++;
    return fails;
}
"""
trv2 = tempfile.mktemp(suffix=".trv")
cf2 = tempfile.mktemp(suffix=".c")
with open(cf2, "w") as f:
    f.write(INT_SRC)
args = [CC, cf2, trv2]
for t in trvos:
    args += ["-l", t]
r = subprocess.run(args, capture_output=True, text=True)
check("真musl/集成场景链接", r.returncode, 0)
prog, mem = load_trv(trv2)
c = vm.VM(prog, mem_init=mem).run(max_steps=50000)
check("真musl/集成场景执行(PyVM)", c.exit_code, 0)

# C VM
if os.path.exists(CVM):
    r = subprocess.run([CVM, trv2], capture_output=True)
    errs = [l for l in r.stderr.decode().split() if l.startswith("exit=")]
    check("真musl/集成场景执行(CVM)", int(errs[0].split("=")[1]), 0)

for p in trvos:
    os.unlink(p)
for p in (trv, trv2, cf, cf2):
    if os.path.exists(p):
        os.unlink(p)

print(f"\n真 musl 源码编译试点：{passed} PASS / {failed} FAIL")
sys.exit(1 if failed else 0)
