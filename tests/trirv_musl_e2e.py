#!/usr/bin/env python3
"""trirv_musl_e2e —— musl 风格 libc 层 + mini-busybox 端到端验证。

覆盖（全部 TRV_CC_STRICT=1，C 后端）：
  1. 语法扩展：typedef / const / do-while / 三目 / 复合赋值 / (cast)expr / (void) 形参
  2. musl string 层：memcpy/memset/memcmp/strcpy/strlcpy/strcat/strcmp/strncmp/strchr
  3. musl stdlib 层：abs/atoi
  4. mini-busybox：echo/cat/wc/head/dirname/basename/true/false + 分派器退出码 127
  5. C VM CLI：stdin 文件 / "-" 管道 / guest argv 三者共存
  6. PyVM 与 C VM 双跑一致
"""
import os
import subprocess
import sys
import tempfile

import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import _trvio
import trirv_vm as vm

HERE = os.path.dirname(os.path.abspath(__file__))
CVM = os.environ.get("TRIRV_VM_BIN", os.path.join(HERE, "..", "vm", "bin", "trirv_vm"))
CRT = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/cc/trirv_crt.c")).read()
MUSL = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/cc/trirv_musl.c")).read()
BB = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/cc/trirv_busybox.c")).read()

passed = 0
failed = 0


def check(name, got, want):
    global passed, failed
    ok = got == want
    if ok:
        passed += 1
        print(f"PASS {name}: {got!r}")
    else:
        failed += 1
        print(f"FAIL {name}: got={got!r} want={want!r}")


def compile_prog(src):
    cf = tempfile.mktemp(suffix=".c")
    tf = tempfile.mktemp(suffix=".trv")
    with open(cf, "w") as f:
        f.write(src)
    r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/tools/export_trv.py"), cf, tf],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"compile fail: {r.stderr.strip()[:200]}")
    data = open(tf, "rb").read()
    prog, mem, _packed = _trvio.load_bytes(data)
    os.unlink(cf)
    os.unlink(tf)
    return prog, mem


def run_py(src, argv=None, stdin=b"", max_steps=100000):
    prog, mem = compile_prog(src)
    c = vm.VM(prog, mem_init=mem, stdin=stdin, argv=argv or []).run(max_steps=max_steps)
    return c.exit_code, bytes(c.output)


# ================ 1. 语法扩展 ================
SYNTAX = [
    ("do-while", "int main(){ int i=0; do { i=i+1; } while(i<3); return i; }", 3),
    ("三目真", "int main(){ int x=3; return x>2 ? 10 : 20; }", 10),
    ("三目假", "int main(){ int x=1; return x>2 ? 10 : 20; }", 20),
    ("复合赋值", "int main(){ int i=0; i+=5; i*=2; i-=1; return i; }", 9),
    ("typedef 标量", "typedef int myint; int main(){ myint x=3; myint y=4; return x+y; }", 7),
    ("typedef 指针", "typedef char* str; int main(){ char b[4]; str p=b; p[0]=65; return b[0]; }", 65),
    ("const 形参", "int f(const char *s){ return s[0]; } int main(){ return f(\"Z\"); }",
     ord('Z')),
    ("(void) 形参", "int f(void){ return 5; } int main(){ return f(); }", 5),
    ("void* 传递", "void *id(void *p){ return p; } int main(){ int x=9; int*p=&x; "
     "return *(int*)id(p); }", 9),
    ("(char) 截断", "int main(){ char c=(char)300; return c; }", 300 % 243),
]
for name, src, want in SYNTAX:
    try:
        got, _ = run_py(src)
        check(f"语法/{name}", got, want)
    except Exception as e:  # noqa: BLE001
        check(f"语法/{name}", f"EXC:{str(e)[:80]}", want)

# ================ 2. musl string 层 ================
MUSL_SRC = CRT + "\n" + MUSL
STR_CASES = [
    ("strcmp 相等", MUSL_SRC + 'int main(){ return strcmp("abc","abc")==0 ? 1 : 0; }', 1),
    ("strcmp 小于", MUSL_SRC + 'int main(){ return strcmp("abc","abd")<0 ? 1 : 0; }', 1),
    ("strncmp", MUSL_SRC + 'int main(){ return strncmp("abcxx","abcy",3)==0 ? 1 : 0; }', 1),
    ("strcpy 往返", MUSL_SRC + 'int main(){ char d[8]; strcpy(d,"xy"); return d[1]; }',
     ord('y')),
    ("strlcpy 截断", MUSL_SRC + 'int main(){ char d[4]; int n=strlcpy(d,"abcdef",4); '
     'return n*10+d[3]; }', 6 * 10 + 0),
    ("strcat", MUSL_SRC + 'int main(){ char d[16]; strcpy(d,"ab"); strcat(d,"cd"); '
     'return strcmp(d,"abcd")==0 ? 1 : 0; }', 1),
    ("memcpy", MUSL_SRC + 'int main(){ char d[8]; memcpy(d,"xyz",4); return d[2]; }',
     ord('z')),
    ("memset", MUSL_SRC + 'int main(){ char d[4]; memset(d,65,3); return d[2]; }', 65),
    ("memcmp", MUSL_SRC + 'int main(){ return memcmp("ab","ab",2)==0 ? 1 : 0; }', 1),
    ("strchr 命中", MUSL_SRC + 'int main(){ char *p=strchr("hello",\'l\'); return p[0]; }',
     ord('l')),
    ("strchr 未命中", MUSL_SRC + 'int main(){ return strchr("hello",\'z\')==0 ? 1 : 0; }', 1),
    ("abs", MUSL_SRC + 'int main(){ return abs(-7)+abs(3); }', 10),
    ("atoi", MUSL_SRC + 'int main(){ return atoi("42")+atoi("-5"); }', 37),
    ("errno 地址", MUSL_SRC + 'int main(){ *__errno_location() = 3; return __triv_errno; }', 3),
]
for name, src, want in STR_CASES:
    try:
        got, _ = run_py(src)
        check(f"musl/{name}", got, want)
    except Exception as e:  # noqa: BLE001
        check(f"musl/{name}", f"EXC:{str(e)[:80]}", want)

