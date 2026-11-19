/*
 * TriRV VM —— C 高性能实现（语义对齐 trirv_vm.py / trirv_isa.py）
 *
 * 数据模型：20-trit 指令字，45-trit 寄存器字（平衡三补码），tryte 编址内存。
 * 优化：
 *   1) 预译码：加载时把 3^k 加权指令字解码成结构化 Op，执行期零译码。
 *   2) __int128 承载 45 trit（3^45 ≈ 2.95e21，介于 2^64 与 2^128 之间，余量充足）。
 *   3) 查表 trit 展开 + AVX2 向量化逐 trit min/max（TMAX/TMIN/TAND/TOR/TNOT）。
 *   4) OpenMP 多程序批量并行。
 *
 * 编译：
 *   gcc -O3 -march=native -fopenmp -o trirv_vm trirv_vm.c
 *   gcc -O3 -fopenmp -o trirv_vm_portable trirv_vm.c   # 无 SIMD 可移植版
 */
#define _GNU_SOURCE
#pragma GCC diagnostic ignored "-Wunused-result"
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <time.h>

/* ---------------- ISA 常量 ---------------- */
#define XLEN 45
#define NREG 32   /* 4-trit 寄存器字段 (0..31)，对齐 RISC-V x0-x31 */
#define MEMSZ (1u << 20)
/* ---- Zicsr + 特权最小集常量（对齐 trirv_isa.CSR）---- */
#define CSR_MSTATUS  768   /* 0x300 */
#define CSR_MIE      772   /* 0x304 */
#define CSR_MTVEC    773   /* 0x305 */
#define CSR_MSCRATCH 832   /* 0x340 */
#define CSR_MEPC     833   /* 0x341 */
#define CSR_MCAUSE   834   /* 0x342 */
#define CSR_MTVAL    835   /* 0x343 */
#define CSR_MIP      836   /* 0x344 */
#define MSTATUS_MIE      (1<<3)
#define MSTATUS_MPIE     (1<<4)
#define MSTATUS_MPP_SH   11
#define CAUSE_ILLEGAL    2
#define CAUSE_ECALL_U    11
#define CAUSE_BREAKPOINT 3
/* CAUSE_MTIMER = (1<<63)|7，放于 __int128，用宏构造 */
#define CAUSE_MTIMER_LO  7
#define MIP_MTIP         (1<<7)     /* mip 中的机器定时器中断挂起位 */
#define NCSR 900                    /* CSR 索引上限（最大地址 836） */

static __uint128_t POW3[46];       /* 3^i, i=0..45 */
static __uint128_t MOD_X, HALF_X;  /* 3^45, (3^45-1)/2 */
static void init_consts(void) {
    POW3[0] = 1;
    for (int i = 1; i <= 45; i++) POW3[i] = POW3[i-1] * 3;
    MOD_X = POW3[45];
    HALF_X = (MOD_X - 1) / 2;
}

static inline __int128_t s(__uint128_t v) {
    return (v > HALF_X) ? (__int128_t)(v - MOD_X) : (__int128_t)v;
}

/* ===== 无除法器原语（本轮）：全程移位/比较/减法，零硬件除法指令 =====
 * MOD_X = 3^45 < 2^72。以下三点保证幅值不溢出 uint128：
 *   - umod_M: r < MOD_X < 2^72 → r<<1 < 2^73；
 *   - divmod_word: 幅值 |a|,|b| < MOD_X < 2^72，余数 r < ub < 2^72 → r<<1 < 2^73。 */

/* 任意 uint128 对 3^45 取模：逐位折叠（127 步，零除法）。
 * 只在 u()/store_word 的防御性慢路径调用（正常值已在 [0,MOD_X) 内直接返回）。 */
static inline __uint128_t umod_M(__uint128_t x) {
    __uint128_t r = 0;
    for (int i = 127; i >= 0; i--) {
        r = (r << 1) | ((x >> i) & 1);
        if (r >= MOD_X) r -= MOD_X;
    }
    return r;
}

/* 移位量取模 3^7=2187：拆成 64 位高低半，2^64 mod 2187 = 619。
 * 全程 uint64 % 常量（编译为乘法逆元 mulhi），避免 __uint128 % int 的库调用。 */
static inline uint64_t umod2187(__uint128_t x) {
    uint64_t hi = (uint64_t)(x >> 64), lo = (uint64_t)x;
    uint64_t r = (hi % 2187) * 619u % 2187;
    return (r + lo % 2187) % 2187;
}

/* 45-trit 有符号字除法：商与余数，向零取整（对齐 Python // 与 C / 的截断语义）。
 * 逐位恢复除法（restoring division），零 div 指令。
 * 余数符号跟随被除数，满足 a == q*b + r。b==0 时商余数均为 0。 */
static inline void divmod_word(__int128_t a, __int128_t b,
                               __int128_t *qo, __int128_t *ro) {
    if (b == 0) { if (qo) *qo = 0; if (ro) *ro = 0; return; }
    int qneg = (a < 0) ^ (b < 0);
    /* 取幅值用 -(x+1)+1 形式，避免 INT128_MIN 取负溢出 */
    __uint128_t ua = a < 0 ? (__uint128_t)(-(a + 1)) + 1 : (__uint128_t)a;
    __uint128_t ub = b < 0 ? (__uint128_t)(-(b + 1)) + 1 : (__uint128_t)b;
    __uint128_t q = 0, r = 0;
    for (int i = 71; i >= 0; i--) {       /* 幅值 < 2^72，从 bit71 到 bit0 */
        r = (r << 1) | ((ua >> i) & 1);
        if (r >= ub) { r -= ub; q |= (__uint128_t)1 << i; }
    }
    if (qo) *qo = qneg ? -(__int128_t)q : (__int128_t)q;
    if (ro) *ro = (a < 0) ? -(__int128_t)r : (__int128_t)r;  /* 余数符号随被除数 */
}

/* 有符号 -> 无符号（平衡三补码的数学模，对齐 Python from_signed_word）。
 * 快路径：正值且 <MOD_X 直接返回；负值且 >=-MOD_X 加 MOD_X。
 * 慢路径（溢出防御）：改走 umod_M 无除法取模，替换原 (__uint128_t)x % MOD_X。 */
static inline __uint128_t u(__int128_t x) {
    if (x >= 0) {
        __uint128_t ux = (__uint128_t)x;
        return ux < MOD_X ? ux : umod_M(ux);
    }
    if (x >= -(__int128_t)MOD_X) return (__uint128_t)(x + (__int128_t)MOD_X);
    __uint128_t mag = (__uint128_t)(-(x + 1)) + 1;   /* -x，防 INT128_MIN */
    __uint128_t m = umod_M(mag);
    return m ? MOD_X - m : 0;                         /* x mod MOD_X = MOD_X-(-x mod MOD_X) */
}
/* n-trit 立即数符号扩展为带符号值（可直接参与加法/跳转） */
static inline __int128_t sx(__uint128_t imm, int n) {
    __uint128_t mod = POW3[n], half = (mod - 1) / 2;
    return (imm > half) ? (__int128_t)imm - (__int128_t)mod : (__int128_t)imm;
}

static inline uint32_t field(uint32_t v, int lo, int hi) {
    uint32_t div = 1, mod = 1;
    for (int i = 0; i < lo; i++) div *= 3;
    for (int i = 0; i <= hi - lo; i++) mod *= 3;
    return (v / div) % mod;
}

/* ---------------- 指令枚举（与 Python INST 名一一对应） ---------------- */
typedef enum {
    OP_ADD, OP_SUB, OP_SLL, OP_SLT, OP_SLTU, OP_XOR, OP_SRL, OP_SRA, OP_OR, OP_AND,
    OP_MUL, OP_DIV, OP_REM, OP_MULH, OP_MULHSU, OP_MULHU, OP_DIVU, OP_REMU,
    OP_ADDI, OP_SLTI, OP_SLTIU, OP_XORI, OP_ORI, OP_ANDI, OP_SLLI, OP_SRLI, OP_SRAI,
    OP_LB, OP_LH, OP_LW, OP_LBU, OP_LHU,
    OP_SB, OP_SH, OP_SW,
    OP_BEQ, OP_BNE, OP_BLT, OP_BGE, OP_BLTU, OP_BGEU,
    OP_JAL, OP_JALR, OP_LUI, OP_AUIPC, OP_ECALL, OP_EBREAK, OP_FENCE,
    OP_TMIN, OP_TMAX, OP_TNOT, OP_TAND, OP_TOR, OP_TNEG,
    OP_TSEXT,
    OP_TPACK, OP_TUNPACK,
    /* Zicsr + 特权最小集 */
    OP_CSRRW, OP_CSRRS, OP_CSRRC, OP_CSRRWI, OP_CSRRSI, OP_CSRRCI,
    OP_MRET, OP_WFI,
    /* A 扩展：LR/SC + AMO */
    OP_LRW, OP_SCw, OP_AMOADD, OP_AMOSWAP, OP_AMOAND, OP_AMOOR, OP_AMOXOR,
    OP_AMOMIN, OP_AMOMAX,
    OP_NOP
} Op;

