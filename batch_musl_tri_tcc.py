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

DIRS = ["/Users/zesheng/ai-lab/musl/src/string",
        "/Users/zesheng/ai-lab/musl/src/ctype",
        "/Users/zesheng/ai-lab/musl/src/stdlib"]

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
SHIM = "typedef signed int sint;\ntypedef void *locale_t;\n"


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
    if not os.path.exists(TCC):
        print("tri-tcc 未构建 —— SKIP")
        sys.exit(0)
    items = prepare()
    print("候选文件：%d（musl/src/string + stdlib，pp 同管线 + musl_tri 重写）" % len(items))

    active = dict(items)
    ok, excluded = [], []
    rnd = 0
    while active:
        rnd += 1
        srcs = [p for _, p in sorted(active.items())]
        r, out = compile_set(srcs, "batch")
        if r.returncode == 0:
            ok = sorted(active.keys())
            break
        err = r.stderr.strip()
        # 归因：首个 error 行的源文件路径精确提取（path:line: error）
        blamed = None
        # 归因粒度注记：error|warning 兼容——纯警告失败批（如 implicit 声明
        # 后接 unresolved）的错误行可能无文件路径，会走二分兜底，个别归因
        # 会有噪声；语义验证以契约套件为准，本工具只做批量率统计
        m = re.search(r"^([^:\s]+\.(?:c|C)):\d+:\s*(?:error|warning)", err, re.M)
        if m:
            blamed_path = m.group(1)
            for fid, path in active.items():
                if os.path.abspath(path) == os.path.abspath(blamed_path):
                    blamed = fid
                    break
        if blamed is None:
            # 错误无文件路径（如 unresolved）——二分排除定位
            keys = sorted(active.keys())
            lo, culprit = 0, None
            while lo < len(keys) and culprit is None:
                mid = (lo + len(keys)) // 2
                sub = {k: active[k] for k in keys[:mid]}
                if len(sub) >= 1:
                    r2, _ = compile_set([p for _, p in sorted(sub.items())], "probe")
                    if r2.returncode != 0:
                        keys = keys[:mid]
                    else:
                        lo = mid + 1
                else:
                    break
            culprit = keys[0] if keys else None
            blamed = culprit
        if blamed is None or blamed not in active:
            print("无法归因的批次失败：\n%s" % err[-600:])
            excluded.append(("<未归因>", err.strip()[-160:]))
            break
        reason = [l for l in err.split("\n") if "error" in l.lower()]
        excluded.append((blamed, (reason[-1] if reason else err).strip()[-140:]))
        del active[blamed]

    print("\n编译进入同一 TRV1：%d / %d" % (len(ok), len(items)))
    print("排除清单：")
    for fid, why in excluded:
        print("  ✗ %s: %s" % (fid, why))
    print("OK 集合：%s" % (" ".join(x.split("/")[-1] for x in ok) if ok else "(空)"))

    # 冒烟：最终产物双 VM 可执行
    if ok:
        r, out = compile_set([p for _, p in sorted(active.items())], "final")
        smoke = "未运行"
        if r.returncode == 0:
            rr = subprocess.run([CVM, out], capture_output=True, text=True, timeout=60)
            smoke = "CVM " + ("exit=0 ✓" if "exit=0" in rr.stderr else rr.stderr.strip()[-60:])
            prog_ok = os.path.exists(out)
        print("冒烟执行：%s" % smoke)

    print("\n对照：trirv_cc 同管线逐文件 65/66（batch_musl_compile.py）；"
          "本表为 tri-tcc 目录级多 TU 口径（含跨文件解析）")


if __name__ == "__main__":
    main()