# ================ 3. mini-busybox（PyVM） ================
BB_SRC = MUSL_SRC + "\n" + BB
BB_PY = [
    ("echo", ["prog", "echo", "hi", "tri"], b"", ("hi tri\n", 0)),
    ("true", ["prog", "true"], b"", ("", 0)),
    ("false", ["prog", "false"], b"", ("", 1)),
    ("未知 applet", ["prog", "foobar"], b"", ("applet not found: foobar\n", 127)),
    ("无参帮助", ["prog"], b"", ("mini-busybox (TriRV)\napps: echo cat wc head dirname basename true false\n", 1)),
    ("basename", ["prog", "basename", "a/b/c.txt"], b"", ("c.txt\n", 0)),
    ("dirname", ["prog", "dirname", "a/b/c.txt"], b"", ("a/b\n", 0)),
]
for name, gargv, stdin, (want_out, want_exit) in BB_PY:
    try:
        got, out = run_py(BB_SRC, argv=gargv, stdin=stdin)
        check(f"busybox/{name}.exit", got, want_exit)
        check(f"busybox/{name}.out", out.decode('latin1'), want_out)
    except Exception as e:  # noqa: BLE001
        check(f"busybox/{name}", f"EXC:{str(e)[:80]}", (want_out, want_exit))

# cat/wc 走 stdin（PyVM 管道输入）
got, out = run_py(BB_SRC, argv=["prog", "cat"], stdin=b"py cat")
check("busybox/cat.py", out.decode('latin1'), "py cat")
got, out = run_py(BB_SRC, argv=["prog", "wc"], stdin=b"12345")
check("busybox/wc.py", out.decode('latin1'), "5\n")

# ================ 4. C VM CLI ================
if os.path.exists(CVM):
    cf = tempfile.mktemp(suffix=".c")
    tf = tempfile.mktemp(suffix=".trv")
    with open(cf, "w") as f:
        f.write(BB_SRC)
    subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/tools/export_trv.py"), cf, tf],
                   check=True, capture_output=True)

    def cvm(*args, stdin=b""):
        r = subprocess.run([CVM, tf] + list(args), capture_output=True, input=stdin)
        errs = [l for l in r.stderr.decode().split() if l.startswith("exit=")]
        return (r.stdout.decode('latin1'),
                int(errs[0].split("=")[1]) if errs else -1)

    check("CVM/echo", cvm("--", "echo", "hello", "tri"),
          ("hello tri\n", 0))
    check("CVM/wc管道", cvm("-", "--", "wc", stdin=b"hello world"), ("11\n", 0))
    check("CVM/cat管道", cvm("-", "--", "cat", stdin=b"pipe"), ("pipe", 0))
    sf = tempfile.mktemp()
    with open(sf, "wb") as f:
        f.write(b"file cat\n")
    check("CVM/cat文件+argv", cvm(sf, "--", "cat"), ("file cat\n", 0))
    check("CVM/basename", cvm("--", "basename", "/usr/bin/ls"), ("ls\n", 0))
    check("CVM/dirname", cvm("--", "dirname", "/usr/bin/ls"), ("/usr/bin\n", 0))
    check("CVM/false", cvm("--", "false")[1], 1)

    # 双跑一致性：PyVM 与 C VM 同一用例
    prog, mem = compile_prog(BB_SRC)
    c = vm.VM(prog, mem_init=mem, argv=["prog", "echo", "dual"]).run(max_steps=100000)
    check("双跑/echo一致", (bytes(c.output).decode('latin1'), c.exit_code),
          cvm("--", "echo", "dual"))
    os.unlink(cf)
    os.unlink(tf)
else:
    print(f"[SKIP] C VM 不存在: {CVM}")

print(f"\nmusl libc + busybox 端到端：{passed} PASS / {failed} FAIL")
sys.exit(1 if failed else 0)
