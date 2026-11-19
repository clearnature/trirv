#!/usr/bin/env python3
"""trirv_musl_wide_e2e —— musl string/ 全目录编译覆盖（批量 + 功能断言）。

对 musl 1.2.5 src/string/ 全部 46 个非宽字符 .c 文件批量走
预处理 → TRVO 编译管线：
  - 40 个编译导出成功
  - 6 个标记 SKIP（语义不适用于 tryte，非编译器缺口）：
      strcspn/strspn/memmem/strstr/explicit_bzero —— 字节位图/打包技巧
      依赖二进制位运算（`<<8 |`、BITOP 宏），tryte 三值逻辑语义不同；
      strsignal —— C99 指定初始化器 + 信号常量（TriRV 无信号子系统）
  - 0 个编译失败

功能断言（真实 musl 代码协作）：strsep 分词 / strlcpy 返回值 /
memmove 重叠拷贝 / strcasecmp 大小写 / memccpy 停止指针。

预处理能力（preprocess_musl.py，本阶段新增）：
  函数式宏展开（带参数替换）/ 续行拼接 / weak_alias·strong_alias
  语句消除 + 别名替换 / NULL·UCHAR_MAX 等常量 / sizeof 常量折叠 /
  数字字面量后缀剥离（9U/100UL）/ static·inline 剥离 /
  语义不适用标记（#error TRIRV_SKIP）

编译器能力（本阶段新增）：
  goto/label 语句 / 表达式语句与 for-init 的逗号链 /
  (void) 空形参守卫修正（void* 形参被误判空表吞掉）
"""
import os
import struct
import subprocess
import sys
import tempfile

import trirv_vm as vm

HERE = os.path.dirname(os.path.abspath(__file__))
CVM = os.environ.get("TRIRV_VM_BIN", os.path.join(HERE, "trirv_vm"))
CC = os.path.join(HERE, "trirv_cc")

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


# ================ 1. 批量编译统计 ================
r = subprocess.run([sys.executable, os.path.join(HERE, "batch_musl_compile.py")],
                   capture_output=True, text=True)
check("批量/管线退出码", r.returncode, 0)
m_ok = [l for l in r.stdout.splitlines() if l.startswith("OK:")]
check("批量/统计行存在", len(m_ok), 1)
ok_n = int(m_ok[0].split(":")[1].split("SKIP")[0].strip())
check("批量/编译成功数>=40", ok_n >= 40, True)
check("批量/编译失败数=0", "FAIL: 0" in r.stdout, True)

# ================ 2. 新覆盖函数功能断言 ================
OUTDIR = "/tmp/musl_batch"
TEST_SRC = """
int strsep(char **str, const char *sep);
int strlcpy(char *d, const char *s, int n);
void *memmove(void *d, const void *s, int n);
int strcasecmp(const char *a, const char *b);
int memccpy(void *d, const void *s, int c, int n);
int main(void) {
    int f = 0;
    char buf[16];
    char *p = buf;
    strlcpy(buf, "a,b", 16);
    char *tok = strsep(&p, ",");
    if (tok[0] != 'a') f += 1;
    if (p[0] != 'b') f += 2;
    char b2[8];
    if (strlcpy(b2, "hi", 8) != 2) f += 4;
    if (b2[0] != 'h') f += 8;
    char b3[8];
    strlcpy(b3, "abcdef", 8);
    memmove(b3 + 2, b3, 4);
    if (b3[2] != 'a' || b3[3] != 'b') f += 16;
    if (strcasecmp("ABC", "abc") != 0) f += 32;
    if (strcasecmp("abd", "abc") <= 0) f += 64;
    char b4[8];
    void *hit = memccpy(b4, "xYz", 'Y', 3);
    if (hit == 0) f += 128;
    return f;
}
"""
trv = tempfile.mktemp(suffix=".trv")
cf = tempfile.mktemp(suffix=".c")
with open(cf, "w") as f:
    f.write(TEST_SRC)
args = [CC, cf, trv]
for fn in ("strsep", "strlcpy", "memmove", "strcasecmp", "memccpy"):
    args += ["-l", os.path.join(OUTDIR, fn + ".trvo")]
r = subprocess.run(args, capture_output=True, text=True)
check("功能/测试程序链接", r.returncode, 0)

data = open(trv, "rb").read()
n_inst, n_seg = struct.unpack_from("<II", data, 8)
prog = list(struct.unpack_from(f"<{n_inst}I", data, 16))
off = 16 + 4 * n_inst
mem = {}
for _ in range(n_seg):
    addr, ln = struct.unpack_from("<II", data, off)
    off += 8
    mem[addr] = data[off:off + ln]
    off += ln
c = vm.VM(prog, mem_init=mem).run(max_steps=10000)
check("功能/位图(PyVM)", c.exit_code, 0)

if os.path.exists(CVM):
    r = subprocess.run([CVM, trv], capture_output=True)
    errs = [l for l in r.stderr.decode().split() if l.startswith("exit=")]
    check("功能/位图(CVM)", int(errs[0].split("=")[1]), 0)

for p in (trv, cf):
    if os.path.exists(p):
        os.unlink(p)

print(f"\nmusl string 全目录覆盖：{passed} PASS / {failed} FAIL")
sys.exit(1 if failed else 0)
