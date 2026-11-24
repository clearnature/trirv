#!/usr/bin/env python3
"""trirv_tcc_syscall_e2e —— tri-tcc 管线 syscall 对接面（tri-link __tri_syscall）。

上游 musl arch 层（arch/riscv64/syscall_arch.h）用内联汇编 `ecall` 实现
__syscall；TriRV 无内联汇编（tri-tcc spike）。tri-link 前置 __tri_syscall
代码块（仿 _start 前置）：

    __tri_syscall(n, a0..a5):   n→a7(x17)，a1..a6 整体下移一档到 a0..a5，
                                ECALL（返回值写回 a0/x10），JALR zero,ra,0

约定与 PyVM/CVM 一致：syscall 号在 x17，参数 x10-x15，返回 x10。
arch_tri/syscall_tri.h 的 read/write→read8/write8 路由由此获得 tri-tcc 路径。
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
CVM = os.environ.get("TRIRV_VM_BIN", os.path.join(ROOT, "vm", "bin", "trirv_vm"))
TCC = os.path.join(ROOT, "tinycc", "trirv-tcc")

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


SRC = r"""
long __tri_syscall(long n, long a0, long a1, long a2, long a3, long a4, long a5);

int main(void)
{
    char b[512];
    long n = __tri_syscall(5000, 0, (long)b, 512, 0, 0, 0);   /* read8(0, b, 512) */
    __tri_syscall(5001, 1, (long)b, n, 0, 0, 0);              /* write8(1, b, n) */
    return (int)n;
}
"""


def compile_tcc():
    cf = tempfile.mktemp(suffix=".c")
    tf = tempfile.mktemp(suffix=".trv")
    with open(cf, "w") as f:
        f.write(SRC)
    r = subprocess.run([TCC, "-nostdlib", cf, "-o", tf],
                       capture_output=True, text=True, timeout=60)
    return r, tf


def load_trv(path):
    data = open(path, "rb").read()
    prog, mem, _packed = _trvio.load_bytes(data)
    return prog, mem


def main():
    print("=== tri-tcc syscall 对接面（__tri_syscall 前置块）===")

    r, tf = compile_tcc()
    check("tri-tcc 编译链接", r.returncode, 0)
    if r.returncode != 0:
        print("  stderr: %s" % (r.stderr or "")[:200])
        print("\nsyscall 契约：%d PASS / %d FAIL" % (passed, failed))
        sys.exit(1)

    prog, mem = load_trv(tf)

    payloads = [
        ("ASCII", b"tcc-syscall-ok"),
        ("高位字节", bytes([0xFF, 0x80, 0x7F, 0x00, 0x41])),
        ("空输入", b""),
    ]
    for name, payload in payloads:
        want_ec = len(payload) & 0xFF
        c = vm.VM(prog, mem_init=mem, stdin=payload).run(max_steps=100000)
        check("PyVM/%s" % name, (bytes(c.output), c.exit_code),
              (payload, want_ec))
        r2 = subprocess.run([CVM, tf, "-"], capture_output=True, input=payload)
        errs = [l for l in r2.stderr.decode().split() if l.startswith("exit=")]
        ec = int(errs[0].split("=")[1]) if errs else -1
        check("CVM/%s" % name, (r2.stdout, ec), (payload, want_ec))

    print("\nsyscall 契约：%d PASS / %d FAIL" % (passed, failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