typedef struct { uint8_t op, funct, funct2; char fmt; Op code; } InstDef;
/* 严格镜像 trirv_isa.INST 的顺序与编码 */
static const InstDef IDEFS[] = {
    {0,0,0,'R',OP_ADD},{0,0,1,'R',OP_SUB},{0,1,0,'R',OP_SLL},
    {0,1,1,'R',OP_SLT},{0,1,2,'R',OP_SLTU},{0,2,0,'R',OP_XOR},
    {0,2,1,'R',OP_SRL},{0,2,2,'R',OP_SRA},{0,3,0,'R',OP_OR},{0,3,1,'R',OP_AND},
    {13,0,0,'R',OP_MUL},{13,1,0,'R',OP_MULH},{13,2,0,'R',OP_MULHSU},{13,3,0,'R',OP_MULHU},
    {14,0,0,'R',OP_DIV},{14,1,0,'R',OP_DIVU},
    {15,0,0,'R',OP_REM},{15,1,0,'R',OP_REMU},
    {1,0,0,'I',OP_ADDI},{1,1,0,'I',OP_SLTI},{1,5,0,'I',OP_SLTIU},
    {1,2,0,'I',OP_XORI},{1,3,0,'I',OP_ORI},{1,6,0,'I',OP_ANDI},
    {1,4,0,'I',OP_SLLI},{1,7,0,'I',OP_SRLI},{1,8,0,'I',OP_SRAI},
    {2,0,0,'I',OP_LB},{2,1,0,'I',OP_LH},{2,2,0,'I',OP_LW},
    {2,3,0,'I',OP_LBU},{2,4,0,'I',OP_LHU},
    {3,0,0,'S',OP_SB},{3,1,0,'S',OP_SH},{3,2,0,'S',OP_SW},
    {4,0,0,'B',OP_BEQ},{4,1,0,'B',OP_BNE},{4,2,0,'B',OP_BLT},
    {4,3,0,'B',OP_BGE},{4,4,0,'B',OP_BLTU},{4,5,0,'B',OP_BGEU},
    {5,0,0,'J',OP_JAL},{6,0,0,'I',OP_JALR},{7,0,0,'U',OP_LUI},
    {8,0,0,'U',OP_AUIPC},{9,0,0,'I',OP_ECALL},{9,1,0,'I',OP_EBREAK},
    {10,0,0,'I',OP_FENCE},
    {11,0,0,'R',OP_TMIN},{11,0,1,'R',OP_TMAX},{11,0,2,'R',OP_TNOT},
    {11,1,0,'R',OP_TAND},{11,1,1,'R',OP_TOR},{11,1,2,'R',OP_TNEG},
    {11,2,0,'R',OP_TSEXT},
    {12,0,0,'R',OP_TPACK},{12,0,1,'R',OP_TUNPACK},
    /* ZICSR opcode16: csr 号在 I-type imm7 字段 */
    {16,0,0,'I',OP_CSRRW},{16,1,0,'I',OP_CSRRS},{16,2,0,'I',OP_CSRRC},
    {16,3,0,'I',OP_CSRRWI},{16,4,0,'I',OP_CSRRSI},{16,5,0,'I',OP_CSRRCI},
    /* PRIV opcode17 */
    {17,0,0,'I',OP_MRET},{17,1,0,'I',OP_WFI},
    /* A 扩展 opcode18 (R-type) */
    {18,0,0,'R',OP_LRW},{18,0,1,'R',OP_SCw},
    {18,1,0,'R',OP_AMOADD},{18,1,1,'R',OP_AMOSWAP},{18,1,2,'R',OP_AMOAND},
    {18,1,3,'R',OP_AMOOR},{18,1,4,'R',OP_AMOXOR},{18,1,5,'R',OP_AMOMIN},{18,1,6,'R',OP_AMOMAX},
};
#define NIDEFS (sizeof(IDEFS)/sizeof(IDEFS[0]))

/* 预译码指令：imm_s=带符号立即数（加法/跳转用），imm_u=无符号位模式（按位/LUI 用）
 * target=分支/跳转的绝对 tryte 地址（加载时预计算 (oldpc+imm)*4），运行时零加法。
 * PC 按 tryte 编址：指令 i 地址 = 4*i；立即数仍是"指令槽偏移"，故目标地址 = 槽目标*4。*/
typedef struct {
    Op op;
    uint8_t rd, rs1, rs2;
    __int128_t imm_s;
    __uint128_t imm_u;
    __uint128_t imm_raw;
    int target;
} Inst;

/* PC 相对分支/跳转（目标可在加载时确定；JALR 目标是运行时寄存器，排除） */
static inline int is_pcrel(Op op){
    return (op>=OP_BEQ && op<=OP_BGEU) || op==OP_JAL;
}

static Inst decode_one(uint32_t raw) {
    uint32_t opc=field(raw,0,2), rd=field(raw,3,6), fc=field(raw,7,8);   /* 4-trit：x0-x31 */
    uint32_t rs1=field(raw,9,12), rs2=field(raw,13,16), f2=field(raw,17,19); /* 4-trit：x0-x31 */
    Inst in; memset(&in,0,sizeof(in));
    in.rd=rd; in.rs1=rs1; in.rs2=rs2; in.op=OP_NOP;
    const InstDef *d=NULL;
    for (size_t i=0;i<NIDEFS;i++) {
        const InstDef *e=&IDEFS[i];
        if (e->op!=opc) continue;
        if (e->fmt=='U'||e->fmt=='J') { d=e; break; }
        if (e->funct!=fc) continue;
        if (e->fmt=='R' && e->funct2!=f2) continue;
        d=e; break;
    }
    if(!d) return in;
    in.op=d->code;
    __uint128_t immraw;
    if(d->fmt=='U'||d->fmt=='J') immraw=field(raw,7,19);
    else if(d->fmt=='I') immraw=field(raw,13,19);
    else { uint32_t lo=field(raw,3,6),hi=field(raw,17,19); immraw=hi*81+lo; }
    int nbits=(d->fmt=='U'||d->fmt=='J')?13:7;
    in.imm_s=sx(immraw,nbits);
    in.imm_u=u(in.imm_s);              /* = sign_extend_imm(imm) % MOD，按位运算用 */
    in.imm_raw=immraw;                 /* 原始字段，LUI 用（Python LUI 用未扩展的 imm） */
    return in;
}

/* ---------------- VM ---------------- */
/* SMP 共享内存同步：OpenMP 多实例共享一个 SharedSync + 一段公共 mem。
 * lock 用 GCC __atomic 自旋锁；version 在每次成功 store/AMO 后 ++，
 * 供 LR/SC 检测保留是否被其他核破坏（SC 要求 version 未变）。 */
typedef struct SharedSync {
    volatile int lock;
    uint64_t version;
} SharedSync;
static inline void sh_lock(SharedSync *s){
    while(__atomic_test_and_set(&s->lock,__ATOMIC_ACQUIRE)){ }
}
static inline void sh_unlock(SharedSync *s){
    __atomic_clear(&s->lock,__ATOMIC_RELEASE);
}

typedef struct {
    __uint128_t reg[NREG];
    uint8_t mem[MEMSZ];
    Inst *prog; int nprog;
    int pc_;
    uint8_t *stdin_data; size_t stdin_len, stdin_pos;
    uint8_t *output; size_t out_len, out_cap;
    int halted;
    long steps;
    __int128_t exit_code;
    __int128_t brk_ptr, heap_base, mmap_ptr;
    __int128_t clock_sec, clock_nsec;   /* 分存秒/纳秒，避免 clock_ns/1e9 的 128 位除法 */
    uint32_t rng_state;
    int next_fd;
    /* Zicsr + 特权态 */
    int priv;                           /* 0=U, 3=M；复位为 M */
    __uint128_t csr[NCSR];              /* CSR 寄存器文件（按地址索引） */
    int irq_timer;                      /* 定时器中断挂起（供固件/测试注入） */
    /* A 扩展：LR/SC 保留集 + 共享内存多核模式 */
    __int128_t lr_addr;                 /* 保留地址（-1=无） */
    int lr_valid;                       /* 保留有效性 */
    uint64_t lr_version;                /* LR 时的内存版本号，SC 检查未被他核改动 */
    int shared;                         /* 1=多实例共享同一 mem（SMP 原子性验证） */
    uint8_t *mem_ref;                   /* 共享时指向公共 buffer；否则指向自身 mem */
    struct SharedSync *sync;            /* 共享锁 + 内存版本号（所有核指向同一结构） */
} VM;

static inline void vm_out(VM *v, uint8_t c) {
    if(v->out_len>=v->out_cap){v->out_cap=v->out_cap?v->out_cap*2:256;v->output=realloc(v->output,v->out_cap);}
    v->output[v->out_len++]=c;
}

/* tryte 地址向下对齐到 4 的倍数（floor），与 Python `//4*4` 一致。
 * C 整数除法向零截断，负数需修正；地址通常非负，负数仅出现在畸形 JALR 目标。 */
