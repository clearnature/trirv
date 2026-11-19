#!/usr/bin/env python3
"""TriRV 全量测试入口 —— 一条命令跑完，一张表看结果。

    python3 run_all_tests.py             # 全量（含 fuzz / wasm / 46164 项差分）
    python3 run_all_tests.py --fast      # 跳过慢套件，本地改代码时用
    python3 run_all_tests.py --only cc   # 只跑名字含 "cc" 的套件
    python3 run_all_tests.py --list      # 只列套件

判定规则（三票否决，任一命中即 FAIL）：
  1. 退出码非 0
  2. 输出里出现 `[FAIL]` 或行首 `FAIL`
  3. 输出里出现 `Traceback`

为什么不能只看退出码：`trirv_difftest.py` 等老脚本逐例打印 PASS/FAIL 但结尾
不 sys.exit，失败时退出码仍是 0 —— 只看退出码会把红的看成绿的。

默认带 `TRV_CC_STRICT=1`：不加这个，C 后端不支持的用例会静默回退 Python，
14 套全绿的假象就是这么来的（README「验证陷阱」）。

已知缺口用 XFAIL 标：`struct 按值传参 / sret` 尚未在 C 后端实现，套件在 STRICT
下必然失败。它出现在表里、但不阻断整体结论 —— 缺口要看得见，而不是被藏起来。
"""
import argparse
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable or "python3"


def _build_binaries():
    """确保 C 编译器与 C VM 是最新的（源码比二进制新就重建）。"""
    notes = []
    cc_src = os.path.join(HERE, "trirv_cc.c")
    cc_bin = os.path.join(HERE, "trirv_cc")
    if os.path.exists(cc_src) and (not os.path.exists(cc_bin)
                                   or os.path.getmtime(cc_bin) < os.path.getmtime(cc_src)):
        for c in ("clang", "gcc", "cc"):
            r = subprocess.run([c, "-std=gnu11", "-O2", "-o", cc_bin, cc_src, "-lm"],
                               capture_output=True, text=True)
            if r.returncode == 0:
                notes.append(f"重建 trirv_cc（{c}）")
                break
        else:
            notes.append("!! trirv_cc 重建失败")
    vm_src = os.path.join(HERE, "trirv_vm.c")
    vm_bin = os.path.join(HERE, "trirv_vm")
    if os.path.exists(vm_src) and (not os.path.exists(vm_bin)
                                   or os.path.getmtime(vm_bin) < os.path.getmtime(vm_src)):
        for c in ("clang", "gcc", "cc"):
            r = subprocess.run([c, "-O2", "-o", vm_bin, vm_src, "-lm"],
                               capture_output=True, text=True)
            if r.returncode == 0:
                notes.append(f"重建 trirv_vm（{c}）")
                break
        else:
            notes.append("!! trirv_vm 重建失败")
    return notes


# (脚本, 显示名, 是否慢, 已知缺口)
SUITES = [
    ("trirv_e2e.py",              "汇编端到端",              False, None),
    ("test_isa_completeness.py",  "ISA 完整性 86 项",        False, None),
    ("trirv_tdd_contract.py",     "契约测试（TDD 59 项）",    False, None),
    ("trirv_tritlogic_e2e.py",    "AND/OR/XOR 逐 trit 锁定",  False, None),
    ("trirv_bitview_e2e.py",      "C 位运算 方案 A 85 项",    False, None),
    ("trirv_difftest.py",         "PyVM vs C VM 差分",       False, None),
    ("trirv_csr_diff.py",         "CSR/trap 差分",           False, None),
    ("trirv_charsign_e2e.py",     "char 符号性 21 项",        False, None),
    ("trirv_cc_e2e.py",           "C 编译器端到端",           False, None),
    ("trirv_array_e2e.py",        "数组",                    False, None),
    ("trirv_multiarray_e2e.py",   "多维数组",                False, None),
    ("trirv_struct_e2e.py",       "结构体",                  False, None),
    ("trirv_ptr_e2e.py",          "指针",                    False, None),
    ("trirv_malloc_e2e.py",       "malloc/free",            False, None),
    ("trirv_syscall_e2e.py",      "系统调用",                False, None),
    ("trirv_fmt_e2e.py",          "格式化输出",              False, None),
    ("trirv_vararg_e2e.py",       "变参",                    False, None),
    ("trirv_csr_e2e.py",          "CSR 指令",                False, None),
    ("trirv_adiff.py",            "A 扩展原子差分",           False, None),
    ("trirv_byvalue_e2e.py",      "按值返回",                False, "struct 按值返回（sret）未实现"),
    ("trirv_stackargs_e2e.py",    "栈上传参",                False, "struct 按值传参未实现"),
    ("trirv_mdiff.py",            "M 扩展差分 700 项",        True,  None),
    ("trirv_wasm_e2e.py",         "Wasm 三路差分 93 例",      True,  None),
    ("trirv_tritref_test.py",     "tryte 参考实现 46164 项",  True,  None),
    ("trirv_fuzz.py",             "随机指令 fuzz 1500",       True,  None),
    ("trirv_fuzz_mem.py",         "随机访存 fuzz 1000",       True,  None),
    ("trirv_fuzz_csr.py",         "随机 CSR fuzz 800",        True,  None),
]

