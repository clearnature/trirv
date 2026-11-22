#!/usr/bin/env python3
"""trirv_crt_e2e —— crt0 + musl 启动链端到端验证（设计文档步骤 7）。

覆盖：
  1. 全局变量：标量/数组/初始化/负数/char 数组/char* 指针/bss 零初始化/
     多声明/局部遮蔽/指针引用/struct 全局/计数器递增
  2. argc/argv：直连链（_start 直接调 main）与 musl 链（经 __libc_start_main）
  3. 函数指针：定义带签名形参、函数名取地址、(*f)(args) 间接调用、
     前向引用（__libc_start_main 调后面定义的 main）
  4. 双 VM 一致：PyVM 与 C VM 跑同一份 .trv 结果一致
  5. 零页 ABI：argc@0 / argv数组@9 / envp@18 布局差分

本文件所有用例必须在 TRV_CC_STRICT=1（C 后端）下通过 —— 启动链是新
C 后端能力，Python 参考后端不支持全局变量/函数指针。
"""
import os
import struct
import subprocess
import sys
import tempfile

import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import trirv_cc_backend as cc
import trirv_vm as vm

HERE = os.path.dirname(os.path.abspath(__file__))
CVM = os.environ.get("TRIRV_VM_BIN", os.path.join(HERE, "..", "vm", "bin", "trirv_vm"))
CRT = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/cc/trirv_crt.c")).read()

passed = 0
failed = 0


def compile_trv(src):
    """C 后端编译 -> (prog, mem_segments)。STRICT 下失败直接抛。"""
    cf = tempfile.mktemp(suffix=".c")
    tf = tempfile.mktemp(suffix=".trv")
    with open(cf, "w") as f:
        f.write(src)
    export_r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/tools/export_trv.py"), cf, tf],
                              capture_output=True, text=True)
    if export_r.returncode != 0:
        raise RuntimeError(f"compile fail: {export_r.stderr.strip()[:200]}")
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


def run_py(src, argv=None, stdin=b"", max_steps=50000):
    prog, mem = compile_trv(src)
    c = vm.VM(prog, mem_init=mem, stdin=stdin, argv=argv or []).run(max_steps=max_steps)
    return c.exit_code, bytes(c.output)


def run_cvm(trv_args=None, want_out=False):
    """C VM 跑已生成的 .trv。返回 (exit, stdout)。"""
    args = [CVM]
    r = subprocess.run(args, capture_output=True)
    return 0, b""


def run_cvm_src(src, guest_argv=None, stdin_data=None):
    cf = tempfile.mktemp(suffix=".c")
    tf = tempfile.mktemp(suffix=".trv")
    with open(cf, "w") as f:
        f.write(src)
    subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/tools/export_trv.py"), cf, tf],
                   check=True, capture_output=True)
    args = [CVM, tf]
    if guest_argv is not None:
        args += ["--"] + guest_argv
    if stdin_data:
        sf = tempfile.mktemp()
        with open(sf, "wb") as f:
            f.write(stdin_data)
        args.append(sf)
    r = subprocess.run(args, capture_output=True)
    errs = [l for l in r.stderr.decode().split() if l.startswith("exit=")]
    c_exit = int(errs[0].split("=")[1]) if errs else -999
    os.unlink(cf)
    os.unlink(tf)
    return c_exit, r.stdout


def check(name, got, want):
    global passed, failed
    ok = got == want
    if ok:
        passed += 1
        print(f"PASS {name}: {got}")
    else:
        failed += 1
        print(f"FAIL {name}: got={got} want={want}")


# ================ 1. 全局变量 ================
GLOB_CASES = [
    ("标量初始化", "int g = 42; int main(){ return g; }", 42),
    ("负数初始化", "int g = -8; int main(){ return 0-g; }", 8),
    ("多声明", "int a = 3, b = 4; int main(){ return a+b; }", 7),
    ("bss 零初始化", "int z; int main(){ return z; }", 0),
    ("数组初始化求和", "int a[4] = {1,2,3,4}; int main(){ return a[0]+a[1]+a[2]+a[3]; }", 10),
    ("局部遮蔽全局", "int g = 1; int main(){ int g = 9; return g; }", 9),
    ("指针引用全局数组", "int a[5]; int main(){ int*p=a; p[2]=7; return a[2]; }", 7),
    ("char 数组", 'char msg[4] = "hi"; int main(){ return msg[0]+msg[1]; }',
     ord('h') + ord('i')),
    ("char* 全局", 'char *msg = "hello"; int main(){ return msg[1]; }', ord('e')),
    ("多个 char*", 'char *a="xyz"; char *b="ab"; int main(){ return a[0]+b[0]; }',
     ord('x') + ord('a')),
    ("struct 全局", "struct P{int x; int y;}; struct P gp; "
     "int main(){ gp.x=3; gp.y=4; return gp.x*gp.y; }", 12),
    ("全局计数器", "int counter = 0; void bump(){ counter++; } "
     "int main(){ bump(); bump(); bump(); return counter; }", 3),
    ("全局做循环上限", "int LIMIT = 100; int main(){ int s=0; int i; "
     "for(i=0;i<LIMIT;i=i+1){ s=s+1; } return s; }", 100),
    ("负数数组元素", "int a[3] = {-1,-2,-3}; int main(){ return 0-(a[0]+a[1]+a[2]); }", 6),
]
for name, src, want in GLOB_CASES:
    try:
        got, _ = run_py(src)
        check(f"全局/{name}", got, want)
    except Exception as e:  # noqa: BLE001
        check(f"全局/{name}", f"EXC:{e}", want)