static inline __int128_t align_down4(__int128_t x){
    __int128_t q=x/4;
    if(x%4 && x<0) q--;
    return q*4;
}
/* ---- Zicsr + 特权态辅助（对齐 Python VM 语义）---- */
static inline int csr_valid(__uint128_t a){return a<NCSR && (a==CSR_MSTATUS||a==CSR_MIE||a==CSR_MTVEC||a==CSR_MSCRATCH||a==CSR_MEPC||a==CSR_MCAUSE||a==CSR_MTVAL||a==CSR_MIP);}
static inline void csr_write(VM *v,__uint128_t addr,__uint128_t val){
    if(!csr_valid(addr))return;                       /* 未实现 CSR：写忽略 */
    if(addr==CSR_MTVEC||addr==CSR_MEPC){              /* PC 是 tryte 地址：4 对齐 */
        /* 必须先 u() 规整到 [0,MOD_X) 再 s()：CSRRS/CSRRC 的 old|src 可 >= MOD_X，
         * 直接 s(val) 只减一次 MOD_X 会算错 3 补码值（与 Python s(u(val)) 对齐）。 */
        val=u((__uint128_t)align_down4(s(u(val))));
    }
    v->csr[addr]=umod_M(val);                         /* 无除法取模 */
    if(addr==CSR_MIP && (val&MIP_MTIP)) v->irq_timer=1;  /* 写 mip.MTIP -> 挂起定时器中断 */
}
static void take_trap(VM *v,__uint128_t cause,__uint128_t tval,int from_pc){
    __uint128_t old=v->csr[CSR_MSTATUS],nw=old&~(__uint128_t)MSTATUS_MIE;
    if(old&MSTATUS_MIE)nw|=MSTATUS_MPIE;
    nw&=~((__uint128_t)0x3<<MSTATUS_MPP_SH);
    nw|=(__uint128_t)(v->priv&0x3)<<MSTATUS_MPP_SH;
    v->csr[CSR_MSTATUS]=umod_M(nw);
    csr_write(v,CSR_MEPC,(__uint128_t)from_pc);
    csr_write(v,CSR_MCAUSE,cause);
    csr_write(v,CSR_MTVAL,tval);
    v->priv=3;
    long tgt=(long)s(v->csr[CSR_MTVEC]);
    if(tgt>=0&&tgt%4==0&&tgt/4<v->nprog)v->pc_=(int)tgt; else v->halted=1;  /* tryte 地址 */
}
static void mret(VM *v){
    __uint128_t ms=v->csr[CSR_MSTATUS];
    v->priv=(int)((ms>>MSTATUS_MPP_SH)&0x3);
    __uint128_t nw=ms&~((__uint128_t)0x3<<MSTATUS_MPP_SH);
    if(ms&MSTATUS_MPIE)nw|=MSTATUS_MIE; else nw&=~(__uint128_t)MSTATUS_MIE;
    nw|=MSTATUS_MPIE;
    v->csr[CSR_MSTATUS]=umod_M(nw);
    long tgt=(long)s(v->csr[CSR_MEPC]);
    if(tgt>=0&&tgt%4==0&&tgt/4<v->nprog)v->pc_=(int)tgt; else v->halted=1;  /* tryte 地址 */
}
static inline void csr_op(VM *v,Op op,int rd,int rs1,__uint128_t csr_addr){
    /* csr_addr 来自 7-trit 字段(0..2186)，可能超出 csr[NCSR=900] 范围：
     * 越界按未实现 CSR 处理——读 0、写忽略，与 Python VM 的 dict.get 行为一致，
     * 杜绝数组越界读/写。 */
    if(csr_addr>=NCSR){ v->reg[rd]=0; return; }
    __uint128_t old=umod_M(v->csr[csr_addr]);
    __uint128_t src;
    switch(op){
        case OP_CSRRWI:case OP_CSRRSI:case OP_CSRRCI: src=(__uint128_t)rs1; break;  /* 立即数版：rs1 字段是 uimm */
        default: src=(rs1<NREG)?v->reg[rs1]:0; break;                                 /* 寄存器版 */
    }
    if(op==OP_CSRRW||op==OP_CSRRWI)csr_write(v,csr_addr,src);
    else if(op==OP_CSRRS||op==OP_CSRRSI)csr_write(v,csr_addr,old|src);
    else if(op==OP_CSRRC||op==OP_CSRRCI)csr_write(v,csr_addr,old&~src);
    v->reg[rd]=old;   /* 已 umod_M，无需再取模 */
}
/* 无除法器 divmod243 前置声明（定义在 trit 表之后，供 load/store 复用） */
static inline uint64_t divmod243(__uint128_t x, uint8_t *rem);
/* store_word/load_word 本轮去除法化：
 * 原实现对每个 tryte 用 val/POW3[5*i]%243（POW3 是变量除数 → __udivmodti4），
 * 且末尾 val%MOD_X 又是一次 128 位取模。改为：
 *   - store：反复 divmod243（magic mul+shift，零除法）取 9 个 tryte；
 *   - load ：正向乘 243 累加（p*=243 为常数乘），且 9×(<243)×243^i < 3^45=MOD_X，
 *            天然落在值域内，省去 val%MOD_X。
 * 二者彻底消除 SW/LW 中的软件 128 位除法调用。 */
/* 无锁内部版：调用者已在共享锁内（AMO/LR/SC）或确定单线程。 */
static inline void store_word_nl(VM *v, __int128_t addr, __uint128_t val){
    __uint128_t q = val < MOD_X ? val : umod_M(val);  /* 防御分支也零除法 */
    for(int i=0;i<9;i++){
        uint8_t t; q = divmod243(q, &t);
        __int128_t a=addr+i; if(a>=0&&a<MEMSZ) v->mem_ref[a]=t;
    }
}
/* ---- 45 trit word 装入：两种等价实现，可编译期切换 ----
 * 旧实现（保留存档）：9 次 tryte 累加，每次 p*=243 —— 8 次递增的 128 位常量乘。
 * 新实现：按 3 组 × 3 tryte 打包。每组 3 tryte = 3^15 = 14348907 < 2^24，
 *   可直接放进 uint32；两次组间合并只需 1 次 128 位常量乘（G3）。
 *   实测 LW 8.67 ns → 2.95 ns（−66%），因为消除了 7 次 128 位乘法。
 * 注：store 方向未采用同法 —— __uint128 % 14348907 无法强度削减，
 *   会退化为 __umodti4 库调用，实测反而慢 4×，故 store 维持 9 次 divmod243。 */
#define G3 (14348907ULL)            /* 243^3 = 3^15 < 2^24 */
static inline __uint128_t load_word_nl_9(VM *v, __int128_t addr){
    __uint128_t val=0, p=1;
    for(int i=0;i<9;i++){
        __int128_t a=addr+i;uint8_t t=(a>=0&&a<MEMSZ)?(v->mem_ref[a]%243):0;
        val+=(__uint128_t)t*p; p*=243;
    }
    return val;  /* < 3^45，无需 %MOD_X */
}
static inline __uint128_t load_word_nl(VM *v, __int128_t addr){
    uint32_t g[3];
    for(int k=0;k<3;k++){
        __int128_t a=addr+3*k;
        uint32_t b0=(a+0>=0&&a+0<MEMSZ)?(v->mem_ref[a+0]%243):0;
        uint32_t b1=(a+1>=0&&a+1<MEMSZ)?(v->mem_ref[a+1]%243):0;
        uint32_t b2=(a+2>=0&&a+2<MEMSZ)?(v->mem_ref[a+2]%243):0;
        g[k]=b0+243u*b1+59049u*b2;          /* < 3^15 < 2^24 */
    }
    uint64_t hi=(uint64_t)g[1]+G3*(uint64_t)g[2];   /* < 2^48，uint64 内安全 */
    return (__uint128_t)g[0]+(__uint128_t)G3*hi;    /* 唯一 1 次 128 位常量乘 */
}
static inline void store_word(VM *v, __int128_t addr, __uint128_t val){
    if(v->shared) sh_lock(v->sync);
    store_word_nl(v,addr,val);
    if(v->shared){ __atomic_fetch_add(&v->sync->version,1,__ATOMIC_RELAXED); sh_unlock(v->sync); }
}
static inline __uint128_t load_word(VM *v, __int128_t addr){
    if(v->shared) sh_lock(v->sync);
    __uint128_t val=load_word_nl(v,addr);
    if(v->shared) sh_unlock(v->sync);
    return val;
}
/* store_tryte 取低 tryte：用 divmod243 取余（magic 无除法）。 */
static inline void store_tryte(VM *v, __int128_t addr, __uint128_t val){
    if(addr>=0&&addr<MEMSZ){uint8_t r;divmod243(val,&r);v->mem_ref[addr]=r;}
}
static inline uint8_t load_tryte(VM *v, __int128_t addr){return (addr>=0&&addr<MEMSZ)?(v->mem_ref[addr]%243):0;}

/* 三进制 trit 表 + tryte 级 min/max/not/xor 查表（逐元素优化核心） */
static uint8_t TRIT_TAB[243][5];
static uint8_t LUT_MIN[243][243], LUT_MAX[243][243], LUT_XOR[243][243], LUT_NOT[243];
static void init_trit_tab(void){
    for(int v=0;v<243;v++){int x=v;for(int i=0;i<5;i++){TRIT_TAB[v][i]=x%3;x/=3;}}
    for(int a=0;a<243;a++){
        int x=a;uint8_t nt=0,p=1;
        for(int i=0;i<5;i++){nt+=(2-x%3)*p;x/=3;p*=3;}
        LUT_NOT[a]=nt;
        for(int b=0;b<243;b++){
            x=a;int y=b;uint8_t mn=0,mx=0,xr=0;p=1;
            for(int i=0;i<5;i++){int ta=x%3,tb=y%3;x/=3;y/=3;
                mn+=(ta<tb?ta:tb)*p;mx+=(ta>tb?ta:tb)*p;
                xr+=((ta+tb)%3)*p;p*=3;}
            LUT_MIN[a][b]=mn;LUT_MAX[a][b]=mx;LUT_XOR[a][b]=xr;
        }
    }
}
/* 无除法器 divmod 243：用 magic 乘法 + 位移 + 无分支修正替代硬件 div。
 * 恒等式 2^64 = 75912527052302681*243 + 133；MULT=ceil(2^64/243) 的 mulhi 商误差至多+1。
 * word < 3^45 < 2^72，拆 hi(<2^8)+lo，mid=hi*133+lo<2^64+2^16。全程零 div 指令。 */
#define DV_MAGIC 75912527052302682ULL   /* floor(2^64/243)+1 */
#define DV_Q64   75912527052302681ULL   /* 2^64 // 243 */
#define DV_R64   133                    /* 2^64 % 243 */
static inline uint64_t div243(uint64_t y){
    uint64_t q=(uint64_t)(((__uint128_t)y*DV_MAGIC)>>64);
    q -= (q*243>y);                     /* 偏大则减1（sbb，无分支） */
    return q;
}
static inline uint64_t divmod243(__uint128_t x,uint8_t *rem){
    uint64_t hi=(uint64_t)(x>>64),lo=(uint64_t)x;
    __uint128_t mid=(__uint128_t)hi*DV_R64+lo;
    uint64_t m_hi=(uint64_t)(mid>>64),m_lo=(uint64_t)mid;
    uint64_t qmid=m_hi?DV_Q64+div243(m_lo+DV_R64):div243(m_lo);
    uint64_t q=hi*DV_Q64+qmid;
    *rem=(uint8_t)(x-(__uint128_t)q*243);
    return q;
}
/* 无除法器三进制右移：v / 3^sh（sh = 移位量 % 3^7 ∈ [0,2186]）。
 * 每 5 个 trit 对应一次 divmod243（除以 243=3^5，magic 无除法），余下 <5 trit 用
 * 逐 trit 除以 3（常数 3 的除法由编译器优化为乘法逆元）。全程零 128 位硬件除法。
 * 正确性：v < 3^45，除 3^sh 商必 < 3^45，天然落在值域内。 */
static inline __uint128_t shr3(__uint128_t v, long sh){
    uint8_t t;
    while(sh>=5){ v=divmod243(v,&t); sh-=5; }         /* 每步 ÷3^5 */
    while(sh-->0){ v=v/3; }                            /* 余 0..4 步 ÷3（常量除数） */
    return v;
}
/* 三进制算术右移：有符号值 ÷3^sh，向零取整（对齐 divmod_word / Python SRA）。
 * sh>=45 时商为 0；否则用无除法器 divmod_word 取商后按 3 补码存回。 */
