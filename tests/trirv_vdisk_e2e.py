#!/usr/bin/env python3
"""TriRV 虚拟磁盘契约（批次 6.3b，ADR-39/6.1 拍板：宿主沙箱目录透传）。

双 VM 同构（PyVM/CVM 逐语义镜像）：
  - openat(dirfd, path, flags, mode)：dirfd 忽略（AT_FDCWD 口径）；
    沙箱=VM 进程 CWD；绝对路径与 ".." 穿越诚实拒绝（-2=ENOENT）
  - read/write fd≥3 宿主直通（A5 口径 count<0 → -EINVAL）；
    fd 0/1/2 仍归 stdin/stdout/stderr（既有契约不变）
  - lseek fd≥3 宿主直通；close fd≥3 宿主 close
  - unlink/rename 家族（at 变体 dirfd 忽略；UNLINKAT AT_REMOVEDIR→rmdir）
  - 旗标口径：guest 用 Linux 数值（preprocess 折算），宿主翻译
    （macOS O_CREAT=512≠Linux 64——host_open_flags 按位重组而非直通）

坑实录：PyVM in-process 沙箱=os.chdir（try/finally 回原 CWD）；
CVM=subprocess cwd=沙箱目录——两 VM 的 CWD 口径必须一致（都是沙箱根）。
"""
import os
import sys
import subprocess

import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path；草稿经 scratch()）
import _trvio
import trirv_vm as vm

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CC = os.path.join(ROOT, "cc", "bin", "trirv_cc")
CVM = os.path.join(ROOT, "vm", "bin", "trirv_vm")

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


