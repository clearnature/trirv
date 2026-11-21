/* trirv_trit_bench.c — 拆分三值运算各阶段耗时，定位真实瓶颈
 * 编译: gcc -O3 -march=native -o trirv_trit_bench trirv_trit_bench.c
 * 运行: ./trirv_trit_bench [迭代次数]
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#ifdef __AVX2__
#include <immintrin.h>
#endif

#define XLEN 45
static __uint128_t POW3[46], MOD_X;
static uint8_t TRIT_TAB[243][5];
static void init(void){
    POW3[0]=1; for(int i=1;i<=45;i++) POW3[i]=POW3[i-1]*3; MOD_X=POW3[45];
    for(int v=0;v<243;v++){int x=v;for(int i=0;i<5;i++){TRIT_TAB[v][i]=x%3;x/=3;}}
}
/* 跨平台 cycle 计时：x86 用 rdtsc，Apple Silicon/ARM 用虚拟计数器 cntvct，
 * 兜底用 clock_gettime 折算纳秒。返回单调递增 tick（用于差值）。 */
static inline uint64_t rdtsc(void){
#if defined(__aarch64__) || defined(__arm__)
    uint64_t c; __asm__ volatile("mrs %0, cntvct_el0":"=r"(c)); return c;
#elif defined(__x86_64__) || defined(__i386__)
    uint32_t a,d;__asm__ volatile("rdtsc":"=a"(a),"+d"(d));return((uint64_t)d<<32)|a;
#else
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC,&ts);
    return (uint64_t)ts.tv_sec*1000000000ULL+ts.tv_nsec;
#endif
}

/* ---- 各阶段，用 volatile 汇点防优化掉 ---- */
static volatile uint64_t sink;
static inline void unpack45(__uint128_t val,uint8_t out[48]){
    for(int i=0;i<9;i++){uint8_t t=(uint8_t)(val/POW3[5*i]%243);memcpy(out+5*i,TRIT_TAB[t],5);}
    out[45]=out[46]=out[47]=0;
}
static inline __uint128_t pack45(const uint8_t in[48]){
    __uint128_t val=0,p=1;for(int i=0;i<45;i++){val+=(__uint128_t)in[i]*p;p*=3;}return val%MOD_X;
}
/* tryte 查表：直接对 8 个 tryte 做 min/max，避免逐 trit 除法 */
static uint8_t LUT_MIN[243][243],LUT_MAX[243][243];
static void init_lut(void){
    for(int a=0;a<243;a++)for(int b=0;b<243;b++){
        uint8_t ta[5],tb[5],mn=0,mx=0,p=1;
        int x=a,y=b;
        for(int i=0;i<5;i++){ta[i]=x%3;tb[i]=y%3;x/=3;y/=3;
            mn+=(ta[i]<tb[i]?ta[i]:tb[i])*p; mx+=(ta[i]>tb[i]?ta[i]:tb[i])*p; p*=3;}
        LUT_MIN[a][b]=mn;LUT_MAX[a][b]=mx;
    }
}
static inline __uint128_t tritwise_lut(int mode,__uint128_t a,__uint128_t b){
    __uint128_t res=0,p=1;
    for(int i=0;i<9;i++){
        uint8_t ta=(uint8_t)(a/POW3[5*i]%243),tb=(uint8_t)(b/POW3[5*i]%243);
        uint8_t r = mode==0?LUT_MIN[ta][tb]:(mode==1?LUT_MAX[ta][tb]:0);
        if(mode==2){/* not: 2 - trit，逐 tryte 需重算 */}
        res+=(__uint128_t)r*p; p*=POW3[5];
    }
    return res%MOD_X;
}

/* 优化版：反复 mod/div 常数 243（编译器替换为乘法），Horner 法 pack，tryte 查表 */
static inline __uint128_t tritwise_opt(int mode,__uint128_t a,__uint128_t b){
    const __uint128_t B243=243;
    __uint128_t res=0,p=1;
    for(int i=0;i<9;i++){          /* 从低 tryte 累加，p 累乘 243 */
        uint8_t ta=(uint8_t)(a%B243), tb=(uint8_t)(b%B243);
        a/=B243; b/=B243;
        uint8_t r = mode==0?LUT_MIN[ta][tb]:LUT_MAX[ta][tb];
        res+=(__uint128_t)r*p; p*=B243;
    }
    return res;
}
/* 优化版 unpack：反复除常数 243 */
static inline void unpack_opt(__uint128_t val,uint8_t out[45]){
    const __uint128_t B243=243;
    for(int i=0;i<45;i+=5){uint8_t t=(uint8_t)(val%B243);val/=B243;memcpy(out+i,TRIT_TAB[t],5);}
}