static inline __uint128_t sar3(__uint128_t word, long sh){
    if(sh<=0) return word;
    if(sh>=XLEN) return 0;
    __int128_t q;
    divmod_word(s(word), (__int128_t)POW3[sh], &q, NULL);
    return u(q);
}
/* 三进制左移：word * 3^sh mod 3^45。sh>=45 时结果为 0，sh<=0 时不变。
 * 恒等式 (word·3^sh) mod 3^45 = (word mod 3^(45-sh))·3^sh 保证两个因子乘积 < 3^45，
 * 规避原 for 循环在 sh 可达 2186 时的 __uint128 溢出。 */
static inline __uint128_t shl3(__uint128_t word, long sh){
    if(sh<=0) return word;
    if(sh>=XLEN) return 0;
    return (word % POW3[XLEN-sh]) * POW3[sh];
}
/* TSEXT：取 word 低 5 trit（1 tryte）按 3 补码符号扩展到 45-trit 字。 */
static inline __uint128_t tsext(__uint128_t word){
    return u(sx(word % 243, 5));                       /* 243=3^5 */
}
/* LH 的符号扩展：取 word 低 10 trit（2 tryte）按 3 补码扩展到 45 trit。
 * 半阈值 (3^10-1)//2 = 29524；RISC-V 的 LH 就是符号扩展，本 ISA 与之对齐。 */
static inline __uint128_t sx10(__uint128_t word){
    return u(sx(word % 59049, 10));                    /* 59049=3^10 */
}
/* 优化版三值运算：无除法器取 tryte + 查表 + 正向累加。
 * 相比原来 unpack(9×128位除法)+SIMD-min+pack(45×乘加)：tryte 提取由 9 次硬件 div
 * 改为 9 次 magic mul+shift（零除指令），运算由 45 次逐 trit 降为 9 次查表。
 * mode 0=min 1=max 2=not 3=(a+b) mod 3 */
static __uint128_t tritwise(int mode,__uint128_t a,__uint128_t b){
    __uint128_t res=0,p=1;
    for(int i=0;i<9;i++){
        uint8_t ta,tb;
        a=divmod243(a,&ta); b=divmod243(b,&tb);
        uint8_t r=(mode==0)?LUT_MIN[ta][tb]:(mode==1)?LUT_MAX[ta][tb]:
                    (mode==2)?LUT_NOT[ta]:LUT_XOR[ta][tb];
        res+=(__uint128_t)r*p;p*=243;
    }
    return res;
}

/* 无除法器 90-trit 乘法：x,y < 3^45，全积 < 3^90。
 * tryte schoolbook：把两数各拆 9 个 tryte(base 243)，卷积得 18 tryte，
 * 进位传播后低 9 tryte=lo(积 mod 3^45)、高 9 tryte=hi(积 / 3^45)。
 * 全程只有 divmod243(magic) 与 32 位乘加，零 128 位硬件除法。
 * 用于 MULH/MULHSU/MULHU 取高半，根治原 u(a*b) 在 __int128 上的溢出。 */
static inline void mul90(__uint128_t x,__uint128_t y,
                         __uint128_t *lo,__uint128_t *hi){
    uint8_t xd[9],yd[9];
    for(int i=0;i<9;i++){ x=divmod243(x,&xd[i]); y=divmod243(y,&yd[i]); }
    uint32_t acc[18]={0};
    for(int i=0;i<9;i++)
        for(int j=0;j<9;j++) acc[i+j]+=(uint32_t)xd[i]*(uint32_t)yd[j]; /* <9*243^2<2^20 */
    uint32_t carry=0;
    for(int k=0;k<18;k++){ uint32_t w=acc[k]+carry; acc[k]=w%243; carry=w/243; }
    __uint128_t l=0,h=0,p=1;
    for(int i=0;i<9;i++){ l+=(__uint128_t)acc[i]*p; p*=243; }          /* p: 1..3^40 */
    p=1;
    for(int i=0;i<9;i++){ h+=(__uint128_t)acc[9+i]*p; p*=243; }
    *lo=l; *hi=h;
}
/* 三进制乘法低半：MUL 只需 (x*y) mod 3^45，低 9 个 tryte 的卷积与进位即可，
 * 省去高 9 tryte 的 36 次乘加 + 进位传播（相比 mul90 减约 44% 乘加）。 */
static inline __uint128_t mul90_lo(__uint128_t x,__uint128_t y){
    uint8_t xd[9],yd[9];
    for(int i=0;i<9;i++){ x=divmod243(x,&xd[i]); y=divmod243(y,&yd[i]); }
    uint32_t acc[9]={0};
    for(int i=0;i<9;i++)
        for(int j=0;i+j<9;j++) acc[i+j]+=(uint32_t)xd[i]*(uint32_t)yd[j];
    uint32_t carry=0;
    for(int k=0;k<9;k++){ uint32_t w=acc[k]+carry; acc[k]=w%243; carry=w/243; }
    __uint128_t l=0,p=1;
    for(int i=0;i<9;i++){ l+=(__uint128_t)acc[i]*p; p*=243; }
    return l;  /* 高 9 tryte 进位自然丢弃 = mod 3^45 */
}

/* MULH 系列统一实现：按 opcode 决定操作数的有/无符号解释，返回高半(3 补码 word)。 */
static inline __uint128_t mulh_hi(Op op,__uint128_t av,__uint128_t bv){
    __uint128_t lo,hi,xa=av,xb=bv;
    if(op!=OP_MULHU && s(av)<0) xa=MOD_X-av;   /* 有符号 a 取幅值 */
    if(op==OP_MULH  && s(bv)<0) xb=MOD_X-bv;   /* 有×有时 b 也取幅值 */
    mul90(xa,xb,&lo,&hi);
    int neg=(op==OP_MULH)?((s(av)<0)^(s(bv)<0)):(op==OP_MULHSU)?(s(av)<0):0;
    /* floor(prod/3^45)：负积且低位非零时，幅值高半需进位后再取补码，
     * 与 Python prod//MOD 的 floor 语义一致（原实现向零截断，负数差 1）。 */
    if(!neg) return hi;
    __uint128_t r=hi+((neg&&lo)?1:0);
    return r?(MOD_X-r):0;
}

/* A 扩展原子读改写。共享模式下全程持锁 -> 对公共内存原子；单线程直接执行。
 * 语义对齐 Python VM：LR 建保留；SC 校验保留+version 后写回并返回 0/1；
 * AMO 读改写返回旧值。addr = 有符号 reg[rs1]。 */
static inline void amo_exec(VM *v,Op op,int rd,int rs1,int rs2){
    __int128_t addr=s(v->reg[rs1]);
    if(v->shared) sh_lock(v->sync);
    if(op==OP_LRW){
        v->reg[rd]=load_word_nl(v,addr);
        v->lr_addr=addr; v->lr_valid=1;
        v->lr_version=v->sync?__atomic_load_n(&v->sync->version,__ATOMIC_RELAXED):0;
    } else if(op==OP_SCw){
        uint64_t cur=v->sync?__atomic_load_n(&v->sync->version,__ATOMIC_RELAXED):0;
        if(v->lr_valid && v->lr_addr==addr && cur==v->lr_version){
            store_word_nl(v,addr,v->reg[rs2]);
            if(v->sync)__atomic_fetch_add(&v->sync->version,1,__ATOMIC_RELAXED);
            v->reg[rd]=0;
        } else v->reg[rd]=1;
        v->lr_valid=0;
    } else {
        __uint128_t old=load_word_nl(v,addr),src=v->reg[rs2],newv;
        switch(op){
            case OP_AMOADD:  newv=u(s(old)+s(src)); break;
            case OP_AMOSWAP: newv=src; break;
            /* 原子位运算同样按逐 trit 三值逻辑（与 AND/OR/XOR 一致） */
            case OP_AMOAND:  newv=tritwise(0,old,src); break;
            case OP_AMOOR:   newv=tritwise(1,old,src); break;
            case OP_AMOXOR:  newv=tritwise(3,old,src); break;
            case OP_AMOMIN:  newv=u(s(old)<s(src)?s(old):s(src)); break;
            case OP_AMOMAX:  newv=u(s(old)>s(src)?s(old):s(src)); break;
            default:         newv=old; break;
        }
        store_word_nl(v,addr,newv);
        if(v->sync)__atomic_fetch_add(&v->sync->version,1,__ATOMIC_RELAXED);
        v->reg[rd]=old;
        v->lr_valid=0;                 /* AMO 破坏保留 */
    }
    if(v->shared) sh_unlock(v->sync);
}
/* 普通 store 破坏本核 LR 保留（区间重叠判断，与 Python _overlap 对齐）。 */
static inline void snoop_store(VM *v,__int128_t addr,int size){
    if(v->lr_valid && v->lr_addr>=0 && addr<v->lr_addr+9 && v->lr_addr<addr+size)
        v->lr_valid=0;
}

/* ---------------- syscall ---------------- */
enum{SYS_GETCWD=17,SYS_OPENAT=56,SYS_CLOSE=57,SYS_LSEEK=62,SYS_READ=63,SYS_WRITE=64,
SYS_READV=65,SYS_WRITEV=66,SYS_FSTATAT=79,SYS_FSTAT=80,SYS_EXIT=93,SYS_EXIT_GROUP=94,
SYS_SET_TID=96,SYS_FUTEX=98,SYS_CLOCK=113,SYS_YIELD=124,SYS_SIGACTION=134,
SYS_SIGPROCMASK=135,SYS_SIGRETURN=139,SYS_UNAME=160,SYS_GETPID=172,SYS_GETTID=178,
SYS_MUNMAP=215,SYS_MMAP=222,SYS_MPROTECT=226,SYS_BRK=214,SYS_GETRANDOM=278};
#define E_BADF  (-9)
#define E_INVAL (-22)
#define E_NOMEM (-12)
#define E_NOSYS (-38)