def compile_guest(src, name):
    cf = _bootstrap.scratch("vd_%s.c" % name)
    tf = _bootstrap.scratch("vd_%s.trv" % name)
    with open(cf, "w") as f:
        f.write(src)
    r = subprocess.run([CC, cf, tf], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("vd_%s 编译失败: %s" % (name, (r.stderr or "")[:200]))
    return tf


def run_cvm(tf, sandbox):
    r = subprocess.run([CVM, tf, "-"], capture_output=True, text=True,
                       cwd=sandbox, timeout=60)
    errs = [l for l in r.stderr.split() if l.startswith("exit=")]
    return int(errs[0].split("=")[1]) if errs else None


def run_py(tf, sandbox):
    prog, mem, packed = _trvio.load_any(tf)
    v = vm.VM(prog, mem_init=mem)
    if packed:
        v.packed = True
        v.imem_offs = __import__("trirv_isa").stream_offsets(
            __import__("trirv_isa").pack_stream(prog))
    cwd0 = os.getcwd()
    os.chdir(sandbox)
    try:
        v.run(max_steps=100000)
    finally:
        os.chdir(cwd0)
    return v.exit_code


# ---- guest 程序（syscall 号：openat=56 close=57 read=63 write=64 exit=93;
#      O_CREAT=64|O_WRONLY=1|O_TRUNC=512 → 577; O_RDONLY=0）----
G_CREATE_WRITE = """
int openat(int a, char *p, int fl, int m);
int write(int fd, char *b, int n);
int close(int fd);
int main(){
  int fd = openat(0, "vd.txt", 577, 0);
  if (fd < 0) return 1;
  char buf[5]; buf[0]=104; buf[1]=101; buf[2]=108; buf[3]=108; buf[4]=111;
  int n = write(fd, buf, 5);
  if (n != 5) { close(fd); return 2; }
  close(fd);
  return 42;
}
"""
G_READ_BACK = """
int openat(int a, char *p, int fl, int m);
int read(int fd, char *b, int n);
int close(int fd);
int main(){
  int fd = openat(0, "vd.txt", 0, 0);
  if (fd < 0) return 1;
  char buf[100];
  int n = read(fd, buf, 100);
  close(fd);
  if (n != 5) return 2;
  return buf[0]+buf[1]+buf[2]+buf[3]+buf[4];
}
"""
G_LSEEK = """
int openat(int a, char *p, int fl, int m);
int read(int fd, char *b, int n);
int close(int fd);
int lseek(int fd, int off, int w);
int main(){
  int fd = openat(0, "vd.txt", 0, 0);
  if (fd < 0) return 1;
  lseek(fd, 2, 0);
  char buf[2];
  read(fd, buf, 2);
  close(fd);
  return buf[0]*10 + buf[1];
}
"""
G_UNLINK = """
int unlink(char *p);
int main(){ return unlink("vd.txt") == 0 ? 42 : 1; }
"""
G_RENAME = """
int rename(char *a, char *b);
int main(){ return rename("vd_old.txt", "vd_new.txt") == 0 ? 42 : 1; }
"""
G_ESCAPE_ABS = """
int openat(int a, char *p, int fl, int m);
int main(){ return openat(0, "/etc/passwd", 0, 0) < 0 ? 42 : 1; }
"""
G_ESCAPE_DOTDOT = """
int openat(int a, char *p, int fl, int m);
int main(){ return openat(0, "../outside.txt", 0, 0) < 0 ? 42 : 1; }
"""
G_APPEND = """
int openat(int a, char *p, int fl, int m);
int write(int fd, char *b, int n);
int close(int fd);
int main(){
  int fd = openat(0, "vd.txt", 1025, 0);   /* O_APPEND|O_WRONLY = 1024|1 */
  if (fd < 0) return 1;
  write(fd, "XY", 2);
  close(fd);
  return 42;
}
"""


def main():
    sandbox = _bootstrap.scratch("vdisk_sandbox")
    os.makedirs(sandbox, exist_ok=True)

    # ---- 1. 创建+写入 → 宿主文件验证（双 VM 各自独立沙箱实例）----
    tf1 = compile_guest(G_CREATE_WRITE, "cw")
    host_after_cvm = None
    for tag, runner in (("CVM", lambda: run_cvm(tf1, sandbox)),
                        ("PyVM", lambda: run_py(tf1, sandbox))):
        f = os.path.join(sandbox, "vd.txt")
        if os.path.exists(f):
            os.unlink(f)
        check("vdisk 创建写入·%s 退出码" % tag, runner(), 42)
        with open(f, "rb") as fh:
            host_after_cvm = fh.read()
        check("vdisk 创建写入·%s 宿主文件内容" % tag, host_after_cvm, b"hello")

    # ---- 2. 重开读回（内容由上一步 CVM 写入）----
    tf2 = compile_guest(G_READ_BACK, "rb")
    # exit 契约 = a0 & 0xFF：532 = 104+101+108+108+111 → &0xFF = 20
    check("vdisk 读回·CVM 校验和", run_cvm(tf2, sandbox), 532 & 0xFF)
    check("vdisk 读回·PyVM 校验和", run_py(tf2, sandbox), 532 & 0xFF)

    # ---- 3. lseek(fd, 2, SEEK_SET) + read 2 字节 → buf[0]*10+buf[1]=108*10+108 ----
    tf3 = compile_guest(G_LSEEK, "ls")
    # exit 契约 = a0 & 0xFF：108*10+108=1188 → &0xFF = 164
    check("vdisk lseek+读·CVM", run_cvm(tf3, sandbox), (108 * 10 + 108) & 0xFF)
    check("vdisk lseek+读·PyVM", run_py(tf3, sandbox), (108 * 10 + 108) & 0xFF)

    # ---- 4. O_APPEND 追加（不截断）----
    tf4 = compile_guest(G_APPEND, "ap")
    check("vdisk O_APPEND·CVM 退出码", run_cvm(tf4, sandbox), 42)
    with open(os.path.join(sandbox, "vd.txt"), "rb") as fh:
        check("vdisk O_APPEND·宿主内容追加", fh.read(), b"helloXY")

    # ---- 5. unlink → 宿主文件消失 ----
    tf5 = compile_guest(G_UNLINK, "ul")
    check("vdisk unlink·CVM 退出码", run_cvm(tf5, sandbox), 42)
    check("vdisk unlink·宿主文件消失",
          os.path.exists(os.path.join(sandbox, "vd.txt")), False)

    # ---- 6. rename roundtrip ----
    tf6 = compile_guest(G_RENAME, "rn")
    with open(os.path.join(sandbox, "vd_old.txt"), "wb") as fh:
        fh.write(b"move")
    check("vdisk rename·CVM 退出码", run_cvm(tf6, sandbox), 42)
    check("vdisk rename·旧名消失",
          os.path.exists(os.path.join(sandbox, "vd_old.txt")), False)
    with open(os.path.join(sandbox, "vd_new.txt"), "rb") as fh:
        check("vdisk rename·新名内容", fh.read(), b"move")
    os.unlink(os.path.join(sandbox, "vd_new.txt"))

    # ---- 7. 沙箱越界拒绝（绝对路径 / ".." 穿越 → -2=ENOENT）----
    tf7 = compile_guest(G_ESCAPE_ABS, "ea")
    tf8 = compile_guest(G_ESCAPE_DOTDOT, "ed")
    check("vdisk 沙箱·绝对路径拒绝·CVM", run_cvm(tf7, sandbox), 42)
    check("vdisk 沙箱·绝对路径拒绝·PyVM", run_py(tf7, sandbox), 42)
    check("vdisk 沙箱·..穿越拒绝·CVM", run_cvm(tf8, sandbox), 42)
    check("vdisk 沙箱·..穿越拒绝·PyVM", run_py(tf8, sandbox), 42)
    check("vdisk 沙箱·宿主未产生越界文件",
          os.path.exists(os.path.join(os.path.dirname(sandbox), "outside.txt")),
          False)

    print("\n虚拟磁盘契约：%d PASS / %d FAIL" % (passed, failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
