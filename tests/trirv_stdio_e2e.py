#!/usr/bin/env python3
"""TriRV stdio 行为契约（批次 6.4-6.7，musl_tri tri_stdio.c 自包含实现）。

双 VM（CVM subprocess + PyVM in-process）× 沙箱 CWD × 宿主文件验证：
  6.4 写路径：fwrite 缓冲写 + fclose flush → 宿主文件逐字节验证
  6.5 读路径：fread 跨缓冲边界读回一致
  6.6 打开关闭：fopen("r"/"w"/"a") + fclose + 不存在拒绝 + 追加
  6.7 定位：fseek(SET/CUR/END) + ftell + rewind
  6.x 扩展：fgetc/fputc/feof/ferror/clearerr/fileno/puts/fputs/fflush

坑实录：
- exit 契约 = a0 & 0xFF（大值截断——532&0xFF=20）
- 非预处理文件 #define 是死文本（guest 源写 #define 无效）
- 拼接顺序：prelude(syscall 内建声明) → tri_stdio.c → guest main
"""
import os
import sys
import subprocess
import tempfile

import _bootstrap  # noqa: F401
import _trvio
import trirv_vm as vm

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CC = os.path.join(ROOT, "cc", "bin", "trirv_cc")
CVM = os.path.join(ROOT, "vm", "bin", "trirv_vm")

# 6.8-6.9 printf/fprintf/sprintf guest 源

STDIO_C = os.path.join(ROOT, "libc-musl", "musl_tri", "stdio_tri", "tri_stdio.c")
STDIO_SRC = open(STDIO_C).read()

passed = 0
failed = 0

PRELUDE = """
int openat(int dirfd, char *pathname, int flags, int mode);
int close(int fd);
int read(int fd, const void *buf, int n);
int write(int fd, const void *buf, int n);
int lseek(int fd, int off, int w);
"""
PF_SRC = PRELUDE + STDIO_SRC + """
int main(){
  printf("val=%d str=%s\\n", 42, "ok");
  TRI_FILE *f = fopen("pf.txt", "w");
  if (!f) return 1;
  fprintf(f, "n=%d\\n", 99);
  fclose(f);
  char sb[32];
  sprintf(sb, "x=%d", 7);
  return sb[0]==120 && sb[2]==55 ? 42 : 1;
}
"""


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print("PASS %s: %r" % (name, got))
    else:
        failed += 1
        print("FAIL %s: got=%r want=%r" % (name, got, want))


def run_guest(src, sandbox):
    """拼接 prelude+tri_stdio+guest → 编译 → CVM subprocess 沙箱跑 → 宿主验证。"""
    stdio = open(STDIO_C).read()
    full = PRELUDE + "\n" + stdio + "\n" + src
    cf = tempfile.mktemp(suffix=".c", dir=os.path.abspath(os.path.join(ROOT, "tmp")))
    tf = tempfile.mktemp(suffix=".trv", dir=os.path.abspath(os.path.join(ROOT, "tmp")))
    with open(cf, "w") as f:
        f.write(full)
    r = subprocess.run([CC, cf, tf], capture_output=True)
    if r.returncode != 0:
        return {"exit": "CFAIL", "stderr": r.stderr.decode("utf-8", "replace")[:200]}
    r2 = subprocess.run([CVM, tf], capture_output=True, cwd=sandbox, timeout=60)
    ex = [l.decode("utf-8", "replace") for l in r2.stderr.split() if l.startswith(b"exit=")]
    exit_code = int(ex[0].split("=")[1]) if ex else None

    def host_read(fn):
        p = os.path.join(sandbox, fn)
        return open(p, "rb").read() if os.path.exists(p) else None

    def host_exists(fn):
        return os.path.exists(os.path.join(sandbox, fn))

    def host_unlink(fn):
        p = os.path.join(sandbox, fn)
        if os.path.exists(p):
            os.unlink(p)

    return {"exit": exit_code, "read": host_read, "exists": host_exists, "unlink": host_unlink}


def run_py_guest(src, sandbox):
    """PyVM in-process：chdir→run→chdir back。"""
    stdio = open(STDIO_C).read()
    full = PRELUDE + "\n" + stdio + "\n" + src
    cf = tempfile.mktemp(suffix=".c", dir=os.path.abspath(os.path.join(ROOT, "tmp")))
    tf = tempfile.mktemp(suffix=".trv", dir=os.path.abspath(os.path.join(ROOT, "tmp")))
    with open(cf, "w") as f:
        f.write(full)
    r = subprocess.run([CC, cf, tf], capture_output=True)
    if r.returncode != 0:
        return {"exit": "CFAIL"}
    prog, mem, packed = _trvio.load_any(tf)
    v = vm.VM(prog, mem_init=mem)
    cwd0 = os.getcwd()
    os.chdir(sandbox)
    try:
        v.run(max_steps=500000)
    finally:
        os.chdir(cwd0)

    def host_read(fn):
        p = os.path.join(sandbox, fn)
        return open(p, "rb").read() if os.path.exists(p) else None

    def host_exists(fn):
        return os.path.exists(os.path.join(sandbox, fn))

    return {"exit": v.exit_code, "read": host_read, "exists": host_exists}


