"""TriRV syscall 接口 端到端验证。

覆盖 glibc 启动最小 syscall 集：进程/线程、时钟、随机、内存映射、
文件 stub、信号 stub；并回归大常量加载（load_imm 借位修复）。
"""
import os
import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import trirv_cc_backend as cc
import trirv_isa as isa
import trirv_vm as vm

S = isa.to_signed_word

# (名称, C 源码, 期望 a0)
CASES = [
    # --- 进程/线程 ---
    ("getpid", "int main(){ return getpid(); }", 1),
    ("gettid", "int main(){ return gettid(); }", 1),
    ("set_tid_address", "int main(){ int t=0; return set_tid_address(&t); }", 1),
    # --- 时钟（r=0，nsec 每次 +1ms）---
    ("clock_gettime sec/nsec",
     "int main(){ int ts[2]; int r=clock_gettime(0,ts); return r*100+ts[0]*10+ts[1]/1000000; }", 1),
    ("clock_gettime 单调 +1ms",
     "int main(){ int a[2]; int b[2]; clock_gettime(0,a); clock_gettime(0,b); return b[1]/1000000-a[1]/1000000; }", 1),
    # --- 随机（确定性 PRNG）---
    ("getrandom 返回字节数",
     "int main(){ char b[4]; return getrandom(b,4,0); }", 4),
    ("getrandom 流式推进（两次不同）",
     "int main(){ char a[8]; char b[8]; getrandom(a,8,0); getrandom(b,8,0); if(a[0]==b[0]) return 0; return 1; }", 1),
    ("getrandom 值域 0..242",
     "int main(){ char b[16]; getrandom(b,16,0); int i=0; while(i<16){ if(b[i]<0 || b[i]>242) return -1; i=i+1; } return 16; }", 16),
    # --- mmap ---
    ("mmap 写读",
     "int main(){ int *p=mmap(0,90,3,2,-1,0); p[0]=77; return p[0]; }", 77),
    ("mmap 两次不相交",
     "int main(){ int *p=mmap(0,90,3,2,-1,0); int *q=mmap(0,90,3,2,-1,0); int d=p-q; if(d<0) d=0-d; return d; }", 10),  # 90/9=10 (int 单位)
    ("munmap/mprotect stub",
     "int main(){ int *p=mmap(0,90,3,2,-1,0); return mprotect(p,90,0)+munmap(p,90); }", 0),
    # --- 文件 stub ---
    ("openat 分配 fd",
     "int main(){ return openat(0,\"x\",0); }", 3),
    ("close 成功/二次失败",
     "int main(){ int fd=openat(0,\"x\",0); int a=close(fd); int b=close(fd); return a*100-b; }", 9),
    ("fstat/lseek stub",
     "int main(){ int fd=openat(0,\"x\",0); return fstat(fd,100)+lseek(fd,0,0); }", 0),
    # --- 信号/调度 stub ---
    ("futex/sched_yield stub",
     "int main(){ return futex(0,0,0,0,0,0)+sched_yield()+rt_sigaction(0,0,0)+rt_sigprocmask(0,0,0,0); }", 0),
    # --- uname ---
    ("uname 机器名 TriRV",
     "int main(){ char b[8]; int r=uname(b); return r*100+b[0]; }", 84),
    # --- 大常量加载回归（load_imm 借位）---
    ("大常量 10000", "int main(){ return 10000; }", 10000),
    ("大常量除法", "int main(){ return 1000000/10000; }", 100),
]

def run(src, steps=400000):
    prog, labels, txt, strs = cc.compile_c(src)
    for i, w in enumerate(prog):
        d = isa.decode(w)
        if d["inst"] is None:
            raise AssertionError(f"pc={i} 非法指令")
    cpu = vm.VM(prog, mem_init=dict(strs or {}))
    cpu.run(max_steps=steps)
    return S(cpu.regs[10]), cpu

def main():
    passed = 0
    for name, src, expected in CASES:
        try:
            got, cpu = run(src)
            ok = (got == expected)
            print(f"{'PASS' if ok else 'FAIL'} {name}: got={got} expected={expected}")
            passed += ok
        except Exception as ex:
            print(f"ERR  {name}: {type(ex).__name__}: {ex}")
    print(f"\n总计 {passed}/{len(CASES)} 通过")
    assert passed == len(CASES), "存在失败用例"
    print("全部通过")

if __name__ == "__main__":
    main()