static void syscall_vm(VM *v){
    __int128_t num=s(v->reg[17]);
    __int128_t a0=s(v->reg[10]),a1=s(v->reg[11]),a2=s(v->reg[12]);
    if(num==0){v->halted=1;return;}
    __int128_t ret=0;
    if(num==SYS_WRITE){
        if(a2<0) ret=E_INVAL;                       /* 畸形 count：与 Python VM 统一 */
        else if((a0==1||a0==2)&&a1>=0&&a1<MEMSZ){
            __int128_t n=a2;if(a1+n>MEMSZ)n=MEMSZ-a1;
            for(__int128_t i=0;i<n;i++)vm_out(v,v->mem_ref[a1+i]);ret=n;
        } else ret=E_BADF;
    } else if(num==SYS_READ){
        if(a0==0&&a1>=0&&a1<MEMSZ){
            size_t n=(size_t)a2;if(n>v->stdin_len-v->stdin_pos)n=v->stdin_len-v->stdin_pos;
            if((size_t)(a1+n)>MEMSZ)n=MEMSZ-(size_t)a1;
            memcpy(v->mem_ref+a1,v->stdin_data+v->stdin_pos,n);v->stdin_pos+=n;ret=(__int128_t)n;
        } else ret=0;
    } else if(num==SYS_EXIT||num==SYS_EXIT_GROUP){v->exit_code=a0&0xFF;v->halted=1;ret=a0;}
    else if(num==SYS_BRK){
        if(a0==0)ret=v->brk_ptr;
        else if(a0>=v->heap_base&&a0<v->mmap_ptr){v->brk_ptr=a0;ret=a0;}
        else ret=v->brk_ptr;
    } else if(num==SYS_GETPID)ret=1;
    else if(num==SYS_GETTID||num==SYS_SET_TID)ret=1;
    else if(num==SYS_CLOCK){
        v->clock_nsec+=1000000;                        /* 每次 +1ms，单调 */
        if(v->clock_nsec>=1000000000){v->clock_nsec-=1000000000;v->clock_sec++;}
        if(a1>=0&&a1<MEMSZ-18){store_word(v,a1,(__uint128_t)v->clock_sec);store_word(v,a1+9,(__uint128_t)v->clock_nsec);ret=0;}
        else ret=E_INVAL;
    } else if(num==SYS_GETRANDOM){
        if(a0>=0&&a0<MEMSZ){
            __int128_t n=a1;if(a0+n>MEMSZ)n=MEMSZ-a0;
            for(__int128_t i=0;i<n;i++){uint32_t x=v->rng_state;x^=(x<<13)&0xFFFFFFFFu;x^=x>>17;x^=(x<<5)&0xFFFFFFFFu;v->rng_state=x;v->mem_ref[a0+i]=x%243;}
            ret=n;
        } else ret=E_INVAL;
    } else if(num==SYS_MMAP){
        __int128_t len=((a1+8)/9)*9,addr=v->mmap_ptr-len;  /* 9 tryte 对齐 */
        if(len>0&&v->brk_ptr<addr){v->mmap_ptr=addr;ret=addr;}else ret=E_NOMEM;
    } else if(num==SYS_MUNMAP||num==SYS_MPROTECT||num==SYS_FUTEX||num==SYS_YIELD||
              num==SYS_FSTAT||num==SYS_FSTATAT||num==SYS_LSEEK||num==SYS_SIGACTION||
              num==SYS_SIGPROCMASK||num==SYS_SIGRETURN||num==SYS_GETCWD||
              num==SYS_READV||num==SYS_WRITEV) ret=0;
    else if(num==SYS_OPENAT)ret=v->next_fd++;
    else if(num==SYS_CLOSE)ret=0;
    else if(num==SYS_UNAME){
        if(a0>=0&&a0<MEMSZ){const char*nm="TriRV";for(int i=0;i<5;i++)v->mem_ref[a0+i]=nm[i]%243;ret=0;}
        else ret=E_INVAL;
    } else ret=E_NOSYS;
    v->reg[10]=u(ret);
}

/* ---------------- 执行引擎 ---------------- */
static inline void step(VM *v){
    /* PC 是 tryte 地址：指令 i 位于 4*i。负/非 4 对齐/越界统一停机。 */
    if(v->halted||v->pc_<0||v->pc_%4||v->pc_>=4*v->nprog){v->halted=1;return;}
    Inst *in=&v->prog[v->pc_/4];
    int rd=in->rd,rs1=in->rs1<32?in->rs1:0,rs2=in->rs2<32?in->rs2:0;
    __int128_t imm=in->imm_s;              /* 带符号：加法/跳转/地址 */
    __uint128_t immu=in->imm_u;            /* 无符号位模式：按位运算 */
    __uint128_t immraw=in->imm_raw;        /* 原始字段：LUI */
    int oldpc=v->pc_; v->pc_+=4;           /* 下一条 = 下一个 tryte 字（+4） */
    __int128_t a=s(v->reg[rs1]),b=s(v->reg[rs2]);
    switch(in->op){
        case OP_ADD:v->reg[rd]=u(a+b);break;
        case OP_SUB:v->reg[rd]=u(a-b);break;
        case OP_MUL:v->reg[rd]=mul90_lo(v->reg[rs1],v->reg[rs2]);break; /* 只需低半 mod 3^45 */
        case OP_DIV:{__int128_t q;divmod_word(a,b,&q,NULL);v->reg[rd]=u(q);break;}   /* 无除法器 */
        case OP_REM:{__int128_t r;divmod_word(a,b,NULL,&r);v->reg[rd]=u(r);break;}   /* 无除法器 */
        case OP_MULH:case OP_MULHSU:case OP_MULHU:
            v->reg[rd]=mulh_hi(in->op,v->reg[rs1],v->reg[rs2]);break;  /* 90位高半,无除法 */
        case OP_DIVU:case OP_REMU:{
            __uint128_t av=v->reg[rs1],bv=v->reg[rs2];
            if(bv==0){v->reg[rd]=0;break;}
            __int128_t q,r;
            divmod_word((__int128_t)av,(__int128_t)bv,&q,&r);  /* 正数幅值,无除法 */
            v->reg[rd]=(in->op==OP_DIVU)?(__uint128_t)q:(__uint128_t)r;
            break;
        }
        case OP_ADDI:v->reg[rd]=u(a+imm);break;
        case OP_SLL:{long sh=(long)(umod2187(v->reg[rs2]));v->reg[rd]=shl3(v->reg[rs1],sh);break;}  /* 2187=3^7 常量除数 */
        case OP_SRL:{long sh=(long)(umod2187(v->reg[rs2]));v->reg[rd]=shr3(v->reg[rs1],sh);break;}  /* 无除法器 */
        case OP_SRA:{long sh=(long)(umod2187(v->reg[rs2]));v->reg[rd]=sar3(v->reg[rs1],sh);break;}  /* 算术右移,向零 */
        case OP_SLLI:{long sh=(long)in->imm_raw;v->reg[rd]=shl3(v->reg[rs1],sh);break;}  /* 移位量=无符号7-trit，与Python对齐 */
        case OP_SRLI:{long sh=(long)in->imm_raw;v->reg[rd]=shr3(v->reg[rs1],sh);break;}
        case OP_SRAI:{long sh=(long)in->imm_raw;v->reg[rd]=sar3(v->reg[rs1],sh);break;}
        case OP_SLT:v->reg[rd]=u(a<b?1:0);break;
        case OP_SLTU:v->reg[rd]=u(v->reg[rs1]<v->reg[rs2]?1:0);break;
        case OP_SLTI:v->reg[rd]=u(a<imm?1:0);break;
        case OP_SLTIU:v->reg[rd]=u(v->reg[rs1]<immu?1:0);break;
        /* 三进制寄存器里没有"二进制位"：AND/OR/XOR 一律**逐 trit**。
         *   AND = min（≡ TAND/TMIN）、OR = max（≡ TOR/TMAX）、XOR = (a+b) mod 3。
         * 早先这三条按宿主整数的二进制位运算实现，与 SLL/SRL（×3^k）不同进制，
         * 属实现遗留；2026-11-19 按 §5.1 决议 B 统一到三进制。 */
        case OP_XOR:v->reg[rd]=tritwise(3,v->reg[rs1],v->reg[rs2]);break;
        case OP_OR:v->reg[rd]=tritwise(1,v->reg[rs1],v->reg[rs2]);break;
        case OP_AND:v->reg[rd]=tritwise(0,v->reg[rs1],v->reg[rs2]);break;
        case OP_XORI:v->reg[rd]=tritwise(3,v->reg[rs1],immu);break;
        case OP_ORI:v->reg[rd]=tritwise(1,v->reg[rs1],immu);break;
        case OP_ANDI:v->reg[rd]=tritwise(0,v->reg[rs1],immu);break;
        /* 三进制无预测分支：比较生成平衡三值条件码 t∈{-1,0,+1}（纯算术无控制流），
         * 再由 t 得 cond，最后用位掩码无分支地在 fallthrough 与预计算目标间选择 PC。
         * 全程无 data-dependent 控制流 → CPU 分支预测器无事可做（无预测）。
         * 定义 TRIRV_PREDICTED 可切回传统 if 版本做性能对照。 */
        case OP_BEQ: case OP_BNE: case OP_BLT: case OP_BGE: case OP_BLTU: case OP_BGEU:{
            int lt,gt,cond;
            if(in->op==OP_BLTU||in->op==OP_BGEU){
                lt=(v->reg[rs1]<v->reg[rs2]); gt=(v->reg[rs1]>v->reg[rs2]);
            }else{
                lt=(a<b); gt=(a>b);
            }
            int t=gt-lt;                       /* 平衡三值 trit: -1=< , 0== , +1=> */
            switch(in->op){
                case OP_BEQ:  cond=(t==0);break;
                case OP_BNE:  cond=(t!=0);break;
                case OP_BLT:  cond=(t< 0);break;
                case OP_BGE:  cond=(t>=0);break;
                case OP_BLTU: cond=(t< 0);break;
                default:      cond=(t>=0);break; /* BGEU */
            }
#ifdef TRIRV_PREDICTED
            if(cond) v->pc_=in->target;        /* 对照：if 控制流，依赖 CPU 动态预测 */
#else
            int mask=-cond;                     /* cond∈{0,1} → mask∈{0,-1} */
            v->pc_ += mask & (in->target - v->pc_); /* 无预测：纯数据选择 */
#endif
            break;
        }
        case OP_SW:store_word(v,a+imm,v->reg[rs2]);snoop_store(v,a+imm,9);break;
        case OP_SB:store_tryte(v,a+imm,v->reg[rs2]);snoop_store(v,a+imm,1);break;
        case OP_SH:{uint8_t t0;uint64_t hi=divmod243(v->reg[rs2],&t0);store_tryte(v,a+imm,t0);store_tryte(v,a+imm+1,hi);snoop_store(v,a+imm,2);break;}  /* 无除法器 */
        case OP_LRW:case OP_SCw:case OP_AMOADD:case OP_AMOSWAP:case OP_AMOAND:
        case OP_AMOOR:case OP_AMOXOR:case OP_AMOMIN:case OP_AMOMAX:
            amo_exec(v,in->op,rd,rs1,rs2);break;
        case OP_LW:v->reg[rd]=load_word(v,a+imm);break;
        /* 与 RISC-V 一致：LB/LH 符号扩展，LBU/LHU 零扩展。
         * char 本身无符号（RISC-V psABI 亦然），编译器对 char 用 LBU，仅 signed char 用 LB。 */
        case OP_LB:v->reg[rd]=tsext(load_tryte(v,a+imm));break;
        case OP_LBU:v->reg[rd]=u(load_tryte(v,a+imm));break;
        case OP_LH:v->reg[rd]=sx10((__uint128_t)load_tryte(v,a+imm)+(__uint128_t)load_tryte(v,a+imm+1)*243);break;
        case OP_LHU:v->reg[rd]=(__uint128_t)load_tryte(v,a+imm)+(__uint128_t)load_tryte(v,a+imm+1)*243;break;
        case OP_TMAX:v->reg[rd]=tritwise(1,v->reg[rs1],v->reg[rs2]);break;
        case OP_TMIN:v->reg[rd]=tritwise(0,v->reg[rs1],v->reg[rs2]);break;
        case OP_TAND:v->reg[rd]=tritwise(0,v->reg[rs1],v->reg[rs2]);break;
        case OP_TOR:v->reg[rd]=tritwise(1,v->reg[rs1],v->reg[rs2]);break;
        case OP_TNOT:v->reg[rd]=tritwise(2,v->reg[rs1],0);break;
        case OP_TNEG:v->reg[rd]=u(-a);break;
        case OP_TSEXT:v->reg[rd]=tsext(v->reg[rs1]);break;
        case OP_TPACK:{__uint128_t x=v->reg[rs1];uint64_t bn=0,p=1;for(int i=0;i<5;i++){bn+=(uint64_t)(x%3)*p;x/=3;p*=3;}v->reg[rd]=u(div243(bn*256+242)%256);break;}  /* 无除法器 */
        case OP_TUNPACK:{__int128_t byte=v->reg[rs1]%256,res=0;for(int i=0;i<5;i++){byte*=3;int d=(int)((byte>>8)&0xFF);byte&=0xFF;res+=d*POW3[4-i];}v->reg[rd]=u(res);break;}
        case OP_JAL:v->reg[rd]=oldpc+4;v->pc_=in->target;break;
        case OP_JALR:{v->reg[rd]=oldpc+4;__int128_t t=s(v->reg[rs1])+imm;v->pc_=(int)align_down4(t);break;}  /* 目标按 tryte 对齐 */
        case OP_LUI:v->reg[rd]=u((__int128_t)immraw*POW3[7]);break;
        case OP_AUIPC:v->reg[rd]=u((__int128_t)oldpc+(__int128_t)immraw*POW3[7]);break;
        case OP_ECALL:
            if(v->priv==3)syscall_vm(v);                        /* M 态直连 syscall（兼容） */
            else take_trap(v,CAUSE_ECALL_U,0,oldpc);            /* U 态陷入 */
            break;
        case OP_EBREAK:take_trap(v,CAUSE_BREAKPOINT,0,oldpc);break;
        case OP_CSRRW:case OP_CSRRS:case OP_CSRRC:
        case OP_CSRRWI:case OP_CSRRSI:case OP_CSRRCI:
            csr_op(v,in->op,rd,rs1,immraw);                     /* immraw = CSR 地址 */
            break;
        case OP_MRET:mret(v);break;
        case OP_WFI:break;                                      /* 提示，中断在步末检查 */
        default:break;
    }
    v->reg[0]=0;
    v->steps++;
    /* 异步中断检查：MIE=1 且定时器挂起 -> 陷入（提交后、下一条前），mepc=下一条 PC */
    if(!v->halted && v->irq_timer && (v->csr[CSR_MSTATUS]&MSTATUS_MIE)){
        v->irq_timer=0;
        take_trap(v,((__uint128_t)1<<63)|CAUSE_MTIMER_LO,0,v->pc_);
    }
}
static void vm_run(VM *v,long max_steps){while(!v->halted&&v->steps<max_steps)step(v);}

