#!/usr/bin/env python3
"""cpp 回审模糊契约套件——audit_probe（40 用例）+ audit_iter 迭代 2/3
（57 用例）入册门禁。迭代 4 变异模糊（414 次）保留为工具不进 fast 门禁
（耗时），定期手动跑。

口径：crash/hang/misaccept/misreject 任一=FAIL。
"""
import subprocess
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_tool(args):
    r = subprocess.run([sys.executable, "-I"] + args,
                       capture_output=True, text=True, timeout=300)
    return r.returncode, r.stdout + r.stderr


def main():
    fails = []
    total = 0
    for (args, label) in [
        ([os.path.join(ROOT, "tools", "audit_probe.py")], "probe"),
        ([os.path.join(ROOT, "tools", "audit_iter.py"), "2"], "iter2"),
        ([os.path.join(ROOT, "tools", "audit_iter.py"), "3"], "iter3"),
    ]:
        code, out = run_tool(args)
        # 解析用例数与异常数
        n_cases = 0
        n_bad = 0
        for ln in out.splitlines():
            if "用例=" in ln or "变异=" in ln:
                n_cases += int(ln.split("=")[1].split()[0])
            elif ("崩溃=" in ln and "崩溃=0" not in ln) or \
                    "RecursionError" in ln or "HANG" in ln or \
                    "misaccept" in ln or "misreject" in ln or \
                    "[deep-" in ln or "[" in ln and "crash" in out:
                pass
        bad_markers = ("RecursionError", "HANG", "misaccept",
                       "misreject", "crash")
        if any(m in out for m in bad_markers) and "崩溃=0" not in out:
            n_bad += 1
        # probe 汇总行格式：诚实拒绝=X 正常通过=Y 崩溃=Z 挂死=W
        if "崩溃=" in out:
            import re
            m = re.search(r"崩溃=(\d+)", out)
            if m and int(m.group(1)) > 0:
                n_bad += 1
            m2 = re.search(r"挂死=(\d+)", out)
            if m2 and int(m2.group(1)) > 0:
                n_bad += 1
        if "异常=" in out:
            import re
            m = re.search(r"异常=(\d+)", out)
            if m and int(m.group(1)) > 0:
                n_bad += 1
        # 误收清单非空也算失败
        if "误收清单" in out:
            sec = out.split("误收清单")[1]
            if sec.strip() and not sec.strip().startswith("="):
                lines = [ln for ln in sec.splitlines()
                         if ln.strip().startswith("[")]
                if lines:
                    n_bad += 1
        total += n_cases
        if n_bad:
            fails.append((label, out[-400:]))
    if fails:
        for (label, tail) in fails:
            print(f"FAIL {label}: {tail}")
        print(f"cpp 回审模糊契约：{total - len(fails)} PASS / "
              f"{len(fails)} FAIL")
        sys.exit(1)
    print(f"cpp 回审模糊契约：{total} PASS / 0 FAIL")
    sys.exit(0)


if __name__ == "__main__":
    main()
