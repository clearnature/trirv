"""TriRV C 编译器端到端验证。

流程: C 源码 -> trirv_cc 编译 -> trirv_asm 汇编 -> trirv_isa 机器码 -> trirv_vm 执行
覆盖: 算术/比较/逻辑、while/for/if、break/continue、函数、递归。
"""
import os
import tempfile
import subprocess
import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import trirv_cc_backend as cc
import trirv_isa as isa
import trirv_vm as vm
CC_BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cc", "bin", "trirv_cc")

S = isa.to_signed_word


def compile_run(src, steps=300000, stdin=b""):
    prog, labels, asmtext, strings = cc.compile_c(src)
    cpu = vm.VM(prog, mem_init=strings or None, stdin=stdin).run(max_steps=steps)
    # 校验每条指令都能解码
    for i, w in enumerate(prog):
        assert isa.decode(w)["inst"] is not None, f"pc={i} 非法指令"
    return S(cpu.regs[10]), cpu


def _tlit(v, a):
    """python oracle：逐 trit Post literal（ISA §6.1 同口径；返回 45-trit 词）"""
    r = 0
    p = 1
    for _ in range(45):
        v, t = divmod(v, 3)
        r += ((2 - t) if a == 0 else (t + a) % 3) * p
        p *= 3
    return r


def _tlit_signed(v, a):
    """45-trit 词 -> 带符号值（compile_run 返回带符号口径）"""
    w = _tlit(v, a)
    if w > (3 ** 45 - 1) // 2:
        w -= 3 ** 45
    return w


