/* 验证 LUT/OPT 版 tritwise 与原版逐位等价 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdint.h>
#include <string.h>
static __uint128_t POW3[46],MOD_X;
static uint8_t TRIT_TAB[243][5],LUT_MIN[243][243],LUT_MAX[243][243],LUT_NOT[243];
static void init(void){
  POW3[0]=1;for(int i=1;i<=45;i++)POW3[i]=POW3[i-1]*3;MOD_X=POW3[45];
  for(int v=0;v<243;v++){int x=v;for(int i=0;i<5;i++){TRIT_TAB[v][i]=x%3;x/=3;}}
  for(int a=0;a<243;a++){
    int x=a;uint8_t nt=0,p=1;for(int i=0;i<5;i++){nt+=(2-x%3)*p;x/=3;p*=3;}LUT_NOT[a]=nt;
    for(int b=0;b<243;b++){
      int y=b;uint8_t mn=0,mx=0;p=1;x=a;
      for(int i=0;i<5;i++){int ta=x%3,tb=y%3;x/=3;y/=3;mn+=(ta<tb?ta:tb)*p;mx+=(ta>tb?ta:tb)*p;p*=3;}
      LUT_MIN[a][b]=mn;LUT_MAX[a][b]=mx;
    }
  }
}
static void unpack45(__uint128_t val,uint8_t out[48]){
  for(int i=0;i<9;i++){uint8_t t=(uint8_t)(val/POW3[5*i]%243);memcpy(out+5*i,TRIT_TAB[t],5);}out[45]=out[46]=out[47]=0;
}
static __uint128_t pack45(const uint8_t in[48]){
  __uint128_t val=0,p=1;for(int i=0;i<45;i++){val+=(__uint128_t)in[i]*p;p*=3;}return val%MOD_X;
}
/* 原版 */
static __uint128_t old(int mode,__uint128_t a,__uint128_t b){
  uint8_t ta[48],tb[48];unpack45(a,ta);unpack45(b,tb);
  for(int i=0;i<45;i++){
    if(mode==0)ta[i]=ta[i]<tb[i]?ta[i]:tb[i];
    else if(mode==1)ta[i]=ta[i]>tb[i]?ta[i]:tb[i];
    else ta[i]=2-ta[i];
  }
  return pack45(ta);
}
/* 无除法器版：magic mul+shift */
#define DV_MAGIC 75912527052302682ULL
#define DV_Q64   75912527052302681ULL
#define DV_R64   133
static inline uint64_t dv243(uint64_t y){uint64_t q=(uint64_t)(((__uint128_t)y*DV_MAGIC)>>64);q-=(q*243>y);return q;}
static inline uint64_t dvmod(__uint128_t x,uint8_t *rem){
  uint64_t hi=(uint64_t)(x>>64),lo=(uint64_t)x;
  __uint128_t mid=(__uint128_t)hi*DV_R64+lo;
  uint64_t mh=(uint64_t)(mid>>64),ml=(uint64_t)mid;
  uint64_t qmid=mh?DV_Q64+dv243(ml+DV_R64):dv243(ml);
  uint64_t q=hi*DV_Q64+qmid;*rem=(uint8_t)(x-(__uint128_t)q*243);return q;
}
static __uint128_t nodev(int mode,__uint128_t a,__uint128_t b){
  const __uint128_t B=243;__uint128_t res=0,p=1;
  for(int i=0;i<9;i++){uint8_t ta,tb;a=dvmod(a,&ta);b=dvmod(b,&tb);
    uint8_t r=mode==0?LUT_MIN[ta][tb]:(mode==1?LUT_MAX[ta][tb]:LUT_NOT[ta]);
    res+=(__uint128_t)r*p;p*=B;}
  return res;
}
/* 优化版：常数243 Horner + 查表 */
static __uint128_t opt(int mode,__uint128_t a,__uint128_t b){
  const __uint128_t B=243;__uint128_t res=0,p=1;
  for(int i=0;i<9;i++){
    uint8_t ta=(uint8_t)(a%B),tb=(uint8_t)(b%B);a/=B;b/=B;
    uint8_t r=mode==0?LUT_MIN[ta][tb]:(mode==1?LUT_MAX[ta][tb]:LUT_NOT[ta]);
    res+=(__uint128_t)r*p;p*=B;
  }
  return res;
}
static uint64_t st=0x9e3779b97f4a7c15ULL;
static uint64_t rnd(void){st^=st<<13;st^=st>>7;st^=st<<17;return st;}
static __uint128_t rnd128(void){return ((__uint128_t)rnd()<<64)|rnd();}
int main(void){
  init();long bad=0;
  for(long i=0;i<500000;i++){
    __uint128_t a=rnd128()%MOD_X,b=rnd128()%MOD_X;
    for(int m=0;m<3;m++){
      if(old(m,a,b)!=opt(m,a,b)){if(bad<3)printf("MISMATCH opt m=%d\n",m);bad++;}
      if(old(m,a,b)!=nodev(m,a,b)){if(bad<3)printf("MISMATCH nodev m=%d a=%llu b=%llu old=%llu nodev=%llu\n",m,(unsigned long long)a,(unsigned long long)b,(unsigned long long)old(m,a,b),(unsigned long long)nodev(m,a,b));bad++;}
    }
  }
  printf("等价性验证: 500000×3 组(opt+nodev), 不一致=%ld → %s\n",bad,bad?"FAIL":"PASS");
  return bad?1:0;
}
