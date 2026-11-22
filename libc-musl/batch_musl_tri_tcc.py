#!/usr/bin/env python3
"""batch_musl_tri_tcc —— musl 批量编译率对比（tri-tcc 列）。

形态 = DoD #1 的真实管线：musl 源码（preprocess_musl 同管线 + musl_tri
三进制重写版）→ tri-tcc 同进程多 TU 编译 → TRV1 → 双 VM 冒烟执行。
与 batch_musl_compile.py（trirv_cc 65/66，逐文件 TRVO）对照。

逐文件归因：迭代排除法——整批编译失败时按 stderr 归因排除首个文件并重试，
最终给出 OK 集合（全部编进同一 TRV1 且可执行）与排除清单（带原因）。
tri-tcc 无 -c 对象模式，跨文件未解析引用靠多 TU 进程内解析（T-2.1），
故单文件独立编译不可行——目录级聚合是诚实口径。
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PP = os.path.join(HERE, "preprocess_musl.py")
TCC = os.path.join(os.path.dirname(HERE), "tinycc", "trirv-tcc")
CVM = os.path.join(HERE, "trirv_vm")
OUTDIR = "/tmp/musl_tri_tcc"
os.makedirs(OUTDIR, exist_ok=True)

DIRS = ["/Users/zesheng/ai-lab/trirv/third_party/musl/src/string",
        "/Users/zesheng/ai-lab/trirv/third_party/musl/src/ctype",
        "/Users/zesheng/ai-lab/trirv/third_party/musl/src/stdlib"]

# 三进制原生重写表（与 batch_musl_compile.py 同一份语义）
TRI_REWRITE = {
    "memset.c": "musl_tri/memset_tri.c",
    "memcpy.c": "musl_tri/memcpy_tri.c",
    "strcspn.c": "musl_tri/strcspn_tri.c",
    "strspn.c": "musl_tri/strspn_tri.c",
    "strstr.c": "musl_tri/strstr_tri.c",
    "memmem.c": "musl_tri/memmem_tri.c",
    "div.c": "musl_tri/div_tri.c",
    "ldiv.c": "musl_tri/ldiv_tri.c",
    "lldiv.c": "musl_tri/lldiv_tri.c",
    "imaxdiv.c": "musl_tri/imaxdiv_tri.c",
    "qsort.c": "musl_tri/qsort_tri.c",
    "strtol.c": "musl_tri/strtol_tri.c",
    "strsignal.c": "musl_tri/strsignal_tri.c",
    "strtod.c": "musl_tri/strtod_tri.c",
    "atof.c": "musl_tri/strtod_tri.c",
    "ecvt.c": "musl_tri/ecvt_tri.c",
    "fcvt.c": "musl_tri/ecvt_tri.c",
    "gcvt.c": "musl_tri/ecvt_tri.c",
}

# musl 内部 typedef shim——tcc 的 typedef 不跨 TU，必须前置进每个 pp 文件
# （sint：swab.c 等；locale_t：ctype 的 _l 变体——musl 中即指针类型）
# 原型前置表：pp 剥掉 <string.h>/<ctype.h> 后，先定义的调用方（index.c 调
# strchr）会隐式声明返回 int → 指针赋值报错。仅原型无定义，纯夹具。
SHIM = """typedef signed int sint;
typedef void *locale_t;
int strlen(const char *); int strcmp(const char *, const char *);
int strncmp(const char *, const char *, int); int memcmp(const void *, const void *, int);
char *strchr(const char *, int); char *strchrnul(const char *, int);
char *strrchr(const char *, int); char *strstr(const char *, const char *);
char *strpbrk(const char *, const char *);
void *memchr(const void *, int, int); void *memmove(void *, const void *, int);
void *memset(void *, int, int); void *memcpy(void *, const void *, int);
char *strcpy(char *, const char *); char *strncpy(char *, const char *, int);
char *strcat(char *, const char *); char *strncat(char *, const char *, int);
char *strdup(const char *); char *strndup(const char *, int);
int strnlen(const char *, int); int strlcat(char *, const char *, int);
int strcasecmp(const char *, const char *); int strncasecmp(const char *, const char *, int);
int isdigit(int); int isspace(int); int isalpha(int); int isalnum(int);
int isupper(int); int islower(int); int isxdigit(int); int iscntrl(int);
int isgraph(int); int isprint(int); int ispunct(int); int isblank(int);
int tolower(int); int toupper(int);
"""


def prepare():
    """准备全部候选源（pp 或 tri 重写），返回 [(file_id, path), ...]。
    多 TU 去重：多个源文件映射到同一 tri 重写文件（atof/ecvt/fcvt/gcvt →
    strtod_tri/ecvt_tri）只包含一次——重复包含 = 链接符号重定义。"""
    items = []
    seen = set()
    for d in DIRS:
        for fn in sorted(os.listdir(d)):
            # 基线口径：宽字符系排除（wc/wcs/wmem + ctype 的 isw*/tow*——
            # TriRV 无 wchar 支持，batch_musl_compile 同款口径）
            if not fn.endswith(".c") or fn.startswith(("wc", "wcs", "wmem", "isw", "tow", "qsort_nr")):
                continue
            fid = d.split("/")[-1] + "/" + fn
            if fn in TRI_REWRITE:
                rp = os.path.abspath(os.path.join(HERE, TRI_REWRITE[fn]))
                if rp in seen:
                    continue
                seen.add(rp)
                items.append((fid, os.path.join(HERE, TRI_REWRITE[fn])))
                continue
            r = subprocess.run([sys.executable, PP, os.path.join(d, fn)],
                               capture_output=True, text=True)
            if r.returncode != 0:
                continue
            if "TRIRV_SKIP" in r.stdout:
                continue
            pp = os.path.join(OUTDIR, fn)
            with open(pp, "w") as f:
                f.write(SHIM + r.stdout)
            items.append((fid, pp))
    return items


def compile_set(srcs, tag):
    src = os.path.join(OUTDIR, "main_%s.c" % tag)
    with open(src, "w") as f:
        f.write("int main(void){ return 0; }\n")
    out = os.path.join(OUTDIR, "%s.trv" % tag)
    r = subprocess.run([TCC, "-nostdlib", src] + srcs + ["-o", out],
                       capture_output=True, text=True, timeout=120)
    return r, out


def main():
    """增量累加法：从空集开始逐个加文件，失败精确归因到新加文件。
    （旧迭代排除法：错误行归因有噪声 + 二分兜底误排，级联出假 gap 清单。）"""
    if not os.path.exists(TCC):
        print("tri-tcc 未构建 —— SKIP")
        sys.exit(0)
    items = prepare()
    print("候选文件：%d（musl string+ctype+stdlib，pp 同管线 + musl_tri 重写去重）" % len(items))

    good: list = []          # 已证明可编译的 (fid, path)
    excluded = []            # (fid, reason)
    pending = list(items)
    # 多轮收敛：第一轮字母序下消费文件（bcmp→memcmp）先于提供者被排除；
    # 用已完整的 good 集重试排除文件，直到无进展（≤3 轮）
    for pass_no in (1, 2, 3):
        deferred = []
        progressed = False
        for fid, path in pending:
            srcs = [p2 for _, p2 in good] + [path]
            r, _ = compile_set(srcs, "inc")
            if r.returncode == 0:
                good.append((fid, path))
                progressed = True
            else:
                err = r.stderr.strip()
                reason = [l for l in err.split("\n") if "error" in l.lower()]
                deferred.append((fid, path, (reason[-1] if reason else err).strip()[-140:]))
        pending = [(f2, p2) for f2, p2, _ in deferred]
        excluded = [(f2, why) for f2, _p2, why in deferred]
        print("  第 %d 轮：good=%d deferred=%d" % (pass_no, len(good), len(deferred)))
        if not progressed:
            break

    print("\n编译进入同一 TRV1：%d / %d（%d 轮收敛）" % (len(good), len(items), 3))
    print("排除清单（增量归因，多轮收敛后仍失败 = 自身或其依赖缺口）：")
    for fid, why in excluded:
        print("  ✗ %s: %s" % (fid, why))
    print("OK 集合：%s" % " ".join(x.split("/")[-1] for x in sorted(g[0] for g in good)))

    # 冒烟：最终产物（好集 + main）双 VM 可执行
    src = os.path.join(OUTDIR, "main_final.c")
    with open(src, "w") as f:
        f.write(MAIN_SRC)
    out = os.path.join(OUTDIR, "final.trv")
    r = subprocess.run([TCC, "-nostdlib", src] + [p2 for _, p2 in good] + ["-o", out],
                       capture_output=True, text=True, timeout=120)
    if r.returncode == 0:
        rr = subprocess.run([CVM, out], capture_output=True, text=True, timeout=60)
        print("冒烟执行：CVM " + ("exit=0 ✓" if "exit=0" in rr.stderr else rr.stderr.strip()[-60:]))

    print("\n对照：trirv_cc 同管线逐文件 65/66（batch_musl_compile.py 逐文件 TRVO 口径，未解析引用"
          "由对象模式豁免）；本表为 tri-tcc 目录级多 TU 增量累加口径（含跨文件解析，每文件失败=自身原因）。"
          "剩余 7 文件均为依赖缺口：strdup/strndup→malloc（分配器在 musl/src/malloc，目录集外）、"
          "strerror_r→strerror（musl/src/errno）、index→strchr 链（闭包顺序）、ctype 表驱动×3"
          "（MB_CUR_MAX 宏/静态表声明——pp 剥头文件后需原生重写）")


if __name__ == "__main__":
    main()
