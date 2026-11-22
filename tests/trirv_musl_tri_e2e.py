#!/usr/bin/env python3
"""trirv_musl_tri_e2e —— musl_tri 三进制原生重写文件的行为断言。

musl_tri/ = musl 源码的三进制原生改编层（纯三进制原则：上游依赖
二进制位运算的文件在 tri 版重写，差异点逐条注释，语义保持一致）。

本套件锁定第一批重写（字节位图系 4 文件）：
  strcspn / strspn / strstr / memmem

TDD：RED = tri 版 .trvo 不存在或行为错时失败；GREEN = 全过。

依赖（tri 版内部引用的函数来自既有 TRVO 批量产物）：
  strstr/memmem 引 strncmp/memcmp/strlen/strchr —— 编译 tri 版时用
  -l 链接 string 目录的对应 TRVO（上游 strcmp/strncmp 等已验证）。
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
BATCH = "/tmp/musl_batch"          # 批量管线 TRVO 输出目录
TRI = os.path.join(HERE, "..", "libc-musl", "musl_tri")

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


def compile_to(src_path, out_path, extra_libs=()):
    args = [CC, src_path, out_path]
    for lib in extra_libs:
        args += ["-l", lib]
    return subprocess.run(args, capture_output=True, text=True)


def run_dual(trv_path, max_steps=30000):
    data = open(trv_path, "rb").read()
    n_inst, n_seg = struct.unpack_from("<II", data, 8)
    prog = list(struct.unpack_from(f"<{n_inst}I", data, 16))
    off = 16 + 4 * n_inst
    mem = {}
    for _ in range(n_seg):
        addr, ln = struct.unpack_from("<II", data, off)
        off += 8
        mem[addr] = data[off:off + ln]
        off += ln
    c = vm.VM(prog, mem_init=mem).run(max_steps=max_steps)
    rc = c.exit_code
    if os.path.exists(CVM):
        r = subprocess.run([CVM, trv_path], capture_output=True)
        errs = [l for l in r.stderr.decode().split() if l.startswith("exit=")]
        cvm_rc = int(errs[0].split("=")[1]) if errs else None
        return rc, cvm_rc
    return rc, None


# ================ 编译 4 个 tri 文件 ================
# strstr 依赖 strncmp/strlen（+memcmp）TRVO（批量产物）；memmem 依赖 memcmp。
# 2026-11-22 闭包转发导出（A5a）后：闭包必须**不相交**链接——两个闭包同携
# strncmp 体 → duplicate 诚实报错（安全网）。链形：strstr 先吸收
# strncmp/strlen/memcmp 成 C1；memmem 链入 C1（∪ memcmp 已在 C1 内）；
# 最终 main -l memmem闭包 ∪ strcspn ∪ strspn，成员互斥。
DEPS_ALL = [os.path.join(BATCH, f) for f in
            ("strncmp.trvo", "strlen.trvo", "memcmp.trvo")]
tri_trvos = {}
for tri_c in ("strcspn_tri", "strspn_tri", "strstr_tri", "memmem_tri"):
    src = os.path.join(TRI, tri_c + ".c")
    tvo = tempfile.mktemp(suffix=".trvo")
    libs = []
    if tri_c == "strstr_tri":
        libs = [d for d in DEPS_ALL if os.path.exists(d)]
    elif tri_c == "memmem_tri":
        libs = [tri_trvos["strstr_tri"]]       # strstr 闭包 ⊇ memcmp，再 -l 原件即 duplicate
    r = compile_to(src, tvo, [x for x in libs if x])
    check(f"tri/{tri_c} 编译", r.returncode, 0)
    tri_trvos[tri_c] = tvo

# ================ 测试程序生成 + 断言 ================
# 每个函数一组用例，返回值位图编码：bit i = 第 i 项断言
TEST_SRC = """
int strcspn(const char *s, const char *c);
int strspn(const char *s, const char *c);
char *strstr(const char *h, const char *n);
void *memmem(const void *h, int l, const void *n, int nl);
int strlen(const char *s);

