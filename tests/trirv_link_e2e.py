#!/usr/bin/env python3
"""trirv_link_e2e —— 多文件链接（mini linker）端到端验证。

覆盖（全部 TRV_CC_STRICT=1，C 后端）：
  1. 跨文件调用：库函数 + 主文件 main，先原型后定义
  2. 多 -l 顺序无关：库文件在主文件前后均可
  3. 重复定义检测：同一函数在两个文件定义 -> link error 退出码 1
  4. 原型声明：type name(参数表); 只签名无体，不参与代码生成
  5. 正式分层：trirv_busybox.c -l trirv_musl.c -l trirv_crt.c
     （替代 Python 文本拼接，这就是真 musl 链接路径的最小语义）
  6. 拼接边界：文件末尾无换行时自动补（避免 token 粘连）
  7. 双 VM 一致
"""
import os
import struct
import subprocess
import sys
import tempfile

import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import trirv_vm as vm

HERE = os.path.dirname(os.path.abspath(__file__))
CVM = os.environ.get("TRIRV_VM_BIN", os.path.join(HERE, "..", "vm", "bin", "trirv_vm"))
CC = os.path.join(HERE, "..", "cc", "bin", "trirv_cc")

passed = 0
failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"PASS {name}: {got!r}")
    else:
        failed += 1
        print(f"FAIL {name}: got={got!r} want={want!r}")


def link_and_load(main_src, libs=(), expect_rc=0):
    """-l 链接 -> (prog, mem)。编译失败返回 None。"""
    cf = tempfile.mktemp(suffix=".c")
    tf = tempfile.mktemp(suffix=".trv")
    with open(cf, "w") as f:
        f.write(main_src)
    args = [CC, cf, tf]
    for lib in libs:
        args += ["-l", lib]
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != expect_rc:
        return None
    if expect_rc != 0:
        return ("LINK_ERR", r.stderr)
    data = open(tf, "rb").read()
    n_inst, n_seg = struct.unpack_from("<II", data, 8)
    prog = list(struct.unpack_from(f"<{n_inst}I", data, 16))
    off = 16 + 4 * n_inst
    mem = {}
    for _ in range(n_seg):
        addr, ln = struct.unpack_from("<II", data, off)
        off += 8
        mem[addr] = data[off:off + ln]
        off += ln
    os.unlink(cf)
    os.unlink(tf)
    return prog, mem


def run_py(prog, mem, argv=None, stdin=b"", max_steps=100000):
    c = vm.VM(prog, mem_init=mem, stdin=stdin, argv=argv or []).run(max_steps=max_steps)
    return c.exit_code, bytes(c.output)


# ================ 1. 跨文件调用 ================
lib1 = tempfile.mktemp(suffix=".c")
with open(lib1, "w") as f:
    f.write('int lib_len(const char *s) { int n = 0; while (s[n] != 0) n = n + 1; return n; }\n')

main1 = tempfile.mktemp(suffix=".c")
with open(main1, "w") as f:
    f.write('int lib_len(const char *s);\n'
            'int helper(int x);\n'
            'int main() { return lib_len("abcd") + helper(10); }\n'
            'int helper(int x) { return x * 2; }\n')

r = link_and_load(open(main1).read(), [lib1])
check("链接/跨文件调用编译", r is not None and not isinstance(r[0], str), True)
got, _ = run_py(*r)
check("链接/跨文件调用执行", got, 24)

# ================ 2. 多 -l 与顺序无关 ================
lib2 = tempfile.mktemp(suffix=".c")
with open(lib2, "w") as f:
    f.write('int lib_twice(int x) { return x * 2; }\n')
main2 = tempfile.mktemp(suffix=".c")
with open(main2, "w") as f:
    f.write('int lib_len(const char *s);\n'
            'int lib_twice(int x);\n'
            'int main() { return lib_len("ab") + lib_twice(5); }\n')