FAIL_PAT = re.compile(r"(\[FAIL\]|^\s*FAIL\b|Traceback)", re.M)
NUM_PAT = re.compile(r"(\d+)\s*/\s*(\d+)")


SUMMARY_PATS = [
    (re.compile(r"总计\s*(\d+)\s*PASS\s*/\s*(\d+)\s*FAIL"),
     lambda a, b: f"{a} PASS / {b} FAIL"),                       # wasm
    (re.compile(r"差分校验：\s*(\d+)\s*项，失败\s*(\d+)"),
     lambda a, b: f"{a} 项 / {b} 失败"),                          # tritref
    (re.compile(r"(\d+)\s*passed[,，]\s*(\d+)\s*failed"),
     lambda a, b: f"{a} passed / {b} failed"),                   # isa 完整性
    (re.compile(r"(\d+)\s*/\s*(\d+)\s*通过"),
     lambda a, b: f"{a}/{b}"),                                   # 多数 e2e
    (re.compile(r"通过\s*(\d+).*失败\s*(\d+)"),
     lambda a, b: f"{a} 通过 / {b} 失败"),
]


def summarize(out):
    """抽一句摘要：优先套具体格式，其次 N/M，最后一行。"""
    for pat, fmt in SUMMARY_PATS:
        hits = pat.findall(out)
        if hits:
            return fmt(*hits[-1])
    for line in reversed(out.strip().splitlines()):
        m = NUM_PAT.search(line)
        if m and re.search(r"通过|passed|PASS|PASSED|一致", line):
            return f"{m.group(1)}/{m.group(2)}"
    for line in reversed(out.strip().splitlines()):
        if line.strip():
            return line.strip()[:46]
    return "(无输出)"


def run_one(script, name, known_gap, strict, timeout):
    path = os.path.join(HERE, script)
    if not os.path.exists(path):
        return "MISS", 0.0, "脚本不存在"
    env = dict(os.environ)
    if strict:
        env["TRV_CC_STRICT"] = "1"
    else:
        env.pop("TRV_CC_STRICT", None)
    t0 = time.time()
    try:
        r = subprocess.run([PY, path], cwd=HERE, capture_output=True,
                           text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return "TIMEOUT", time.time() - t0, f">{timeout}s"
    dt = time.time() - t0
    out = (r.stdout or "") + (r.stderr or "")
    bad = r.returncode != 0 or bool(FAIL_PAT.search(out))
    if bad:
        return ("XFAIL" if known_gap else "FAIL"), dt, summarize(out)
    return ("XPASS" if known_gap else "PASS"), dt, summarize(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="跳过慢套件")
    ap.add_argument("--only", default=None, help="只跑名字/脚本名含该子串的套件")
    ap.add_argument("--list", action="store_true", help="只列套件")
    ap.add_argument("--no-strict", action="store_true", help="不设 TRV_CC_STRICT")
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()

    # 去重（占位项 name=None 直接丢）
    seen, suites = set(), []
    for script, name, slow, gap in SUITES:
        if name is None or script in seen:
            continue
        seen.add(script)
        suites.append((script, name, slow, gap))
    if args.fast:
        suites = [s for s in suites if not s[2]]
    if args.only:
        k = args.only.lower()
        suites = [s for s in suites if k in s[0].lower() or k in s[1].lower()]
    if args.list:
        for script, name, slow, gap in suites:
            print(f"{'慢' if slow else '  '}  {script:26s} {name}"
                  + (f"   [已知缺口: {gap}]" if gap else ""))
        return 0

    print("TriRV 全量测试")
    for note in _build_binaries():
        print(f"  · {note}")
    print(f"  · TRV_CC_STRICT={'0' if args.no_strict else '1'}   套件={len(suites)}")
    print()

    rows = []
    t_all = time.time()
    for script, name, slow, gap in suites:
        status, dt, summary = run_one(script, name, gap, not args.no_strict, args.timeout)
        rows.append((script, name, status, dt, summary))
        print(f"  {status:7s} {name:26s} {dt:6.2f}s  {summary}")
        sys.stdout.flush()

    total = time.time() - t_all
    n = {k: sum(1 for r in rows if r[2] == k)
         for k in ("PASS", "FAIL", "XFAIL", "XPASS", "TIMEOUT", "MISS")}
    print("\n" + "=" * 72)
    print(f"合计 {len(rows)} 套，用时 {total:.1f}s："
          f"PASS {n['PASS']}，FAIL {n['FAIL']}，XFAIL {n['XFAIL']}，"
          f"XPASS {n['XPASS']}，TIMEOUT {n['TIMEOUT']}，MISS {n['MISS']}")
    if n["XFAIL"]:
        print("已知缺口（不阻断）：")
        for script, name, status, _dt, _s in rows:
            if status == "XFAIL":
                gap = next(g for s, _n, _sl, g in SUITES if s == script and g)
                print(f"  - {name}：{gap}")
    if n["FAIL"] or n["TIMEOUT"] or n["MISS"]:
        print("失败：")
        for script, name, status, _dt, s in rows:
            if status in ("FAIL", "TIMEOUT", "MISS"):
                print(f"  - {name}（{script}）：{s}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
