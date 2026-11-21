/*
 * 无除法器原语等价性验证：把 trirv_vm.c 里的 divmod_word/umod_M/shr3/umod2187
 * 与 C 原生 128 位运算（作为参考）逐项对比。
 * 编译：gcc -O2 -o trirv_div_verify trirv_div_verify.c
 */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>

static __uint128_t POW3[46], MOD_X;
static void init(void){
    POW3[0]=1; for(int i=1;i<=45;i++)POW3[i]=POW3[i-1]*3; MOD_X=POW3[45];
}
/* ---- 以下函数逐字复制自 trirv_vm.c ---- */
static inline __uint128_t umod_M(__uint128_t x) {
    __uint128_t r = 0;
    for (int i = 127; i >= 0; i--) { r=(r<<1)|((x>>i)&1); if(r>=MOD_X)r-=MOD_X; }
    return r;
}
static inline uint64_t umod2187(__uint128_t x) {
    uint64_t hi=(uint64_t)(x>>64),lo=(uint64_t)x;
    uint64_t r=(hi%2187)*619u%2187;
    return (r+lo%2187)%2187;
}
static inline void divmod_word(__int128_t a,__int128_t b,__int128_t*qo,__int128_t*ro){
    if(b==0){if(qo)*qo=0;if(ro)*ro=0;return;}
    int qneg=(a<0)^(b<0);
    __uint128_t ua=a<0?(__uint128_t)(-(a+1))+1:(__uint128_t)a;
    __uint128_t ub=b<0?(__uint128_t)(-(b+1))+1:(__uint128_t)b;
    __uint128_t q=0,r=0;
    for(int i=71;i>=0;i--){ r=(r<<1)|((ua>>i)&1); if(r>=ub){r-=ub;q|=(__uint128_t)1<<i;} }
    if(qo)*qo=qneg?-(__int128_t)q:(__int128_t)q;
    if(ro)*ro=(a<0)?-(__int128_t)r:(__int128_t)r;
}
static inline uint64_t div243(uint64_t y){
    static const uint64_t M=75912527052302682ULL;
    uint64_t q=(uint64_t)(((__uint128_t)y*M)>>64); q-=(q*243>y); return q;
}
static inline uint64_t divmod243(__uint128_t x,uint8_t*rem){
    uint64_t hi=(uint64_t)(x>>64),lo=(uint64_t)x;
    __uint128_t mid=(__uint128_t)hi*133+lo;
    uint64_t m_hi=(uint64_t)(mid>>64),m_lo=(uint64_t)mid;
    uint64_t qmid=m_hi?75912527052302681ULL+div243(m_lo+133):div243(m_lo);
    uint64_t q=hi*75912527052302681ULL+qmid;
    *rem=(uint8_t)(x-(__uint128_t)q*243); return q;
}
static inline __uint128_t shr3(__uint128_t v,long sh){
    uint8_t t; while(sh>=5){v=divmod243(v,&t);sh-=5;} while(sh-->0){v=v/3;} return v;
}
/* ---- 伪随机 ---- */
static uint64_t st=0x9E3779B97F4A7C15ULL;
static uint64_t rnd(void){st^=st<<13;st^=st>>7;st^=st<<17;return st;}
static __uint128_t rnd_mod(void){ return ((__uint128_t)rnd()<<64 | rnd()) % MOD_X; }

int main(void){
    init();
    long fail=0;
    /* umod_M vs 参考（x mod MOD_X，x 用 uint128 全域随机） */
    for(long i=0;i<200000;i++){
        __uint128_t x=((__uint128_t)rnd()<<64)|rnd();
        __uint128_t got=umod_M(x), ref=x%MOD_X;
        if(got!=ref){ if(fail<5)printf("umod_M FAIL x=%llu got=%llu ref=%llu\n",(unsigned long long)x,(unsigned long long)got,(unsigned long long)ref); fail++; }
    }
    printf("umod_M: 20万随机 %s\n", fail?"FAIL":"PASS");

    long f0=fail;
    /* umod2187 */
    for(long i=0;i<200000;i++){
        __uint128_t x=rnd_mod();
        uint64_t got=umod2187(x), ref=(uint64_t)(x%2187);
        if(got!=ref){ if(fail-f0<5)printf("umod2187 FAIL\n"); fail++; }
    }
    printf("umod2187: 20万随机 %s\n", fail==f0?"PASS":"FAIL");

    f0=fail;
    /* divmod_word vs 参考，覆盖正数/负数/零/边界 */
    __int128_t cases[][2]={
        {0,0},{7,3},{7,-3},{-7,3},{-7,-3},{1,-3},{-1,3},
        {100,7},{-100,7},{100,-7},{-100,-7},{242,243},{-242,243},
    };
    for(unsigned k=0;k<sizeof(cases)/sizeof(cases[0]);k++){
        __int128_t a=cases[k][0],b=cases[k][1],q,r,qr,rr;
        divmod_word(a,b,&q,&r);
        if(b==0){qr=0;rr=0;} else {qr=a/b;rr=a%b;}   /* C 截断语义 = Python 向零 */
        if(q!=qr||r!=rr){printf("divmod_word 边界 FAIL a=%d b=%d q=%d/%d r=%d/%d\n",(int)a,(int)b,(int)q,(int)qr,(int)r,(int)rr);fail++;}
    }
    for(long i=0;i<200000;i++){
        __int128_t a=(__int128_t)rnd_mod(), b=(__int128_t)rnd_mod();
        if(i&1)a=-a; if(i&2)b=-b;
        __int128_t q,r,qr,rr;
        divmod_word(a,b,&q,&r);
        qr=(b==0)?0:a/b; rr=(b==0)?0:a%b;
        if(q!=qr||r!=rr){ if(fail-f0<5)printf("divmod_word FAIL\n"); fail++; }
        /* 验证恒等式 a == q*b + r */
        if(b!=0 && a!=q*b+r){ if(fail-f0<5)printf("恒等式 FAIL\n"); fail++; }
    }
    printf("divmod_word: 边界12 + 20万随机 %s\n", fail==f0?"PASS":"FAIL");

    f0=fail;
    /* shr3: v/3^sh vs 参考。sh 限定小范围避免 3^sh 溢出（VM 中 sh<=2186 但 v<3^45，sh>45 必为0） */
    for(long i=0;i<100000;i++){
        __uint128_t v=rnd_mod();
        long sh=(long)(rnd()%50);
        __uint128_t got=shr3(v,sh);
        __uint128_t den=1; for(long j=0;j<sh;j++)den*=3;
        __uint128_t ref=v/den;
        if(got!=ref){ if(fail-f0<5)printf("shr3 FAIL sh=%ld\n",sh); fail++; }
    }
    printf("shr3: 10万随机 %s\n", fail==f0?"PASS":"FAIL");

    printf("\n总失败数=%ld → %s\n", fail, fail?"FAIL":"全部通过");
    return fail?1:0;
}
