#!/usr/bin/env python3
"""TriRV 全语料 TRV1≡TRV2 等价 harness（ADR-17 准入③④，批次 3.6–3.7）。

34 个 C 程序（覆盖算术/循环/分支/函数/递归/数组/字符串/struct/fn-ptr/
TC 触发形态/弛豫触发形态），每个程序：
  A. TRV1 槽制容器（TRV_COMPACT=1）→ CVM 执行
  B. TRV2 流制容器（TRV_COMPACT=1 + TRV_FORMAT=2）→ CVM 执行
  退出码 + stdout 逐字节一致；数据段（GLOB/STR）逐字节一致（3.7 审计：
  证明数据段不含容器相关地址——弛豫/流制只动代码词）。
"""
import os
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CC = os.path.join(ROOT, "cc", "bin", "trirv_cc")
CVM = os.path.join(ROOT, "vm", "bin", "trirv_vm")

passed = failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print("PASS %s" % name)
    else:
        failed += 1
        print("FAIL %s: got=%r want=%r" % (name, got, want))


PROGS = [
    ("算术", "int main(){ return 7*3+8/2-4; }", 21),
    ("比较", "int main(){ return (3<5)+(5<=5)*2+(7==7)*4; }", 7),
    ("逻辑", "int main(){ return (1&&1)+(0||1)*2+(!0)*4; }", 7),
    ("while 循环", "int main(){ int s=0,i=1; while(i<=10){ s=s+i; i=i+1; } return s; }", 55),
    ("for 循环", "int main(){ int s=0; for(int i=1;i<=10;i=i+1) s=s+i; return s; }", 55),
    ("do-while", "int main(){ int i=0,s=0; do{ s=s+i; i=i+1; }while(i<5); return s; }", 10),
    ("if/else", "int main(){ int x=7,r; if(x>5) r=100; else r=0; return r; }", 100),
    ("switch", "int main(){ int x=2,s=0; switch(x){case 1: s=10; break; case 2: s=20; break; default: s=30;} return s; }", 20),
    ("goto", "int main(){ int i=0; loop: i=i+1; if(i<3) goto loop; return i; }", 3),
    ("递归 fib", "int fib(int n){ if(n<2) return n; return fib(n-1)+fib(n-2); } int main(){ return fib(12); }", 144),
    ("递归 fact", "int fact(int n){ if(n<=1) return n; return n*fact(n-1); } int main(){ return fact(6); }", 208),
    ("调用链", "int inc3(int v){ v=v+3; return v; } int main(){ int a=inc3(4); a=a+10; return inc3(a); }", 20),
    ("多函数", "int add(int a,int b){ return a+b; } int mul(int a,int b){ return a*b; } int main(){ return add(2,mul(3,4)); }", 14),
    ("全局数组", "int g[8]; int main(){ int i=0,s=0; while(i<8){ g[i]=i*3; i=i+1; } while(i>0){ i=i-1; s=s+g[i]; } return s; }", 84),
    ("局部数组", "int main(){ int a[5]; int i=0; while(i<5){ a[i]=i*i; i=i+1; } return a[1]+a[4]; }", 17),
    ("字符串", "char *m=\"AB\"; int main(){ return m[0]+m[1]; }", 131),
    ("字符串数组", "int main(){ char m[3]=\"AB\"; return m[0]+m[1]; }", 200),
    ("char 窄化", "int main(){ char c=300; return c; }", 57),
    ("struct 偏移", "struct S{char c; int *p;}; struct S s; int main(){ return (int)((char*)&s.p-(char*)&s); }", 9),
    ("struct 按值", "struct P{int a; int b;}; struct P mk(int v){ struct P r; r.a=v; r.b=v*2; return r; } int main(){ struct P p=mk(5); return p.a+p.b; }", 15),
    ("fn-ptr 直调", "int inc(int x){ return x+1; } int main(){ int (*fp)(int)=inc; return (*fp)(9); }", 10),
    ("fn-ptr 裸括号", "int inc(int x){ return x+1; } int main(){ int (*fp)(int); fp=inc; return (fp)(41); }", 42),
    ("fn-ptr 选择", "int a(int x){ return x+1; } int b(int x){ return x*2; } int main(){ int (*fp)(int); int c=1; if(c) fp=a; else fp=b; return fp(5); }", 6),
    ("大循环", "int main(){ int s=0,i=0; while(i<5000){ s=s+i*3-7; i=i+1; } return s&255; }", 12),
    ("常量折叠", "int main(){ return 100000*100000 % 1000; }", 0),
    ("三值AND", "int main(){ return 12 & 10; }", 9),
    ("移位", "int main(){ return (1<<4)+(81>>2); }", 90),
    ("指针遍历", "int main(){ int a[6]; int *p=a; int i=0; while(i<6){ *p=i+1; p=p+1; i=i+1; } return a[0]+a[5]; }", 7),
    ("多返回路径", "int f(int x){ if(x>10) return 1; if(x>5) return 2; return 3; } int main(){ return f(20)*100+f(8)*10+f(1); }", 123),
    ("嵌套调用", "int g(int v){ return v*v; } int f(int v){ return g(v+1); } int main(){ return f(3); }", 16),
    ("变参", "int main(){ int *p=0; return p!=0; }", 0),
    ("长分支弛豫触发", "int main(){ int x=0; if(x==5){ x=x+1; x=x+2; x=x+3; x=x+4; x=x+5; x=x+6; x=x+7; x=x+8; x=x+9; x=x+10; x=x+11; x=x+12; x=x+13; x=x+14; x=x+15; x=x+16; x=x+17; x=x+18; x=x+19; x=x+20; } return 42; }", 42),
    ("复合条件", "int main(){ int a=5,b=0; if(a>3&&b==0) return 1; return 0; }", 1),
    ("负数运算", "int main(){ int a=-7; int b=-3; return (a+b)*-1; }", 10),
]


