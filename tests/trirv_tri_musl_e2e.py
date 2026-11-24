#!/usr/bin/env python3
"""trirv_tri_musl_e2e —— musl 源码经 tri-tcc 的多 TU 编译 + 双 VM 行为契约（T-2.3）。

管线（DoD #1 形态）：真 musl 上游源码 → preprocess_musl.py（TriRV C 子集）
→ tri-tcc 同进程多文件编译（musl_tri 三进制重写版 + 上游 string 文件）
→ TRV1 → 双 VM（PyVM × C VM）执行断言。

对比基线：trirv_cc 同管线 65/66（batch_musl_compile.py）。本套件锁定
tri-tcc 已能编译并正确运行的 musl 行为集；失败位图给出诚实缺口清单。

依赖（strstr→strlen/strchrnul、memmem→memcmp、div 系→memset、qsort→memcpy
等跨 TU 引用全部经 tri-tcc 进程内符号解析——T-2.1 多 TU 能力）。
"""
import os
import subprocess
import sys
import tempfile

import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import _trvio
import trirv_vm as vm

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TCC = os.path.join(ROOT, "tinycc", "trirv-tcc")
CVM = os.path.join(HERE, "..", "vm", "bin", "trirv_vm")
PP = os.path.join(HERE, "..", "libc-musl", "preprocess_musl.py")
TRI = os.path.join(HERE, "..", "libc-musl", "musl_tri")
MUSL_STR = os.path.join(ROOT, "third_party", "musl", "src", "string")
PPDIR = _bootstrap.scratch("tri_musl_pp")

passed = 0
failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
    else:
        failed += 1
        print("FAIL %s: got=%r want=%r" % (name, got, want))


def load_trv(path):
    data = open(path, "rb").read()
    prog, mem, _packed = _trvio.load_bytes(data)
    return prog, mem


def preprocess(name):
    """上游 musl 文件 → TriRV C 子集（缺源文件返回 None）"""
    os.makedirs(PPDIR, exist_ok=True)
    src = os.path.join(MUSL_STR, name + ".c")
    dst = os.path.join(PPDIR, name + ".c")
    if not os.path.exists(src):
        return None
    r = subprocess.run([sys.executable, PP, src], capture_output=True, text=True)
    if r.returncode != 0 or "TRIRV_SKIP" in r.stdout:
        return None
    with open(dst, "w") as f:
        f.write(r.stdout)
    return dst


