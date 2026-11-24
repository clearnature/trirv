#!/usr/bin/env python3
"""trirv_arch_e2e —— TriRV musl arch 层契约（内联汇编 → read8/write8 重写）。

上游 musl arch 层（third_party/musl/arch/riscv64/{syscall_arch,atomic_arch,
pthread_arch,crt_arch}.h）全部依赖内联汇编；TriRV 工具链无内联汇编
（tri-tcc spike），arch 层以纯 C 重写于 libc-musl/musl_tri/arch_tri/：
  - syscall_tri.h：read/write 路由 read8/write8（8-bit 视图系统调用，
    号 5000/5001，octet <-> 双 tryte [lo=o%243, hi=o/243]）——musl 源层
    唯一 syscall 对接面；
  - atomic_tri.h：单核非抢占口径下 a_cas/a_swap/a_fetch_add/a_and/a_or/
    a_inc/a_dec/a_ctz_64/a_barrier 纯 C 化（A 扩展 ISA 保留，SMP 再切）。

本契约锁三件事：
  1. arch 头文件可经 trirv_cc 管线编译（PP 剥离拼接，与 musl 管线同法）；
  2. read/write 路由语义与上游 write(2)/read(2) 三对齐（fd/计数/返回值），
     高位字节无损往返，双 VM 一致；
  3. 原子纯 C 语义逐项对 oracle。
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
CVM = os.environ.get("TRIRV_VM_BIN", os.path.join(ROOT, "trirv_vm"))
CC = os.path.join(ROOT, "cc", "bin", "trirv_cc")
ARCH = os.path.join(ROOT, "libc-musl", "musl_tri", "arch_tri")

passed = 0
failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print("PASS %s: %r" % (name, got))
    else:
        failed += 1
        print("FAIL %s: got=%r want=%r" % (name, got, want))


def strip_pp(text):
    """剥预处理行 + static/inline 修饰——与 preprocess_musl 同法（管线无头
    文件系统、trirv_cc 无 internal-linkage 语义，:15-17 同款剥离）。"""
    import re
    text = "\n".join(ln for ln in text.splitlines()
                     if not ln.lstrip().startswith("#"))
    text = re.sub(r"\binline\s+", "", text)
    text = re.sub(r"\bstatic\s+", "", text)
    return text


def compile_prog(main_src):
    arch_src = ""
    for h in ("syscall_tri.h", "atomic_tri.h"):
        with open(os.path.join(ARCH, h)) as f:
            arch_src += strip_pp(f.read()) + "\n"
    cf = tempfile.mktemp(suffix=".c")
    tf = tempfile.mktemp(suffix=".trv")
    with open(cf, "w") as f:
        f.write(arch_src + main_src)
    r = subprocess.run([CC, cf, tf], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("arch 拼接源编译失败: %s" % (r.stderr or r.stdout)[:300])
    data = open(tf, "rb").read()
    prog, mem, _packed = _trvio.load_bytes(data)
    return tf, prog, mem


def run_cvm(tf, stdin_bytes):
    r = subprocess.run([CVM, tf, "-"], capture_output=True, input=stdin_bytes)
    errs = [l for l in r.stderr.decode().split() if l.startswith("exit=")]
    return r.stdout, int(errs[0].split("=")[1]) if errs else -1


def run_pym(prog, mem, stdin_bytes):
    c = vm.VM(prog, mem_init=mem, stdin=stdin_bytes).run(max_steps=100000)
    return bytes(c.output), c.exit_code


def main():
    print("=== TriRV musl arch 层契约（read8/write8 + 原子纯 C）===")

    # ---- 1. read/write 路由：高位字节无损往返 + 计数返回 ----
    tf, prog, mem = compile_prog(
        "int main(void){ char b[1024]; int n = (int)__tri_read(0, b, 512); "
        "__tri_write(1, b, n); return n; }\n")
    payloads = [
        ("全256值遍历", bytes(range(256))),
        ("高位段", bytes([0xFF, 0xFE, 0xFD, 0xFC])),
        ("混合", bytes([0xE4, 0xB8, 0x89, 0x41, 0xFF])),
        ("ASCII", b"tri-arch-ok"),
    ]
    for name, payload in payloads:
        out, ec = run_cvm(tf, payload)
        check("路由往返/%s.CVM" % name, (out.hex(), ec), (payload.hex(), len(payload) & 0xFF))
        out, ec = run_pym(prog, mem, payload)
        check("路由往返/%s.PyVM" % name, (out.hex(), ec), (payload.hex(), len(payload) & 0xFF))

    # ---- 2. 原子纯 C 语义（oracle 逐项）----
    tf2, prog2, mem2 = compile_prog(
        "int main(void){ int p = 7; int f = 0; "
        "if (a_cas(&p, 7, 42) != 7) f|=1; if (p != 42) f|=2; "
        "if (a_swap(&p, 5) != 42) f|=4; if (p != 5) f|=8; "
        "if (a_fetch_add(&p, 10) != 5) f|=16; if (p != 15) f|=32; "
        "a_and(&p, 6); if (p != 6) f|=64; "
        "a_or(&p, 1); if (p != 7) f|=128; "
        "a_inc(&p); a_dec(&p); if (p != 7) f|=256; "
        "if (f) return f; return 22; }\n")
    for tag, runf, prog3, mem3 in (("CVM", run_cvm, (tf2, None, None), None),
                                   ("PyVM", run_pym, (None, prog2, mem2), None)):
        if tag == "CVM":
            _, ec = runf(tf2, b"")
        else:
            _, ec = runf(prog2, mem2, b"")
        check("原子纯C/%s" % tag, ec, 22)

    # ---- 3. a_ctz_64 + barrier ----
    tf3, prog3, mem3 = compile_prog(
        "int main(void){ if (a_ctz_64(54) != 3) return 21; /* 54=2000₃ */ "
        "if (a_ctz_64(1) != 0) return 23; a_barrier(); return 22; }\n")
    _, ec = run_cvm(tf3, b"")
    check("ctz/barrier.CVM", ec, 22)
    _, ec = run_pym(prog3, mem3, b"")
    check("ctz/barrier.PyVM", ec, 22)

    print("\narch 层契约：%d PASS / %d FAIL" % (passed, failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