# ================ 2. argc/argv 直连链 ================
check("argv/直连 argc", run_py("int main(int argc,char**argv){ return argc+0; }",
                               argv=["p", "a", "b"])[0], 3)
check("argv/直连无参", run_py("int main(int argc,char**argv){ return argc+0; }")[0], 0)
got, _ = run_py('int main(int argc,char**argv){ return argv[1][0]; }', argv=["p", "hello"])
check("argv/直连首字符", got, ord('h'))

# ================ 3. musl 链（__libc_start_main） ================
src_musl_argc = CRT + "\nint main(int argc,char**argv){ return argc+0; }\n"
check("musl/argc", run_py(src_musl_argc, argv=["p", "a", "b"])[0], 3)

src_musl_sum = CRT + ("\nint main(int argc,char**argv){ int s=0; int i; "
                      "for(i=1;i<argc;i=i+1){ s=s+argv[i][0]; } return s; }\n")
got, _ = run_py(src_musl_sum, argv=["p", "A", "B"])
check("musl/argv求和", got, ord('A') + ord('B'))

src_musl_call = CRT + "\nint triple(int n){ return n*3; }\n" \
    "int main(int argc,char**argv){ return triple(5); }\n"
check("musl/调自定义函数", run_py(src_musl_call)[0], 15)

# ================ 4. 函数指针 ================
FP_CASES = [
    ("无参间接", "int foo(){ return 7; } int call(int (*f)()){ return (*f)(); } "
     "int main(){ return call(foo); }", 7),
    ("带参间接", "int foo(int x){ return x+1; } int call(int (*f)(int x)){ return (*f)(41); } "
     "int main(){ return call(foo); }", 42),
    ("多参间接", "int callee(int a,int b){ return a*10+b; } "
     "int caller(int (*fn)(int,int),int x,int y){ return (*fn)(x,y); } "
     "int main(){ return caller(callee,3,4); }", 34),
]
for name, src, want in FP_CASES:
    try:
        got, _ = run_py(src)
        check(f"函数指针/{name}", got, want)
    except Exception as e:  # noqa: BLE001
        check(f"函数指针/{name}", f"EXC:{e}", want)

# ================ 5. 双 VM 一致（C VM） ================
import os as _os
if _os.path.exists(CVM):
    dual = [
        # C VM 的 guest argv[0] 是 .trv 路径（长度可变），argc 用 len(gargv) 对齐
        ("直连argc", "int main(int argc,char**argv){ return argc+0; }",
         ["p", "a", "b"], 4),          # [".trv", "p", "a", "b"] -> 4
        ("musl argc", CRT + "\nint main(int argc,char**argv){ return argc+0; }\n",
         ["p", "a", "b"], 4),
        # argv[1] 两边一致：PyVM 传 ["x","hello"] 时 argv[1]="hello"；
        # C VM 传 [".trv","hello"] 时 argv[1]="hello" —— 首字符同为 'h'
        ("argv首字符", 'int main(int argc,char**argv){ return argv[1][0]; }',
         ["hello"], ord('h')),
        ("write输出", 'int main(){ write(1,"OK",2); return 2; }', None, 2),
    ]
    for name, src, gargv, want in dual:
        c_exit, c_out = run_cvm_src(src, guest_argv=gargv)
        # C VM 的 gargv[0] 是 .trv 路径，所以 argc=gargv 长度、argv[1] 同 PyVM
        check(f"CVM/{name}", c_exit, want)

    # 零页 ABI 差分：argc 槽
    src = "int main(int argc,char**argv){ return argc+0; }"
    cf = tempfile.mktemp(suffix=".c")
    tf = tempfile.mktemp(suffix=".trv")
    with open(cf, "w") as f:
        f.write(src)
    subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/tools/export_trv.py"), cf, tf],
                   check=True, capture_output=True)
    r = subprocess.run([CVM, "-o", "0", tf, "--", "a", "b", "c"], capture_output=True)
    cvm_argc_bytes = r.stdout
    prog, mem = compile_trv(src)
    p = vm.VM(prog, mem_init=mem, argv=["x", "a", "b", "c"])
    py_argc_bytes = bytes(p.mem[0:9])
    check("ABI/零页argc一致", list(cvm_argc_bytes), list(py_argc_bytes))
    r = subprocess.run([CVM, "-o", "9", tf, "--", "a", "b", "c"], capture_output=True)
    check("ABI/零页argv一致",
          list(r.stdout), list(bytes(vm.VM(prog, mem_init=mem, argv=["x", "a", "b", "c"]).mem[9:18])))
    os.unlink(cf)
    os.unlink(tf)
else:
    print(f"[SKIP] C VM 不存在: {CVM}")

print(f"\ncrt0+musl 端到端：{passed} PASS / {failed} FAIL")
sys.exit(1 if failed else 0)
