#!/usr/bin/env python3
"""trirv_qsort_tri_e2e —— musl_tri/qsort_tri.c（三进制原生 qsort）契约。

语义（C99 qsort）：对 base 起 nel 个、每个 width 字节的元素按 cmp
回调排序。cmp(a,b)<0 → a 在前。本实现为 musl smoothsort 的三进制
原生替换（插入+快速排序混合），语义等价、无位掩码依赖。

覆盖：随机乱序 / 已序 / 逆序 / 重复元素 / 单元素 / 零元素 /
负数 / 稳定性无关性（值排序正确即可）/ 大数组 / char 数组按字节排序。
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
BATCH = "/tmp/musl_batch"

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
int qsort(void *base, int nel, int width, int (*cmp)(const void *, const void *));
int memcmp(const void *a, const void *b, int n);

int cmp_int(const int *a, const int *b) { return *a - *b; }
int cmp_char(const char *a, const char *b) { return *a - *b; }

int main(void) {
    int f = 0;
    /* 1. 随机乱序 */
    int a[8]; a[0]=5;a[1]=3;a[2]=8;a[3]=1;a[4]=9;a[5]=2;a[6]=7;a[7]=4;
    qsort(a, 8, 4, cmp_int);
    { int i; for(i=0;i<8;i++) if(a[i]!=i+1) f+=1; }
    /* 2. 已序输入 */
    int b[5]; b[0]=1;b[1]=2;b[2]=3;b[3]=4;b[4]=5;
    qsort(b, 5, 4, cmp_int);
    { int i; for(i=0;i<5;i++) if(b[i]!=i+1) f+=2; }
    /* 3. 逆序输入 */
    int c[5]; c[0]=5;c[1]=4;c[2]=3;c[3]=2;c[4]=1;
    qsort(c, 5, 4, cmp_int);
    { int i; for(i=0;i<5;i++) if(c[i]!=i+1) f+=4; }
    /* 4. 重复元素 */
    int d[6]; d[0]=2;d[1]=1;d[2]=2;d[3]=1;d[4]=3;d[5]=1;
    qsort(d, 6, 4, cmp_int);
    if (d[0]!=1||d[1]!=1||d[2]!=1||d[3]!=2||d[4]!=2||d[5]!=3) f+=8;
    /* 5. 单元素与零元素（不应崩溃） */
    int e[1]; e[0]=42;
    qsort(e, 1, 4, cmp_int);
    if (e[0]!=42) f+=16;
    qsort(e, 0, 4, cmp_int);
    /* 6. 负数 */
    int g[4]; g[0]=-5;g[1]=3;g[2]=-2;g[3]=0;
    qsort(g, 4, 4, cmp_int);
    if (g[0]!=-5||g[1]!=-2||g[2]!=0||g[3]!=3) f+=32;
    /* 7. 大数组（60 个逆序偶数） */
    int h[60];
    { int i; for(i=0;i<60;i++) h[i]=(60-i)*2; }
    qsort(h, 60, 4, cmp_int);
    { int i; for(i=0;i<60;i++) if(h[i]!=(i+1)*2) f+=64; }
    /* 8. char 数组按字节排序 */
    char s[7]; s[0]='d';s[1]='a';s[2]='c';s[3]='b';s[4]='f';s[5]='e';s[6]=0;
    qsort(s, 6, 1, cmp_char);
    if (s[0]!='a'||s[1]!='b'||s[2]!='c'||s[3]!='d'||s[4]!='e'||s[5]!='f') f+=128;
    return f;
}
"""

tri_c = os.path.join(HERE, "musl_tri", "qsort_tri.c")
trvo = tempfile.mktemp(suffix=".trvo")
r = subprocess.run([CC, tri_c, trvo], capture_output=True, text=True)
check("qsort_tri 编译", r.returncode, 0)

trv = tempfile.mktemp(suffix=".trv")
cf = tempfile.mktemp(suffix=".c")
with open(cf, "w") as f:
    f.write(TEST_SRC)
r = subprocess.run([CC, cf, trv, "-l", trvo,
                    "-l", os.path.join(BATCH, "memcmp.trvo"),
                    "-l", os.path.join(BATCH, "memcpy.trvo")],
                   capture_output=True, text=True)
if r.returncode != 0:
    print(f"  [link rc={r.returncode}] {r.stderr.strip()[:150]}")
check("qsort/测试程序链接", r.returncode, 0)

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
c = vm.VM(prog, mem_init=mem).run(max_steps=200000)
BITNAMES = {1: "乱序", 2: "已序", 4: "逆序", 8: "重复", 16: "单元素",
            32: "负数", 64: "大数组", 128: "char 数组"}
check("qsort/行为位图(PyVM)", c.exit_code, 0)
for bit, nm in BITNAMES.items():
    if c.exit_code & bit:
        print(f"  FAIL: {nm}")

if os.path.exists(CVM):
    r2 = subprocess.run([CVM, trv], capture_output=True)
    errs = [l for l in r2.stderr.decode().split() if l.startswith("exit=")]
    check("qsort/行为位图(CVM)", int(errs[0].split("=")[1]), 0)

for p in (trvo, trv, cf):
    if os.path.exists(p):
        os.unlink(p)

print(f"\nqsort 三进制重写契约：{passed} PASS / {failed} FAIL")
sys.exit(1 if failed else 0)