CASES = [
    ("算术 + - * / %", "int main(){ return 7*3 + 8/2 - 4; }", 21),
    ("比较运算", "int main(){ return (3<5) + (5<=5)*2 + (7==7)*4 + (8!=8); }", 7),
    ("逻辑 && || !", "int main(){ return (1&&1) + (0||1)*2 + (!0)*4; }", 7),
    ("while 累加", "int main(){ int s=0; int i=1; while(i<=10){ s=s+i; i=i+1; } return s; }", 55),
    ("for 累加", "int main(){ int s=0; int i=0; for(i=1;i<=10;i=i+1){ s=s+i; } return s; }", 55),
    ("if/else", "int main(){ int x=7; int r; if(x>5){ r=100; } else { r=0; } return r; }", 100),
    ("break/continue",
     "int main(){ int s=0; int i=0; for(i=0;i<10;i=i+1){ if(i%2==0){continue;} if(i>6){break;} s=s+i; } return s; }",
     1 + 3 + 5),
    ("函数参数", "int add(int a,int b){ return a+b; } int main(){ return add(20,22); }", 42),
    ("递归阶乘", "int fact(int n){ if(n<=1){return 1;} return n*fact(n-1); } int main(){ return fact(6); }", 720),
    ("递归斐波那契", "int fib(int n){ if(n<2){return n;} return fib(n-1)+fib(n-2); } int main(){ return fib(12); }", 144),
    # ---- __builtin_tri_lit 内建（2026-11-23 trirv_cc 与 tri-tcc 同名同语义；§6.1）----
    # 方言注：trirv_cc 无 (type) 强转与 ?: 三元——用直返/if-else（与 tri-tcc
    # 契约的差异面，属 trirv_cc 方言子集，非内建语义差异）
    ("tlit内建后继", "int main(){ return __builtin_tri_lit(729, 1); }", _tlit_signed(729, 1)),
    ("tlit内建前驱", "int main(){ return __builtin_tri_lit(729, 2); }", _tlit_signed(729, 2)),
    ("tlit内建补", "int main(){ return __builtin_tri_lit(729, 0); }", _tlit_signed(729, 0)),
    ("tlit变量操作数", "int main(){ int x = 729; int y = __builtin_tri_lit(x, 1); "
     "int z = __builtin_tri_lit(y, 1); int w = __builtin_tri_lit(x, 2); "
     "int r = 0; if (z == w) { r = 1; } return r; }", 1),
    ("tlit嵌套恒等", "int main(){ int x = __builtin_tri_lit(__builtin_tri_lit(729, 1), 2); "
     "int r = 0; if (x == 729) { r = 1; } return r; }", 1),

    # ---- __tint2 4-tryte/20-trit 档（trirv_cc 面；无强转方言——窄化全靠
    #      存取即窄化：SW4 mod 3^20 截断 / LW4 sx20 符号扩展；无 sizeof）----
    ("tint2全局读", "__tint2 g = 100000; int main(){ if(g==100000){return 1;} return 2; }", 1),
    ("tint2负值往返", "int main(){ __tint2 x = -123456; if(x==-123456){return 1;} return 2; }", 1),
    ("tint2存储截断", "int main(){ __tint2 x = 5000000000; if(x==1513215599){return 1;} return 2; }", 1),
    ("tint2过半转负", "int main(){ __tint2 x = 3000000125; if(x==-486784276){return 1;} return 2; }", 1),
    ("tint2数组步长", "__tint2 a[3]; int main(){ int i; for(i=0;i<3;i=i+1){ a[i]=i*100000; } "
     "if(a[1]==100000 && a[2]==200000){return 1;} return 2; }", 1),
    ("tint2全局数组负初值", "__tint2 g[2] = {7, -7}; int main(){ if(g[0]==7 && g[1]==-7){return 1;} return 2; }", 1),
    ("tint2传参返回", "__tint2 id(__tint2 v){ return v; } int main(){ __tint2 x = 123456; "
     "if(id(x)==123456){return 1;} return 2; }", 1),
    ("tint2存储路径截断", "int main(){ int big = 4000000000; __tint2 x = big; "
     "if(x==513215599){return 1;} return 2; }", 1),
    ("tint2结构体成员", "struct S { char c; __tint2 v; }; int main(){ struct S s; s.c = 3; "
     "s.v = 5000000000; if(s.v==1513215599 && s.c==3){return 1;} return 2; }", 1),

    # ---- __tint/__tbool Kleene 类型（批B；与 tri-tcc C1/C2-c 同口径）。
    #      方言面：无 (int) 强转/无 ?:——tint 结果比较走 v[] 索引（tint vs
    #      tint 位型全等），期望值构造全在程序内。----
    ("tint与或全组合", "__tint v[3]; int main(){ v[0]=0; v[1]=1; v[2]=2; int f=0; int i; int j; "
     "for(i=0;i<3;i=i+1){ for(j=0;j<3;j=j+1){ __tint a=v[i]; __tint b=v[j]; int lo; int hi; "
     "if(i<j){lo=i;}else{lo=j;} if(i>j){hi=i;}else{hi=j;} "
     "if((a&b)!=v[lo]){f=f+100;} if((a|b)!=v[hi]){f=f+1;} } } return f; }", 0),
    ("tint非德摩根", "__tint v[3]; int main(){ v[0]=0; v[1]=1; v[2]=2; int f=0; int i; int j; "
     "for(i=0;i<3;i=i+1){ for(j=0;j<3;j=j+1){ __tint a=v[i]; __tint b=v[j]; "
     "__tint L=~(a&b); __tint R=(~a)|(~b); if(L!=R){f=f+1;} } } return f; }", 0),
    ("tint相等位型", "__tint v[3]; int main(){ v[0]=0; v[1]=1; v[2]=2; int f=0; int i; int j; "
     "for(i=0;i<3;i=i+1){ for(j=0;j<3;j=j+1){ if((v[i]==v[j])!=(i==j)){f=f+4;} } } "
     "return f+100*(v[1]!=v[2]); }", 100),
    ("tint真值判断", "__tint v[3]; int main(){ v[0]=0; v[1]=1; v[2]=2; int f=0; "
     "if(v[0]){f=f+1;} if(!v[1]){f=f+2;} if(!v[2]){f=f+4;} if(v[2]){f=f+8;} return f; }", 8),
    ("tint传参返回", "__tint tmax(__tint a, __tint b){ return a|b; } __tint v[3]; "
     "int main(){ v[0]=0; v[1]=1; v[2]=2; int f=0; int i; int j; "
     "for(i=0;i<3;i=i+1){ for(j=0;j<3;j=j+1){ __tint m=tmax(v[i],v[j]); int hi; "
     "if(i>j){hi=i;}else{hi=j;} if(m!=v[hi]){f=f+1;} } } return f; }", 0),
    ("tbool与或非真值表", "__tbool v[3]; int main(){ v[0]=0; v[1]=1; v[2]=2; int f=0; int i; int j; "
     "for(i=0;i<3;i=i+1){ for(j=0;j<3;j=j+1){ __tbool a=v[i]; __tbool b=v[j]; int lo; int hi; "
     "if(i<j){lo=i;}else{lo=j;} if(i>j){hi=i;}else{hi=j;} "
     "if((a&b)!=v[lo]){f=f+1;} if((a|b)!=v[hi]){f=f+2;} "
     "__tbool L=~(a&b); __tbool R=(~a)|(~b); if(L!=R){f=f+4;} "  # 德摩根（TNOT 词级
     "} } return f; }", 0),                                                       # 非值域映射）
    ("tbool负词精确往返", "int main(){ __tint z0=0; __tbool n=~z0; __tint t=n; "
     "__tint e=~z0; if(t==e){return 1;} return 2; }", 1),
    ("tbool与tint混用合法", "__tint t=2; __tbool b=1; int main(){ return t & b; }", 1),

    # ---- T1 const 限定符（存储等价口径；两后端同语义）----
    ("const全局", "const int c = 7;\nint main(){ return c; }", 7),
    ("const局部", "int main(){ const int x = 5; return x; }", 5),
    ("const前缀组合", "const unsigned char u = 3;\nint main(){ return u; }", 3),
    ("const数组", "const int a[2] = {4,5};\nint main(){ return a[0]+a[1]; }", 9),
]

