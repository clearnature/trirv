#!/usr/bin/env python3
"""musl 覆盖基线扫描（只读）——把现有 libc-musl/preprocess_musl.py 的规则施加到仓内
vendored 的 musl 源码，给出后续目录（ctype/stdio/multibyte/time）的起点数据。

对每个目录（排除宽字符 wc*/wcs*/wmem*，与 batch_musl_compile.py 的过滤一致）统计：
  文件数 | 现规则直接 SKIP | 过预处理 | 过预处理且含二进制敏感惯用法 | 被静默删除的行

用法：
  python3 tools/musl_baseline.py                 # 六个目录的汇总表
  python3 tools/musl_baseline.py stdio time      # 只看指定目录
  python3 tools/musl_baseline.py -v              # 另列 SKIP 原因分布与静默删行明细
  python3 tools/musl_baseline.py --self-check    # 负向控制：筛查必须能对已知样本报命中

口径：「含惯用法」是词法筛查（十六进制常量之类无害的行也计入）——线索池，不是判决；
「过预处理」不等于编译通过。
出处：docs/ARCH_PLAN_REVIEW-2026-11-24.md D3（2026-11-24 首测）。
"""
import collections
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "libc-musl"))
import preprocess_musl as pm  # noqa: E402

DIRS = ("string", "stdlib", "ctype", "stdio", "multibyte", "time")

# preprocess_musl.py 里「静默删行」用的三条模式（原样照抄只为识别它查了哪一行；
# 判定仍以它自己的 re.search 结果为准，模式改了这里要跟着改）。
P_MASK = r'&\s*-?\s*[0-9]'
P_MASK_ASSIGN = r'&=\s*-?\s*[0-9]'
P_CONTROL = r'\bif\b|\bwhile\b|\breturn\b'

# 二进制敏感惯用法：移位、异或、按位或、取反、十六进制掩码、位宽相关宏。
IDIOM = re.compile(r'>>|<<|\^|[^|]\|[^|]|~|0[xX][0-9a-fA-F]+|UINT_MAX|ULONG_MAX|CHAR_BIT')
SKIP_MARK = "#error TRIRV_SKIP"


class _ReShim:
    """垫在 preprocess_musl 的 re 上：不改变任何结果，只记录它对删行三条模式的查询。"""

    def __init__(self):
        self.log = []            # (行文本, 模式, 是否命中)

    def __getattr__(self, name):
        return getattr(re, name)

    def search(self, pat, s, *a, **k):
        m = re.search(pat, s, *a, **k)
        if pat in (P_MASK, P_MASK_ASSIGN, P_CONTROL):
            self.log.append((s, pat, bool(m)))
        return m


def dropped_lines(log):
    """重放每行的判定：含 `& 数字` 且同行无 if/while/return → 删；含 `&= 数字` → 删。"""
    dropped, cur = [], None
    for s, pat, hit in log:
        if pat == P_MASK:
            cur = [s, hit, False, False]
        elif pat == P_CONTROL and cur is not None and s == cur[0]:
            cur[2] = hit
        elif pat == P_MASK_ASSIGN and cur is not None and s == cur[0]:
            cur[3] = hit
            if cur[1] and not cur[2]:
                dropped.append((s, "a"))
            elif cur[3]:
                dropped.append((s, "b"))
            cur = None
    return dropped


def scan_file(rel):
    """返回 (是否 SKIP, SKIP 原因, 惯用法行列表, 被删行列表)。"""
    src = open(os.path.join(ROOT, "third_party", "musl", "src", rel), encoding="utf-8").read()
    shim = _ReShim()
    real_re = pm.re
    pm.re = shim
    try:
        out = pm.preprocess(src)
    finally:
        pm.re = real_re
    idioms = [ln.strip() for ln in out.split("\n")
              if IDIOM.search(ln) and not ln.strip().startswith(("#", "/*", "*", "//#"))]
    dropped = dropped_lines(shim.log)
    first = out.split("\n", 1)[0]
    if first.startswith(SKIP_MARK):
        return True, first.split("TRIRV_SKIP", 1)[1].lstrip(":").strip(), idioms, dropped
    return False, "", idioms, dropped


def non_wide(d):
    p = os.path.join(ROOT, "third_party", "musl", "src", d)
    return sorted(f for f in os.listdir(p) if f.endswith(".c") and not f.startswith(("wc", "wcs", "wmem")))


def self_check():
    """负向控制：筛查必须对已知含位运算的文件报命中、对已知删行报命中，否则这个工具是摆设。"""
    ok = True
    for rel in ("string/strspn.c", "stdlib/qsort.c"):
        skip, _, idioms, _ = scan_file(rel)
        hit = skip and len(idioms) >= 1
        ok &= hit
        print(f"  [{'ok' if hit else 'FAIL'}] {rel}: SKIP={skip} 惯用法行={len(idioms)}（应 SKIP 且 >=1）")
    skip, _, _, dropped = scan_file("string/memset.c")
    hit = any("n &= -4;" in s for s, _ in dropped)
    ok &= hit
    print(f"  [{'ok' if hit else 'FAIL'}] string/memset.c: 静默删行含 `n &= -4;`={hit}")
    print("自检通过" if ok else "自检失败：筛查失去判别力")
    return 0 if ok else 1


def main(argv):
    if "--self-check" in argv:
        return self_check()
    verbose = "-v" in argv
    dirs = [a for a in argv if not a.startswith("-")] or list(DIRS)

    print(f"{'目录':<10}{'文件数':>6}{'SKIP':>6}{'过预处理':>9}{'含惯用法(文件/行)':>20}{'静默删行':>10}")
    for d in dirs:
        files = non_wide(d)
        n_skip = n_idiom = n_idiom_lines = 0
        reasons = collections.Counter()
        drops = []
        for fn in files:
            skip, why, idioms, dropped = scan_file(d + "/" + fn)
            if skip:
                n_skip += 1
                reasons[why] += 1
            elif idioms:
                n_idiom += 1
                n_idiom_lines += len(idioms)
            if dropped:
                drops += [(fn, skip, s, rule) for s, rule in dropped]
        print(f"{d:<10}{len(files):>6}{n_skip:>6}{len(files) - n_skip:>9}"
              f"{f'{n_idiom}/{n_idiom_lines}':>20}{len(drops):>10}")
        if verbose:
            for why, n in reasons.most_common():
                print(f"     SKIP ×{n}: {why}")
            for fn, skip, s, rule in drops:
                print(f"     删行[{rule}] {fn}{'' if skip else '  ← 过预处理'}: {s[:90]}")
    if not verbose:
        print("\n加 -v 查看 SKIP 原因与静默删行明细；--self-check 跑负向控制。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