/* ---------------- computed goto（直接线程化）执行引擎 ----------------
 * 消除 switch 的跳转表索引 + 范围检查：goto *dispatch[op] 一次间接跳转直达 handler。
 * 6 个分支用独立标签，避免原 step() 里共享标签后的内层 switch 与 lt/gt 双重比较。
 * 语义逐条对齐 step()；定义 TRIRV_SWITCH 可在 main 中切回传统 switch 版对照。 */
static void vm_run_cg(VM *v, long max_steps){
    static void *const dispatch[] = {
        [OP_ADD]=&&L_ADD,[OP_SUB]=&&L_SUB,[OP_SLL]=&&L_SLL,[OP_SLT]=&&L_SLT,
        [OP_SLTU]=&&L_SLTU,[OP_XOR]=&&L_XOR,[OP_SRL]=&&L_SRL,[OP_SRA]=&&L_SRA,
        [OP_OR]=&&L_OR,[OP_AND]=&&L_AND,[OP_MUL]=&&L_MUL,[OP_DIV]=&&L_DIV,
        [OP_REM]=&&L_REM,[OP_MULH]=&&L_MULH,[OP_MULHSU]=&&L_MULHSU,[OP_MULHU]=&&L_MULHU,
        [OP_DIVU]=&&L_DIVU,[OP_REMU]=&&L_REMU,
        [OP_ADDI]=&&L_ADDI,[OP_SLTI]=&&L_SLTI,[OP_SLTIU]=&&L_SLTIU,
        [OP_XORI]=&&L_XORI,[OP_ORI]=&&L_ORI,[OP_ANDI]=&&L_ANDI,[OP_SLLI]=&&L_SLLI,
        [OP_SRLI]=&&L_SRLI,[OP_SRAI]=&&L_SRAI,[OP_LB]=&&L_LB,[OP_LH]=&&L_LH,
        [OP_LW]=&&L_LW,[OP_LBU]=&&L_LBU,[OP_LHU]=&&L_LHU,[OP_SB]=&&L_SB,
        [OP_SH]=&&L_SH,[OP_SW]=&&L_SW,[OP_BEQ]=&&L_BEQ,[OP_BNE]=&&L_BNE,
        [OP_BLT]=&&L_BLT,[OP_BGE]=&&L_BGE,[OP_BLTU]=&&L_BLTU,[OP_BGEU]=&&L_BGEU,
        [OP_JAL]=&&L_JAL,[OP_JALR]=&&L_JALR,[OP_LUI]=&&L_LUI,[OP_AUIPC]=&&L_AUIPC,
        [OP_ECALL]=&&L_ECALL,[OP_EBREAK]=&&L_EBREAK,[OP_FENCE]=&&L_NOP,
        [OP_TMIN]=&&L_TMIN,[OP_TMAX]=&&L_TMAX,[OP_TNOT]=&&L_TNOT,[OP_TAND]=&&L_TAND,
        [OP_TOR]=&&L_TOR,[OP_TNEG]=&&L_TNEG,[OP_TSEXT]=&&L_TSEXT,
        [OP_TPACK]=&&L_TPACK,[OP_TUNPACK]=&&L_TUNPACK,
        [OP_CSRRW]=&&L_CSRRW,[OP_CSRRS]=&&L_CSRRS,[OP_CSRRC]=&&L_CSRRC,
        [OP_CSRRWI]=&&L_CSRRWI,[OP_CSRRSI]=&&L_CSRRSI,[OP_CSRRCI]=&&L_CSRRCI,
        [OP_MRET]=&&L_MRET,[OP_WFI]=&&L_WFI,
        [OP_LRW]=&&L_LRW,[OP_SCw]=&&L_SCw,[OP_AMOADD]=&&L_AMOADD,
        [OP_AMOSWAP]=&&L_AMOSWAP,[OP_AMOAND]=&&L_AMOAND,[OP_AMOOR]=&&L_AMOOR,
        [OP_AMOXOR]=&&L_AMOXOR,[OP_AMOMIN]=&&L_AMOMIN,[OP_AMOMAX]=&&L_AMOMAX,
        [OP_NOP]=&&L_NOP,
    };
    Inst *in; int rd,rs1,rs2,oldpc;
    __int128_t imm,a,b; __uint128_t immu,immraw;

    if(v->halted||v->pc_<0||v->pc_%4||v->pc_>=4*v->nprog){v->halted=1;return;}  /* PC=tryte 地址 */
    in=&v->prog[v->pc_/4]; oldpc=v->pc_; v->pc_+=4;
    rd=in->rd; rs1=in->rs1<32?in->rs1:0; rs2=in->rs2<32?in->rs2:0;
    imm=in->imm_s; immu=in->imm_u; immraw=in->imm_raw;
    a=s(v->reg[rs1]); b=s(v->reg[rs2]);
    goto *dispatch[in->op];

#define NEXT() do { v->reg[0]=0; v->steps++; \
    if(__builtin_expect(v->irq_timer&&(v->csr[CSR_MSTATUS]&MSTATUS_MIE),0)){ \
        v->irq_timer=0; take_trap(v,((__uint128_t)1<<63)|CAUSE_MTIMER_LO,0,v->pc_); \
    } \
    if(v->halted||v->pc_<0||v->pc_%4||v->pc_>=4*v->nprog||v->steps>=max_steps) goto L_DONE; /* PC=tryte 地址 */ \
    in=&v->prog[v->pc_/4]; oldpc=v->pc_; v->pc_+=4; \
    rd=in->rd; rs1=in->rs1<32?in->rs1:0; rs2=in->rs2<32?in->rs2:0; \
    imm=in->imm_s; immu=in->imm_u; immraw=in->imm_raw; \
    a=s(v->reg[rs1]); b=s(v->reg[rs2]); \
    goto *dispatch[in->op]; } while(0)