/* 无除法器版本：magic mul+shift 替代 /243 */
#define DV_MAGIC 75912527052302682ULL
#define DV_Q64   75912527052302681ULL
#define DV_R64   133
static inline uint64_t dv243(uint64_t y){uint64_t q=(uint64_t)(((__uint128_t)y*DV_MAGIC)>>64);q-=(q*243>y);return q;}
static inline __uint128_t dvmod(__uint128_t x,uint8_t *rem){
  uint64_t hi=(uint64_t)(x>>64),lo=(uint64_t)x;
  __uint128_t mid=(__uint128_t)hi*DV_R64+lo;
  uint64_t mh=(uint64_t)(mid>>64),ml=(uint64_t)mid;
  uint64_t qmid=mh?DV_Q64+dv243(ml+DV_R64):dv243(ml);
  uint64_t q=hi*DV_Q64+qmid;*rem=(uint8_t)(x-(__uint128_t)q*243);return q;
}
static inline __uint128_t tritwise_nodev(int mode,__uint128_t a,__uint128_t b){
  __uint128_t res=0,p=1;
  for(int i=0;i<9;i++){uint8_t ta,tb;a=dvmod(a,&ta);b=dvmod(b,&tb);
    uint8_t r=mode==0?LUT_MIN[ta][tb]:(mode==1?LUT_MAX[ta][tb]:0);
    res+=(__uint128_t)r*p;p*=243;}
  return res;
}
int main(int argc,char**argv){
    init();init_lut();
    long N=argc>1?atol(argv[1]):2000000;
    __uint128_t A=0x123456789abcdefULL,B=0xfedcba987654321ULL;
    uint8_t ta[48],tb[48];unpack45(A,ta);unpack45(B,tb);
    uint64_t t0,t1;
    double cyc_stage,cyc_full;

    /* 1) unpack only */
    t0=rdtsc();
    for(long i=0;i<N;i++){unpack45(A+i,ta);sink+=ta[i%45];}
    t1=rdtsc(); double cy_unpack=(double)(t1-t0)/N;
    printf("unpack45 (9×128位除法)      : %6.2f cyc/次\n",cy_unpack);

    /* 2) min/max only (SIMD vs scalar) */
    t0=rdtsc();
    for(long i=0;i<N;i++){
#ifdef __AVX2__
        __m256i va=_mm256_loadu_si256((const __m256i*)ta),vb=_mm256_loadu_si256((const __m256i*)tb);
        __m256i vr=_mm256_max_epu8(va,vb);_mm256_storeu_si256((__m256i*)ta,vr);
#else
        for(int j=0;j<45;j++) if(ta[j]<tb[j]) ta[j]=tb[j];
#endif
        sink+=ta[i%45];
    }
    t1=rdtsc(); double cy_max=(double)(t1-t0)/N;
#ifdef __AVX2__
#define MAX_LABEL "AVX2"
#else
#define MAX_LABEL "scalar"
#endif
    printf("max45    (%s)       : %6.2f cyc/次\n",MAX_LABEL,cy_max);

    /* 3) pack only */
    t0=rdtsc();
    for(long i=0;i<N;i++){sink+=(uint64_t)(pack45(ta)>>64);}
    t1=rdtsc(); double cy_pack=(double)(t1-t0)/N;
    printf("pack45   (45×乘加+取模)     : %6.2f cyc/次\n",cy_pack);

    /* 4) 完整 tritwise (基线) */
    unpack45(A,ta);unpack45(B,tb);
    t0=rdtsc();
    for(long i=0;i<N;i++){
        unpack45(A+i,ta);unpack45(B+i,tb);
#ifdef __AVX2__
        __m256i va=_mm256_loadu_si256((const __m256i*)ta),vb=_mm256_loadu_si256((const __m256i*)tb);
        __m256i vr=_mm256_max_epu8(va,vb);_mm256_storeu_si256((__m256i*)ta,vr);
#else
        for(int j=0;j<45;j++) if(ta[j]<tb[j]) ta[j]=tb[j];
#endif
        sink+=(uint64_t)(pack45(ta)>>64);
    }
    t1=rdtsc(); cyc_full=(double)(t1-t0)/N;
    printf("完整 tritwise 基线          : %6.2f cyc/次\n",cyc_full);

    /* 5) tryte 查表版 */
    t0=rdtsc();
    for(long i=0;i<N;i++){sink+=(uint64_t)(tritwise_lut(1,A+i,B+i)>>64);}
    t1=rdtsc(); double cy_lut=(double)(t1-t0)/N;
    printf("tritwise LUT (8×查表)       : %6.2f cyc/次  加速 %5.2fx\n",cy_lut,cyc_full/cy_lut);

    /* 6) 优化版：常数243 Horner + 查表 */
    t0=rdtsc();
    for(long i=0;i<N;i++){sink+=(uint64_t)(tritwise_opt(1,A+i,B+i)>>64);}
    t1=rdtsc(); double cy_opt=(double)(t1-t0)/N;
    printf("tritwise OPT (常数243+查表)  : %6.2f cyc/次  加速 %5.2fx\n",cy_opt,cyc_full/cy_opt);

    /* 7) 优化版 unpack */
    t0=rdtsc();
    for(long i=0;i<N;i++){unpack_opt(A+i,ta);sink+=ta[i%45];}
    t1=rdtsc(); double cy_unpack_opt=(double)(t1-t0)/N;
    printf("unpack OPT   (常数243)       : %6.2f cyc/次  加速 %5.2fx\n",cy_unpack_opt,cy_unpack/cy_unpack_opt);
    /* 8) 无除法器版 */
    t0=rdtsc();
    for(long i=0;i<N;i++){sink+=(uint64_t)(tritwise_nodev(1,A+i,B+i)>>64);}
    t1=rdtsc(); double cy_nodev=(double)(t1-t0)/N;
    printf("tritwise NO-DIV (magic+shift): %6.2f cyc/次  加速 %5.2fx\n",cy_nodev,cyc_full/cy_nodev);
    printf("  vs 常数243版(OPT): %6.2f → %6.2f = %5.2fx\n",cy_opt,cy_nodev,cy_opt/cy_nodev);

    printf("\n瓶颈判定: unpack=%6.2f  pack=%6.2f  max=%6.2f  → 二者合计占基线 %.0f%%\n",
           cy_unpack,cy_pack,cy_max,(cy_unpack+cy_pack)/cyc_full*100);
    printf("总优化: 基线 %.2f → OPT %.2f cyc  = %.2fx\n",cyc_full,cy_opt,cyc_full/cy_opt);
    printf("汇点(sink)=%llu\n",(unsigned long long)sink);
    return 0;
}