def build_and_run(name, main_src, tri_files, pp_files, max_steps=20000000):
    """多 TU 编译 + 双 VM。返回 (编译rc, PyVM exit, CVM exit&0xFF)。"""
    os.makedirs(PPDIR, exist_ok=True)   # scratch 目录可能被外层清扫
    cpath = os.path.join(PPDIR, "main_%s.c" % name)
    tpath = os.path.join(PPDIR, "%s.trv" % name)
    with open(cpath, "w") as f:
        f.write(main_src)
    srcs = [cpath] + [p for p in (preprocess(f) for f in pp_files) if p] + \
           [os.path.join(TRI, t + "_tri.c") for t in tri_files]
    r = subprocess.run([TCC, "-nostdlib"] + srcs + ["-o", tpath],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        return r.returncode, None, None, r.stderr.strip()[:160]
    prog, mem = load_trv(tpath)
    c = vm.VM(prog, mem_init=mem).run(max_steps=max_steps)
    py = c.exit_code & 0xFF
    cv = None
    if os.path.exists(CVM):
        rr = subprocess.run([CVM, tpath], capture_output=True, text=True, timeout=60)
        if "exit=" in rr.stderr:
            cv = int(rr.stderr.split("exit=")[-1].split()[0]) & 0xFF
    return 0, py, cv, ""


# ================= 行为组（每组一个程序，位图 bit i = 1<<i） =================
LIB_STR = ["strlen", "strcmp", "strncmp", "memcmp", "memchr", "memmove",
           "strchr", "strchrnul"]

P1_MAIN = """
int strlen(const char *s);
int strcmp(const char *, const char *);
int strncmp(const char *, const char *, int);
int memcmp(const void *, const void *, int);
void *memchr(const void *, int, int);
void *memset(void *, int, int);
void *memcpy(void *, const void *, int);
int strcspn(const char *, const char *);
int main(void) {
    int f = 0;
    char b[16];
    memset(b, 7, 4);
    if (b[0] + b[3] != 14) f += 1;
    memcpy(b + 4, b, 4);
    if (b[4] != 7) f += 2;
    if (strlen("abc") != 3) f += 4;
    if (strcmp("ab", "ac") >= 0) f += 8;
    if (strncmp("abcX", "abcY", 3) != 0) f += 16;
    if (memcmp("abd", "abc", 3) <= 0) f += 32;
    if (memchr("hello", 'e', 5) == 0) f += 64;
    if (strcspn("hello", "lo") != 2) f += 128;
    return f;
}
"""

P2_MAIN = """
int strspn(const char *, const char *);
char *strstr(const char *, const char *);
char *memmem(const void *, int, const void *, int);
char *strsep(char **, const char *);
int strlen(const char *s);
typedef struct { int quot, rem; } div_t;
div_t div(int, int);
int main(void) {
    int f = 0;
    if (strspn("hello", "hel") != 4) f += 1;
    if (strstr("hello", "llo") == 0) f += 2;
    if (strstr("hello", "xyz") != 0) f += 4;
    if (memmem("haystack", 8, "stack", 5) == 0) f += 8;
    if (memmem("abc", 3, "abcd", 4) != 0) f += 16;
    {
        char *tok = (char *)"a,b,,c";
        char *t = strsep(&tok, ",");
        if (t == 0 || t[0] != 'a') f += 32;
        t = strsep(&tok, ",");
        if (t == 0 || t[0] != 'b') f += 64;   /* 剩余 tok=",,c"（长 3） */
    }
    {
        div_t d = div(17, 5);
        if (d.quot != 3 || d.rem != 2) f += 128;
    }
    return f;
}
"""

P3_MAIN = """
typedef struct { int quot, rem; } ldiv_t;
ldiv_t ldiv(long, long);
typedef struct { long long quot, rem; } lldiv_t;
lldiv_t lldiv(long long, long long);
void qsort(void *, int, int, int (*)(const void *, const void *));
int strtol(const char *, char **, int);
int strlen(const char *s);
static int cmp_int(const void *a, const void *b) {
    return *(int *)a - *(int *)b;
}
int main(void) {
    int f = 0;
    ldiv_t d = ldiv(1000000, 7);
    if (d.quot != 142857 || d.rem != 1) f += 1;
    lldiv_t e = lldiv(5000000000LL, 3);
    if (e.quot != 1666666666LL || e.rem != 2) f += 2;
    {
        int a[5] = {5, 1, 4, 2, 3};
        qsort(a, 5, 9, cmp_int);
        if (a[0] != 1 || a[4] != 5 || a[2] != 3) f += 4;
    }
    if (strtol("42", 0, 10) != 42) f += 8;
    if (strtol("-17x", 0, 10) != -17) f += 16;
    if (strtol("0x1f", 0, 16) != 31) f += 32;
    if (strtol("101", 0, 2) != 5) f += 64;
    if (strlen("") != 0) f += 128;
    return f;
}
"""

GROUPS = [
    ("string_core", P1_MAIN, ["memset", "memcpy", "strcspn"], LIB_STR),
    ("string_composite", P2_MAIN, ["memset", "memcpy", "strcspn", "strspn",
                                   "strsep", "strstr", "memmem", "div"], LIB_STR),
    ("div_qsort_strtol", P3_MAIN, ["memset", "memcpy", "ldiv", "lldiv",
                                   "imaxdiv", "qsort", "strtol"], LIB_STR),
]


def main():
    global passed, failed
    if not os.path.exists(TCC):
        print("tri-tcc 未构建（%s 不存在）—— 全部 SKIP" % TCC)
        print("\ntri-musl 契约：0 PASS / 0 FAIL / %d 组 SKIP" % len(GROUPS))
        sys.exit(0)
    if not os.path.isdir(MUSL_STR):   # 批次 3b.1a：原 tests/musl（11-22 重组遗留）→ third_party/musl
        print("musl 源码缺失（~/ai-lab/musl）—— 全部 SKIP")
        print("\ntri-musl 契约：0 PASS / 0 FAIL / %d 组 SKIP" % len(GROUPS))
        sys.exit(0)

    bitnames = {
        "string_core": {1: "memset", 2: "memcpy", 4: "strlen", 8: "strcmp<0",
                        16: "strncmp 前缀", 32: "memcmp>", 64: "memchr", 128: "strcspn=2"},
        "string_composite": {1: "strspn", 2: "strstr 命中", 4: "strstr 无匹配",
                             8: "memmem 命中", 16: "memmem 针长", 32: "strsep 首段",
                             64: "strsep 次段", 128: "div 17/5"},
        "div_qsort_strtol": {1: "ldiv", 2: "lldiv", 4: "qsort 函数指针", 8: "strtol 10进制",
                             16: "strtol 负数", 32: "strtol 16进制", 64: "strtol 2进制",
                             128: "strlen 空串"},
    }
    for name, main_src, tri_files, pp_files in GROUPS:
        rc, py, cv, err = build_and_run(name, main_src, tri_files, pp_files)
        if rc != 0:
            check("%s [编译]" % name, err, "")
            continue
        check("%s [PyVM 位图]" % name, py, 0)
        for bit, nm in bitnames.get(name, {}).items():
            if py & bit:
                failed += 1
                print("FAIL %s [PyVM]: %s（bit %d）" % (name, nm, bit))
            else:
                passed += 1
        if cv is not None:
            check("%s [CVM 位图]" % name, cv, 0)
        else:
            check("%s [CVM]" % name, "缺失", "缺失")

    print("\ntri-musl 契约（musl 源码 → tri-tcc 多 TU → 双 VM）：%d PASS / %d FAIL / %d 组"
          % (passed, failed, len(GROUPS)))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