#ifdef TRIRV_PREDICTED
#define BRANCH(c) do { int cond=(c); \
    if(cond) v->pc_=in->target; \
    NEXT(); } while(0)
#else
#define BRANCH(c) do { int cond=(c); \
    int mask=-cond; v->pc_+=mask&(in->target-v->pc_); \
    NEXT(); } while(0)
#endif

L_ADD:   v->reg[rd]=u(a+b); NEXT();
L_SUB:   v->reg[rd]=u(a-b); NEXT();
L_MUL:   v->reg[rd]=mul90_lo(v->reg[rs1],v->reg[rs2]); NEXT(); /* 只需低半 mod 3^45 */
L_DIV:   {__int128_t q;divmod_word(a,b,&q,NULL);v->reg[rd]=u(q);} NEXT();          /* 无除法器 */
L_REM:   {__int128_t r;divmod_word(a,b,NULL,&r);v->reg[rd]=u(r);} NEXT();          /* 无除法器 */
L_MULH:   v->reg[rd]=mulh_hi(in->op,v->reg[rs1],v->reg[rs2]); NEXT();
L_MULHSU: v->reg[rd]=mulh_hi(in->op,v->reg[rs1],v->reg[rs2]); NEXT();
L_MULHU:  v->reg[rd]=mulh_hi(in->op,v->reg[rs1],v->reg[rs2]); NEXT();
L_DIVU:   {__uint128_t av=v->reg[rs1],bv=v->reg[rs2];if(!bv)v->reg[rd]=0;
           else{__int128_t q;divmod_word((__int128_t)av,(__int128_t)bv,&q,NULL);v->reg[rd]=(__uint128_t)q;}} NEXT();
L_REMU:   {__uint128_t av=v->reg[rs1],bv=v->reg[rs2];if(!bv)v->reg[rd]=0;
           else{__int128_t r;divmod_word((__int128_t)av,(__int128_t)bv,NULL,&r);v->reg[rd]=(__uint128_t)r;}} NEXT();
L_ADDI:  v->reg[rd]=u(a+imm); NEXT();
L_SLL:   {long sh=(long)(umod2187(v->reg[rs2]));v->reg[rd]=shl3(v->reg[rs1],sh);} NEXT();          /* 2187=3^7 常量除数 */
L_SRL:   {long sh=(long)(umod2187(v->reg[rs2]));v->reg[rd]=shr3(v->reg[rs1],sh);} NEXT();          /* 无除法器 */
L_SRA:   {long sh=(long)(umod2187(v->reg[rs2]));v->reg[rd]=sar3(v->reg[rs1],sh);} NEXT();          /* 算术右移,向零 */
L_SLLI:  {long sh=(long)immraw;v->reg[rd]=shl3(v->reg[rs1],sh);} NEXT();  /* 移位量=无符号7-trit */
L_SRLI:  {long sh=(long)immraw;v->reg[rd]=shr3(v->reg[rs1],sh);} NEXT();
L_SRAI:  {long sh=(long)immraw;v->reg[rd]=sar3(v->reg[rs1],sh);} NEXT();
L_SLT:   v->reg[rd]=u(a<b?1:0); NEXT();
L_SLTU:  v->reg[rd]=u(v->reg[rs1]<v->reg[rs2]?1:0); NEXT();
L_SLTI:  v->reg[rd]=u(a<imm?1:0); NEXT();
L_SLTIU: v->reg[rd]=u(v->reg[rs1]<immu?1:0); NEXT();
L_XOR:   v->reg[rd]=tritwise(3,v->reg[rs1],v->reg[rs2]); NEXT();
L_OR:    v->reg[rd]=tritwise(1,v->reg[rs1],v->reg[rs2]); NEXT();
L_AND:   v->reg[rd]=tritwise(0,v->reg[rs1],v->reg[rs2]); NEXT();
L_XORI:  v->reg[rd]=tritwise(3,v->reg[rs1],immu); NEXT();
L_ORI:   v->reg[rd]=tritwise(1,v->reg[rs1],immu); NEXT();
L_ANDI:  v->reg[rd]=tritwise(0,v->reg[rs1],immu); NEXT();
L_LB:    v->reg[rd]=tsext(load_tryte(v,a+imm)); NEXT();
L_LBU:   v->reg[rd]=u(load_tryte(v,a+imm)); NEXT();
L_LH:    v->reg[rd]=sx10((__uint128_t)load_tryte(v,a+imm)+(__uint128_t)load_tryte(v,a+imm+1)*243); NEXT();
L_LHU:   v->reg[rd]=(__uint128_t)load_tryte(v,a+imm)+(__uint128_t)load_tryte(v,a+imm+1)*243; NEXT();
L_LW:    v->reg[rd]=load_word(v,a+imm); NEXT();
L_SB:    store_tryte(v,a+imm,v->reg[rs2]);snoop_store(v,a+imm,1); NEXT();
L_SH:    {uint8_t t0;uint64_t hi=divmod243(v->reg[rs2],&t0);store_tryte(v,a+imm,t0);store_tryte(v,a+imm+1,hi);snoop_store(v,a+imm,2);} NEXT();  /* 无除法器 */
L_SW:    store_word(v,a+imm,v->reg[rs2]);snoop_store(v,a+imm,9); NEXT();
L_LRW:   amo_exec(v,OP_LRW,rd,rs1,rs2); NEXT();
L_SCw:   amo_exec(v,OP_SCw,rd,rs1,rs2); NEXT();
L_AMOADD:amo_exec(v,OP_AMOADD,rd,rs1,rs2); NEXT();
L_AMOSWAP:amo_exec(v,OP_AMOSWAP,rd,rs1,rs2); NEXT();
L_AMOAND:amo_exec(v,OP_AMOAND,rd,rs1,rs2); NEXT();
L_AMOOR: amo_exec(v,OP_AMOOR,rd,rs1,rs2); NEXT();
L_AMOXOR:amo_exec(v,OP_AMOXOR,rd,rs1,rs2); NEXT();
L_AMOMIN:amo_exec(v,OP_AMOMIN,rd,rs1,rs2); NEXT();
L_AMOMAX:amo_exec(v,OP_AMOMAX,rd,rs1,rs2); NEXT();
L_BEQ:   BRANCH(a==b);
L_BNE:   BRANCH(a!=b);
L_BLT:   BRANCH(a<b);
L_BGE:   BRANCH(a>=b);
L_BLTU:  BRANCH(v->reg[rs1]<v->reg[rs2]);
L_BGEU:  BRANCH(v->reg[rs1]>=v->reg[rs2]);
L_JAL:   v->reg[rd]=oldpc+4;v->pc_=in->target; NEXT();
L_JALR:  {v->reg[rd]=oldpc+4;__int128_t t=s(v->reg[rs1])+imm;v->pc_=(int)align_down4(t);} NEXT();  /* 目标按 tryte 对齐 */
L_LUI:   v->reg[rd]=u((__int128_t)immraw*POW3[7]); NEXT();
L_AUIPC: v->reg[rd]=u((__int128_t)oldpc+(__int128_t)immraw*POW3[7]); NEXT();
L_ECALL: if(v->priv==3)syscall_vm(v); else take_trap(v,CAUSE_ECALL_U,0,oldpc); NEXT();
L_EBREAK: take_trap(v,CAUSE_BREAKPOINT,0,oldpc); NEXT();
L_TMIN:  v->reg[rd]=tritwise(0,v->reg[rs1],v->reg[rs2]); NEXT();
L_TMAX:  v->reg[rd]=tritwise(1,v->reg[rs1],v->reg[rs2]); NEXT();
L_TAND:  v->reg[rd]=tritwise(0,v->reg[rs1],v->reg[rs2]); NEXT();
L_TOR:   v->reg[rd]=tritwise(1,v->reg[rs1],v->reg[rs2]); NEXT();
L_TNOT:  v->reg[rd]=tritwise(2,v->reg[rs1],0); NEXT();
L_TNEG:  v->reg[rd]=u(-a); NEXT();
L_TSEXT: v->reg[rd]=tsext(v->reg[rs1]); NEXT();
L_TPACK: {__uint128_t x=v->reg[rs1];uint64_t bn=0,p=1;for(int i=0;i<5;i++){bn+=(uint64_t)(x%3)*p;x/=3;p*=3;}v->reg[rd]=u(div243(bn*256+242)%256);} NEXT();  /* 无除法器 */
L_TUNPACK:{__int128_t byte=v->reg[rs1]%256,res=0;for(int i=0;i<5;i++){byte*=3;int d=(int)((byte>>8)&0xFF);byte&=0xFF;res+=d*POW3[4-i];}v->reg[rd]=u(res);} NEXT();
L_CSRRW: csr_op(v,OP_CSRRW,rd,rs1,immraw); NEXT();
L_CSRRS: csr_op(v,OP_CSRRS,rd,rs1,immraw); NEXT();
L_CSRRC: csr_op(v,OP_CSRRC,rd,rs1,immraw); NEXT();
L_CSRRWI:csr_op(v,OP_CSRRWI,rd,rs1,immraw); NEXT();
L_CSRRSI:csr_op(v,OP_CSRRSI,rd,rs1,immraw); NEXT();
L_CSRRCI:csr_op(v,OP_CSRRCI,rd,rs1,immraw); NEXT();
L_MRET:  mret(v); NEXT();
L_WFI:   NEXT();
L_NOP:   NEXT();
L_DONE:  ;
#undef BRANCH
#undef NEXT
}