int main(void) {
    int f = 0;
    /* ---- strcspn：不含集合字符的前缀长度 ---- */
    if (strcspn("hello world", " ") != 5) f += 1;        /* 空格截断 */
    if (strcspn("ab=cd", "=xy") != 2) f += 1048576;      /* 集合首中在中段 */
    if (strcspn("abcdef", "xyz") != 6) f += 2;           /* 无匹配=全长 */
    if (strcspn("", "abc") != 0) f += 4;                 /* 空串 */
    if (strcspn("12345", "") != 5) f += 8;               /* 空集合=全长 */
    if (strcspn("a-b=c", "=-") != 1) f += 16;            /* 集合多字符 */
    /* ---- strspn：只含集合字符的前缀长度 ---- */
    if (strspn("abcxyz", "cba") != 3) f += 32;
    if (strspn("abcdef", "xyz") != 0) f += 64;           /* 首字符不在集合 */
    if (strspn("", "abc") != 0) f += 128;
    if (strspn("aaaa", "a") != 4) f += 256;              /* 单字符集合 */
    /* ---- strstr：子串首现 ---- */
    if (strstr("hello trirv", "trirv") == 0) f += 512;
    if (strstr("hello", "xyz") != 0) f += 1024;          /* 无匹配 */
    if (strstr("hello", "") == 0) f += 2048;             /* 空针=返回 h */
    if (strstr("aaa", "aa") == 0) f += 4096;             /* 重叠匹配 */
    /* ---- memmem：字节序列查找 ---- */
    if (memmem("haystack", 8, "stack", 5) == 0) f += 8192;
    if (memmem("haystack", 8, "needle", 6) != 0) f += 16384;
    if (memmem("abcabc", 6, "cab", 3) == 0) f += 32768;  /* 跨越重复段 */
    if (memmem("abc", 3, "abcd", 4) != 0) f += 65536;    /* 针长于 haystack */
    return f;
}
"""
trv = tempfile.mktemp(suffix=".trv")
cf = tempfile.mktemp(suffix=".c")
with open(cf, "w") as f:
    f.write(TEST_SRC)
args = [CC, cf, trv]
for t in (tri_trvos["memmem_tri"], tri_trvos["strcspn_tri"], tri_trvos["strspn_tri"]):
    args += ["-l", t]       # memmem 闭包 ⊇ strstr/strncmp/strlen/memcmp；互斥补 strcspn/strspn
# 2026-11-22 闭包转发导出（A5a）后：strstr/memmem 的 .trvo 已闭包含
# strncmp/strlen/memcmp 依赖体，DEPS 平铺 -l 会 duplicate 报错（安全网）
r = subprocess.run(args, capture_output=True, text=True)
if r.returncode != 0:
    print(f"  [link rc={r.returncode}] {r.stderr.strip()[:200]}")
check("tri/测试程序链接", r.returncode, 0)

prog, mem = load_trv(trv)
c = vm.VM(prog, mem_init=mem).run(max_steps=50000)
BITNAMES = {1: "strcspn 空格", 2: "strcspn 无匹配", 4: "strcspn 空串",
            8: "strcspn 空集合", 16: "strcspn 集合",
            32: "strspn 前缀", 64: "strspn 零", 128: "strspn 空串",
            256: "strspn 单字符", 512: "strstr 命中", 1024: "strstr 无匹配",
            2048: "strstr 空针", 4096: "strstr 重叠",
            1048576: "strcspn 中段", 8192: "memmem 命中", 16384: "memmem 无匹配",
            32768: "memmem 跨段", 65536: "memmem 针长"}
check("tri/行为位图(PyVM)", c.exit_code, 0)
for bit, nm in BITNAMES.items():
    if c.exit_code & bit:
        print(f"  FAIL: {nm}")

rc2, cvm_rc = run_dual(trv)
if cvm_rc is not None:
    check("tri/行为位图(CVM)", cvm_rc, 0)

for t in tri_trvos.values():
    if os.path.exists(t):
        os.unlink(t)
for p in (trv, cf):
    if os.path.exists(p):
        os.unlink(p)

print(f"\nmusl_tri 第一批（字节位图系 4 文件）：{passed} PASS / {failed} FAIL")
sys.exit(1 if failed else 0)