r = link_and_load(open(main2).read(), [lib1, lib2])
got, _ = run_py(*r)
check("链接/双库按序", got, 2 + 10)
r = link_and_load(open(main2).read(), [lib2, lib1])
got, _ = run_py(*r)
check("链接/双库乱序", got, 12)

# 库当第一个位置参数（主文件角色互换）也按拼接顺序工作
r = link_and_load(open(lib1).read() +
                  'int main() { return lib_len("xyz"); }\n', [])
got, _ = run_py(*r)
check("链接/单文件多段", got, 3)

# ================ 3. 重复定义检测 ================
dup1 = tempfile.mktemp(suffix=".c")
with open(dup1, "w") as f:
    f.write('int dup() { return 1; }\n')
dup2 = tempfile.mktemp(suffix=".c")
with open(dup2, "w") as f:
    f.write('int dup() { return 2; }\nint main() { return dup(); }\n')
r = link_and_load(open(dup2).read(), [dup1], expect_rc=1)
ok = isinstance(r[0], str) and "duplicate function 'dup'" in r[1]
check("链接/重复定义报错", ok, True)

# 单文件内部重复也拦
r = link_and_load('int f(){ return 1; }\nint f(){ return 2; }\n'
                  'int main(){ return f(); }\n', [], expect_rc=1)
ok = isinstance(r[0], str) and "duplicate function 'f'" in r[1]
check("链接/单文件重复", ok, True)

# ================ 4. 原型声明不产码 ================
r = link_and_load('int foo(int x);\n'
                  'int main(){ return foo(4); }\n'
                  'int foo(int x){ return x+1; }\n', [])
got, _ = run_py(*r)
check("链接/原型+定义共存", got, 5)

# ================ 5. 正式分层：busybox = musl + crt ================
MUSL_P = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/cc/trirv_musl.c")
CRT_P = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/cc/trirv_crt.c")
BB_P = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/cc/trirv_busybox.c")
r = link_and_load(open(BB_P).read(), [MUSL_P, CRT_P])
check("链接/busybox 分层编译", r is not None and not isinstance(r[0], str), True)
prog, mem = r

got, out = run_py(prog, mem, argv=["bb", "echo", "linked", "layer"])
check("链接/busybox echo", (out.decode('latin1'), got), ("linked layer\n", 0))
got, out = run_py(prog, mem, argv=["bb", "basename", "/usr/bin/env"])
check("链接/busybox basename", (out.decode('latin1'), got), ("env\n", 0))
got, out = run_py(prog, mem, argv=["bb", "wc"], stdin=b"layered")
check("链接/busybox wc", out.decode('latin1'), "7\n")

# ================ 6. 拼接边界（末尾无换行） ================
nolib_nl = tempfile.mktemp(suffix=".c")
with open(nolib_nl, "w") as f:
    f.write('int t(){ return 8; }')          # 故意无结尾换行
main_nl = tempfile.mktemp(suffix=".c")
with open(main_nl, "w") as f:
    f.write('int t(void);\nint main(){ return t(); }')   # 也无换行
r = link_and_load(open(main_nl).read(), [nolib_nl])
got, _ = run_py(*r)
check("链接/无换行边界", got, 8)

# ================ 7. C VM 跑分层产物 ================
if os.path.exists(CVM):
    cf = tempfile.mktemp(suffix=".c")
    tf = tempfile.mktemp(suffix=".trv")
    with open(cf, "w") as f:
        f.write(open(BB_P).read())
    subprocess.run([CC, cf, tf, "-l", MUSL_P, "-l", CRT_P],
                   check=True, capture_output=True)
    r2 = subprocess.run([CVM, tf, "--", "echo", "cvm", "layer"],
                        capture_output=True)
    errs = [l for l in r2.stderr.decode().split() if l.startswith("exit=")]
    check("CVM/分层链接运行",
          (r2.stdout.decode('latin1'), int(errs[0].split("=")[1])),
          ("cvm layer\n", 0))
    os.unlink(cf)
    os.unlink(tf)

print(f"\n多文件链接端到端：{passed} PASS / {failed} FAIL")
sys.exit(1 if failed else 0)