/* ---------------- 加载 / 初始化 ---------------- */
static int load_trv(const char *path,Inst **out_prog,int *out_n,uint8_t segmem[MEMSZ]){
    FILE *f=fopen(path,"rb");if(!f){fprintf(stderr,"无法打开 %s\n",path);return -1;}
    char magic[4];if(fread(magic,1,4,f)!=4||memcmp(magic,"TRV1",4)){fprintf(stderr,"非 TRV1 格式\n");fclose(f);return -1;}
    uint32_t ver,n_inst,n_seg;fread(&ver,4,1,f);fread(&n_inst,4,1,f);fread(&n_seg,4,1,f);
    Inst *prog=malloc(sizeof(Inst)*n_inst);
    for(uint32_t i=0;i<n_inst;i++){uint32_t raw;fread(&raw,4,1,f);prog[i]=decode_one(raw);
        /* PC 是 tryte 地址：槽目标 (i+imm_s) 需 *4 转为 tryte 地址 */
        if(is_pcrel(prog[i].op)) prog[i].target=((int)i+(int)prog[i].imm_s)*4;}
    for(uint32_t s=0;s<n_seg;s++){
        uint32_t addr,len;fread(&addr,4,1,f);fread(&len,4,1,f);
        if(addr+len<=MEMSZ)fread(segmem+addr,1,len,f);else fseek(f,len,SEEK_CUR);
    }
    fclose(f);*out_prog=prog;*out_n=(int)n_inst;return 0;
}
static void vm_init_ex(VM *v,Inst *prog,int n,uint8_t *segmem,uint8_t *stdin_data,size_t stdin_len,
                       uint8_t *shared_mem,SharedSync *sync,int gargc,char **gargv){
    memset(v,0,sizeof(*v));
    v->mem_ref=shared_mem?shared_mem:v->mem;   /* 必须在 memset 之后设置 */
    v->shared=shared_mem?1:0; v->sync=sync;
    v->prog=prog;v->nprog=n;v->reg[2]=4096;
    if(!shared_mem) memcpy(v->mem_ref,segmem,MEMSZ);  /* 共享镜像已由调用方初始化一次，避免逐核清零 */
    v->heap_base=1<<19;v->brk_ptr=v->heap_base;v->mmap_ptr=(1<<20)-(1<<16);
    v->clock_sec=0;v->clock_nsec=0;v->rng_state=0x12345678;v->next_fd=3;
    v->priv=3;v->irq_timer=0;memset(v->csr,0,sizeof(v->csr));   /* Zicsr: 复位 M 态, CSR 清零 */
    v->lr_addr=-1;v->lr_valid=0;v->lr_version=0;                /* A 扩展保留集复位 */
    v->stdin_data=stdin_data;v->stdin_len=stdin_len;v->stdin_pos=0;
    /* ---- crt0 进程信息（零页固定地址）----
     * 本 VM 栈向上生长（ADDI sp,sp,+frame），RISC-V Linux 的"sp 指向 argc"
     * 惯例会立刻被第一帧覆盖，故放零页固定地址。这是栈方向决定的必要偏差：
     *   [0..8]   argc（1 字 = 9 tryte）
     *   [9..17]  argv 数组首地址（数组放 mmap 区之上的高地址带）
     *   [18..26] envp（NULL：本 VM 无环境变量）
     * argv 数组：argc+1 个字（argv[argc]=NULL 结尾），每个元素指向
     * 对应参数字符串（同样在高地址带，按 9 tryte 对齐，NUL 结尾）。 */
    if(gargc>0&&gargv){
        __int128_t base=v->mmap_ptr-16;         /* 参数区顶部（mmap 之下） */
        __int128_t arr=base-(__int128_t)(gargc+1)*9;   /* argv 数组（字对齐天然成立） */
        arr=((arr)/9)*9;
        store_word_nl(v,0,(__uint128_t)gargc);
        store_word_nl(v,9,(__uint128_t)arr);
        store_word_nl(v,18,0);                  /* envp=NULL */
        __int128_t sp=arr-(__int128_t)gargc*9*12;  /* 字符串区：每参数最多 9*12 tryte */
        sp=(sp/9)*9;
        for(int i=0;i<gargc;i++){
            size_t sl=strlen(gargv[i]);
            __int128_t need=(((__int128_t)sl+1+8)/9)*9;   /* 串+NUL，9 对齐 */
            if(sp-need<4096) break;             /* 安全下界：不碰代码/栈区 */
            __int128_t sentry=sp-need;          /* 先减后写：串占 [sentry,sp)，互不重叠 */
            store_word_nl(v,arr+(__int128_t)i*9,(__uint128_t)sentry);
            for(size_t k=0;k<sl;k++)
                v->mem_ref[sentry+k]=(uint8_t)(gargv[i][k]%243);
            v->mem_ref[sentry+sl]=0;
            sp=sentry;
        }
        store_word_nl(v,arr+(__int128_t)gargc*9,0);    /* argv[argc]=NULL */
    } else {
        /* 无 guest 参数：完全不动内存（零页可能被数据段占用，
         * wasm hello 的 data 段从 0 起，写零会把段数据清掉）。 */
    }
}
static void vm_init(VM *v,Inst *prog,int n,uint8_t *segmem,uint8_t *stdin_data,size_t stdin_len){
    vm_init_ex(v,prog,n,segmem,stdin_data,stdin_len,NULL,NULL,0,NULL);
}

/* ---------------- CLI ---------------- */
static void usage(const char *p){fprintf(stderr,"用法: %s [-n 次数] [-t 线程数] [-s] [-o 地址] <prog.trv> [stdin文件] [-- guest参数...]\n"
    "  -s  共享内存 SMP 模式：所有实例共享同一内存与原子锁\n"
    "  -o  运行后把该 tryte 地址的 8-tryte word 以 9 字节写到 stdout\n"
    "  --  之后的参数作为 guest 程序的 argv（argv[0]=.trv 路径）\n",p);}
int main(int argc,char**argv){
    init_consts();init_trit_tab();
    int repeat=1,nthreads=0,argi=1,shared=0,outaddr=-1;
    while(argi<argc&&argv[argi][0]=='-'){
        if(!strcmp(argv[argi],"-n")){repeat=atoi(argv[++argi]);argi++;}
        else if(!strcmp(argv[argi],"-t")){nthreads=atoi(argv[++argi]);argi++;}
        else if(!strcmp(argv[argi],"-s")){shared=1;argi++;}
        else if(!strcmp(argv[argi],"-o")){outaddr=atoi(argv[++argi]);argi++;}
        else break;
    }
    if(argi>=argc){usage(argv[0]);return 1;}
    uint8_t *segmem=calloc(1,MEMSZ);
    Inst *prog=NULL;int nprog=0;
    if(load_trv(argv[argi],&prog,&nprog,segmem))return 1;
    argi++;
    /* guest 命令行参数：
     *   prog [stdin文件] -- guest参数...   （-- 前的下一个参数若不是 --，
     *   仍按旧行为当 stdin 文件——全部既有测试脚本不受影响）
     * gargv[0]=程序路径（.trv），其余依序映射 guest argv。 */
    char **gargv=NULL;int gargc=0;
    uint8_t *stdin_data=NULL;size_t stdin_len=0;
    {   /* 先认 stdin 文件：从 argi 起到 "--" 之前的非选项参数 */
        int stop=argi;
        while(stop<argc&&strcmp(argv[stop],"--")) stop++;
        if(argi<stop){                      /* [argi, stop) 有 stdin 文件候选 */
            if(!strcmp(argv[argi],"-")){    /* "-" = 宿主 stdin 管道（unix 惯例） */
                size_t cap=1<<16;stdin_data=malloc(cap);size_t got=0;
                if(stdin_data){
                    size_t r;
                    while((r=fread(stdin_data+got,1,cap-got,stdin))>0){got+=r;if(got==cap){cap*=2;stdin_data=realloc(stdin_data,cap);}}
                    stdin_len=got;
                }
            } else {
                FILE *sf=fopen(argv[argi],"rb");
                if(sf){fseek(sf,0,SEEK_END);stdin_len=ftell(sf);rewind(sf);stdin_data=malloc(stdin_len?stdin_len:1);fread(stdin_data,1,stdin_len,sf);fclose(sf);}
            }
        }
        if(stop<argc){                      /* 有 "--"：其后全部为 guest argv */
            argi=stop+1;
            gargc=argc-argi+1;
            gargv=malloc(sizeof(char*)*(size_t)gargc);
            gargv[0]=argv[argi-2];          /* argv[0] = 程序路径（.trv） */
            for(int i=1;i<gargc;i++) gargv[i]=argv[argi-1+i];
        }
    }
    /* SMP 模式：所有实例共享一段公共内存 + 一把原子锁。
     * 关键：初始内存镜像只能加载一次——若每核都 memcpy 会在串行/并行下反复清零共享计数器。 */
    uint8_t *shared_mem=shared?calloc(1,MEMSZ):NULL;
    if(shared) memcpy(shared_mem,segmem,MEMSZ);   /* 共享镜像：初始化一次 */
    SharedSync sync={0,0};
    VM *vms=calloc(1,sizeof(VM)*(size_t)repeat);
    struct timespec t0,t1;clock_gettime(CLOCK_MONOTONIC,&t0);
    #pragma omp parallel for num_threads(nthreads?nthreads:1) if(repeat>1)
    for(int r=0;r<repeat;r++){
        vm_init_ex(&vms[r],prog,nprog,shared_mem?shared_mem:segmem,stdin_data,stdin_len,shared_mem,&sync,gargc,gargv);
#ifdef TRIRV_SWITCH
        vm_run(&vms[r],50000000);
#else
        vm_run_cg(&vms[r],50000000);
#endif
    }
    clock_gettime(CLOCK_MONOTONIC,&t1);
    double sec=(t1.tv_sec-t0.tv_sec)+(t1.tv_nsec-t0.tv_nsec)/1e9;
    if(outaddr>=0 && outaddr+9<=MEMSZ){
        uint8_t *base=shared_mem?shared_mem:vms[0].mem_ref;
        for(int i=0;i<9;i++) fwrite(&base[outaddr+i],1,1,stdout);  /* 9 tryte */
    } else fwrite(vms[0].output,1,vms[0].out_len,stdout);
    long total_steps=0;for(int r=0;r<repeat;r++)total_steps+=vms[r].steps;
    fprintf(stderr,"\n[TriRV C VM] 实例=%d 指令=%d 步数=%ld 时间=%.4fs 吞吐=%.2f MIPS exit=%lld halted=%d shared=%d\n",
            repeat,nprog,total_steps,sec,total_steps/sec/1e6,(long long)vms[0].exit_code,vms[0].halted,shared);
    return 0;
}