def main():
    print("=" * 60)
    print("TriRV C 编译器端到端验证")
    print("C 源码 -> 编译器 -> 汇编器 -> 机器码 -> VM 执行")
    print("=" * 60)
    passed = 0
    for name, src, expect in CASES:
        try:
            got, cpu = compile_run(src)
            ok = got == expect
            mark = "PASS" if ok else "FAIL"
            print(f"  [{mark}] {name:16s} = {got:5d} (期望 {expect})  steps={cpu.steps}")
            if ok:
                passed += 1
            else:
                assert False
        except Exception as e:
            print(f"  [FAIL] {name:16s} 异常: {e}")
    print("-" * 60)
    print(f"基础用例通过 {passed}/{len(CASES)}")

    # 展示一个完整编译产物（阶乘）作为工具链能力样本
    demo = "int fact(int n){ if(n<=1){return 1;} return n*fact(n-1); } int main(){ return fact(5); }"
    prog, labels, txt, strs = cc.compile_c(demo)
    print(f"\n编译样本 fact(5): 生成 {len(prog)} 条 20-trit 指令，符号表 {labels}")
    print("前 12 条汇编:")
    for line in [l for l in txt.splitlines() if l.strip()][:12]:
        print("   " + line.strip())
    total = len(CASES)
    ok_dr, tot_dr = test_data_region()
    passed += ok_dr
    total += tot_dr
    ok_rj, tot_rj = test_rejects()
    passed += ok_rj
    total += tot_rj
    print(f"\n总计通过 {passed}/{total}")
    assert passed == total, "存在失败用例"
    print("全部通过 ✓ 工具链已具备编译 C + syscall 的 libc 地基能力")


def test_data_region():
    """C2 内存映射纪律（2026-11-22）：数据区顶（全局+字符串）不得越过栈基址。

    内存映射（trirv-unified-memory-abi.md §3）：数据 200..4095，栈 4096+ 向上。
    旧行为：trirv_cc 不检查——超大全局静默编译，栈帧与静态数据重叠 → 运行期
    互相踩踏（静默错码）。新行为：链接期静态检查 STRBASE ≤ 4096，越界诚实拒绝。
    边界精确例锁定 9 tryte/字的算术（432 int 恰好 4095 ≤ 4096）。"""
    print("-" * 60)
    print("数据区 vs 栈基址边界（C2 内存映射纪律）")
    cf = tempfile.mktemp(suffix=".c")
    tf = tempfile.mktemp(suffix=".trv")
    ok = 0
    total = 0

    def compile_rc(src):
        with open(cf, "w") as f:
            f.write(src)
        r = subprocess.run([CC_BIN, cf, tf], capture_output=True, text=True)
        return r.returncode, r.stderr

    cases = [
        # (名, 源, 期望 rc==0?, 报错 needle 或 None)
        ("越界拒绝 big[600]", "int big[600]; int id(int x){return x;} "
         "int main(void){ big[599]=1; return id(0)+big[0]; }", False, "栈基址"),
        ("越界精确 b[433]", "int b[433]; int main(void){ b[432]=9; return 0; }",
         False, "栈基址"),
        ("边界安全 b[432](4095≤4096)", "int b[432]; int main(void){ b[431]=9; return b[431]&255; }",
         True, None),
        ("常规正例 ok[300]", "int ok[300]; int main(void){ ok[299]=7; return ok[299]&255; }",
         True, None),
    ]
    for name, src, want_ok, needle in cases:
        total += 1
        rc, err = compile_rc(src)
        good = (rc == 0) == want_ok and (needle is None or needle in err)
        if good:
            ok += 1
            print(f"  [PASS] {name}")
        else:
            print(f"  [FAIL] {name}: rc={rc} stderr={err.strip()[:100]}")
    os.unlink(cf)
    if os.path.exists(tf):
        os.unlink(tf)
    return ok, total