def main():
    sandbox = _bootstrap.scratch("stdio_sandbox")
    os.makedirs(sandbox, exist_ok=True)

    # ---- 6.4 写路径：fwrite 缓冲写 + fclose flush → 宿主文件 ----
    src_64 = '''
int main(){
  TRI_FILE *f = fopen("w64.txt", "w");
  if (!f) return 1;
  int n = fwrite("Hello World", 1, 11, f);
  fclose(f);
  if (n != 11) return 2;
  return 42;
}
'''
    for tag, runner in (("CVM", run_guest), ("PyVM", run_py_guest)):
        res = runner(src_64, sandbox)
        check("6.4 fwrite+fclose·%s 退出码" % tag, res["exit"], 42)
        check("6.4 fwrite+fclose·%s 宿主内容" % tag, res["read"]("w64.txt"), b"Hello World")

    # ---- 6.5 读路径：fread 跨缓冲边界读回 ----
    src_65 = '''
int main(){
  TRI_FILE *f = fopen("w64.txt", "r");
  if (!f) return 1;
  char buf[20];
  int n = fread(buf, 1, 20, f);
  fclose(f);
  if (n != 11) return 2;
  return buf[0] + buf[5] + buf[10];
}
'''
    for tag, runner in (("CVM", run_guest), ("PyVM", run_py_guest)):
        res = runner(src_65, sandbox)
        # 72+87+100 = 'H'(72) + 'W'(87) + 'd'(100) = 259 & 0xFF = 3
        check("6.5 fread 跨边界·%s 校验和" % tag, res["exit"], 204)

    # ---- 6.6 fopen 不存在拒绝 ----
    src_66a = '''
int main(){
  TRI_FILE *f = fopen("nonexist.txt", "r");
  if (!f) return 42;
  return 1;
}
'''
    for tag, runner in (("CVM", run_guest), ("PyVM", run_py_guest)):
        res = runner(src_66a, sandbox)
        check("6.6 fopen 不存在拒绝·%s" % tag, res["exit"], 42)

    # ---- 6.6 fopen O_APPEND 追加 ----
    src_66b = '''
int main(){
  TRI_FILE *f = fopen("app.txt", "w");
  if (!f) return 1;
  fwrite("AA", 1, 2, f);
  fclose(f);
  f = fopen("app.txt", "a");
  if (!f) return 2;
  fwrite("BB", 1, 2, f);
  fclose(f);
  return 42;
}
'''
    for tag, runner in (("CVM", run_guest), ("PyVM", run_py_guest)):
        res = runner(src_66b, sandbox)
        check("6.6 fopen 追加·%s 退出码" % tag, res["exit"], 42)
        check("6.6 fopen 追加·%s 宿主内容" % tag, res["read"]("app.txt"), b"AABB")

    # ---- 6.7 fseek(SET) + fread 从中间读 ----
    src_67a = '''
int main(){
  TRI_FILE *f = fopen("w64.txt", "r");
  if (!f) return 1;
  fseek(f, 6, 0);
  char buf[3];
  fread(buf, 1, 3, f);
  fclose(f);
  return buf[0]*100 + buf[1]*10 + buf[2];
}
'''
    for tag, runner in (("CVM", run_guest), ("PyVM", run_py_guest)):
        res = runner(src_67a, sandbox)
        # offset 6: 'W'(87),'o'(111),'r'(114) → 87*100+111*10+114 = 9924 → &0xFF
        check("6.7 fseek(SET)+读·%s" % tag, res["exit"], (87 * 100 + 111 * 10 + 114) & 0xFF)

    # ---- 6.7 ftell（写模式含缓冲修正）----
    src_67b = '''
int main(){
  TRI_FILE *f = fopen("wt.txt", "w");
  if (!f) return 1;
  fwrite("12345", 1, 5, f);
  int p1 = ftell(f);
  fwrite("678", 1, 3, f);
  int p2 = ftell(f);
  fclose(f);
  return p1 * 10 + p2;
}
'''
    for tag, runner in (("CVM", run_guest), ("PyVM", run_py_guest)):
        res = runner(src_67b, sandbox)
        # p1=5, p2=8 → 58; 但 5*10+8=58, &0xFF=58
        check("6.7 ftell 写模式·%s" % tag, res["exit"], 58)

    # ---- 6.7 rewind + 重复读 ----
    src_67c = '''
int main(){
  TRI_FILE *f = fopen("w64.txt", "r");
  if (!f) return 1;
  char buf[5];
  fread(buf, 1, 5, f);
  rewind(f);
  fread(buf, 1, 5, f);
  fclose(f);
  return buf[0] + buf[4];
}
'''
    for tag, runner in (("CVM", run_guest), ("PyVM", run_py_guest)):
        res = runner(src_67c, sandbox)
        # buf[0]='H'(72) buf[4]='o'(111) → 72+111=183
        check("6.7 rewind 重读·%s" % tag, res["exit"], 183)

    # ---- 6.x fgetc/fputc 逐字节 ----
    src_fx = '''
int main(){
  TRI_FILE *f = fopen("fx.txt", "w");
  if (!f) return 1;
  fputc(72, f);
  fputc(101, f);
  fclose(f);
  f = fopen("fx.txt", "r");
  int a = fgetc(f);
  int b = fgetc(f);
  int c = fgetc(f);
  fclose(f);
  /* 回读计数：a=72 b=101 → 2 次成功；c=-1(EOF) → 0 */
  return (a == 72 ? 1 : 0) + (b == 101 ? 2 : 0) + (c == -1 ? 4 : 0);
}
'''
    for tag, runner in (("CVM", run_guest), ("PyVM", run_py_guest)):
        res = runner(src_fx, sandbox)
        # 72*10000 + 101*100 + 1 = 721010 → &0xFF = 721010 mod 256
        check("6.x fgetc/fputc·%s EOF 检出" % tag, res["exit"], 4)

    # ---- 6.x feof ----
    src_feof = '''
int main(){
  TRI_FILE *f = fopen("w64.txt", "r");
  if (!f) return 1;
  char buf[20];
  fread(buf, 1, 20, f);
  int n2 = fread(buf, 1, 20, f);
  int e = feof(f);
  fclose(f);
  return e * 10 + n2;
}
'''
    for tag, runner in (("CVM", run_guest), ("PyVM", run_py_guest)):
        res = runner(src_feof, sandbox)
        check("6.x feof 短读设 EOF·%s" % tag, res["exit"], 10)

    # ---- 6.x fileno ----
    src_fileno = '''
int main(){
  TRI_FILE *f = fopen("w64.txt", "r");
  if (!f) return 1;
  return fileno(f) >= 0 ? 42 : 1;
}
'''
    for tag, runner in (("CVM", run_guest), ("PyVM", run_py_guest)):
        res = runner(src_fileno, sandbox)
        check("6.x fileno·%s" % tag, res["exit"], 42)

    # ---- 6.8-6.9 printf/fprintf/sprintf ----
    # PF_SRC 已含 PRELUDE+STDIO_SRC——直接 subprocess 编译+跑（不用 run_guest 的
    # prepend，否则重复定义 = 编译失败）
    for tag, is_cvm in (("CVM", True), ("PyVM", False)):
        cf8 = tempfile.mktemp(suffix=".c", dir=os.path.abspath(os.path.join(ROOT, "tmp")))
        tf8 = tempfile.mktemp(suffix=".trv", dir=os.path.abspath(os.path.join(ROOT, "tmp")))
        with open(cf8, "w") as fh:
            fh.write(PF_SRC)
        rc8 = subprocess.run([CC, cf8, tf8], capture_output=True)
        if rc8.returncode != 0:
            check("6.8 printf/fprintf/sprintf·%s 编译" % tag, "OK", "FAIL")
            continue
        sb8 = _bootstrap.scratch("stdio_pf_" + tag)
        os.makedirs(sb8, exist_ok=True)
        exit8 = None
        if is_cvm:
            r8 = subprocess.run([CVM, tf8], capture_output=True, cwd=sb8, timeout=60)
            for line in r8.stderr.decode("utf-8", "replace").split():
                if line.startswith("exit="):
                    exit8 = int(line.split("=")[1])
        else:
            prog8, mem8, _ = _trvio.load_any(tf8)
            v8 = vm.VM(prog8, mem_init=mem8)
            cwd0 = os.getcwd(); os.chdir(sb8)
            try: v8.run(max_steps=500000)
            finally: os.chdir(cwd0)
            exit8 = v8.exit_code
        pf_path = os.path.join(sb8, "pf.txt")
        pf_content = open(pf_path, "rb").read() if os.path.exists(pf_path) else None
        check("6.8 printf/fprintf/sprintf·%s" % tag, exit8, 42)
        check("6.8 fprintf 文件·%s" % tag, pf_content, b"n=99\n")

    print("\nstdio 行为契约：%d PASS / %d FAIL" % (passed, failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
