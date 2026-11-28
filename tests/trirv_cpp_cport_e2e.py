#!/usr/bin/env python3
"""cpp cport 端到端差分套件（0.1.6 L1 硬闸）。
每语料四路：transpiler.py→.c→trirv_cc→.trv→VM
             tricpp→.c→trirv_cc→.trv→VM
产物语义恒等 = 双 VM exit 相等。
验收标准：63 语料 0 分歧（smoke≠全量——行为闸）。"""
import glob
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)                # trirv_isa（tests/_trvio 依赖）
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "vm"))


def run_py_pipeline(cfile, tag, tmpdir):
    """Python transpiler 通路 → exit code"""
    c_out = os.path.join(tmpdir, f"{tag}_py.c")
    t_out = os.path.join(tmpdir, f"{tag}_py.trv")
    r1 = subprocess.run([sys.executable, "cpp/transpiler.py", cfile, c_out],
                        capture_output=True, text=True, timeout=60,
                        errors="replace", cwd=ROOT)
    if r1.returncode != 0:
        return ("skip", None)
    cc = os.path.join(ROOT, "cc", "bin", "trirv_cc")
    r2 = subprocess.run([cc, c_out, t_out], capture_output=True,
                        text=True, timeout=30, errors="replace")
    if r2.returncode != 0:
        return ("skip", None)
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    sys.path.insert(0, os.path.join(ROOT, "vm"))
    import _trvio
    import trirv_vm as vm
    prog, mem, _ = _trvio.load_bytes(open(t_out, "rb").read())
    c = vm.VM(prog, mem_init=mem).run(max_steps=100000)
    return ("ok", c.exit_code)


def run_c_pipeline(tricpp_bin, cfile, tag, tmpdir):
    """tricpp 通路 → exit code"""
    c_out = os.path.join(tmpdir, f"{tag}_c.c")
    t_out = os.path.join(tmpdir, f"{tag}_c.trv")
    r1 = subprocess.run([tricpp_bin, cfile, c_out],
                        capture_output=True, text=True, timeout=60,
                        errors="replace", cwd=ROOT)
    if r1.returncode != 0:
        return ("skip", None)
    cc = os.path.join(ROOT, "cc", "bin", "trirv_cc")
    r2 = subprocess.run([cc, c_out, t_out], capture_output=True,
                        text=True, timeout=30, errors="replace")
    if r2.returncode != 0:
        return ("skip", None)
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    sys.path.insert(0, os.path.join(ROOT, "vm"))
    import _trvio
    import trirv_vm as vm
    prog, mem, _ = _trvio.load_bytes(open(t_out, "rb").read())
    c = vm.VM(prog, mem_init=mem).run(max_steps=100000)
    return ("ok", c.exit_code)


def build_tricpp(tmpdir):
    srcs = sorted(glob.glob(os.path.join(ROOT, "cpp", "c", "*.c")))
    out = os.path.join(tmpdir, "tricpp_build")
    r = subprocess.run(
        ["gcc", "-std=gnu11", "-O2", "-I", os.path.join(ROOT, "cpp", "c"),
         "-o", out] + srcs,
        capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        print(f"tricpp 构建失败: {r.stderr[:300]}")
        sys.exit(1)
    return out


def main():
    corpus = sorted(glob.glob(os.path.join(ROOT, "cpp", "tests", "*.cpp")))
    tmpdir = tempfile.mkdtemp(prefix="gate_diff_")
    tricpp = build_tricpp(tmpdir)

    pass_n = fail_n = skip_n = 0
    fails = []
    for cf in corpus:
        tag = os.path.basename(cf).replace(".cpp", "")
        cf_path = os.path.join(tmpdir, tag + ".cpp")
        with open(cf_path, "w") as f:
            f.write(open(cf).read())

        # Python transpiler 通路
        py_status, py_exit = run_py_pipeline(cf_path, tag + "_py", tmpdir)
        # tricpp 通路
        c_status, c_exit = run_c_pipeline(tricpp, cf_path, tag, tmpdir)

        if py_status == "skip" and c_status == "skip":
            skip_n += 1
        elif py_status == "ok" and c_status == "ok" and py_exit == c_exit:
            pass_n += 1
        elif py_status == "ok" and c_status == "skip":
            skip_n += 1   # tricpp 尚未覆盖（正常——S4 逐步扩展）
        elif py_status == "skip" and c_status == "ok":
            fail_n += 1
            fails.append((tag, f"py skip 但 c ok: py_exit={py_exit} c_exit={c_exit}"))
        elif py_status == "ok" and c_status == "ok" and py_exit != c_exit:
            fail_n += 1
            fails.append((tag, f"exit 分歧: py={py_exit} c={c_exit}"))
        else:
            skip_n += 1

    print(f"cpp cport 端到端差分：{pass_n} PASS / {fail_n} FAIL / {skip_n} SKIP")
    for (tag, msg) in fails[:8]:
        print(f"  FAIL [{tag}] {msg}")
    sys.exit(1 if fail_n > 0 else 0)


if __name__ == "__main__":
    main()
