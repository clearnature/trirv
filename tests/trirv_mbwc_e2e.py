#!/usr/bin/env python3
"""TriRV multibyte+wctype+wcs 契约（批次 7，三值本征 multibyte 恒等映射）。

三值本征特性：char=1 tryte，wchar_t=int——tryte 序列已是平坦编码，
无 UTF-8 多字节状态机。mbrtowc/wcrtomb=恒等映射。
wcs/wmem=与 str*/mem* 同构（按 9 tryte 全字步进）。
"""
import os, sys, subprocess, tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
CC = os.path.join(ROOT, "..", "cc", "bin", "trirv_cc")
CVM = os.path.join(ROOT, "..", "vm", "bin", "trirv_vm")
MBWC = os.path.join(ROOT, "..", "libc-musl", "musl_tri", "mbwc_tri.c")
WCS = os.path.join(ROOT, "..", "libc-musl", "musl_tri", "wcs_tri.c")

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

def run(src):
    mbwc = open(MBWC).read()
    wcs = open(WCS).read()
    full = mbwc + "\n" + wcs + "\n" + src
    cf = tempfile.mktemp(suffix=".c", dir=os.path.join(ROOT, "..", "tmp"))
    tf = tempfile.mktemp(suffix=".trv", dir=os.path.join(ROOT, "..", "tmp"))
    open(cf, "w").write(full)
    r = subprocess.run([CC, cf, tf], capture_output=True)
    if r.returncode != 0:
        return "CFAIL:" + r.stderr.decode("utf-8", "replace")[:120]
    sandbox = tempfile.mkdtemp(dir=os.path.join(ROOT, "..", "tmp"))
    r2 = subprocess.run([CVM, tf], capture_output=True, cwd=sandbox, timeout=60)
    ex = [l.decode("utf-8", "replace") for l in r2.stderr.split() if l.startswith(b"exit=")]
    return int(ex[0].split("=")[1]) if ex else None

# ---- 7.2 multibyte 恒等映射 ----
check("mbrtowc 'A'→65", run('''
int mbrtowc(int *p, const char *s, int n, int *st);
int main(){ int w; int r = mbrtowc(&w, "A", 1, 0); return r*100 + w; }'''), 165)

check("mbrtowc NUL→0", run('''
int mbrtowc(int *p, const char *s, int n, int *st);
int main(){ int w; return mbrtowc(&w, "\\0", 1, 0); }'''), 0)

check("wcrtomb 恒等", run('''
int wcrtomb(char *s, int wc, int *st);
int main(){ char b; int n = wcrtomb(&b, 72, 0); return n*100 + b; }'''), 172)

check("mbrlen 恒 1", run('''
int mbrlen(const char *s, int n, int *st);
int main(){ return mbrlen("A", 1, 0); }'''), 1)

check("mbsinit 恒 1", run('''
int mbsinit(int *st);
int main(){ return mbsinit(0); }'''), 1)

check("mbstowcs 逐 tryte", run('''
int mbstowcs(int *d, const char *s, int n);
int main(){ int w[10]; int n = mbstowcs(w, "AB", 10);
  return n*100 + w[0] + w[1]; }'''), (2*100 + 65 + 66) & 0xFF)

# ---- 7.3 wctype 分类 ----
check("isdigit '5'", run('int isdigit(int c);\nint main(){ return isdigit(53); }'), 1)
check("isdigit 'A'", run('int isdigit(int c);\nint main(){ return isdigit(65); }'), 0)
check("isalpha 'A'", run('int isalpha(int c);\nint main(){ return isalpha(65); }'), 1)
check("isalpha '5'", run('int isalpha(int c);\nint main(){ return isalpha(53); }'), 0)
check("isupper 'Z'", run('int isupper(int c);\nint main(){ return isupper(90); }'), 1)
check("islower 'z'", run('int islower(int c);\nint main(){ return islower(122); }'), 1)
check("isspace ' '", run('int isspace(int c);\nint main(){ return isspace(32); }'), 1)
check("isxdigit 'f'", run('int isxdigit(int c);\nint main(){ return isxdigit(102); }'), 1)
check("toupper 'a'→'A'", run('int toupper(int c);\nint main(){ return toupper(97); }'), 65)
check("tolower 'A'→'a'", run('int tolower(int c);\nint main(){ return tolower(65); }'), 97)

# ---- 7.4 wcs/wmem 全字步进 ----
check("wcslen", run('''
int wcslen(const int *s);
int main(){ int w[3]; w[0]=72; w[1]=101; w[2]=0; return wcslen(w); }'''), 2)

check("wcscpy+wcslen", run('''
int *wcscpy(int *d, const int *s);
int wcslen(const int *s);
int main(){ int src[3]; src[0]=72; src[1]=101; src[2]=0;
  int dst[3]; wcscpy(dst, src);
  return wcslen(dst)*100 + dst[0]; }'''), (2*100 + 72) & 0xFF)

check("wcscmp 相等→0", run('''
int wcscmp(const int *a, const int *b);
int main(){ int a[2]; a[0]=1; a[1]=0;
  int b[2]; b[0]=1; b[1]=0;
  return wcscmp(a, b); }'''), 0)

check("wcscmp 不等", run('''
int wcscmp(const int *a, const int *b);
int main(){ int a[2]; a[0]=2; a[1]=0;
  int b[2]; b[0]=1; b[1]=0;
  return wcscmp(a, b) > 0 ? 42 : 1; }'''), 42)

check("wmemset+wmemchr", run('''
int *wmemset(int *s, int c, int n);
int *wmemchr(const int *s, int c, int n);
int main(){ int w[5];
  wmemset(w, 7, 5);
  int *p = wmemchr(w, 7, 5);
  return p ? w[3] : 0; }'''), 7)

check("wmemcpy", run('''
int *wmemcpy(int *d, const int *s, int n);
int main(){ int src[3]; src[0]=10; src[1]=20; src[2]=0;
  int dst[3]; wmemcpy(dst, src, 3);
  return dst[0]*10 + dst[1]; }'''), 120)

print("\nmbwc 契约：%d PASS / %d FAIL" % (passed, failed))
sys.exit(1 if failed else 0)