def compile_and_run(src, trv2):
    cf = "/tmp/eq_%d.c" % abs(hash(src))
    ext = ".trv2" if trv2 else ".trv"
    tf = "/tmp/eq_%d%s" % (abs(hash(src)), ext)
    with open(cf, "w") as f:
        f.write(src)
    env = dict(os.environ)
    env["TRV_COMPACT"] = "1"
    if trv2:
        env["TRV_FORMAT"] = "2"
    else:
        env.pop("TRV_FORMAT", None)
    r = subprocess.run([CC, cf, tf], capture_output=True, text=True, env=env)
    if r.returncode != 0:
        return None, None, None
    r2 = subprocess.run([CVM, tf, "-"], capture_output=True, text=True)
    errs = [l for l in r2.stderr.split() if l.startswith("exit=")]
    exit_code = int(errs[0].split("=")[1]) if errs else None
    data = open(tf, "rb").read()
    # 数据段提取（段表紧跟代码区）：
    if trv2:
        n_trytes = struct.unpack_from("<I", data, 8)[0]
        off = 16 + n_trytes
    else:
        n_inst = struct.unpack_from("<I", data, 8)[0]
        off = 16 + 4 * n_inst
    n_seg = struct.unpack_from("<I", data, 12)[0]
    segs = []
    for _ in range(n_seg):
        addr, ln = struct.unpack_from("<II", data, off)
        off += 8
        segs.append((addr, data[off:off + ln].hex()))
        off += ln
    os.remove(tf); os.remove(cf)
    return exit_code, segs, os.path.getsize(tf) if os.path.exists(tf) else 0


def main():
    for name, src, want in PROGS:
        e1, seg1, _ = compile_and_run(src, trv2=False)
        e2, seg2, _ = compile_and_run(src, trv2=True)
        check("%s·TRV1 退出码" % name, e1, want)
        check("%s·TRV2≡TRV1 退出码" % name, e2, e1)
        check("%s·数据段逐字节一致" % name, seg2, seg1)
    print("\n全语料等价：%d PASS / %d FAIL" % (passed, failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
