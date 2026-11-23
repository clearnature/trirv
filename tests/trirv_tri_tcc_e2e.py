#!/usr/bin/env python3
"""trirv_tri_tcc_e2e —— tri-tcc（TinyCC TriRV 后端）端到端契约（T-1）。

被测：~/ai-lab/tinycc 分支 trirv 的 trirv-tcc 交叉编译器（tri-gen.c/tri-link.c）。
管线：C 源 → trirv-tcc(-nostdlib) → TRV1 → 双 VM（PyVM × C VM）执行，
期望值 = C 语义的计算结果（写死在用例里，对齐 trirv_cc e2e 风格）。

覆盖（T-1.1/1.2 + T-1 收尾批次）：
  - ALU：+ - * / % 与全部六种比较（含 unsigned，物化/分支两路、等号边界）
  - 位运算三值本征（2026-11-22 方言拍板，方案 A 退役）：& | ^ = 逐 trit
    TAND(min)/TOR(max)/XOR(mod-3)，~ = TNOT（~0=-1）；代数律锁定
    （幂等/零元/对合/交换/德摩根 ✓，XOR 不自反锁定）
  - 移位本征：<< = ×3^k（SLLI/SLL）、>> = ÷3^k 向零截断（SRAI/SRA ≡ 除法）、
    无符号 raw（SRLI/SRL）、k≥45 恒 0
  - 常量装载（B2）：负常量借位双向、i32 窗口溢出走 3×20 分段
  - 链接（B3）：静态初值 base-243（≥243/负数/long long）、text 基址、
    UNDEF 符号/自定义节诚实报错（NEG 用例）
  - load/store 宽度：char/short/int、有符号/无符号（LB/LBU/LH/LHU/LW）
  - 控制流：if/else、while、for、递归、多实参（非交换序敏感）、函数指针

运行前提：~/ai-lab/tinycc 存在且已构建 trirv-tcc（TINYCC_PORT_PLAN.md T-0.2）；
缺失时全部 SKIP（不阻断全量回归）。
"""
import os
import struct
import subprocess
import sys

import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import trirv_vm as vm

HERE = os.path.dirname(os.path.abspath(__file__))
TCC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tinycc", "trirv-tcc")
CVM = os.path.join(HERE, "..", "vm", "bin", "trirv_vm")

passed = 0
failed = 0
skipped = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
    else:
        failed += 1
        print("FAIL %s: got=%r want=%r" % (name, got, want))


# (name, c_source, expected_exit&0xFF)

def _tlit(v, a):
    """python oracle：逐 trit Post literal（ISA §6.1 同口径；返回 45-trit 词）"""
    r = 0
    p = 1
    for _ in range(45):
        v, t = divmod(v, 3)
        r += ((2 - t) if a == 0 else (t + a) % 3) * p
        p *= 3
    return r


def _tlit_exit(v, a):
    """exit 口径：带符号值 &0xFF（坑实录 3b：词 > 半阈值即为负）"""
    w = _tlit(v, a)
    if w > (3 ** 45 - 1) // 2:
        w -= 3 ** 45
    return w & 0xFF

