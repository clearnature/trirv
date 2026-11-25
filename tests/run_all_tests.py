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
ROOT = os.path.dirname(HERE)
PY = sys.executable or "python3"


def _build_binaries():
    """确保 C 编译器与 C VM 是最新的（源码比二进制新就重建）。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cc_src = os.path.join(root, "cc", "trirv_cc.c")
    cc_bin = os.path.join(root, "cc", "bin", "trirv_cc")
    vm_src = os.path.join(root, "vm", "trirv_vm.c")
    vm_bin = os.path.join(root, "vm", "bin", "trirv_vm")
    notes = []
    if os.path.exists(cc_src) and (not os.path.exists(cc_bin)
                                   or os.path.getmtime(cc_bin) < os.path.getmtime(cc_src)):
        for c in ("clang", "gcc", "cc"):
            r = subprocess.run([c, "-std=gnu11", "-O2", "-o", cc_bin, cc_src, "-lm"],
                               capture_output=True, text=True)
            if r.returncode == 0:
                notes.append(f"重建 trirv_cc（{c}）")
                break
        else:
            print("FATAL: trirv_cc 重建失败——拒绝用旧二进制继续回归"
                  "（源码改坏+旧二进制残留时全绿是假绿）")
            sys.exit(1)
    # trirv_float.h 也是 vm 依赖（审计实证：头文件改后不触发重建→旧二进制假绿）
    vm_hdr = os.path.join(root, "trirv_float.h")
    vm_stale = (not os.path.exists(vm_bin)
                or os.path.getmtime(vm_bin) < os.path.getmtime(vm_src)
                or (os.path.exists(vm_hdr) and os.path.getmtime(vm_bin) < os.path.getmtime(vm_hdr)))
    if os.path.exists(vm_src) and vm_stale:
        for c in ("clang", "gcc", "cc"):
            r = subprocess.run([c, "-O2", "-I", root, "-o", vm_bin, vm_src, "-lm"],
                               capture_output=True, text=True)
            if r.returncode == 0:
                notes.append(f"重建 trirv_vm（{c}）")
                break
        else:
            print("FATAL: trirv_vm 重建失败——拒绝用旧二进制继续回归")
            sys.exit(1)
    return notes


def _build_forced(cc_src, cc_bin, flags):
    """强制重建（不看 mtime）——--san 跑完恢复 O2 用。
    cwd=源码目录：trirv_vm.c 的 #include "trirv_float.h" 在仓库根，
    引号 include 搜索源目录+cwd，缺 cwd 是历史坑（常规构建因 mtime
    检查从不触发，强制重建首次暴露）。"""
    inc = os.path.dirname(os.path.dirname(cc_src))     # 仓库根（trirv_float.h 所在）
    for c in ("clang", "gcc", "cc"):
        r = subprocess.run([c] + flags + ["-I", inc, "-o", cc_bin, cc_src, "-lm"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return f"重建（{c}）"
    return "重建失败"


def _build_san():
    """ADR-29 门禁：ASan/UBSan 版编译器与 CVM 写到规范路径（套件用绝对路径）。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pairs = (("trirv_cc", os.path.join(root, "cc", "trirv_cc.c"),
              os.path.join(root, "cc", "bin", "trirv_cc")),
             ("trirv_vm", os.path.join(root, "vm", "trirv_vm.c"),
              os.path.join(root, "vm", "bin", "trirv_vm")))
    flags = ["-std=gnu11", "-O1", "-g", "-fsanitize=address,undefined",
             "-fno-omit-frame-pointer"]
    notes = []
    for tag, s, b in pairs:
        r = subprocess.run(["clang"] + flags + ["-o", b, s, "-lm"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            # 审计实证：静默退化=门禁假绿——san 版构建失败必须硬失败
            print(f"FATAL: {tag} ASan/UBSan 构建失败: {r.stderr[-200:]}")
            sys.exit(1)
        notes.append(f"{tag} ASan/UBSan 构建✓")
    return notes


def _restore_o2():
    """--san 跑完恢复 O2 规范二进制（强制重建——san 版 mtime 更新会骗过常规检查）。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pairs = (("trirv_cc", os.path.join(root, "cc", "trirv_cc.c"),
              os.path.join(root, "cc", "bin", "trirv_cc")),
             ("trirv_vm", os.path.join(root, "vm", "trirv_vm.c"),
              os.path.join(root, "vm", "bin", "trirv_vm")))
    notes = []
    for tag, s, b in pairs:
        notes.append(f"{tag} 恢复 O2：{_build_forced(s, b, ['-std=gnu11', '-O2'])}")
    return notes


# (脚本, 显示名, 是否慢, 已知缺口)
SUITES = [
    ("trirv_e2e.py",              "汇编端到端",              False, None),
    ("../debugger/test_trirv_dbg.py", "调试器 MVP 18 项",       False, None),
    ("test_isa_completeness.py",  "ISA 完整性 86 项",        False, None),  # 仓库根（目录重组后路径）
    ("trirv_tdd_contract.py",     "契约测试（TDD 89 项）",    False, None),
    ("trirv_tritlogic_e2e.py",    "AND/OR/XOR 逐 trit 锁定",  False, None),
    ("trirv_bitview_e2e.py",      "C 位运算/移位 三值本征",  False, None),
    ("trirv_cc_py_bitop_e2e.py",  "py 后端位运算差分 15 项",  False, None),
    ("trirv_float_e2e.py",        "TF 浮点编解码 144 项",     False, None),
    ("trirv_float_isa_e2e.py",    "F 扩展指令双VM差分 39 项",  False, None),
    ("trirv_base12_e2e.py",       "十二进制表示层 72 项",     False, None),
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
    ("tools/trirv_adiff.py",      "A 扩展原子差分",           False, None),
    ("trirv_byvalue_e2e.py",      "按值返回",                False, None),
    ("trirv_stackargs_e2e.py",    "栈上传参",                False, None),
    ("trirv_crt_e2e.py",          "crt0+musl 启动链 29 项",   False, None),
    ("trirv_musl_e2e.py",         "musl libc + busybox 48 项", False, None),
    ("trirv_link_e2e.py",         "多文件链接 14 项",         False, None),
    ("trirv_octet_e2e.py",        "8-bit 视图层 13 项",       False, None),
    ("trirv_trvo_e2e.py",         "TRVO 目标文件 25 项",      False, None),
    ("trirv_musl_real_e2e.py",    "真 musl 源码编译试点 11 项", False, None),
    ("trirv_musl_wide_e2e.py",    "musl string 全目录 7 项",   False, None),
    ("trirv_switch_e2e.py",       "switch 契约 9 项",         False, None),
    ("trirv_musl_tri_e2e.py",     "musl_tri 位图系重写 7 项",  False, None),
    ("trirv_float_libc_e2e.py",   "浮点 libc 五函数（P4）",    False, None),
    ("trirv_undeclared_e2e.py",   "未声明标识符诚实报错 12 项", False, None),
    ("trirv_tri_tcc_e2e.py",      "tri-tcc 后端 88 例双 VM",  False, None),
    ("trirv_tri_musl_e2e.py",     "musl→tri-tcc 多TU 3 组 30 断言", False, None),
    ("trirv_difftest_tcc.py",     "双工具链差分 22 组四路一致", False, None),
    ("trirv_tri_isa_guard.py",    "T-1.8 ISA 一致性守卫",      False, None),
    ("trirv_mem4_e2e.py",         "LW4/SW4 4-tryte 契约 6 项", False, None),
    ("trirv_arch_e2e.py",         "musl arch 层 read8/write8 12 项", False, None),
    ("trirv_tcc_syscall_e2e.py",  "tri-tcc __tri_syscall 对接面 7 项", False, None),
    ("trirv_tlit_e2e.py",         "T-LIT 文字运算双 VM 6 项", False, None),
    ("trirv_compact_e2e.py",      "TC 压缩发射契约 42 项",    False, None),
    ("trirv_amp_e2e.py",          "AMP mailbox/mrecvw/mipi 22 项",  False, None),
    ("trirv_fw_tick_e2e.py",       "固件定时器/ISR 向量 14 项",   False, None),
    ("trirv_vdisk_e2e.py",         "虚拟磁盘透传 20 项",        False, None),
    ("trirv_stdio_e2e.py",         "stdio 行为契约 24 项",      False, None),
    ("trirv_mbwc_e2e.py",          "multibyte+wctype+wcs 22 项", False, None),
    ("trirv_ctype_e2e.py",        "ctype M3 查表差分 12 项",   False, None),
    ("trirv_mersenne_e2e.py",     "梅森素数高精度验证 16 项",  False, None),
    ("trirv_bignum_e2e.py",       "bignum 无限精度库 9 项",    False, None),
    ("trirv_gf3_witness_e2e.py",  "GF(3)/C12 见证机检 7 项",   False, None),
    ("trirv_time_e2e.py",          "time 日历/strftime/时钟 14 项", False, None),
    ("trirv_trv2_relax_e2e.py",    "TRV2 长分支弛豫 10 项",     False, None),
    ("trirv_wasm_spec_smoke.py",   "wasm spec 冒烟 5 文件",     False, None),
    ("research_p_trit_e2e.py",     "p-trit 三值热力学契约 4 项", False, None),
    ("trirv_cc_py_fnptr_e2e.py",  "py fn-ptr 双后端同构 12 项", False, None),
    ("trirv_trv2_e2e.py",         "TRV2 打包正式化 50 项",    False, None),
    ("trirv_tcjump_e2e.py",       "TC sf5 C-J/C-JAL 12 项",   False, None),
    ("trirv_golden_e2e.py",       "手算黄金补缺 26 项",       False, None),
    ("trirv_dual_fuzz.py",        "双引擎随机差分 200+40 对",  False, None),
    ("trirv_relax_e2e.py",        "长分支弛豫契约 5 项",      False, None),
    ("trirv_trv2_equiv.py",       "全语料 TRV1≡TRV2 102 项",  False, None),
    ("trirv_compound_e2e.py",     "复合字面量契约 6 项",      False, None),
    ("trirv_qsort_tri_e2e.py",    "qsort 三进制重写 4 项",    False, None),
    ("trirv_strtol_tri_e2e.py",   "strtol 三进制重写 4 项",   False, None),
    ("trirv_mdiff.py",            "M 扩展差分 700 项",        True,  None),
    ("trirv_wasm_e2e.py",         "Wasm 三路差分 112 例(含 P3 浮点 19)", True, None),
    ("trirv_tritref_test.py",     "tryte 参考实现 46164 项",  True,  None),
    ("trirv_fuzz.py",             "随机指令 fuzz 1500",       True,  None),
    ("trirv_fuzz_mem.py",         "随机访存 fuzz 1000",       True,  None),
    ("trirv_fuzz_csr.py",         "随机 CSR fuzz 800",        True,  None),
]

FAIL_PAT = re.compile(r"(\[FAIL\]|^\s*FAIL\b|Traceback)"
                      r"|AddressSanitizer|UndefinedBehaviorSanitizer|LeakSanitizer"
                      r"|runtime error:", re.M)   # 末三段=sanitizer 门禁（--san）
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


def run_one(script, name, known_gap, strict, timeout, fuzz_seed="0"):
    path = os.path.join(HERE, script)
    if not os.path.exists(path) and os.path.exists(os.path.join(ROOT, script)):
        path = os.path.join(ROOT, script)   # 兼容仓库根脚本（test_isa_completeness.py 等）
    if not os.path.exists(path):
        return "MISS", 0.0, "脚本不存在"
    env = dict(os.environ)
    if strict:
        env["TRV_CC_STRICT"] = "1"
    else:
        env.pop("TRV_CC_STRICT", None)
    # fuzz 套件种子透传（ADR-29）：脚本读 argv[1]=seed；默认 0 可复现
    cmd = [PY, path]
    if fuzz_seed and script in ("trirv_fuzz.py", "trirv_fuzz_mem.py", "trirv_fuzz_csr.py"):
        cmd.append(str(fuzz_seed))
    t0 = time.time()
    try:
        r = subprocess.run(cmd, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           capture_output=True,
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
    ap.add_argument("--san", action="store_true",
                    help="ASan/UBSan 构建编译器与 CVM 后跑全量（ADR-29 门禁），跑完恢复 O2")
    ap.add_argument("--fuzz-seed", default="0",
                    help="fuzz 套件种子：数字、random（CSPRNG 展开并追加到 "
                         "tmp/fuzz-seeds.log 供精确回放，ADR-29）")
    args = ap.parse_args()
    if args.fuzz_seed == "random":
        import secrets
        args.fuzz_seed = str(secrets.randbelow(1000000))
        os.makedirs(os.path.join(ROOT, "tmp"), exist_ok=True)
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True,
                              cwd=ROOT).stdout.strip()
        with open(os.path.join(ROOT, "tmp", "fuzz-seeds.log"), "a") as sf:
            sf.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} "
                     f"seed={args.fuzz_seed} head={head or '?'}\n")
        print(f"  · fuzz 种子（CSPRNG，已存档 tmp/fuzz-seeds.log）：{args.fuzz_seed} @ {head or '?'}")

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
    if args.san:
        for note in _build_san():
            print(f"  · [san] {note}")
    print(f"  · TRV_CC_STRICT={'0' if args.no_strict else '1'}"
          + ("   SAN=1" if args.san else "") + f"   套件={len(suites)}")
    print()

    rows = []
    t_all = time.time()
    for script, name, slow, gap in suites:
        status, dt, summary = run_one(script, name, gap, not args.no_strict, args.timeout,
                                      fuzz_seed=args.fuzz_seed)
        rows.append((script, name, status, dt, summary))
        print(f"  {status:7s} {name:26s} {dt:6.2f}s  {summary}")
        sys.stdout.flush()

    if args.san:
        for note in _restore_o2():
            print(f"  · [san] {note}")
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