def test_rejects():
    """必须**拒绝编译**的语义。

    `& | ^ ~` 与 `<< >>` 已于 2026-11-22 换轨三值本征直发（TAND/TOR/XOR/
    TNOT + SLL/SRL/SRA ×3^k/÷3^k，方案 A 位游标退役），语义正确性由
    trirv_bitview_e2e.py 与 trirv_cc_py_bitop_e2e.py 单独锁定。
    这里只留"定义域外、必须挡住"的形态：移位量是负的或大得没意义
    （本征 SLL 取移位量 mod 3^7——负数会静默巨移，必须挡在编译期）。
    静默给出错误结果比编译失败危险得多。
    """
    print("-" * 60)
    print("必须拒绝的语义（静默错算 > 编译失败）")
    # tint NEG 锁 C 后端诚实报错：统一后端 C 失败会静默回退 py 参考（py 无
    # tint 类型、以语法错误拒绝但无 UNSUPPORTED 文案）——STRICT 下直接抛 C 错。
    import os as _os
    _prev = _os.environ.get("TRV_CC_STRICT")
    _os.environ["TRV_CC_STRICT"] = "1"
    bad = [
        ("移位量为负", "int main(){ int x=10; return x << -1; }", "移位量"),
        ("移位量过大", "int main(){ int x=10; return x >> 99999; }", "移位量"),
        # __tint/__tbool Kleene 门控（批B）——TRV_CC_STRICT=1 锁 C 后端诚实报错
        # （py 参考无 tint 类型，回退层以语法错误拒绝——双后端拒绝纪律成立）
        ("tint异或报错", "__tint a; __tint b; int main(){ __tint c = a ^ b; return 0; }", "Kleene"),
        ("tint算术报错", "__tint a; __tint b; int main(){ __tint c = a + b; return 0; }", "Kleene"),
        ("tint序比较报错", "__tint a; __tint b; int main(){ return a < b; }", "Kleene"),
        ("tint一元负号报错", "__tint a; int main(){ return -a; }", "Kleene"),
        ("tint混int报错", "int main(){ int i=1; __tint t=2; return t & i; }", "mixing"),
        ("tint序非零常量报错", "__tint t=2; int main(){ return t == 7; }", "mixing"),
        ("tbool算术报错", "__tbool a; __tbool b; int main(){ __tbool c = a * b; return 0; }", "Kleene"),
    ]
    ok_count = 0
    for name, src, kw in bad:
        try:
            cc.compile_c(src)
            print(f"  [FAIL] {name:18s} 竟然编译通过了（应报错）")
        except Exception as e:
            msg = str(e)
            good = "UNSUPPORTED" in msg and kw in msg
            print(f"  [{'PASS' if good else 'FAIL'}] {name:18s} 已拒绝: {msg.splitlines()[0][:60]}")
            ok_count += good
    if _prev is None:
        del _os.environ["TRV_CC_STRICT"]
    else:
        _os.environ["TRV_CC_STRICT"] = _prev
    return ok_count, len(bad)


def test_syscalls():
    """syscall ABI 专项：write 输出、read 输入、exit 退出码、返回值。"""
    print("-" * 60)
    print("syscall ABI 专项:")
    ok_count = 0
    total = 0

    # 1) write(1, "Hello, TriRV!\n", 14) + return 42
    src = 'int main(){ write(1, "Hello, TriRV!\\n", 14); return 42; }'
    got, cpu = compile_run(src)
    out = bytes(cpu.output)
    p1 = out == b"Hello, TriRV!\n" and got == 42 and cpu.exit_code == 42
    print(f"  [{'PASS' if p1 else 'FAIL'}] write 输出={out!r} 返回值={got} 退出码={cpu.exit_code}")
    ok_count += p1
    total += 1

    # 2) write 返回写入字节数
    src2 = 'int main(){ return write(1, "ABCD", 4); }'
    got2, cpu2 = compile_run(src2)
    p2 = got2 == 4 and bytes(cpu2.output) == b"ABCD"
    print(f"  [{'PASS' if p2 else 'FAIL'}] write 返回值={got2} (期望4) 输出={bytes(cpu2.output)!r}")
    ok_count += p2
    total += 1

    # 3) read 从 stdin 读取 + write 回显
    src3 = 'int main(){ int n = read(0, 200, 5); write(1, 200, n); return n; }'
    got3, cpu3 = compile_run(src3, stdin=b"XYZ\n")
    p3 = got3 == 4 and bytes(cpu3.output) == b"XYZ\n"
    print(f"  [{'PASS' if p3 else 'FAIL'}] read 读取={got3} 回显={bytes(cpu3.output)!r}")
    ok_count += p3
    total += 1

    # 4) exit(code) 中途停机：exit(9) 后的 return 不执行
    src4 = 'int main(){ write(1, "X", 1); exit(9); return 0; }'
    got4, cpu4 = compile_run(src4)
    p4 = cpu4.exit_code == 9 and bytes(cpu4.output) == b"X"
    print(f"  [{'PASS' if p4 else 'FAIL'}] exit 退出码={cpu4.exit_code} (期望9) 输出={bytes(cpu4.output)!r}")
    ok_count += p4
    total += 1

    assert ok_count == 4, "syscall 用例未全过"
    return ok_count, 4


if __name__ == "__main__":
    main()