CASES = [
    ("常量返回", "int main(void){ return 42; }", 42),

    # ---- T-1.2 ALU ----
    ("加", "int main(void){ int a=1234; int b=567; return a+b; }", (1234+567)&0xFF),
    ("减", "int main(void){ int a=1000; int b=234; return a-b; }", (1000-234)&0xFF),
    ("乘", "int main(void){ int a=123; int b=456; return a*b; }", (123*456)&0xFF),
    ("除", "int main(void){ int a=100000; int b=7; return a/b; }", (100000//7)&0xFF),
    ("模", "int main(void){ int a=100000; int b=7; return a%b; }", (100000%7)&0xFF),
    # 位运算：三值本征（2026-11-22 方言拍板，方案 A 位游标退役）——& | ^ =
    # 逐 trit TAND(min)/TOR(max)/XOR((ta+tb) mod 3)，~ = TNOT（~0 = -1），
    # 移位 = ×3^k（SLL/SLLI）/ ÷3^k 向零截断（SRA/SRAI ≡ 除法口径）。
    # 期望值全部由本征 oracle 推导；gen_opic 已同步三值折叠——常量版用例
    # 第一次真正锁定后端/折叠口径一致。本征 XOR 不自反（a^a≠0）；
    # a^-1 = 逐 trit 数字递减 (t+2)%3，≠ TNOT（12^-1=-25 vs ~12=-13）。
    ("与逐tritmin", "int main(void){ int a=6; int b=4; return a & b; }", 3),
    ("或逐tritmax", "int main(void){ int a=2; int b=1; return a | b; }", 2),
    ("异或mod3", "int main(void){ int a=3; int b=1; return a ^ b; }", 4),
    ("与常量折叠", "int main(void){ return 12 & 10; }", 9),
    ("或常量折叠", "int main(void){ return 12 | 10; }", 13),
    ("异或常量折叠", "int main(void){ return 3 ^ 1; }", 4),
    ("与非零trit掩", "int main(void){ int a=0x1234; return a & 0xFF; }", 255),  # min 不清 trit：保留 0xFF 的全部非零 trit
    ("或组合", "int main(void){ int a=0x1200; return a | 0x34; }", 4615 & 0xFF),
    ("异或组合", "int main(void){ int a=0xFF; return a ^ 0x0F; }", 261 & 0xFF),
    ("分配组合", "int main(void){ int a=0xF0; int b=0x0F; int c=0xFF; return (a&b)|(a&c); }", 15),
    # 本征代数律锁定（min/max/TNOT(2-t) 是完整 De Morgan 代数）
    ("幂等律", "int main(void){ int a=123; return (a&a)==a; }", 1),
    ("零元律", "int main(void){ int a=123; return ((a|0)==a) && ((a^0)==a); }", 1),
    ("对合律", "int main(void){ int a=123; return (~(~a))==a; }", 1),
    ("交换律", "int main(void){ int a=0xF0; int b=0x0F; return (a&b)==(b&a); }", 1),
    ("德摩根与或", "int main(void){ int a=123; int b=456; return (~(a&b))==((~a)|(~b)); }", 1),
    ("德摩根或与", "int main(void){ int a=123; int b=456; return (~(a|b))==((~a)&(~b)); }", 1),
    ("XOR不自反", "int main(void){ int a=123; return (a^a)!=0; }", 1),  # mod-3 固有
    ("XOR负1递减", "int main(void){ int a=12; return (a^-1)==-25; }", 1),  # (t+2)%3 ≠ TNOT
    ("TNOT等负1减", "int main(void){ int a=12; return (~a)==-13; }", 1),
    ("回绕锁定", "int main(void){ int z=0; return (~z)==(-1); }", 1),  # 本征 TNOT(0) = 全 trit 2 = -1（方案 A 时代此例为 0）
    # 6561 & 0xFF = 161（exit 低 8 位）；×3^45 ≡ 0 mod 3^45
    ("左移", "int main(void){ return 1 << 8; }", 6561 & 0xFF),
    ("移位K过45清零", "int main(void){ return 1 << 45; }", 0),
    ("逻辑右移", "int main(void){ unsigned u = 0x80000000u; return (int)(u >> 31); }", 0),  # raw ÷3^31 = 0
    ("算术右移", "int main(void){ int a = -64; return a >> 3; }", 254),   # trunc(-64/27) = -2
    ("算术右移截断对照", "int main(void){ int a = -65; return a >> 3; }", 254),  # trunc(-65/27) = -2（floor 会给 -3——锁定截断口径）
    ("变量左移", "int main(void){ int s=10; int i=2; return s<<i; }", 90),
    ("变量右移", "int main(void){ int s=10; int i=2; return s>>i; }", 1),
    ("变量算术右移", "int main(void){ int a=-65; int i=3; return a>>i; }", 254),
    ("变量无符号右移", "int main(void){ unsigned u=0x80000000u; int i=31; return u>>i; }", 0),
    ("负数", "int main(void){ return -100; }", (-100) & 0xFF),

    # ---- 比较（真=1） ----
    ("相等真", "int main(void){ return 3 == 3; }", 1),
    ("相等假", "int main(void){ return 3 == 4; }", 0),
    ("不等", "int main(void){ return 3 != 4; }", 1),
    ("小于", "int main(void){ return 3 < 4; }", 1),
    ("大于等于", "int main(void){ return 4 >= 4; }", 1),
    ("无符号比较", "int main(void){ unsigned a = 3000000000u; return a > 5; }", 1),
    # 物化比较（B1：等号边界 + 假值侧，暴露 D2/D3/D4）
    ("GT等号边界", "int main(void){ int a=3; int b=3; return a>b; }", 0),
    ("GE假", "int main(void){ int a=3; int b=4; return a>=b; }", 0),
    ("LE假", "int main(void){ int a=4; int b=3; return a<=b; }", 0),
    ("无符号GT等号", "int main(void){ unsigned a=3; unsigned b=3; return a>b; }", 0),
    ("无符号GE假", "int main(void){ unsigned a=3; unsigned b=4; return a>=b; }", 0),
    ("无符号LE假", "int main(void){ unsigned a=4; unsigned b=3; return a<=b; }", 0),
    ("NE物化", "int main(void){ int a=3; int b=4; return a!=b; }", 1),
    ("EQ物化假", "int main(void){ int a=3; int b=4; return a==b; }", 0),
    ("物化存变量", "int main(void){ int a=3; int b=4; int r=(a!=b); return r+10*(a==b); }", 1),

    # ---- T-1.1 load/store 宽度 ----
    ("char 有符号负", "int main(void){ signed char c = -5; return c; }", (-5)&0xFF),
    ("char 无符号回绕", "int main(void){ unsigned char c = 200; return c; }", 200),
    ("short 宽度", "int main(void){ short s = 12345; return s; }", 12345&0xFF),
    ("short 负", "int main(void){ short s = -1000; return s; }", (-1000)&0xFF),
    ("char 数组读写", "int main(void){ char b[4]; b[0]=65; b[1]=66; b[3]=68; return b[0]+b[1]+b[3]-b[2]; }", (65+66+68)&0xFF),
    ("short 数组", "int main(void){ short a[3]; a[0]=1000; a[1]=2000; a[2]=a[0]+a[1]; return a[2]; }", 3000&0xFF),
    ("int 数组", "int main(void){ int a[4]; int i=0; while(i<4){ a[i]=i*i; i=i+1; } return a[1]+a[2]+a[3]; }", 14),

    # ---- 控制流 ----
    ("while 求和", "int main(void){ int i=0; int s=0; while(i<3){ s=s+i; i=i+1; } return s; }", 3),
    ("for 求和", "int main(void){ int s=0; int i; for(i=1;i<=20;i=i+1) s=s+i; return s; }", 210),
    ("if-else 双路径", "int main(void){ int x=5; if(x>3) return 1; else return 2; }", 1),
    ("递归 fib10", "int fib(int n){ if(n<2) return n; return fib(n-1)+fib(n-2); } int main(void){ return fib(10); }", 55),
    ("三实参", "int add3(int a,int b,int c){ return a+b+c; } int main(void){ return add3(100,20,3); }", 123),
    ("深实参调用", "int f8(int a,int b,int c,int d,int e,int f,int g,int h){ return a+b+c+d+e+f+g+h; } int main(void){ return f8(1,2,3,4,5,6,7,8); }", 36),
    # 实参序敏感（B1：暴露 D1 倒装）
    ("非交换双实参", "int sub(int a,int b){ return a-b; } int main(void){ return sub(10,4); }", 6),
    ("首实参返回", "int id(int a,int b){ return a; } int main(void){ return id(5,3); }", 5),
    ("第三实参", "int id3(int a,int b,int c){ return c; } int main(void){ return id3(5,4,3); }", 3),
    # 逻辑运算取值物化（B1：暴露 D5 未初始化 fc）
    ("且取值真", "int main(void){ int a=1; return (a && a<5); }", 1),
    ("且取值假", "int main(void){ int a=8; return (a && a<5); }", 0),
    ("或取值", "int main(void){ int a=3; return (a>5 || a==3); }", 1),
    # 函数指针（B1：暴露 D8 JALR rd==rs1）
    ("函数指针调用", "int f(void){ return 7; } int main(void){ int (*fp)(void)=f; return fp(); }", 7),
    ("全局函数指针调用", "int f(void){ return 7; } int (*gfp)(void)=f; int main(void){ return gfp(); }", 7),

    # ---- 常量装载（B2：D6） ----
    ("负常量借位上", "int main(void){ int x=-1300; return -x; }", 1300&0xFF),
    ("负常量借位下", "int main(void){ int x=-1094; return -x; }", 1094&0xFF),
    ("窗口溢出正", "int main(void){ int x=2000000007; return x%1000; }", 7),
    ("窗口溢出负", "int main(void){ int x=-2000000007; return -x%1000; }", 7),
    ("运行时取负", "int main(void){ int a=5; return -a; }", (-5)&0xFF),
    ("llong大常量", "int main(void){ long long x=4294967296; return (int)(x%1000); }", (4294967296%1000)&0xFF),

    # ---- 全局 ----
    ("全局初始化读写", "int g = 7; int main(void){ g = g + 1; return g; }", 8),
    ("全局跨函数", "int g = 100; void bump(void){ g = g + 5; } int main(void){ bump(); bump(); return g; }", 110),
    ("全局数组", "int ga[3] = {10, 20, 30}; int main(void){ return ga[0]+ga[1]+ga[2]; }", 60),
    ("char* 字符串", "char *m = \"AB\"; int main(void){ return m[0]+m[1]; }", (65+66)&0xFF),
    # 静态初值 base-243（B3：≥243/负数/llong）
    ("全局int千", "int g = 1000; int main(void){ return g; }", 1000&0xFF),
    ("全局负初值", "int g = -5; int main(void){ return -g; }", 5),
    ("全局llong初值", "long long g = 5000000007; int main(void){ return (int)(g%1000); }", 7),
    ("函数指针地址一致", "int f(void){ return 7; } int main(void){ int (*fp)(void)=&f; return (fp==&f)?fp():0; }", 7),

    # ---- 组合 ----
    ("表达式嵌套", "int main(void){ return (1+2)*(3+4)-(5-3); }", 19),
    ("混合宽度", "int main(void){ char c = 10; short s = 1000; int i = 100000; return c + s + (i/1000); }", (10+1000+100)&0xFF),

    # ---- T-1.6 浮点（F 扩展 TF45；避开 .5 边界——VM FCVT.W 是 round 不是 trunc） ----
    ("浮点加", "int main(void){ double a=1.5; double b=2.5; return (int)(a+b); }", 4),
    ("浮点乘", "int main(void){ double a=1.5; double b=2.0; return (int)(a*b); }", 3),
    ("浮点除", "int main(void){ double a=6.0; double b=2.0; return (int)(a/b); }", 3),
    ("float域", "int main(void){ float a=0.5; float b=0.25; return (int)((a+b)*4); }", 3),
    ("浮点大于", "int main(void){ double a=2.0; return a>1.5; }", 1),
    ("浮点小于", "int main(void){ double a=2.0; return a<1.5; }", 0),
    ("浮点相等", "int main(void){ double a=2.5; return a==2.5; }", 1),
    ("int转浮点", "int main(void){ int x=8; double d=x; return (int)(d/2); }", 4),
    ("浮点负数", "int main(void){ double a=-3.0; return (int)a; }", (-3)&0xFF),
    ("浮点实参", "double add(double a, double b){ return a+b; } int main(void){ return (int)add(1.5,2.5); }", 4),
    ("浮点全局", "double g=2.5; int main(void){ return (int)(g*2); }", 5),
    ("浮点组合", "int main(void){ return (int)(1.5*2.0+1.0); }", 4),
    ("FCVT.W.T截断", "int main(void){ double d=3.7; return (int)d; }", 3),   # 拍板：rtz 变体，双轨合一
    ("浮点大常量", "int main(void){ double d=1e8; return (int)(d/1e6); }", 100),   # TF45 扩域：精确整数常量
    ("浮点小常量", "int main(void){ double d=1e-9; return (int)(d*1e9+0.5); }", 1),  # 扩域：1e-9 装载+乘法+round
    ("浮点超域正", "int main(void){ double d=1e300; return d>0; }", 1),   # 扩域：exp3≈628
    ("浮点超域负", "int main(void){ double d=-1e-300; return d<0; }", 1),  # 扩域：exp3≈-628

    # ---- __tint 三值逻辑类型（批次 C1：TAND/TOR/TNOT 直发，TERNARY_COMPAT_PLAN §3.1）----
    # Kleene 真值表：0=假 1=不定 2=真；TAND=min TOR=max TNOT=2-x
    ("tint全组合遍历", "__tint v[3]; int main(void){ v[0]=(__tint)0; v[1]=(__tint)1; v[2]=(__tint)2; "
     "int f=0; for(int i=0;i<3;i++){ for(int j=0;j<3;j++){ __tint a=v[i]; __tint b=v[j]; "
     "if((int)(a&b) != ((i<j)?i:j)) f|=1; if((int)(a|b) != ((i>j)?i:j)) f|=2; } } return f; }", 0),
    ("tint或max", "__tint v[3]; int main(void){ v[0]=(__tint)0; v[1]=(__tint)1; v[2]=(__tint)2; "
     "return (int)(v[2]|v[0])*100 + (int)(v[0]|v[1])*10 + (int)(v[1]|v[2]); }", (2*100+1*10+2)),
    ("tint与min", "__tint v[3]; int main(void){ v[0]=(__tint)0; v[1]=(__tint)1; v[2]=(__tint)2; "
     "return (int)(v[2]&v[0])*100 + (int)(v[0]&v[1])*10 + (int)(v[1]&v[2]); }", (0*100+0*10+1)),
    ("tint非", "__tint v[3]; int main(void){ v[0]=(__tint)0; v[1]=(__tint)1; v[2]=(__tint)2; "
     "return (int)(~v[0])*100 + (int)(~v[1])*10 + (int)(~v[2]); }", ((-1)*100 + (-2)*10 + (-3)) & 0xFF),  # 带符号: −123→133
    ("tint德摩根", "__tint v[3]; int main(void){ v[0]=(__tint)0; v[1]=(__tint)1; v[2]=(__tint)2; "
     "int f=0; for(int i=0;i<3;i++){ for(int j=0;j<3;j++){ __tint a=v[i]; __tint b=v[j]; "
     "int l=(int)(~(a&b)); int r=(int)((~a)|(~b)); if(l!=r) f|=1; } } return f; }", 0),
    ("tint相等", "__tint v[3]; int main(void){ v[0]=(__tint)0; v[1]=(__tint)1; v[2]=(__tint)2; "
     "int f=0; for(int i=0;i<3;i++) for(int j=0;j<3;j++) if(((int)(v[i]==v[j])) != (i==j)) f|=4; "
     "return f + 100*((int)(v[1]!=v[2])); }", 100),
    ("tint真值判断", "__tint v[3]; int main(void){ v[0]=(__tint)0; v[1]=(__tint)1; v[2]=(__tint)2; "
     "int f=0; if(v[0]) f|=1; if(!v[1]) f|=2; if(!v[2]) f|=4; if(v[2]) f|=8; return f; }", 8),
    ("tint传参返回", "__tint tmax(__tint a, __tint b){ return a|b; } int main(void){ "
     "__tint v[3]; v[0]=(__tint)0; v[1]=(__tint)1; v[2]=(__tint)2; "
     "return (int)tmax(v[0],v[2]) + (int)tmax(v[1],v[1])*10; }", (2+1*10)),
    # 全 trit1 = 每 tryte 121（1+3+9+27+81）= (3^45-1)/2 正大数；全 trit2 = 每 tryte 242 = 3^45-1（带符号 -1）
    # 坑实录：曾写 p[i]=1/p[i]=2——那是 char 值 1/2（tryte 十进制 1/2，仅低 trit 非 0），
    # 不是全 trit 1/2；TNOT 语义与 VM 均正确，2026-11-22 用例口径修正。
    ("tint全trit1不变", "int main(void){ __tint x; char *p=(char*)&x; "
     "for(int i=0;i<9;i++) p[i]=121; return ((int)x > 0) + ((int)(~x) == (int)x); }", 2),  # TNOT 不变
    ("tint全trit2清零", "int main(void){ __tint x; char *p=(char*)&x; "
     "for(int i=0;i<9;i++) p[i]=242; return ((int)x == -1) + ((int)(~x) == 0); }", 2),  # TNOT 清零

    # ---- char* punning 非 TINT 锁模型（a946373 登记「for+指针元素写 body 丢失」专项销案：
    #      幻影——登记时对照程序同踩 p[i]=2 期望值坑（tryte 值 2 = 00002₃ 仅低 trit 非 0，
    #      并非全 trit 2），输出「不对」反推 BLT 错位，前提为假故三重假设不收束。
    #      2026-11-22 复核：该组合在 c1bda721（登记时刻）与 5c6a3ea7（现行）双编译器、
    #      for 四式/while/do-while/嵌套/break/continue/指针行走 20+ 形状 × 双 VM 零异常。
    #      本组锁正向口径 + 反向锚（旧错误期望）+ body 计数反证 + exit &0xFF 口径。 ----
    ("charpun_for全负1", "int main(void){ int x; char *p=(char*)&x; "
     "for(int i=0;i<9;i++){ p[i]=242; } return ((int)x==-1)*10 + 1; }", 11),
    ("charpun_while全正大", "int main(void){ int x; char *p=(char*)&x; int i=0; "
     "while(i<9){ p[i]=121; i=i+1; } return ((int)x>0)*100 + ((int)x==-1); }", 100),  # 全 trit1 = +max 非 -1
    ("charpun_单写等值", "int main(void){ int x; char *p=(char*)&x; "
     "p[0]=242;p[1]=242;p[2]=242;p[3]=242;p[4]=242;p[5]=242;p[6]=242;p[7]=242;p[8]=242; "
     "return ((int)x==-1); }", 1),
    ("charpun_旧模型反向锁", "int main(void){ int x; char *p=(char*)&x; "
     "for(int i=0;i<9;i++){ p[i]=2; } return ((int)x==-1); }", 0),  # 旧错误期望的锚：tryte 值 2 非 -1
    ("charpun_for计数", "int main(void){ int x; char *p=(char*)&x; int s=0; "
     "for(int i=0;i<9;i++){ p[i]=242; s=s+1; } return ((int)x==-1)*10+s; }", 19),  # body 跑满 9 次——「body 丢失」反证
    ("charpun_raw负1出口", "int main(void){ int x; char *p=(char*)&x; "
     "for(int i=0;i<9;i++){ p[i]=242; } return (int)x; }", (-1)&0xFF),  # exit 口径 &0xFF：-1→255 非 %256→82

    # ---- 大整数常量通道（T-2.4 诊断实锤：>2^32 的 VT_INT 字面量曾被 gen_cast
    #      0xffffffff 掩码截断——TriRV int=45 trit 全域需 64 位常量通道） ----
    ("int常量2^32边界", "int mod97(int x){ return x - x/97*97; } int main(void){ int x = 5000000000; return mod97(x); }", 5000000000 % 97),
    ("int常量3^34", "int mod97(int x){ return x - x/97*97; } int main(void){ int x = 16677181699666569; return mod97(x); }", 16677181699666569 % 97),
    ("int常量3^38", "int mod97(int x){ return x - x/97*97; } int main(void){ int x = 1350851717672992089; return mod97(x); }", 1350851717672992089 % 97),
    ("int常量静态初值", "int g = 16677181699666569; int mod97(int x){ return x - x/97*97; } int main(void){ return mod97(g); }", 16677181699666569 % 97),
    ("int常量1e16系数", "int g = 10000000000000000; int main(void){ return g - g/97*97; }", 10**16 % 97),
    ("负多段常量", "int main(void){ int t = -14130; return t > -14131; }", 1),   # 3^45-14130 三段含中段 0
    ("负多段常量2", "int main(void){ int t = -14130; return t < -14000; }", 1),
    ("负多段比较", "int main(void){ int t = -14130; int m = 1; return (m > t) * 7; }", 7),
    ("FCVT.W.T负数", "int main(void){ double d=-3.7; return (int)d; }", (-3)&0xFF),  # 向零：-3（round 给 -4）
    ("FCVT.W.T半途", "int main(void){ double d=3.5; return (int)d; }", 3),   # 向零：3（round-half-even 给 4）

    # ---- char/short mod-243 全轨（拍板 2026-11-21：char=1 tryte，截断 mod-243，
    #      unsigned 0..242 / signed -121..121；与硬件 SB/LB/LBU、trirv_cc、
    #      charsign 契约一致；ISO C 8-bit 语义不保留） ----
    ("char常量折叠", "int main(void){ return (char)300; }", 57),        # RED: 折叠曾 0xff→44
    ("char 243边界", "int main(void){ char c=243; return c; }", 0),
    ("signed char 200", "int main(void){ signed char c=200; return c; }", (200-243)&0xFF),
    ("char R值窄化", "int main(void){ int v=300; return (char)v + 1; }", 58),  # RED: shift 往返 8bit→45
    ("char 算术提升", "int main(void){ char a=250; char b=10; return a+b; }", 17),
    ("short mod-59049", "int main(void){ int v=60000; return (short)v; }", (60000 % 59049)&0xFF),  # RED: 8/16bit→96

    # ---- struct 9-对齐（拍板 2026-11-21：成员起始一律 9-对齐、尺寸 9-圆整，
    #      对齐 ABI §6 与 trirv_cc；char 数组成员元素连续、起始 9-对齐） ----
    ("struct 成员9对齐", "struct S{char c; int *p;}; struct S s; int main(void){ return (int)((char*)&s.p - (char*)&s); }", 9),
    ("struct 尺寸", "struct S{char c; int *p;}; int main(void){ return sizeof(struct S); }", 18),
    ("struct 纯标量间距", "struct P{int a; int b;}; struct P p; int main(void){ return (int)((char*)&p.b - (char*)&p.a); }", 9),
    ("struct 数组元素步长", "struct S{char c; int *p;}; struct S arr[2]; int main(void){ return (int)((char*)&arr[1] - (char*)&arr[0]); }", 18),
    ("struct char数组成员", "struct S{char buf[4]; int i;}; struct S s; int main(void){ return (int)((char*)&s.i - (char*)&s); }", 9),
    ("struct 嵌套", "struct I{int x;}; struct O{char c; struct I in;}; struct O o; int main(void){ return (int)((char*)&o.in - (char*)&o); }", 9),

    # ---- __tint2 4-tryte/20-trit 档（批次 C2 方案 A：LW4/SW4 存取 + mod 3^20
    #      窄化，2026-11-23 拍板；sizeof=4，寄存器内 int 语义）----
    ("tint2声明sizeof", "__tint2 x = 7; int main(void){ if (sizeof(__tint2)!=4) return 9; return (int)x; }", 7),
    ("tint2全局读", "__tint2 g = 100000; int main(void){ if ((int)g == 100000) return 1; return 2; }", 1),
    ("tint2负值往返", "int main(void){ __tint2 x = -123456; if (x == -123456) return 1; return 2; }", 1),
    ("tint2存储截断", "int main(void){ __tint2 x = 5000000000LL; if (x == 1513215599) return 1; return 2; }", 1),
    ("tint2过半转负", "int main(void){ __tint2 x = 3000000125LL; if (x == -486784276) return 1; return 2; }", 1),
    ("tint2数组步长", "__tint2 a[3]; int main(void){ for(int i=0;i<3;i++) a[i]=i*100000; "
     "if (a[1]==100000 && a[2]==200000 && sizeof(a)==12) return 1; return 2; }", 1),
    ("tint2全局数组负初值", "__tint2 g[2] = {7, -7}; int main(void){ if (g[0]==7 && g[1]==-7) return 1; return 2; }", 1),
    ("tint2传参返回", "__tint2 id(__tint2 v){ return v; } int main(void){ __tint2 x = 123456; "
     "if (id(x)==123456) return 1; return 2; }", 1),
    ("tint2显式右值窄化", "int main(void){ int big = 4000000000; if ((__tint2)big == 513215599) return 1; return 2; }", 1),
    ("tint2存储路径截断", "int main(void){ int big = 4000000000; __tint2 x = big; "
     "if (x == 513215599) return 1; return 2; }", 1),
    ("tint2混合算术", "int main(void){ __tint2 x = 1000; int y = x + 1; if (y == 1001) return 1; return 2; }", 1),
    ("tint2结构体成员", "struct S { char c; __tint2 v; }; int main(void){ struct S s; s.c = 3; s.v = 5000000000LL; "
     "if (s.v == 1513215599 && s.c == 3 && sizeof(struct S)==8) return 1; return 2; }", 1),

    # ---- __tbool 1-tryte Kleene 布尔（批次 C2-c：VT_BYTE|VT_TINT 复用 C1 门
    #      语义；VT_DEFSIGN 保证有符号 LB——plain char 无符号盖章会破坏精确往返）----
    ("tbool与或真值表", "__tbool v[3]; int main(void){ v[0]=(__tbool)0; v[1]=(__tbool)1; v[2]=(__tbool)2; "
     "int f=0; for(int i=0;i<3;i++){ for(int j=0;j<3;j++){ __tbool a=v[i]; __tbool b=v[j]; "
     "if ((int)(a&b) != (i<j?i:j)) f|=1; if ((int)(a|b) != (i>j?i:j)) f|=2; }} return f; }", 0),
    ("tbool非", "__tbool v[3]; int main(void){ v[0]=(__tbool)0; v[1]=(__tbool)1; v[2]=(__tbool)2; "
     "return (int)(~v[0])*100 + (int)(~v[1])*10 + (int)(~v[2]); }", ((-1)*100 + (-2)*10 + (-3)) & 0xFF),
    ("tbool德摩根", "__tbool v[3]; int main(void){ v[0]=(__tbool)0; v[1]=(__tbool)1; v[2]=(__tbool)2; "
     "int f=0; for(int i=0;i<3;i++){ for(int j=0;j<3;j++){ __tbool a=v[i]; __tbool b=v[j]; "
     "int l=(int)(~(a&b)); int r=(int)((~a)|(~b)); if(l!=r) f|=1; } } return f; }", 0),
    ("tbool相等", "__tbool v[3]; int main(void){ v[0]=(__tbool)0; v[1]=(__tbool)1; v[2]=(__tbool)2; "
     "int f=0; for(int i=0;i<3;i++) for(int j=0;j<3;j++) if(((int)(v[i]==v[j])) != (i==j)) f|=4; "
     "return f + 100*((int)(v[1]!=v[2])); }", 100),
    ("tbool真值判断", "__tbool v[3]; int main(void){ v[0]=(__tbool)0; v[1]=(__tbool)1; v[2]=(__tbool)2; "
     "int f=0; if(v[0]) f|=1; if(!v[1]) f|=2; if(!v[2]) f|=4; if(v[2]) f|=8; return f; }", 8),
    ("tbool存储往返", "__tbool v[3]; int main(void){ v[0]=(__tbool)0; v[1]=(__tbool)1; v[2]=(__tbool)2; "
     "if (v[0]==(__tbool)0 && v[1]==(__tbool)1 && v[2]==(__tbool)2) return 1; return 2; }", 1),
    ("tbool负词精确往返", "__tbool n; int main(void){ n = ~(__tbool)0; if ((int)n == -1) return 1; return 2; }", 1),
    ("tbool尺寸数组", "int main(void){ __tbool a[2]; if (sizeof(__tbool)!=1) return 9; if (sizeof(a)!=2) return 8; return 1; }", 1),
    ("tbool数组步长", "__tbool a[2]; int main(void){ a[0]=(__tbool)1; a[1]=(__tbool)2; if ((int)a[0]==1 && (int)a[1]==2) return 1; return 2; }", 1),
    ("tbool传参返回", "__tbool tmax(__tbool a, __tbool b){ return a|b; } int main(void){ __tbool v[3]; "
     "v[0]=(__tbool)0; v[1]=(__tbool)1; v[2]=(__tbool)2; int f=0; "
     "for(int i=0;i<3;i++) for(int j=0;j<3;j++) if ((int)tmax(v[i],v[j]) != (i>j?i:j)) f|=1; return f; }", 0),
    ("tbool与tint混用", "__tint t=(__tint)2; __tbool b=(__tbool)1; int main(void){ return (int)(t & b); }", 1),

    # ---- T-LIT 文字运算内建（2026-11-23 内建拍板：__builtin_tri_lit(x,a)，
    #      a 编译期常量 0..2 直发 LIT0/1/2；ternary-isa-design.md §6.1）----
    ("tlit内建后继", "int main(void){ return (int)__builtin_tri_lit(729, 1); }", _tlit_exit(729, 1)),
    ("tlit内建前驱", "int main(void){ return (int)__builtin_tri_lit(729, 2); }", _tlit_exit(729, 2)),
    ("tlit内建补", "int main(void){ return (int)__builtin_tri_lit(729, 0); }", _tlit_exit(729, 0)),
    ("tlit嵌套恒等", "int main(void){ int x = __builtin_tri_lit(__builtin_tri_lit(729, 1), 2); "
     "return (x == 729) ? 1 : 0; }", 1),
    ("tlit变量操作数", "int main(void){ int x = 729; int y = __builtin_tri_lit(x, 1); "
     "int z = __builtin_tri_lit(y, 1); int w = __builtin_tri_lit(x, 2); "
     "return (z == w) ? 1 : 0; }", 1),  # LIT1^2=LIT2 全在程序内自洽（>uint64
     # 巨常量不可作 C 字面量——坑实录 2）
    ("tlit负词后继", "int main(void){ int x = -1; int y = __builtin_tri_lit(x, 1); return (y == 0) ? 1 : 0; }", 1),
]

# 多 TU 用例（T-2.1）：(name, [src1, src2, ...], expected) —— 同进程多文件编译，
# 符号经 symtab 解析（声明在 main TU / 定义在后续 TU，含 static 跨 TU 边界）
MULTI_CASES = [
    ("多TU跨文件调用", ["int dbl2(int x){ return x+x; }\n",
                        "int dbl2(int x);\nint main(void){ return dbl2(21); }\n"], 42),
    ("多TU后续TU带static", ["static int h(int a){ return a*2; }\nint dbl(int x){ return h(x); }\n",
                            "int dbl(int x);\nint main(void){ return dbl(21); }\n"], 42),
    ("多TU共享全局", ["int g_counter = 100;\nint add_n(int x) { g_counter = g_counter + x; return g_counter; }\n",
                      "extern int g_counter;\nint add_n(int x);\nint main(void){ return add_n(5) + (g_counter == 105); }\n"], 106),
    ("多TU三文件", ["int f3(void){ return 3; }\n",
                    "int f3(void);\nint f4(void){ return f3()+1; }\n",
                    "int f4(void);\nint main(void){ return f4(); }\n"], 4),
]

# (name, c_source, expected_error_substring) —— 编译必须失败且 stderr 含子串
NEG_CASES = [
    ("未解析符号", "extern int zzz; int main(void){ return (int)(char*)&zzz; }", "unresolved"),
    ("未解析数据重定位", "extern int zzz; char *p = (char*)&zzz; int main(void){ return p != 0; }", "unresolved"),
    ("自定义节", "__attribute__((section(\".foo\"))) int g = 7; int main(void){ return g; }", "section"),
    ("移位量超限", "int main(void){ int x=1; return x << 5000; }", "shift"),
    ("移位量负", "int main(void){ int x=1; return x >> -1; }", "shift"),
    ("tint异或报错", "__tint a; __tint b; int main(void){ __tint c = a ^ b; return 0; }", "tint"),
    ("tint小于报错", "__tint a; __tint b; int main(void){ return a < b; }", "tint"),
    ("tint算术报错", "__tint a; __tint b; int main(void){ __tint c = a + b; return 0; }", "tint"),
    ("tint隐式混算报错", "int main(void){ int i=1; __tint t=(__tint)2; return (int)(i & t); }", "tint"),
    ("tint2混tint算术报错", "int main(void){ __tint a=(__tint)1; __tint2 b=1; __tint c = a + b; return 0; }", "tint"),
    ("tint2混tint位运算报错", "int main(void){ __tint a=(__tint)1; __tint2 b=1; return (int)(a & b); }", "tint"),
    ("tbool算术报错", "__tbool a; __tbool b; int main(void){ __tbool c = a + b; return 0; }", "tint"),
    ("tbool序比较报错", "__tbool a; __tbool b; int main(void){ return a < b; }", "tint"),
    ("tbool混int报错", "__tbool a=(__tbool)1; int main(void){ int i=1; return (int)(a & i); }", "tint"),
    ("tlit超界a报错", "int main(void){ return __builtin_tri_lit(1, 3); }", "tri_lit"),
    ("tlit非常量a报错", "int main(void){ int a=1; return __builtin_tri_lit(1, a); }", "constant expression"),
]


def load_trv(path):
    data = open(path, "rb").read()
    n_inst, n_seg = struct.unpack_from("<II", data, 8)
    prog = list(struct.unpack_from("<%dI" % n_inst, data, 16))
    off = 16 + 4 * n_inst
    mem = {}
    for _ in range(n_seg):
        addr, ln = struct.unpack_from("<II", data, off)
        off += 8
        mem[addr] = list(data[off:off + ln])
        off += ln
    return prog, mem


def main():
    global skipped, passed, failed
    # __tint 特性检测：未实现时 TINT 组 SKIP（TERNARY_COMPAT_PLAN 批次 C1 待专项）
    with open("/tmp/_tint_probe.c", "w") as _f:
        _f.write("__tint x;\nint main(void){ return (__tint)0; }\n")
    _r = subprocess.run([TCC, "-nostdlib", "/tmp/_tint_probe.c", "-o", "/tmp/_tint_probe.trv"],
                        capture_output=True, text=True, timeout=30)
    _tint_ok = _r.returncode == 0
    if not _tint_ok:
        print("-- __tint 未实现：TINT 组 SKIP（TERNARY_COMPAT_PLAN 批次 C1 待专项）--")
    if not os.path.exists(TCC):
        print("tri-tcc 未构建（%s 不存在）—— 全部 SKIP" % TCC)
        print("\ntri-tcc 契约：0 PASS / 0 FAIL / %d 用例" % (len(CASES) + len(NEG_CASES)))
        sys.exit(0)

    for name, src, want in CASES:
        if not _tint_ok and name.startswith(("tint",)):
            skipped += 1
            continue
        cpath = "/tmp/tri_e2e_%s.c" % abs(hash(name))
        tpath = "/tmp/tri_e2e_%s.trv" % abs(hash(name))
        with open(cpath, "w") as f:
            f.write(src)
        r = subprocess.run([TCC, "-nostdlib", cpath, "-o", tpath],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            check("%s [编译]" % name, r.stderr.strip()[:120], "")
            continue
        # C VM
        rr = subprocess.run([CVM, tpath], capture_output=True, text=True, timeout=60)
        ce = rr.stderr.split("exit=")[-1].split()[0] if "exit=" in rr.stderr else "?"
        check("%s [CVM]" % name, int(ce) & 0xFF if ce != "?" else "?", want)
        # PyVM
        prog, mem = load_trv(tpath)
        c = vm.VM(prog, mem_init=mem).run(max_steps=50000000)
        check("%s [PyVM]" % name, c.exit_code & 0xFF, want)

    # 多 TU：同进程多源文件编译（T-2.1 前置：符号进程内解析）
    for name, srcs, want in MULTI_CASES:
        cpaths = []
        tpath = "/tmp/tri_e2e_multi_%s.trv" % abs(hash(name))
        for k, s in enumerate(srcs):
            cp = "/tmp/tri_e2e_multi_%s_%d.c" % (abs(hash(name)), k)
            with open(cp, "w") as f:
                f.write(s)
            cpaths.append(cp)
        r = subprocess.run([TCC, "-nostdlib"] + cpaths + ["-o", tpath],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            check("%s [编译]" % name, r.stderr.strip()[:120], "")
            continue
        rr = subprocess.run([CVM, tpath], capture_output=True, text=True, timeout=60)
        ce = rr.stderr.split("exit=")[-1].split()[0] if "exit=" in rr.stderr else "?"
        check("%s [CVM]" % name, int(ce) & 0xFF if ce != "?" else "?", want)
        prog, mem = load_trv(tpath)
        c = vm.VM(prog, mem_init=mem).run(max_steps=50000000)
        check("%s [PyVM]" % name, c.exit_code & 0xFF, want)

    # NEG：编译必须失败且报错信息含子串（诚实报错契约）
    for name, src, sub in NEG_CASES:
        if not _tint_ok and name.startswith(("tint",)):
            skipped += 1
            continue
        cpath = "/tmp/tri_e2e_neg_%s.c" % abs(hash(name))
        tpath = "/tmp/tri_e2e_neg_%s.trv" % abs(hash(name))
        with open(cpath, "w") as f:
            f.write(src)
        r = subprocess.run([TCC, "-nostdlib", cpath, "-o", tpath],
                           capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            check("%s [NEG须报错]" % name, "编译意外成功", "")
        elif sub in r.stderr:
            passed += 1
        else:
            failed += 1
            print("FAIL %s [NEG]: 报错信息缺 %r：%r" % (name, sub, r.stderr.strip()[:120]))

    print("\ntri-tcc 契约：%d PASS / %d FAIL / %d 用例（含 %d NEG + %d 多TU）"
          % (passed, failed, len(CASES) + len(NEG_CASES) + len(MULTI_CASES),
             len(NEG_CASES), len(MULTI_CASES)))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
