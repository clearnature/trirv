import subprocess, os
import os
import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import trirv_cc_backend as cc, trirv_vm as vm
from export_trv import export

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根（重组后上跳一层）  # 可移植：原硬编码 /workspace
STDIO=open(os.path.join(R,"trirv_stdio.c")).read()
LIBC=open(os.path.join(R,"trirv_libc.c")).read()
CVM = os.environ.get("TRIRV_VM_BIN", os.path.join(R,"vm","bin","trirv_vm"))  # C VM 二进制路径可覆盖

cases = [
 ("算术", 'int main(){return 7*3+8/2-4;}', "", False),
 ("递归fib", 'int fib(int n){if(n<2)return n;return fib(n-1)+fib(n-2);} int main(){return fib(12);}', "", False),
 ("while", 'int main(){int s=0;int i=1;while(i<=10){s=s+i;i=i+1;}return s;}', "", False),
 ("for+break", 'int main(){int s=0;int i=0;for(i=0;i<10;i=i+1){if(i>6)break;s=s+i;}return s;}', "", False),
 ("printf", 'int main(){printf("z=%d",0);return 3;}', "", True),
 ("write", 'int main(){write(1,"ABCD",4);return 4;}', "", False),
 ("read", 'int main(){int n=read(0,200,5);write(1,200,n);return n;}', "XYZ\n", False),
 ("大常量", 'int main(){return 10000/100;}', "", False),
 ("struct", 'struct P{int x;int y;}; int main(){struct P p;p.x=30;p.y=12;return p.x+p.y;}', "", False),
 ("数组", 'int main(){int a[5];int i=0;for(i=0;i<5;i=i+1){a[i]=i*3;}return a[4];}', "", False),
 ("malloc", 'int main(){int*p=malloc(9);p[0]=77;int r=p[0];free(p);return r;}', "XYZ\n", True),
]

passed=0
for name,body,stdin,need_lib in cases:
    src=(STDIO+"\n"+LIBC+"\n"+body) if need_lib else body
    pf=_bootstrap.scratch(f"d_{name}.c"); tf=_bootstrap.scratch(f"d_{name}.trv")
    open(pf,"w").write(src)
    prog,lab,txt,strs=cc.compile_c(src)
    c=vm.VM(prog,mem_init=strs or None,stdin=stdin.encode()).run(max_steps=500000)
    py_exit=c.exit_code&0xFF; py_out=bytes(c.output).decode('latin1')
    export(pf,tf)
    din=_bootstrap.scratch("din")
    open(din,"wb").write(stdin.encode())
    r=subprocess.run([CVM,tf,din],capture_output=True)
    c_out=r.stdout.decode('latin1')
    errs=[l for l in r.stderr.decode().split() if l.startswith('exit=')]
    c_exit=int(errs[0].split('=')[1]) if errs else -999
    ok=(py_exit==c_exit and py_out==c_out)
    print(f"{'PASS' if ok else 'FAIL'} {name:9s} py_exit={py_exit:4d} c_exit={c_exit:4d} out匹配={py_out==c_out} py={py_out!r} c={c_out!r}")
    if ok: passed+=1
print(f"\n差异测试 {passed}/{len(cases)} 通过")
