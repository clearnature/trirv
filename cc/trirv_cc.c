/* trirv_cc.c — TriRV 优化 C 编译器（寄存器式代码生成，纯 C 实现）
 *
 * 与 trirv_cc.py（栈式、教学型）的区别：
 *   - 局部变量优先映射到物理寄存器（s1/s2-s6），不再每次 SW/LW 到栈帧槽
 *   - 表达式用寄存器传递（gen_expr 返回寄存器号），二元运算直接 OP rd,ra,rb
 *   - 纯寄存器函数无 prologue/epilogue
 *   - 常量折叠、立即数装载优化
 * 目标：消除 trirv_cc.py 中 83% 的栈搬运指令（70 条/迭代 -> ~5 条/迭代）。
 *
 * 输出 TRV1 格式（小端），现有 trirv_vm 可直接加载。
 * 用法: ./trirv_cc <src.c> <out.trv>
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <ctype.h>
#include <stdarg.h>

/* ===================== ISA 常量（对齐 trirv_isa.py）===================== */
#define XLEN 45
#define NREG 32
#define TRV_W 20

/* 物理寄存器编号（3-trit 字段 0..26）*/
#define ZERO 0
#define RA   1
#define SP   2
#define GP   3
#define TP   4
#define T0   5
#define T1   6
#define T2   7
#define FP   8    /* s0 */
#define S1   9
#define A0   10
#define A7   17
#define T3   28
#define T4   29
#define T5   30
#define T6   31
#define S2   18
#define S11  27

/* opcode */
#define O_OP 0
#define O_OPIMM 1
#define O_LOAD 2
#define O_STORE 3
#define O_BRANCH 4
#define O_JAL 5
#define O_JALR 6
#define O_LUI 7
#define O_AUIPC 8
#define O_SYSTEM 9
#define O_FENCE 10
#define O_TLOGIC 11
#define O_TPACK 12
#define O_TLIT 22
#define O_TC 23
#define O_ZICSR 16
#define O_PRIV 17
#define O_MEXT 13
#define O_DIVEXT 14
#define O_REMEXT 15

static char CURASM[512]="";
static int p3(int n){ int r=1; while(n-->0) r*=3; return r; }
static const int PW[20] = {1,3,9,27,81,243,729,2187,6561,19683,59049,
    177147,531441,1594323,4782969,14348907,43046721,129140163,387420489,1162261467};
#define MODX_LL 2954312706550833698643LL  /* 3^45 仅注释参考，实际用 __int128 */

/* 字段放置：把 w 位(3进制)值 x 放到 [lo,hi]，要求该区间为 0 */
static uint32_t putf(uint32_t v,int lo,int hi,int x){
    int w=hi-lo+1, mx=p3(w);
    if(x<0 || x>=mx){ fprintf(stderr,"putf overflow x=%d w=%d lo=%d | line: %s\n",x,w,lo,CURASM); exit(1); }
    return v + (uint32_t)((uint64_t)x*(uint64_t)PW[lo]);
}
static uint32_t encR(int op,int f2,int rs2,int rs1,int funct,int rd){
    uint32_t v=0;
    v=putf(v,0,2,op); v=putf(v,3,6,rd); v=putf(v,7,8,funct);
    v=putf(v,9,12,rs1); v=putf(v,13,16,rs2); v=putf(v,17,19,f2);
    return v;
}
/* 立即数越界必须炸，不能取模。
 * 取模会生成"能跑但算错"的指令：ADDI x1,x0,5000 静默变成 626，LUI 1594323 变成 0，
 * 程序照常输出数字，只是数字是错的 —— 比编译失败危险得多（2026-11-19 定为此策略）。
 * 范围与 trirv_asm.parse_int 一致：正数是原始位模式 0..3^n-1，负数按 3 补码 -lim..-1。 */
#define IMM7_LIM   1093
#define IMM13_LIM  797161
static int imm7ok(int v){
    if(v< -IMM7_LIM || v> 2186){ fprintf(stderr,"IMM7 overflow %d | line: %s\n",v,CURASM); exit(1); }
    return ((v%2187)+2187)%2187;
}
static int imm13ok(int v){
    if(v< -IMM13_LIM || v> 1594322){ fprintf(stderr,"IMM13 overflow %d | line: %s\n",v,CURASM); exit(1); }
    return ((v%1594323)+1594323)%1594323;
}
static uint32_t encI(int op,int imm7,int rs1,int funct,int rd){
    uint32_t v=0;
    v=putf(v,0,2,op); v=putf(v,3,6,rd); v=putf(v,7,8,funct);
    v=putf(v,9,12,rs1);
    int m=imm7ok(imm7);
    v=putf(v,13,19,m);
    return v;
}
static uint32_t encS(int op,int imm7,int rs2,int rs1,int funct){
    uint32_t v=0;
    v=putf(v,0,2,op); v=putf(v,7,8,funct);
    v=putf(v,9,12,rs1); v=putf(v,13,16,rs2);
    int m=imm7ok(imm7);
    v=putf(v,3,6,m%81); v=putf(v,17,19,m/81);
    return v;
}
static uint32_t encU(int op,int imm13,int rd){
    uint32_t v=0;
    v=putf(v,0,2,op); v=putf(v,3,6,rd);
    int m=imm13ok(imm13);
    v=putf(v,7,19,m);
    return v;
}

/* 助记符表: name -> (opcode,funct,funct2,fmt)  fmt: 0=R 1=I 2=S/B 3=U/J */
typedef struct { const char *n; int op,fu,f2,fmt; } InstDef;
static InstDef ITAB[] = {
    {"ADD",O_OP,0,0,0},{"SUB",O_OP,0,1,0},{"SLL",O_OP,1,0,0},{"SLT",O_OP,1,1,0},
    {"SLTU",O_OP,1,2,0},{"XOR",O_OP,2,0,0},{"SRL",O_OP,2,1,0},{"SRA",O_OP,2,2,0},
    {"OR",O_OP,3,0,0},{"AND",O_OP,3,1,0},
    {"MUL",O_MEXT,0,0,0},{"MULH",O_MEXT,1,0,0},{"MULHSU",O_MEXT,2,0,0},{"MULHU",O_MEXT,3,0,0},
    {"DIV",O_DIVEXT,0,0,0},{"DIVU",O_DIVEXT,1,0,0},{"REM",O_REMEXT,0,0,0},{"REMU",O_REMEXT,1,0,0},
    {"ADDI",O_OPIMM,0,0,1},{"SLTI",O_OPIMM,1,0,1},{"SLTIU",O_OPIMM,5,0,1},
    {"XORI",O_OPIMM,2,0,1},{"ORI",O_OPIMM,3,0,1},{"ANDI",O_OPIMM,6,0,1},
    {"SLLI",O_OPIMM,4,0,1},{"SRLI",O_OPIMM,7,0,1},{"SRAI",O_OPIMM,8,0,1},
    {"LB",O_LOAD,0,0,1},{"LH",O_LOAD,1,0,1},{"LW",O_LOAD,2,0,1},
    {"LBU",O_LOAD,3,0,1},{"LHU",O_LOAD,4,0,1},{"LW4",O_LOAD,5,0,1},
    {"SB",O_STORE,0,0,2},{"SH",O_STORE,1,0,2},{"SW",O_STORE,2,0,2},{"SW4",O_STORE,3,0,2},
    {"BEQ",O_BRANCH,0,0,2},{"BNE",O_BRANCH,1,0,2},{"BLT",O_BRANCH,2,0,2},
    {"BGE",O_BRANCH,3,0,2},{"BLTU",O_BRANCH,4,0,2},{"BGEU",O_BRANCH,5,0,2},
    {"JAL",O_JAL,0,0,3},{"JALR",O_JALR,0,0,1},{"LUI",O_LUI,0,0,3},
    {"SEQ",O_TLOGIC,3,0,0},{"SNE",O_TLOGIC,3,1,0},  /* 占位, 用 SUB+SLTIU 代替 */
    {"TMIN",O_TLOGIC,0,0,0},{"TMAX",O_TLOGIC,0,1,0},   /* R 型：逐 trit min/max，供位运算适配层 */
    {"TAND",O_TLOGIC,1,0,0},{"TOR",O_TLOGIC,1,1,0},
    /* TNOT {11,funct=0,funct2=2}：2026-11-22 本征方言启用。坑实录：TNOT 曾
       不在 ITAB——find_inst 返回 NULL → 行报 unknown mnemonic 后槽位留零字
       （NOP），~ 静默变成原值穿透；fmt==0 块内的特判因 d==NULL 是死代码。 */
    {"TNOT",O_TLOGIC,0,2,0},
    /* T-LIT 文字运算（opcode 22，§6.1）：R 型单操作数，funct=a */
    {"LIT0",O_TLIT,0,0,0},{"LIT1",O_TLIT,1,0,0},{"LIT2",O_TLIT,2,0,0},
    {"ECALL",O_SYSTEM,0,0,1},
    /* TC 压缩助记符（T10d：fmt=5 标识 TC 格式——15-trit / 3-tryte 对齐） */
    {"C-ADDI",O_TC,0,0,5},{"C-LW",O_TC,1,0,5},{"C-SW",O_TC,2,0,5},
    {"C-BEQ",O_TC,3,0,5},{"C-BNE",O_TC,4,0,5},
    {NULL,0,0,0,0}
};
static InstDef* find_inst(const char*n){
    for(InstDef*p=ITAB;p->n;p++) if(!strcmp(p->n,n)) return p;
    return NULL;
}
/* 寄存器别名表 */
typedef struct { const char*n; int r; } RegDef;
static RegDef REGTAB[] = {
    {"zero",ZERO},{"x0",0},{"ra",RA},{"sp",SP},{"gp",GP},{"tp",TP},
    {"t0",T0},{"x5",5},{"t1",T1},{"x6",6},{"t2",T2},{"x7",7},
    {"s0",FP},{"fp",FP},{"x8",8},{"s1",S1},{"x9",9},
    {"a0",10},{"a1",11},{"a2",12},{"a3",13},{"a4",14},{"a5",15},{"a6",16},{"a7",17},
    {"s2",S2},{"s3",19},{"s4",20},{"s5",21},{"s6",22},
    {"s7",23},{"s8",24},{"s9",25},{"s10",26},{"s11",S11},
    {"t3",T3},{"t4",T4},{"t5",T5},{"t6",T6},
    {"x10",10},{"x11",11},{"x12",12},{"x13",13},{"x14",14},{"x15",15},{"x16",16},{"x17",17},
    {"x18",18},{"x19",19},{"x20",20},{"x21",21},{"x22",22},{"x23",23},{"x24",24},{"x25",25},
    {"x26",26},{"x27",27},{"x28",28},{"x29",29},{"x30",30},{"x31",31},
    {NULL,0}
};
static int find_reg(const char*n){
    for(RegDef*p=REGTAB;p->n;p++) if(!strcmp(p->n,n)) return p->r;
    return -1;
}
static const char* regname(int r){
    static char buf[8];
    switch(r){
        case 0:return "zero"; case 1:return "ra"; case 2:return "sp";
        case 3:return "gp"; case 4:return "tp";
        case 5:return "t0"; case 6:return "t1"; case 7:return "t2";
        case 8:return "s0"; case 9:return "s1";
        case 10:return "a0"; case 11:return "a1"; case 12:return "a2"; case 13:return "a3";
        case 14:return "a4"; case 15:return "a5"; case 16:return "a6"; case 17:return "a7";
        case 18:return "s2"; case 19:return "s3"; case 20:return "s4"; case 21:return "s5";
        case 22:return "s6"; case 23:return "s7"; case 24:return "s8"; case 25:return "s9";
        case 26:return "s10"; case 27:return "s11";
        case 28:return "t3"; case 29:return "t4"; case 30:return "t5"; case 31:return "t6";
    }
    snprintf(buf,sizeof(buf),"x%d",r); return buf;
}

/* ===================== 词法分析 ===================== */
typedef enum { TK_EOF,TK_ID,TK_NUM,TK_STR,TK_KW,TK_OP } TokKind;
typedef struct {
    TokKind kind;
    long long num;      /* TK_NUM（截断值，供维度等小整数用） */
    __int128 numw;      /* TK_NUM 全 45-trit 精度 */
    char *s;            /* TK_ID/TK_KW/TK_OP 文本, TK_STR 解码字节 */
    int slen;           /* TK_STR 长度 */
    int straddr;        /* TK_STR 数据段起始地址(延迟分配) */
} Token;

static Token *TOKS; static int NTOK, CAPTOK, IPOS;

static int isid0(int c){ return isalpha(c)||c=='_'; }
static int isidc(int c){ return isalnum(c)||c=='_'; }

static void tok_push(Token t){
    if(NTOK>=CAPTOK){ CAPTOK=CAPTOK?CAPTOK*2:256; TOKS=realloc(TOKS,CAPTOK*sizeof(Token)); }
    TOKS[NTOK++]=t;
}
static int is_kw(const char*s){
    static const char*kw[]={"int","char","void","unsigned","signed","struct","va_list","__tint2","__tint","__tbool",
        "if","else","while","do","for","return","break","continue","typedef","const","goto","switch","case","default","else","extern",NULL};
    for(int i=0;kw[i];i++) if(!strcmp(s,kw[i])) return 1;
    return 0;
}
static void alias_add(const char*a,const char*b);
static void weakg_add(const char*n);   /* 弱别名注册（定义见 find_label 区）*/
static const char*alias_follow(const char*n);       /* 弱别名跟随：a → b 的体（find_label 区）*/
static void lex(const char*src){
    const char*p=src;
    while(*p){
        if(isspace((unsigned char)*p)){p++;continue;}
        if(p[0]=='/'&&p[1]=='/'){ while(*p&&*p!='\n')p++; continue; }
        if(p[0]=='/'&&p[1]=='*'){ p+=2; while(*p&&!(p[0]=='*'&&p[1]=='/'))p++; if(*p)p+=2; continue; }
        if(*p=='#'){                                        /* 行指令（2026-11-22 A6）：
               #pragma weak a = b —— 弱别名（a 引用绑 b 的体，强定义可覆盖）；
               其余 # 行跳过（本编译器无预处理指令集） */
            p++;
            while(*p==' ')p++;
            if(!strncmp(p,"pragma",6)&&!isalnum((unsigned char)p[6])&&p[6]!='_'){ p+=6;
                while(*p==' ')p++;
                if(!strncmp(p,"weak",4)&&!isalnum((unsigned char)p[4])&&p[4]!='_'){ p+=4;
                    while(*p==' ')p++;
                    char a[128]={0},b[128]={0};
                    int k=0; while(*p&&!isspace((unsigned char)*p)&&*p!='='&&k<127) a[k++]=*p++;
                    a[k]=0; while(*p==' ')p++;
                    if(*p=='='){ p++; while(*p==' ')p++;
                        k=0; while(*p&&!isspace((unsigned char)*p)&&k<127) b[k++]=*p++;
                        b[k]=0;
                        if(b[0]) alias_add(a,b);
                    } else {
                        /* 无目标形态 = 弱全局（A6 顺延）：同名强定义让位之 */
                        weakg_add(a);
                        if(getenv("TRV_ASM_DEBUG")) fprintf(stderr,"[weakg] %s\n",a);
                    }
                }
            }
            while(*p&&*p!='\n')p++;
            continue;
        }
        Token t={0};
        if(isdigit((unsigned char)*p)){
            /* 45-trit 字面量超出 long long：用 __int128 累加 */
            __int128 v=0; int base=10;
            if(p[0]=='0'&&(p[1]=='x'||p[1]=='X')){ base=16; p+=2; }
            char*end=p;
            for(;;){
                int d=-1;
                char ch=*end;
                if(ch>='0'&&ch<='9') d=ch-'0';
                else if(ch>='a'&&ch<='f') d=ch-'a'+10;
                else if(ch>='A'&&ch<='F') d=ch-'A'+10;
                if(d<0||d>=base) break;
                v=v*base+d; end++;
            }
            t.kind=TK_NUM; t.num=(long long)v; t.numw=v; tok_push(t); p=end; continue;
        }
        if(*p=='"'){
            p++; char*buf=malloc(1024); int n=0;
            while(*p&&*p!='"'){
                if(*p=='\\'&&p[1]){ p++; char c=*p;
                    if(c=='n')buf[n++]='\n'; else if(c=='t')buf[n++]='\t';
                    else if(c=='r')buf[n++]='\r'; else if(c=='0')buf[n++]='\0';
                    else buf[n++]=c; p++;
                } else buf[n++]=*p++;
            }
            if(*p)p++;
            buf[n++]='\0'; t.kind=TK_STR; t.s=buf; t.slen=n; tok_push(t); continue;
        }
        if(*p=='\''){
            p++; char c=*p;
            if(c=='\\'&&p[1]){ p++; c=*p; if(c=='n')c='\n'; else if(c=='t')c='\t'; else if(c=='0')c='\0'; }
            t.kind=TK_NUM; t.num=(unsigned char)c; t.numw=(__int128)(unsigned char)c; tok_push(t);
            p++; if(*p=='\'')p++; continue;
        }
        if(isid0((unsigned char)*p)){
            const char*q=p; while(isidc((unsigned char)*q))q++;
            int len=q-p; char*s=malloc(len+1); memcpy(s,p,len); s[len]=0;
            t.s=s; t.kind=is_kw(s)?TK_KW:TK_ID; tok_push(t); p=q; continue;
        }
        /* 多字符运算符 */
        static const char*ops[]={"...","<<=",">>=","==","!=","<=",">=","&&","||","<<",">>","++","--","->",
            "*=","/=","%=","+=","-=","&=","^=","|=",NULL};
        int matched=0;
        for(int i=0;ops[i];i++){ int L=strlen(ops[i]);
            if(!strncmp(p,ops[i],L)){ char*s=malloc(L+1); memcpy(s,ops[i],L); s[L]=0;
                t.kind=TK_OP; t.s=s; tok_push(t); p+=L; matched=1; break; }
        }
        if(matched) continue;
        { char*s=malloc(2); s[0]=*p; s[1]=0; t.kind=TK_OP; t.s=s; tok_push(t); p++; }
    }
    Token e={0}; e.kind=TK_EOF; e.s=""; tok_push(e);
}

/* ===================== AST ===================== */
typedef enum {
    N_NUM,N_STR,N_VAR,N_BIN,N_UN,N_ASSIGN,N_CALL,N_INDEX,N_MEMBER,
    N_PREINC,N_POSTINC,N_DEREF,N_ADDR,
    N_BLOCK,N_DECL,N_IF,N_WHILE,N_FOR,N_RETURN,N_EXPR,N_BREAK,N_CONT,
    N_FUNC,N_INITLIST,N_VASTART,N_VAARG,N_VAEND,N_DOWHILE,N_CAST,N_LABEL,N_GOTO,N_SWITCH,N_CASE,N_DEFAULT,N_COMPOUND
} NodeKind;
typedef struct Node Node;
struct Node {
    NodeKind k;
    long long num;
    __int128 numw;      /* N_NUM 的全精度字面量 */
    char *s;            /* 名称/运算符/成员名 */
    int slen;           /* TK_STR 字节长度 */
    int straddr;
    int strsym;         /* TK_STR 字符串符号序号 = STRSEG 下标（跨单元符号化）*/
    Node *l,*r,*e;      /* 二元左右 / 一元子 */
    Node *cond,*then,*els,*init,*step,*expr;
    Node *stmt;         /* while/for 单语句体 */
    Node *fbody;        /* 函数体(block) */
    Node **body;        /* block 语句数组 */
    Node **args; int nargs;
    Node **items; int nitems;   /* initlist / decl 列表 */
    char **dnames; char **dtypes; Node **dinit; int ndecls;  /* decl */
    char **params; char **ptypes; int nparams;  /* func */
    char *rettype; int variadic;
    int arrow;          /* member -> */
    int deref;          /* parse 辅助 */
    int sret_off;       /* 本调用点的 sret 返回缓冲在帧内的偏移；-1 = 不需要 */
};
static Node* nnew(NodeKind k){
    Node*n=calloc(1,sizeof(Node)); n->k=k; n->sret_off=-1; return n;
}
static void *grow(void*p,int*cap,int need,int sz){
    if(need>=*cap){ *cap=(*cap?*cap*2:8); while(need>=*cap)*cap*=2; p=realloc(p,*cap*sz); }
    return p;
}

/* ===================== 递归下降 Parser ===================== */
static Token* cur(){ return &TOKS[IPOS]; }
static int at(const char*v){ Token*t=cur(); return t->s&&!strcmp(t->s,v)&&(t->kind==TK_OP||t->kind==TK_KW); }
static int atk(TokKind k){ return cur()->kind==k; }
static Token* take(){ return &TOKS[IPOS++]; }
static Token* expect(const char*v){ if(!at(v)){ fprintf(stderr,"expect '%s' got '%s'\n",v,cur()->s); exit(1);} return take(); }
static int accept(const char*v){ if(at(v)){IPOS++;return 1;} return 0; }
/* 数组维度可能是数字 token（只有 num）也可能是符号 */
static void dim_text(char*out,size_t cap){
    Token*t=cur();
    if(t->kind==TK_NUM) snprintf(out,cap,"%lld",t->num);
    else snprintf(out,cap,"%s",t->s?t->s:"0");
    take();
}


static Node *parse_expr(void);
static Node *parse_stmt(void);
static Node *parse_block(void);

/* 类型：返回 malloc 的字符串, 如 "int","char*","int[3]","struct Foo" */
/* typedef 名 -> 原类型字符串。C 子集：typedef 只做纯别名（无指针/数组组合声明）。 */
typedef struct { char*name; char*real; } Typedef;
static Typedef *TYPEDEFS; static int NTDEF, CAPTDEF;
static const char* typedef_find(const char*name){
    for(int i=0;i<NTDEF;i++) if(!strcmp(TYPEDEFS[i].name,name)) return TYPEDEFS[i].real;
    return NULL;
}
static void struct_def_semi(const char*name,int semi);   /* 匿名 struct（typedef 用，semi=0） */
static char* parse_type(void){
    /* typedef 别名先行（TK_ID 形态）；别名可再带 * */
    if(atk(TK_ID)){
        const char*real=typedef_find(cur()->s);
        if(real){
            take();
            char*ty=strdup(real);
            int len=strlen(ty);
            while(at("*")){ take(); ty[len++]='*'; ty[len]=0; }
            return ty;
        }
    }
    if(!(atk(TK_KW)||atk(TK_ID))){ fprintf(stderr,"parse_type: bad token '%s'\n",cur()->s); exit(1); }
    while(accept("const")){ }               /* 限定符：解析后丢弃（无写保护检查） */
    while(accept("extern")){ }              /* 存储类说明符：纯引用语义（2026-11-22 A3）——
                                               不分配存储，绑定到定义单元的符号 */
    char base[128];
    /* char 的符号性：RISC-V psABI 规定 char 默认无符号（GCC DEFAULT_SIGNED_CHAR=0），
     * 与 TriRV 的 tryte 值域 0..242 天然吻合。故 char/unsigned char 走 LBU 零扩展，
     * 只有显式 signed char 走 LB 符号扩展取 -121..121。 */
    if(accept("unsigned")){
        while(accept("const")){ }              /* unsigned const char 顺序 */
        if(accept("char")) snprintf(base,sizeof(base),"uchar");
        else { accept("int"); snprintf(base,sizeof(base),"unsigned"); }
    } else if(accept("signed")){
        while(accept("const")){ }
        if(accept("char")) snprintf(base,sizeof(base),"schar");
        else { accept("int"); snprintf(base,sizeof(base),"int"); }
    } else if(accept("__tint2")){
        /* __tint2（2026-11-23 C2 方案 A）：4-tryte/20-trit 档——存取即窄化
           （SW4 mod 3^20 截断 / LW4 sx20 符号扩展），寄存器内 int 语义 */
        snprintf(base,sizeof(base),"__tint2");
    } else if(accept("__tint")){
        /* __tint（批B）：Kleene 逻辑词（9 tryte，与 int 同宽）——& | ~ ==
           != 真值判断直发，^/序比较/算术/混 int 门控拒绝 */
        snprintf(base,sizeof(base),"__tint");
    } else if(accept("__tbool")){
        /* __tbool（批B）：1-tryte Kleene 布尔（值域 0/1/2，与 __tint 同编码）——
           有符号 LB 容器（与 tri-tcc VT_DEFSIGN 口径一致，精确往返） */
        snprintf(base,sizeof(base),"__tbool");
    } else if(accept("int")){
        while(accept("const")){ }
        snprintf(base,sizeof(base),"int");
    }
    else if(accept("char")) snprintf(base,sizeof(base),"char");
    else if(accept("void")) snprintf(base,sizeof(base),"void");
    else if(accept("struct")){
        if(at("{")){
            /* 匿名 struct 定义（typedef struct {...} NAME; 用）：
             * 生成内部名并解析成员体 */
            static int anon_seq=0;
            char anm[32]; snprintf(anm,sizeof(anm),"__anon%d",anon_seq++);
            struct_def_semi(anm,0);
            snprintf(base,sizeof(base),"struct %s",anm);
        } else {
            char*nm=take()->s; snprintf(base,sizeof(base),"struct %s",nm);
        }
    }
    else if(accept("va_list")) snprintf(base,sizeof(base),"va_list");
    else { snprintf(base,sizeof(base),"%s",take()->s); }
    int len=strlen(base); char*ty=malloc(256); strcpy(ty,base);
    while(at("*")){ take(); ty[len++]='*'; ty[len]=0; }
    return ty;
}

static Node* parse_unary(void);   /* forward：cast 在 parse_primary 里递归用 */

/* cast 消歧：当前在 "(" 之后。KW 类型 -> 一定 cast。
 * （变量名与 KW 不会有交集，无需二义分析） */
static int is_cast_ambiguous(void){
    (void)0;
    return 0;   /* KW 分支总走 cast；此钩子留作将来 (fn)(x) 二义扩展 */
}

static Node* parse_initval(void);       /* 复合字面量 (T){...} 引用 */
static void gen_init_at(const char*ty,int off,Node*init,int*slot);
static Node* parse_primary(void){
    if(atk(TK_NUM)){ Token*tk=take(); Node*n=nnew(N_NUM); n->num=tk->num; n->numw=tk->numw; return n; }
    if(atk(TK_STR)){
        Token*t=take(); Node*n=nnew(N_STR); n->s=t->s; n->slen=t->slen; return n;
    }
    if(accept("(")){
        /* cast 探测： "(" 后是类型关键字/typedef别名 则按 cast 解析。
         * TriRV 所有标量等宽（45-trit 字），指针 cast 是类型标注；
         * (char)/(unsigned char) 整数 cast 有 mod 243 截断语义。 */
        if((atk(TK_KW) && !at("(") && !is_cast_ambiguous()) ||
           (atk(TK_ID) && typedef_find(cur()->s))){
            /* 统一回溯探测：parse_type 后若紧跟 '{' → 复合字面量
             * (TYPE){init}；否则回溯按 cast 解析（(char)x 等）。
             * 注意 C99 语法 (type){init} 的 ')' 在 type 之后、'{' 之前，
             * 所以 parse_type 返回时 cur==")" 需先回溯再看 "{"。 */
            int save=IPOS;
            char*sv = parse_type();
            /* (type){init}：type 之后是 ')' 再 '{'——先吃 ')' 再探测 */
            expect(")");
            if(at("{")){
                Node*init=parse_initval();
                Node*n=nnew(N_COMPOUND); n->s=sv; n->e=init;
                return n;
            }
            IPOS=save;   /* 非 {：回溯重走 cast 路径（会再次吃 ')'） */
            sv = parse_type();
            expect(")");
            Node*n=nnew(N_CAST); n->s=sv;
            n->e=parse_unary();
            return n;
        }
        Node*n=parse_expr(); expect(")"); return n;
    }
    if(atk(TK_ID)){
        char*name=take()->s;
        /* 变参内建：va_arg 第二个实参是类型，不能按表达式解析 */
        if(!strcmp(name,"va_start")||!strcmp(name,"va_arg")||!strcmp(name,"va_end")){
            if(accept("(")){
                Node*n=nnew(!strcmp(name,"va_start")?N_VASTART:(!strcmp(name,"va_arg")?N_VAARG:N_VAEND));
                n->e=parse_expr();
                if(n->k==N_VAARG){ expect(","); n->s=parse_type(); }
                expect(")");
                return n;
            }
        }
        if(accept("(")){
            Node*n=nnew(N_CALL); n->s=name;
            int cap=0;
            if(!at(")")){
                do { n->args=grow(n->args,&cap,n->nargs,sizeof(Node*));
                     n->args[n->nargs++]=parse_expr(); } while(accept(","));
            }
            expect(")");
            return n;
        }
        /* 函数指针调用：(*fp)(...) 形态（当前 token 是 "*"）。
         * 名字已消费（TK_ID 后紧跟 "(" 时早退；这里 name 后是 "(" 且再后 "*"）。
         * 实际上进入本分支时 cur=="("：name 已 take，accept 那次 "(" 没发生。 */
        if(at("(")&&TOKS[IPOS+1].s&&!strcmp(TOKS[IPOS+1].s,"*")){
            take();                               /* ( */
            take();                               /* * */
            Node*target=parse_expr();             /* fp 表达式 */
            expect(")");
            if(accept("(")){
                Node*n=nnew(N_CALL);
                n->s=strdup("(*target)");         /* 间接调用标记 */
                n->e=target;                      /* 目标表达式挂在 e 上 */
                int cap=0;
                if(!at(")")){
                    do { n->args=grow(n->args,&cap,n->nargs,sizeof(Node*));
                         n->args[n->nargs++]=parse_expr(); } while(accept(","));
                }
                expect(")");
                return n;
            }
            /* (*fp) 不跟调用：退化为普通解引用 */
            Node*d=nnew(N_DEREF); d->e=target; return d;
        }
        Node*n=nnew(N_VAR); n->s=name; return n;
    }
    fprintf(stderr,"parse_primary: bad '%s' (prev='%s')\n",cur()->s,
            IPOS>0&&TOKS[IPOS-1].s?TOKS[IPOS-1].s:"?");
    { int k; for(k=IPOS-3;k<IPOS+3;k++)
        if(k>=0&&k<NTOK&&TOKS[k].s) fprintf(stderr,"   tok[%d]='%s'\n",k,TOKS[k].s); }
    exit(1);
}
static Node* parse_postfix(void){
    Node*n=parse_primary();
    for(;;){
        if(accept("[")){ Node*i=parse_expr(); expect("]"); Node*x=nnew(N_INDEX); x->l=n; x->r=i; n=x; }
        else if(at("(")&&(n->k==N_DEREF||(n->k==N_CAST&&n->s&&strchr(n->s,'*')))){
            /* 间接调用两种形态：
             *   (*fp)(...)          —— 解引用调用
             *   ((cmpfun)cmp)(...)  —— 函数指针类型 cast 后调用（qsort_nr）
             * 目标挂 n->e，s 标记 "(*target)"。 */
            take();                               /* ( */
            Node*call=nnew(N_CALL);
            call->s=strdup("(*target)");
            call->e=n->e;
            int cap=0;
            if(!at(")")){
                do { call->args=grow(call->args,&cap,call->nargs,sizeof(Node*));
                     call->args[call->nargs++]=parse_expr(); } while(accept(","));
            }
            expect(")");
            n=call;
        }
        else if(at(".")||at("->")){
            int arrow = at("->"); take();
            char*mt=take()->s;
            Node*x=nnew(N_MEMBER); x->l=n; x->s=mt; x->arrow=arrow; n=x;
        }
        else if(accept("++")){ Node*x=nnew(N_POSTINC); x->e=n; x->num=1; n=x; }
        else if(accept("--")){ Node*x=nnew(N_POSTINC); x->e=n; x->num=-1; n=x; }
        else break;
    }
    return n;
}
static Node* parse_unary(void){
    if(accept("++")){ Node*x=nnew(N_PREINC); x->num=1; x->e=parse_unary(); return x; }
    if(accept("--")){ Node*x=nnew(N_PREINC); x->num=-1; x->e=parse_unary(); return x; }
    if(accept("-")){ Node*x=nnew(N_UN); x->s="-"; x->e=parse_unary(); return x; }
    if(accept("+")){ return parse_unary(); }
    if(accept("!")){ Node*x=nnew(N_UN); x->s="!"; x->e=parse_unary(); return x; }
    if(accept("~")){ Node*x=nnew(N_UN); x->s="~"; x->e=parse_unary(); return x; }
    if(accept("*")){ Node*x=nnew(N_DEREF); x->e=parse_unary(); return x; }
    if(accept("&")){ Node*x=nnew(N_ADDR); x->e=parse_unary(); return x; }
    return parse_postfix();
}
/* 二元优先级 climbing */
static int prec(const char*op){
    if(!strcmp(op,"*")||!strcmp(op,"/")||!strcmp(op,"%"))return 10;
    if(!strcmp(op,"+")||!strcmp(op,"-"))return 9;
    if(!strcmp(op,"<<")||!strcmp(op,">>"))return 8;
    if(!strcmp(op,"<")||!strcmp(op,"<=")||!strcmp(op,">")||!strcmp(op,">="))return 7;
    if(!strcmp(op,"==")||!strcmp(op,"!="))return 6;
    if(!strcmp(op,"&"))return 5;
    if(!strcmp(op,"^"))return 4;
    if(!strcmp(op,"|"))return 3;
    if(!strcmp(op,"&&"))return 2;
    if(!strcmp(op,"||"))return 1;
    return -1;
}
static Node* parse_bin(int minp){
    Node*l=parse_unary();
    for(;;){
        Token*t=cur(); if(t->kind!=TK_OP)break;
        int p=prec(t->s); if(p<minp)break;
        take(); Node*r=parse_bin(p+1);
        Node*x=nnew(N_BIN); x->s=t->s; x->l=l; x->r=r; l=x;
    }
    return l;
}
static Node* parse_assign(void){
    Node*l=parse_bin(1);
    if(at("=")){
        take(); Node*r=parse_assign();
        Node*x=nnew(N_ASSIGN); x->l=l; x->r=r; return x;
    }
    /* 复合赋值 a+=b -> a = a+b */
    static const char*cs[]={"*=","/=","%=","+=","-=","<<=",">>=","&=","^=","|=",NULL};
    for(int i=0;cs[i];i++) if(at(cs[i])){
        take(); Node*r=parse_assign();
        char op[4]; strncpy(op,cs[i],strlen(cs[i])-1); op[strlen(cs[i])-1]=0;
        Node*bin=nnew(N_BIN); bin->s=strdup(op); bin->l=l; bin->r=r;
        Node*x=nnew(N_ASSIGN); x->l=l; x->r=bin; return x;
    }
    return l;
}
static Node* parse_expr(void){
    /* 三目 cond ? a : b（右结合，优先级在赋值之上、|| 之下） */
    Node*c=parse_assign();
    if(at("?")){
        take();
        Node*a=parse_expr();
        expect(":");
        Node*b=parse_expr();
        Node*n=nnew(N_IF);
        n->cond=c; n->then=a; n->els=b;
        return n;
    }
    return c;
}

/* initlist or expr */
static Node* parse_initval(void){
    if(accept("{")){
        Node*n=nnew(N_INITLIST); int cap=0;
        if(!at("}")){
            do{ n->items=grow(n->items,&cap,n->nitems,sizeof(Node*));
                n->items[n->nitems++]=parse_initval(); } while(accept(","));
        }
        expect("}"); return n;
    }
    return parse_expr();
}

static Node* parse_decl_nosemi(void);   /* for(init) 用：声明不吃分号 */
static int DECL_KEEP_SEMI;
static Node* parse_decl(void){
    /* 声明: type name[=init], name[=init], ... ; */
    char*ty=parse_type();
    /* base = ty 去掉全部尾部 '*'：后续声明符各自的星号独立叠加
     * （int *a, *b 的 b 也是 int*；若叠加则会错成 int**） */
    char base[256]; snprintf(base,sizeof(base),"%s",ty);
    { int bl=strlen(base);
      while(bl>0&&base[bl-1]=='*') base[--bl]=0; }
    Node*n=nnew(N_DECL);
    n->dnames=malloc(sizeof(char*)*64);
    n->dtypes=malloc(sizeof(char*)*64);
    n->dinit =malloc(sizeof(Node*)*64);
    do{
        char*dty;
        /* 首声明符的星号已在 parse_type 并入 ty；后续声明符从 base 起步 */
        if(n->ndecls==0) dty=ty;
        else {
            dty=base;
            while(at("*")){ take();
                char buf[256]; snprintf(buf,sizeof(buf),"%s*",dty);
                dty=strdup(buf);
            }
        }
        /* fn-ptr 声明符：type (* name) ( params ) —— 局部 fn-ptr（A6 顺延）。
         * 类型记 "int*"（通用指针，9 tryte）；形参表解析后丢弃（签名不参与布局）。 */
        if(at("(")){
            take(); expect("*");
            char*fname=take()->s; expect(")");
            if(at("(")){ int dp=0;
                do{ if(at("("))dp++; else if(at(")"))dp--;
                    else if(atk(TK_EOF)) break; take(); }while(dp>0);
            }
            if(!DECL_KEEP_SEMI) expect(";");
            n->dnames[n->ndecls]=fname; n->dtypes[n->ndecls]=strdup("int*");
            n->dinit [n->ndecls]=NULL; n->ndecls++;
            return n;
        }
        char*name=take()->s;
        /* 数组维度并入类型: int a[100] -> "int[100]" */
        while(at("[")){
            take(); char cnt[32]; dim_text(cnt,sizeof(cnt)); expect("]");
            char buf[256]; snprintf(buf,sizeof(buf),"%s[%s]",dty,cnt);
            dty=malloc(256); snprintf(dty,256,"%s",buf);
        }
        Node*init=NULL;
        if(accept("=")) init=parse_initval();
        n->dnames[n->ndecls]=name;
        n->dtypes[n->ndecls]=dty;
        n->dinit [n->ndecls]=init;
        n->ndecls++;
    } while(accept(","));
    if(!DECL_KEEP_SEMI) expect(";");
    return n;
}
/* for(init;...) 的声明：分号属于 for 语法，声明本身不吃 */
static Node* parse_decl_nosemi(void){
    DECL_KEEP_SEMI=1; Node*n=parse_decl(); DECL_KEEP_SEMI=0;
    return n;
}
static int is_decl_start(void){
    Token*t=cur();
    if(t->kind==TK_ID && typedef_find(t->s)) return 1;   /* typedef 别名 */
    if(t->kind==TK_KW){
        /* 坑实录：类型清单共三处硬编码（is_kw / parse_type accept 链 /
           本表）——新类型三处都要补，漏一处即 parse_primary: bad 报错 */
        static const char*dt[]={"int","char","void","unsigned","signed","struct","va_list","const","extern","__tint2","__tint","__tbool",NULL};
        for(int i=0;dt[i];i++) if(!strcmp(t->s,dt[i])){
            /* struct X; 与 struct X f() 区分：先看后面 */
            return 1;
        }
    }
    return 0;
}

static Node* parse_stmt(void){
    /* C 标签语句 name:（musl strlcpy 的 finish: 惯用法）——
       TK_ID + ":" 且非 case/default 形态 */
    if(atk(TK_ID)&&TOKS[IPOS+1].s&&!strcmp(TOKS[IPOS+1].s,":")
       &&strcmp(cur()->s,"case")&&strcmp(cur()->s,"default")){
        Node*n=nnew(N_LABEL); n->s=strdup(cur()->s);
        take(); take();                       /* name : */
        return n;
    }
    if(at("{")) return parse_block();
    if(accept("if")){
        expect("("); Node*n=nnew(N_IF); n->cond=parse_expr(); expect(")");
        n->then=parse_stmt();
        if(accept("else")) n->els=parse_stmt();
        return n;
    }
    if(accept("while")){
        expect("("); Node*n=nnew(N_WHILE); n->cond=parse_expr(); expect(")");
        n->stmt=parse_stmt(); return n;
    }
    if(accept("do")){
        /* do body while(cond); —— 用 N_FOR 表示：init=NULL cond=条件
           step=NULL，stmt=体。执行顺序：体 -> 判条件（do-while 语义）。
           gen 侧对 N_FOR 是 {init; while(cond){ stmt; step;} }，
           与 do-while 不符 —— 所以专门发 N_DOWHILE。 */
        Node*n=nnew(N_DOWHILE);
        n->stmt=parse_stmt();
        expect("while"); expect("("); n->cond=parse_expr(); expect(")"); expect(";");
        return n;
    }
    if(accept("for")){
        expect("("); Node*n=nnew(N_FOR);
        if(!at(";")){
            if(is_decl_start()){
                n->init=parse_decl_nosemi();
            } else {
                n->init=parse_assign();
                Node*ci=n->init;
                while(accept(",")){ ci->els=parse_assign(); ci=ci->els; }
            }
        }
        expect(";");
        if(!at(";")) n->cond=parse_expr();
        expect(";");
        if(!at(")")){
            /* step 段支持逗号列表 i++, r++（musl 双指针惯用法）：
               首个进 n->step，其余挂 n->els 链（gen FOR 分支逐个 eval）。 */
            n->step=parse_assign();
            Node*cur=n->step;
            while(accept(",")){
                Node*nx=parse_assign();
                cur->els=nx; cur=nx;
            }
        }
        expect(")"); n->stmt=parse_stmt(); return n;
    }
    if(accept("switch")){
        expect("("); Node*n=nnew(N_SWITCH); n->cond=parse_expr(); expect(")");
        expect("{");
        /* 扁平收集：N_CASE/N_DEFAULT/普通语句 */
        n->items=malloc(sizeof(Node*)*64);
        while(!at("}")&&!atk(TK_EOF)){
            if(accept("case")){
                /* case 常量:（限字面量/一元负号字面量） */
                int neg=accept("-");
                Node*vn=parse_primary();
                expect(":");
                Node*c=nnew(N_CASE); c->num=neg?-vn->num:vn->num;
                n->items[n->nitems++]=c;
            } else if(accept("default")){
                expect(":");
                n->items[n->nitems++]=nnew(N_DEFAULT);
            } else {
                n->items[n->nitems++]=parse_stmt();
            }
        }
        expect("}");
        return n;
    }
    if(accept("goto")){
        Node*n=nnew(N_GOTO); n->s=take()->s; expect(";"); return n;
    }
    if(accept("return")){
        Node*n=nnew(N_RETURN);
        if(!at(";")) n->expr=parse_expr();
        expect(";"); return n;
    }
    if(accept(";")) return nnew(N_BLOCK);   /* 空语句 ";"（for(...); 惯用法） */
    if(accept("break")){ expect(";"); return nnew(N_BREAK); }
    if(accept("continue")){ expect(";"); return nnew(N_CONT); }
    if(is_decl_start()) return parse_decl();
    Node*n=nnew(N_EXPR); n->expr=parse_expr();
    /* 逗号表达式语句 n--, d[n]=s[n];：沿 els 链逐个求值 */
    {
        Node*cur=n->expr;
        while(accept(",")){ cur->els=parse_assign(); cur=cur->els; }
    }
    expect(";"); return n;
}
static Node* parse_block(void){
    expect("{"); Node*n=nnew(N_BLOCK); int cap=0;
    while(!at("}")&&!atk(TK_EOF)){
        n->body=grow(n->body,&cap,n->nitems,sizeof(Node*));
        n->body[n->nitems++]=parse_stmt();
    }
    expect("}"); return n;
}

/* 动态表扩容：n 为即将写入的下标 */
#define GROW(arr, cap, n, type) do{                                 \
        if((n)>=(cap)){                                             \
            (cap)=(cap)?(cap):8;                                    \
            while((n)>=(cap))(cap)*=2;                              \
            (arr)=realloc((arr),(size_t)(cap)*sizeof(type));        \
            if(!(arr)){ fprintf(stderr,"oom\n"); exit(1); }         \
        }                                                           \
    }while(0)

/* ===================== 类型系统 =====================
 * 类型用字符串表示：int / char / unsigned / void / va_list /
 * "struct S" / 后缀 * / 后缀 [n]（可多维）。与 trirv_cc.py 约定一致：
 *   int 与指针 = 9 tryte（WORD_TRYTES），char = 1 tryte，结构体成员按 9 对齐。
 */
typedef struct { char*name; char*ty; int off; } Member;
typedef struct { char*name; int size; Member*m; int nm, cap; } StructDef;
static StructDef *STRUCTS; static int NSTRUCT, CAPSTRUCT;

static StructDef* struct_find(const char*ty){
    if(!ty||strncmp(ty,"struct ",7)) return NULL;
    const char*nm=ty+7;
    for(int i=0;i<NSTRUCT;i++) if(!strcmp(STRUCTS[i].name,nm)) return &STRUCTS[i];
    return NULL;
}
static int ty_uns(const char*ty){ return ty && !strcmp(ty,"unsigned"); }
static int is_ptr(const char*ty){ return ty&&ty[0]&&ty[strlen(ty)-1]=='*'; }
/* 三种 1-tryte 整数类型：char（无符号，RISC-V psABI 默认）、unsigned char、signed char。
 * 尺寸都是 1 tryte；区别只在载入用 LBU（零扩展）还是 LB（符号扩展）。 */
static int is_char_ty(const char*ty){
    return ty && (!strcmp(ty,"char")||!strcmp(ty,"uchar")||!strcmp(ty,"schar"));
}
static int char_signed(const char*ty){ return ty && !strcmp(ty,"schar"); }
/* Kleene 系类型判定（批B）：__tint / __tbool——门控与类型传播共用 */
static int is_tinty(const char*ty){
    return ty && (!strcmp(ty,"__tint") || !strcmp(ty,"__tbool"));
}
/* 真数组：带维度且不是"指向数组的指针"（int[3]* 是指针，大小仍是 9） */
static int is_array(const char*ty){
    return ty && strchr(ty,'[') && ty[strlen(ty)-1]!='*';
}
static const char* pointee(const char*ty);          /* forward */
static int ty_bytes(const char*ty);

static void struct_def_semi(const char*name,int semi);
static void struct_def(const char*name){ struct_def_semi(name,1); }
static void struct_def_semi(const char*name,int semi){
    StructDef sd={0}; sd.name=strdup(name);
    expect("{");
    int pos=0, maxaln=9;                                /* 成员对齐最大值（__tint2=4 档时 4） */
    while(!at("}")){
        char*mty=parse_type();
        char*mn=take()->s;
        while(accept("[")){                            /* 成员数组 */
            char buf[96]; snprintf(buf,sizeof(buf),"%s[%s]",mty,cur()->s);
            mty=strdup(buf); take(); expect("]");
        }
        int aln = !strcmp(mty,"__tint2") ? 4 : 9;       /* __tint2 4-对齐档（C2 方案 A），
                                                           其余成员仍 9-对齐（ABI §6） */
        int off=((pos+aln-1)/aln)*aln;
        if(aln>maxaln) maxaln=aln;
        pos=off+ty_bytes(mty);
        GROW(sd.m,sd.cap,sd.nm,Member);
        sd.m[sd.nm].name=mn; sd.m[sd.nm].ty=mty; sd.m[sd.nm].off=off; sd.nm++;
        expect(";");
    }
    expect("}"); if(semi) expect(";");
    sd.size=((pos+maxaln-1)/maxaln)*maxaln;   /* 尺寸按 maxalign 圆整（含 __tint2
                                                 成员的结构体 4-圆整，其余 9-圆整） */
    GROW(STRUCTS,CAPSTRUCT,NSTRUCT,StructDef);
    STRUCTS[NSTRUCT++]=sd;
}
static Member* member_find(const char*sty,const char*mn){
    StructDef*sd=struct_find(sty);
    if(!sd) return NULL;
    for(int i=0;i<sd->nm;i++) if(!strcmp(sd->m[i].name,mn)) return &sd->m[i];
    return NULL;
}
/* 去掉最外层 [n]：int[5] -> int；int[3][4] -> int[4] */
static char* array_elem(const char*ty){
    const char*b=strchr(ty,'[');
    if(!b) return strdup(ty);
    const char*e=strchr(b,']');
    char*out=malloc(strlen(ty)+1);
    size_t n=(size_t)(b-ty);
    memcpy(out,ty,n); out[n]=0;
    if(e&&e[1]) strcat(out,e+1);
    return out;
}
static int array_count(const char*ty){
    const char*b=strchr(ty,'[');
    return b?atoi(b+1):0;
}
static const char* pointee(const char*ty){
    static char buf[128];
    if(!is_ptr(ty)) return ty;
    snprintf(buf,sizeof(buf),"%s",ty);
    buf[strlen(buf)-1]=0;
    return buf;
}
static int ty_bytes(const char*ty){
    if(!ty) return 9;
    if(is_ptr(ty)) return 9;
    if(is_array(ty)){ char*el=array_elem(ty); int n=array_count(ty); return n*ty_bytes(el); }
    if(is_char_ty(ty)) return 1;
    if(!strcmp(ty,"__tint2")) return 4;    /* __tint2 = 4 tryte（C2 方案 A） */
    if(!strcmp(ty,"__tbool")) return 1;    /* __tbool = 1 tryte（批B） */
    /* __tint = 9 tryte（默认分支同 int） */
    StructDef*sd=struct_find(ty);
    if(sd) return sd->size;
    return 9;
}
static int ty_size(const char*ty,int *isarr){
    if(isarr) *isarr=is_array(ty)?1:0;
    return ty_bytes(ty);
}

static Node** FNS; static int NFN, CAPFN;
static char** PROTOS; static int NPROT, CAPPROT;   /* 原型名登记（2026-11-22）：N_CALL 符号直发的判据 */



/* ===================== 全局变量 =====================
 * 布局：全局区从 GBASE=200 起，按地址升序依次分配；字符串段（STRBASE）在
 * 全局扫描完成后从全局区末尾起分配 —— 所以顺序必须是：
 *   1) parse_program 收集全局声明（只记类型/初始化表达式，不编数据）
 *   2) main 里 assign_glob_addrs()：给每个全局分配地址 -> 得出全局区末尾
 *   3) STRBASE = 全局区末尾；assign_strings/register_strings 照常分配字符串
 *   4) gen_glob_data()：此时字符串地址已定，char* g="..." 的指针值可回填
 * 未初始化的全局也占地址（bss 语义），但不写 TRV1 段（内存镜像天然为 0）。
 */
typedef struct {
    char *name;
    char *ty;          /* "int" / "char[4]" / "int*" / "struct P" ... */
    int addr;          /* tryte 地址（数据段） */
    int size;          /* tryte 数 */
    Node *init;        /* 初始化表达式（编译期常量 / INITLIST / 字符串），NULL=零 */
    int strinit;       /* 1 = init 是 N_STR 且 ty 是指针：数据=字符串地址，延迟回填 */
    int weak;          /* A6 顺延：弱全局（#pragma weak 无目标）——合并时让位强定义 */
    unsigned char *data;   /* 最终数据（gen_glob_data 之后有效） */
} Global;

static Global *GLOBS; static int NGLOB, CAPGLOB;
static int GBASE = 200;            /* 全局区起始 tryte 地址（避开零页与低地址暂存） */
static Global* global_find(const char*name){
    for(int i=0;i<NGLOB;i++) if(!strcmp(GLOBS[i].name,name)) return &GLOBS[i];
    return NULL;
}
static char**WEAKG_NAMES; static int NWEAKG, CAPWEAKG;   /* 弱全局名集合（无目标形态）*/
static int is_weakg(const char*n){
    for(int i=0;i<NWEAKG;i++) if(!strcmp(WEAKG_NAMES[i],n)) return 1;
    return 0;
}
static void weakg_add(const char*n){
    GROW(WEAKG_NAMES,CAPWEAKG,NWEAKG,sizeof(char*));
    WEAKG_NAMES[NWEAKG++]=strdup(n);
}

static Global* global_add(const char*name,char*ty,int size,Node*init){
    /* A3 同名合并（2026-11-22，C file-scope tentative/common 语义）：
     * - 双方均有初值 → 重复定义，诚实报错；
     * - 单方有初值 → 初值方为准（tentative 让位，槽共用）；
     * - 双方 tentative → 合并一块，尺寸取大。
     * 坑实录：旧实现同名全局各得一块独立存储且无报错——跨单元读写静默不同步
     * （R2 实证：库 GLOB hex=42 与主 tentative 零条目并存，两路读不一致）。 */
    Global*ex=global_find(name);
    if(ex){
        int ex_init=(ex->init!=NULL);
        int nw_init=(init!=NULL);
        if(ex_init && nw_init && !ex->weak){
            fprintf(stderr,"link error: duplicate global '%s'（双方均有初始化）\n",name);
            exit(1);
        }
        if(size>ex->size) ex->size=size;        /* common 模型：尺寸取大 */
        if(nw_init && (!ex_init || ex->weak)){  /* 弱让位：新强定义接管（弱标记清除） */
            ex->init=init; ex->weak=0;
        }
        return ex;
    }
    GROW(GLOBS,CAPGLOB,NGLOB,Global);
    Global*g=&GLOBS[NGLOB++];
    memset(g,0,sizeof(*g));
    g->name=strdup(name); g->ty=ty; g->size=size; g->init=init;
    return g;
}
/* 3^45 装不进任何整型字面量，运行时算。TRV_P45V = 3^45（模数本体）。 */
static __int128 TRV_P45V;              /* main 里初始化为 3^45 */
static __int128 TRV_WT[9];             /* 权重表 3^(5b)，b=0..8（p3 是 int，这里必须 128 位） */
static void enc_word(__int128 v,unsigned char*d,int nbytes){
    __int128 pv = v % TRV_P45V;
    if(pv<0) pv += TRV_P45V;              /* [0,3^45) */
    for(int b=0;b<nbytes&&b<9;b++) d[b]=(unsigned char)((pv/TRV_WT[b])%243);
}
/* 初始化表达式 -> tryte 数据。只接受编译期常量：
 *   标量/指针：整数常量（可负）；指针也可为字符串（strinit=1，地址后填）
 *   数组：{a,b,c} 逐元素；char 数组可整体 "abc"，不足补零（NUL 由 calloc 保证）
 */
static void global_init_data(const char*ty,Node*init,unsigned char**out_data,
                             int size,int *strinit){
    unsigned char*d=calloc(1,(size_t)size);
    *strinit=0;
    if(init){
        if(init->k==N_INITLIST){
            int flat_isarr=0; ty_size(ty,&flat_isarr);
            int esz = flat_isarr? ty_bytes(array_elem((char*)ty))
                                : ty_bytes((char*)ty);   /* 标量用自身宽（__tint2=4），
                                                            原固定 9 会越槽写 */
            int off=0;
            for(int i=0;i<init->nitems&&off<size;i++){
                Node*it=init->items[i];
                __int128 w128=0;
                if(it->k==N_NUM) w128=it->numw;
                else if(it->k==N_UN&&!strcmp(it->s,"-")&&it->e->k==N_NUM) w128=-it->e->numw;
                else { fprintf(stderr,"UNSUPPORTED: 全局初始化必须是编译期常量\n"); exit(2); }
                enc_word(w128,d+off,esz);
                off+=esz;
            }
        } else if(init->k==N_STR){
            if(ty[0]=='c'&&ty[1]=='h'&&ty[2]=='a'&&ty[3]=='r'&&ty[4]=='['){
                /* char 数组：内容直接铺，NUL 由 calloc 保证 */
                for(int i=0;i<init->slen&&i<size;i++) d[i]=(unsigned char)init->s[i];
            } else {
                *strinit=1;                  /* 指针 -> 字符串地址，gen_glob_data 回填 */
            }
            *out_data=d;                     /* N_STR 两条路都直接结束（防穿透到标量编码） */
            return;
        } else if(init->k==N_NUM){
            enc_word(init->numw,d,size<9?size:9);
        } else if(init->k==N_UN&&!strcmp(init->s,"-")&&init->e->k==N_NUM){
            enc_word(-init->e->numw,d,size<9?size:9);
        } else {
            fprintf(stderr,"UNSUPPORTED: 全局初始化必须是编译期常量（got kind=%d）\n",init->k);
            exit(2);
        }
    }
    *out_data=d;
}
/* 地址分配与数据生成挪到 STRBASE 定义之后（见 "全局变量（续）"）。 */

static void parse_program(void){
    while(!atk(TK_EOF)){
        if(getenv("TRV_TOP")) fprintf(stderr,"[top] '%s' kind=%d\n",cur()->s,cur()->kind);
        if(at("typedef")){
            take();
            if(getenv("TRV_TOP")) fprintf(stderr,"[typedef] cur='%s' IPOS=%d\n",cur()->s,IPOS);
            /* 形态一：typedef ret (*NAME)(params) —— parse_type 吃 ret 后
             * cur=="(" 且 next=="*"。本 ABI 所有指针等宽 45-trit，签名不
             * 参与布局，记 "int*" 即可。 */
            {
                /* 先探查：保存游标，试 parse_type，回溯判定 */
                int save=IPOS;
                char*real=parse_type();
                if(at("(")&&TOKS[IPOS+1].s&&!strcmp(TOKS[IPOS+1].s,"*")){
                    take(); expect("*");
                    char*nm=take()->s;
                    expect(")");
                    if(at("(")){                        /* 跳过参数表 */
                        int d=0;
                        do{ if(at("("))d++; else if(at(")"))d--;
                            else if(atk(TK_EOF)) break; take(); }while(d>0);
                    }
                    expect(";");
                    GROW(TYPEDEFS,CAPTDEF,NTDEF,Typedef);
                    TYPEDEFS[NTDEF].name=strdup(nm); TYPEDEFS[NTDEF].real=strdup("int*"); NTDEF++;
                    continue;
                }
                IPOS=save;                       /* 非函数指针：回溯走纯别名 */
            }
            /* 形态二：纯别名（T7：可带数组维度 typedef int arr3[3]——维度
               并入别名类型串；struct 别名也支持）。 */
            char*real=parse_type();
            if(getenv("TRV_TOP")) fprintf(stderr,"[alias] real='%s' nm='%s'\n",real,cur()->s);
            char*nm=take()->s;
            while(accept("[")){
                Token*dim=take();
                char buf[128];
                snprintf(buf,sizeof(buf),"%s[%s]",real,dim->s);
                real=strdup(buf);
                expect("]");
            }
            expect(";");
            GROW(TYPEDEFS,CAPTDEF,NTDEF,Typedef);
            TYPEDEFS[NTDEF].name=strdup(nm); TYPEDEFS[NTDEF].real=strdup(real); NTDEF++;
            continue;
        }
        if(at("struct")){
            /* 顶层 `struct` 有两种含义，看第三个 token 区分：
             *   struct P { … }   定义（t1=P, t2="{"）
             *   struct P f(…) …  返回 struct 的函数（t1=P, t2=f）—— 不能当定义吃掉 */
            Token*t1=&TOKS[IPOS+1], *t2=&TOKS[IPOS+2];
            if(t1->s&&t2->s&&!strcmp(t2->s,"{")){
                take();                      /* struct */
                Token*nm=take();             /* 名字 */
                struct_def(nm->s);
                continue;
            }
        }
        char*ret=parse_type();
        /* 顶层函数指针定义：int (*f)(参数表) { ... }
         * 处理成普通函数 f（签名不参与布局，调用约定由调用点决定）。
         * __libc_start_main 就是以这种形态出现在 musl 源里的。 */
        if(at("(")&&TOKS[IPOS+1].s&&!strcmp(TOKS[IPOS+1].s,"*")){
            take();                              /* ( */
            expect("*");
            char*fname=take()->s;                /* 函数名 */
            expect(")");                         /* ) */
            if(at("(")){                         /* 跳过 (参数表) */
                int d=0;
                do{
                    if(at("(")) d++;
                    else if(at(")")) d--;
                    else if(atk(TK_EOF)) break;
                    take();
                } while(d>0);
            }
            for(int k=0;k<NFN;k++) if(!strcmp(FNS[k]->s,fname)){
                fprintf(stderr,"link error: duplicate function '%s'\n",fname);
                exit(1);
            }
            /* A6 顺延（2026-11-22）：无体形态 = 全局 fn-ptr 声明
             *   int (*gp)(void);   —— 注册为通用指针型全局（9 tryte），
             * 赋值/间接调用同 fn-ptr 变量语义（gp = f0; gp();）。 */
            if(at(";")){
                take();
                global_add(fname,strdup("int*"),0,NULL);
                continue;
            }
            Node*f=nnew(N_FUNC); f->rettype=ret; f->s=fname;
            f->ptypes=malloc(sizeof(char*)*32);
            f->params =malloc(sizeof(char*)*32);
            f->fbody=parse_block();
            FNS=grow(FNS,&CAPFN,NFN,sizeof(Node*)); FNS[NFN++]=f;
            continue;
        }
        char*name=take()->s;
        /* 全局声明：type name ; / type name = init ; / type name[N] ... ;
         * type name, name2 ;   （函数则下一个 token 是 "("） */
        if(!at("(")){
            /* 顶层声明：首个名字已在上面 take()。此处处理它的维度/初始化，
             * 以及后续 `, name` 声明（每个名字可带自己的维度）。 */
            char*base_ty=strdup(ret);
            Node dmy={0}; Node*decl=&dmy;
            decl->dnames=malloc(sizeof(char*)*64);
            decl->dtypes=malloc(sizeof(char*)*64);
            decl->dinit =malloc(sizeof(Node*)*64);
            decl->ndecls=0;
            char*gn=name;                       /* 首个名字（已在 746 行取过） */
            for(;;){
                char*gty=base_ty;
                while(at("[")){
                    take(); char cnt[32]="";
                    if(!at("]")) dim_text(cnt,sizeof(cnt));   /* 空维度 [] */
                    expect("]");
                    char buf[256]; snprintf(buf,sizeof(buf),"%s[%s]",gty,cnt);
                    gty=strdup(buf);
                }
                Node*init=NULL;
                if(accept("=")) init=parse_initval();
                decl->dnames[decl->ndecls]=gn;
                decl->dtypes[decl->ndecls]=gty;
                decl->dinit [decl->ndecls]=init;
                decl->ndecls++;
                if(!accept(",")) break;
                gn=take()->s;                   /* 下一声明的名字 */
            }
            expect(";");
            for(int i=0;i<decl->ndecls;i++){
                char*gty=decl->dtypes[i];
                if(is_array(gty)){
                    /* 未显式给维度且初始化为字符串/列表的：C 语义由初始化推断。
                       我们的 dim_text 对空维度记 "0" —— 这里按初始化补齐。 */
                    char*el=array_elem(gty);
                    int cnt=array_count(gty);
                    Node*init=decl->dinit[i];
                    if(cnt==0&&init){
                        if(init->k==N_STR) cnt=init->slen+1;
                        else if(init->k==N_INITLIST) cnt=init->nitems;
                    }
                    if(cnt<=0){ fprintf(stderr,"UNSUPPORTED: 全局数组需要显式维度\n"); exit(2); }
                    char buf[256]; snprintf(buf,sizeof(buf),"%s[%d]",el,cnt);
                    gty=strdup(buf);
                }
                {   Global*gg=global_add(decl->dnames[i],gty,0,decl->dinit[i]);
                    gg->weak = is_weakg(decl->dnames[i]);
                    if(getenv("TRV_ASM_DEBUG")&&gg->weak) fprintf(stderr,"[weakg] parse 标记 %s\n",decl->dnames[i]); }
            }
            free(decl->dnames); free(decl->dtypes); free(decl->dinit);
            continue;
        }
        expect("(");
        Node*f=nnew(N_FUNC); f->rettype=ret; f->s=name;
        f->ptypes=malloc(sizeof(char*)*32);
        f->params =malloc(sizeof(char*)*32);
        if(!at(")")){
            int void_only = strcmp(ret,"void")==0 && at("void")
                            && TOKS[IPOS+1].s && !strcmp(TOKS[IPOS+1].s,")");
            if(void_only) take();              /* 吃掉 void，留给 expect(")") */
            if(!void_only){
                do{
                    if(accept("...")){ f->variadic=1; break; }
                    if(at("void") && TOKS[IPOS+1].s && !strcmp(TOKS[IPOS+1].s,")")) {
                        take(); break;                     /* (void) 形参表：lookahead 再吃 */ 
                    }
                    char*pt=parse_type();
                    /* 函数指针形参：int (*f)(参数表)。
                     * parse_type 吃掉 "int" 后 cur=="(" 且 next=="*"。
                     * 跳过 (*name) 与参数表，类型记 "int*"（本 ABI 所有指针
                     * 等宽，签名不参与布局）。 */
                    if(at("(")&&TOKS[IPOS+1].s&&!strcmp(TOKS[IPOS+1].s,"*")){
                        take();                          /* ( */
                        expect("*");
                        char*pn=take()->s;               /* 名字 */
                        expect(")");                     /* ) */
                        if(at("(")){                     /* (参数表) 逐层跳过 */
                            int d=0;
                            do{
                                if(at("(")) d++;
                                else if(at(")")) d--;
                                else if(atk(TK_EOF)) break;
                                take();
                            } while(d>0);
                        }
                        pt=strdup("int*");
                        f->ptypes[f->nparams]=pt;
                        f->params [f->nparams]=pn;
                        f->nparams++;
                        continue;                        /* 回 do-while 判定 "," */
                    }
                    char*pn=take()->s;
                    /* 数组形参退化指针 */
                    {   /* 形参数组退化为指针：int a[5] -> int*；int m[][3] -> int[3]* */
                        char dims[128]=""; 
                        while(accept("[")){
                            char cnt[32]="";
                            if(!at("]")) dim_text(cnt,sizeof(cnt));
                            expect("]");
                            char tmp[160]; snprintf(tmp,sizeof(tmp),"[%s]",cnt);
                            strncat(dims,tmp,sizeof(dims)-strlen(dims)-1);
                        }
                        if(dims[0]){
                            /* 去掉第一维（它退化为指针），其余维度保留 */
                            char*first_end=strchr(dims,']');
                            char rest[128]=""; if(first_end&&first_end[1]) snprintf(rest,sizeof(rest),"%s",first_end+1);
                            char buf[256]; snprintf(buf,sizeof(buf),"%s%s*",pt,rest);
                            pt=strdup(buf);
                        }
                    }
                    f->ptypes[f->nparams]=pt;
                    f->params [f->nparams]=pn;
                    f->nparams++;
                } while(accept(","));
            }
        }
        expect(")");
        if(accept(";")){                       /* 函数原型声明：只有签名，无体 */
            /* 2026-11-22 登记原型名：N_CALL 解析用它区分「原型/隐式声明 →
               符号直发 JAL（TRVO 导入期解析 / 链接期 scan_undef 收口）」与
               「彻底未声明的变量语境误用 → gen 报错」。此前原型被整体丢弃，
               原型-only 调用（如 qsort_tri 的 memcpy）落进 fn-ptr 形参合成的
               N_VAR 静默兜底——调用消失或跳错目标（潜伏错码，测试未踩到
               msort_wide 分支故长期未暴露）。 */
            int k2; int seen=0;
            for(k2=0;k2<NPROT;k2++) if(!strcmp(PROTOS[k2],name)){seen=1;break;}
            if(!seen){ PROTOS=grow(PROTOS,&CAPPROT,NPROT,sizeof(char*)); PROTOS[NPROT++]=strdup(name); }
            continue;                          /* 签名不参与代码生成；供调用的 FNRETS 在统一阶段建表 */
        }
        for(int k=0;k<NFN;k++) if(!strcmp(FNS[k]->s,name)){
                fprintf(stderr,"link error: duplicate function '%s'\n",name);
                exit(1);
            }
            f->fbody=parse_block();
        FNS=grow(FNS,&CAPFN,NFN,sizeof(Node*)); FNS[NFN++]=f;
    }
}

/* ===================== 寄存器式代码生成 =====================
 * 核心：局部标量变量静态分配到物理寄存器；表达式值在寄存器间传递。
 * 与栈式编译器（trirv_cc.py）的本质区别：不产生 SW/LW/ADDI sp 的压栈弹栈。
 */
typedef struct { char*name; int reg; int spilled; int off; char*ty; } LocVar;

/* 输出缓冲：每条指令一行文本，最后统一汇编 */
static char **OUT; static int NOUT, CAPOUT;
static int LBL;
static LocVar *LOC; static int NLOC, CAPLOC;   /* 动态：单函数局部数不设上限 */
static int USEDREG[32];          /* 当前函数占用的寄存器 */
static int SPILL_TOP;            /* 溢出区 tryte 偏移(相对 sp) */
static char **FNRETS; static int *FNVARGS; static char **FNEYS;
static int NFNRET, CAPFNRET, CAPFNVARGS, CAPFNEYS;
static char **STRSEG; static int *STRLEN; static int NSTR, CAPSTR, CAPSTRLEN; static int STRBASE=100;
/* 栈基址（tryte）：VM sp 初值（PyVM regs[2]=C VM reg[2]，一致）。
 * 内存映射：数据 200..STACK_BASE-1（全局+字符串），栈 STACK_BASE+ 向上。
 * 契约文档 trirv-unified-memory-abi.md §3；编译器链接期静态检查越界拒绝。 */
#define TRI_STACK_BASE 4096
static int STRSEG_START=100;             /* 字符串段写出的起始地址（= 分配前的 STRBASE） */
static void assign_glob_addrs(void);     /* 全局地址分配（会改写 STRBASE），实现见下方 */
static void gen_glob_data(void);         /* 全局数据生成（依赖字符串地址），实现见下方 */

/* ===================== 全局变量（续）：地址分配与数据生成 ===================== */
static void assign_glob_addrs(void){
    int pos=GBASE;
    for(int i=0;i<NGLOB;i++){
        Global*g=&GLOBS[i];
        g->addr=((pos+8)/9)*9;           /* 字/结构体 9 对齐；char 数组从对齐位置起也无妨 */
        g->size=ty_bytes(g->ty);
        pos=g->addr+g->size;
    }
    STRBASE = pos;                        /* 字符串段接在全局区之后 */
}
/* TRVO GLOB 初值回填表（tvo_import 收集，gen_glob_data 末尾按名回填 data） */
static char **TVO_GNAME; static char **TVO_GHEX; static int NTG, CAPTGN, CAPTGH;

static void gen_glob_data(void){
    for(int i=0;i<NGLOB;i++){
        Global*g=&GLOBS[i];
        global_init_data(g->ty,g->init,&g->data,g->size,&g->strinit);
        if(g->strinit){                   /* 指针初始化为字符串字面量 */
            Node*strnode=g->init;
            enc_word(strnode->straddr,g->data,9);
        }
    }
    /* TRVO 导入的 GLOB 初值回填（hex → data；tvo_import 收集） */
    for(int i=0;i<NTG;i++){
        for(int j=0;j<NGLOB;j++){
            if(strcmp(GLOBS[j].name,TVO_GNAME[i])) continue;
            Global*g=&GLOBS[j];
            const char*h=TVO_GHEX[i];
            for(int b=0;b<g->size&&h[b*2]&&h[b*2+1];b++){
                unsigned v; sscanf(h+b*2,"%2x",&v);
                g->data[b]=(unsigned char)v;
            }
            break;
        }
    }
}
static const char *CURFN;
static char **LOOPEND, **LOOPCONT; static int NLOOP, CAPLOOP, CAPLOOPC;
static char *FUNC_EPILOGUE;   /* 当前函数的 epilogue 标签（return 跳这里统一恢复帧） */
static int XCALL_SLOT;        /* 当前帧内"跨调用表达式临时保存槽"相对 s0 的偏移 */

static void emitf(const char*fmt,...){
    char buf[512]; va_list ap; va_start(ap,fmt); vsnprintf(buf,sizeof(buf),fmt,ap); va_end(ap);
    if(NOUT>=CAPOUT){CAPOUT=CAPOUT?CAPOUT*2:256;OUT=realloc(OUT,CAPOUT*sizeof(char*));}
    OUT[NOUT++]=strdup(buf);
}
static char* newlbl(const char*tag){ char b[32]; snprintf(b,sizeof(b),"_L%d",++LBL); (void)tag; return strdup(b); }
static void lbl(const char*s){ emitf("%s:",s); }

/* 相邻跳转消除：`JAL zero, L` 紧邻 `L:` 时等于空转，直接删掉。
 * 最典型的来源是函数体末尾的 return 跳到紧跟其后的 epilogue。
 * 只处理"跳转目标就是下一条指令"这一种，语义上恒等，不依赖数据流分析。 */
static void drop_jump_to_next(void){
    int w=0;
    for(int i=0;i<NOUT;i++){
        if(!strncmp(OUT[i],"JAL zero, ",10)){
            const char*tgt=OUT[i]+10;
            int hit=0;
            for(int j=i+1;j<NOUT;j++){
                int L=(int)strlen(OUT[j]);
                if(L>0&&OUT[j][L-1]==':'){
                    if(L-1==(int)strlen(tgt)&&!strncmp(OUT[j],tgt,L-1)) hit=1;
                } else break;                       /* 标签之后还有指令，就不是空转 */
            }
            if(hit) continue;
        }
        OUT[w++]=OUT[i];
    }
    NOUT=w;
}

static int reg_free(int r){ return !USEDREG[r]; }
static void reg_take(int r){ if(r>0&&r<32) USEDREG[r]=1; }
/* 持久化寄存器池
 * ---------------
 * 有调用的函数只能用 callee-saved 的 s1,s2..s6——被调者不会破坏它们，
 * 代价是进函数要存、出函数要恢复（每个变量 2 条）。
 * 但"叶子函数"（函数体内没有任何调用，含 syscall）根本没人会踩它的寄存器，
 * 于是可以直接用 caller-saved 的 a 系列，一个字节的保存/恢复都不用付。
 * 池从 a_nparams 开始，避开形参占用的 a0..a(n-1)，装载时不会互相覆盖。
 * 只避开 t0-t5(表达式临时)、t6(变参块基址)、s0(帧指针)、sp/ra/zero。 */
static int PP_POOL[8]={18,19,20,21,22,9,-1,-1};
static int PP_N=6, PP_SAVED=1;      /* PP_SAVED: 池里的寄存器是否需要存/恢复 */
static void pp_set(int leaf,int nparams){
    static int cpup[6]={22,23,24,25,26,9};
    if(leaf&&nparams<8){
        int k=0;
        for(int i=nparams;i<8;i++) PP_POOL[k++]=10+i;   /* a_nparams .. a7 */
        PP_N=k; PP_SAVED=0;
    } else {
        for(int i=0;i<6;i++) PP_POOL[i]=cpup[i];
        PP_N=6; PP_SAVED=1;
    }
}
static int reg_alloc_persistent(void){
    for(int i=0;i<PP_N;i++)
        if(PP_POOL[i]>=0&&reg_free(PP_POOL[i])){ reg_take(PP_POOL[i]); return PP_POOL[i]; }
    return -1;   /* 溢出到栈 */
}
/* 表达式临时寄存器：t0,t1,t2, 再用剩余空闲 */
static int TMPREG[16], NTMP;
static int tmp_alloc(void){
    static int pool[]={5,6,7};
    for(unsigned i=0;i<sizeof(pool)/sizeof(pool[0]);i++) if(reg_free(pool[i])){return pool[i];}
    /* 兜底：任意空闲且非持久局部 */
    for(int r=10;r<32;r++) if(reg_free(r)) return r;
    return 5; /* 极端情况复用 t0（表达式树深度有限，实践中不会） */
}
static int is_tmp(int r){ for(int i=0;i<NTMP;i++) if(TMPREG[i]==r) return 1; return 0; }

static LocVar* findloc(const char*name){
    for(int i=0;i<NLOC;i++) if(!strcmp(LOC[i].name,name)) return &LOC[i];
    return NULL;
}
/* 取一个可写的局部变量槽（必要时扩容） */
static LocVar* loc_new(void){
    GROW(LOC,CAPLOC,NLOC,LocVar);
    LocVar*v=&LOC[NLOC++];
    memset(v,0,sizeof(*v));
    return v;
}

/* tp(x4)：立即数/常量装配专用 scratch，不参与表达式池，也不碰 t6（变参参数块基址） */
static const int SCRATCH=4;

/* 装一个 |n| <= (3^20-1)/2 的有符号段：LUI(13 trit) + ADDI(7 trit，带借位) */
static void load_seg(int reg, long long n){
    if(n<0){ load_seg(reg,-n); emitf("SUB %s, zero, %s",regname(reg),regname(reg)); return; }
    long long h=n/2187, lo=n%2187;         /* 2187=3^7 */
    const long long HALF7=(2187-1)/2;
    if(lo>HALF7){ lo-=2187; h++; }
    if(h==0) emitf("ADDI %s, zero, %lld",regname(reg),lo);
    else { emitf("LUI %s, %lld",regname(reg),h); if(lo) emitf("ADDI %s, %s, %lld",regname(reg),regname(reg),lo); }
}
/* load_imm：把 45-trit 立即数装入 reg，按 3^20 平衡分段（对齐 trirv_cc.py） */
static void load_imm128(int reg, __int128 n);
static void load_imm(int reg, long long n){ load_imm128(reg,(__int128)n); }
static void load_imm128(int reg, __int128 n){
    if(n==0){ emitf("ADDI %s, zero, 0",regname(reg)); return; }
    const __int128 B=3486784401LL;         /* 3^20 */
    const __int128 HALFB=(B-1)/2;
    long long segs[8], ns=0; __int128 m=n;
    while(m){ __int128 seg=m%B; m/=B;
        if(seg>HALFB){ seg-=B; m++; } segs[ns++]=(long long)seg; }
    if(ns==0){ emitf("ADDI %s, zero, 0",regname(reg)); return; }
    if(ns>1 && reg==SCRATCH){
        /* 坑实录：多段装载经 load_seg(SCRATCH,...) 中转，reg==SCRATCH 时
           每段覆写前段（3^20 装完即被 0 段清零 → REMU 除零 → 窄化恒 0）。
           调用方改用 tacq 临时寄存器。 */
        fprintf(stderr,"load_imm: multi-segment into SCRATCH(tp) unsupported\n");
        exit(1);
    }
    load_seg(reg, segs[ns-1]);
    for(int i=ns-2;i>=0;i--){
        emitf("SLLI %s, %s, 20",regname(reg),regname(reg));
        load_seg(SCRATCH, segs[i]);
        emitf("ADD %s, %s, %s",regname(reg),regname(reg),regname(SCRATCH));
    }
}

/* ============ 三值本征位运算/移位（2026-11-22 方言拍板，方案 A 退役） ============
 * C 方言的 & | ^ ~ << >> 就是 ISA 本征指令，逐 trit 直发，各 1 条、零工作寄存器：
 *   & = TAND(min)   | = TOR(max)   ^ = XOR((ta+tb) mod 3)   ~ = TNOT(2-t，~0 = -1)
 *   << = SLL/SLLI ×3^k；>> 无符号 = SRL raw ÷3^k、有符号 = SRA ÷3^k 向零截断
 *   （≡ 除法口径：a>>k ≡ a/3^k；k≥45 商恒 0——×3^45 ≡ 0 且 |x|<3^45=除数）
 * 勿混口径：a^-1 是逐 trit 数字递减 (t+2)%3，≠ TNOT（2-t）；
 * 本征 XOR 不自反：a^a = 逐 trit 2a mod 3 ≠ 0（三进制固有，勿按二进制直觉用）。
 * 历史（方案 A，2026-11-19~11-22）：C 二进制语义靠 72 位位游标软件模拟，
 * 每次位运算 ~750 条动态指令 + 按函数抢占 5-7 个 callee-saved 工作寄存器
 * （不够硬报错）——语义层退役后这些成本整体消失。 */
static void emit_shl_k(int dst,int aR,long long k){
    if(k<=0){ if(dst!=aR) emitf("ADDI %s, %s, 0",regname(dst),regname(aR)); return; }
    if(k>=45){ emitf("ADDI %s, zero, 0",regname(dst)); return; }   /* ×3^k ≡ 0 mod 3^45 */
    if(dst!=aR) emitf("ADDI %s, %s, 0",regname(dst),regname(aR));
    emitf("SLLI %s, %s, %lld",regname(dst),regname(aR),k);
}

static void emit_shr_k(int dst,int aR,long long k,int uns){
    if(k<=0){ if(dst!=aR) emitf("ADDI %s, %s, 0",regname(dst),regname(aR)); return; }
    if(k>=45){
        /* |x| < 3^45 = 除数 → 向零截断商恒 0（无符号/有符号同） */
        emitf("ADDI %s, zero, 0",regname(dst));
        return;
    }
    if(dst!=aR) emitf("ADDI %s, %s, 0",regname(dst),regname(aR));
    emitf(uns?"SRLI %s, %s, %lld":"SRAI %s, %s, %lld",regname(dst),regname(aR),k);
}

/* ===================== 表达式求值：带深度的临时寄存器池 =====================
 * 旧实现把结果固定放 t0/t1：嵌套表达式里右子树的 "ADDI t1, t0, 0" 会覆盖左值，
 * 于是 7*3+8/2-4 被算成 8。现在改为 LIFO 池——深度 d 用 TPOOL[d]，
 * 二元运算直接写回左操作数所在寄存器，既不覆盖也不产生搬运指令。
 */
static const int TPOOL[6]={5,6,7,28,29,30};      /* t0 t1 t2 t3 t4 t5 */
static int TDN;                                   /* 已占用的临时深度 */
static int CSAVE_OFF, ARG_OFF;                    /* 跨调用保存区 / 实参暂存区基址 */
static int NFIXED;                                /* 当前函数固定参数个数（va_start 用） */
static int A0_DIRECT;                             /* 调用结果就要留在 a0，别再拷回临时（return f() 用） */
/* sret（struct 按值返回）状态：当前函数是否 sret、隐藏返回缓冲指针存在哪个局部槽、
 * 返回结构体多大；以及本帧里 sret 调用点缓冲区的基址。 */
static int FUNC_SRET, FUNC_SRET_LOC, FUNC_RET_BYTES, SRET_BASE;

/* 函数级大常量缓存
 * ----------------
 * 超过 7-trit（±1093）的字面量要说 LUI+ADDI 两条才能装出来。这种量往往出现在
 * 循环条件里（while(i<=200000)），每轮重装就是纯浪费。叶子函数里 s 系列是空的，
 * 拿几个把它们预先装好，循环里直接当只读寄存器用。 */
#define CC_MAX 2
static __int128 CC_V[CC_MAX]; static int CC_R[CC_MAX]; static int CC_N;   /* 45-trit 常量超 long long */
static void collect_fn_consts(Node*n){
    if(!n||CC_N>=CC_MAX) return;
    if(n->k==N_NUM){
        __int128 v=n->numw;                    /* 必须用全精度：3^40 已超 long long */
        __int128 av=v<0?-v:v;
        if(av>1093){
            for(int i=0;i<CC_N;i++) if(CC_V[i]==v) return;
            CC_V[CC_N++]=v;
        }
        return;
    }
    Node*all[]={n->l,n->r,n->e,n->cond,n->then,n->els,n->init,n->step,n->stmt,n->expr};
    for(int i=0;i<10;i++) collect_fn_consts(all[i]);
    if(n->body) for(int i=0;i<n->nitems;i++) collect_fn_consts(n->body[i]);
    if(n->args) for(int i=0;i<n->nargs;i++) collect_fn_consts(n->args[i]);
    if(n->items) for(int i=0;i<n->nitems;i++) collect_fn_consts(n->items[i]);
    if(n->dinit) for(int i=0;i<n->ndecls;i++) collect_fn_consts(n->dinit[i]);
}

static int has_call(Node*n);   /* forward */

static const char* DBG_PT;
static void too_deep(int off){
    (void)off;
    fprintf(stderr,"UNSUPPORTED: expression nesting needs more than 7 temps\n");
    exit(2);
}
/* 7-trit 访存偏移范围检查：超出就明确拒绝，不能悄悄错位 */
static void chk_off(int off){
    if(off>1093||off<-1093){
        fprintf(stderr,"UNSUPPORTED: frame offset %d exceeds 7-trit range\n",off);
        exit(2);
    }
}
static int tacq(void){ if(TDN>=6) too_deep(0); return TPOOL[TDN++]; }

/* 表达式求值的"可写性"约定
 * -------------------------
 * 为了让 `s = s + i` 直接落在变量自己的寄存器上（而不是先算进 t、再拷回 s），
 * eval 允许把常驻寄存器（s 系列）当作只读结果返给上级。能否覆写由 in_tpool 判定：
 *   - 在 TPOOL 里   -> 本次表达式的私有临时，可以当目标寄存器随手写
 *   - 不在 TPOOL 里 -> 变量/参数的常驻寄存器，只能读
 * 任何"要往结果里写"的地方都必须先过 dstof()，否则会写坏变量。
 * 两个集合不相交（TPOOL={5,6,7,28,29,30}，s 系={9,18..27}），判定是精确的。 */
static int in_tpool(int r){ for(int i=0;i<6;i++) if(TPOOL[i]==r) return 1; return 0; }
/* 为 src 的结果挑一个可写目标：src 本身可写就复用，否则另取临时。
   注意它只分配目标、不搬值——适合 "dst = op(src)" 这种 dst 由指令写入的场合。 */
static int dstof(int src){ return in_tpool(src)? src : tacq(); }
/* 把 src 复制到一个可写寄存器（src 是常驻寄存器、后面要就地累加/加偏移时用） */
static int rwcopy(int src){
    if(in_tpool(src)) return src;
    int t=tacq();
    emitf("ADDI %s, %s, 0",regname(t),regname(src));
    return t;
}

/* 整块拷贝：dst/src 都是地址寄存器，struct 尺寸已按 9 tryte 对齐（见 struct_def）。
 * 用一个固定 scratch 走字，不占临时池 —— 与 N_ASSIGN 的结构体赋值同一套做法。 */
static const char* fn_ret_type(const char*name);   /* 定义在后面 */
static void copy_words(int dstReg,int srcReg,int bytes){
    for(int o=0;o<bytes;o+=9){
        emitf("LW %s, %d(%s)",regname(SCRATCH),o,regname(srcReg));
        emitf("SW %s, %d(%s)",regname(SCRATCH),o,regname(dstReg));
    }
}

/* sret 调用点缓冲编号：每个"返回 struct 的调用"各占一块独立缓冲，避免
 * f(g(),h()) 里两个缓冲互相覆盖。帧大小必须先于代码生成确定，所以先走一遍 AST
 * 编号；字段固定在节点上（每个节点只生成一次代码），循环里复用同一块是安全的。 */
static void sret_slot_walk(Node*n,int*cur){
    if(!n) return;
    if(n->k==N_CALL){
        n->sret_off=-1;
        const char*rt=fn_ret_type(n->s);
        if(struct_find(rt)){ n->sret_off=*cur; *cur+=ty_bytes(rt); }
    }
    if(n->k==N_COMPOUND){
        /* 复合字面量的匿名临时缓冲（帧内，与 sret 缓冲同区） */
        n->sret_off=*cur; *cur+=ty_bytes(n->s);
    }
    Node*all[]={n->l,n->r,n->e,n->cond,n->then,n->els,n->init,n->step,n->stmt,n->expr};
    for(int i=0;i<10;i++) sret_slot_walk(all[i],cur);
    if(n->body) for(int i=0;i<n->nitems;i++) sret_slot_walk(n->body[i],cur);
    if(n->args) for(int i=0;i<n->nargs;i++) sret_slot_walk(n->args[i],cur);
    if(n->items) for(int i=0;i<n->nitems;i++) sret_slot_walk(n->items[i],cur);
    if(n->dinit) for(int i=0;i<n->ndecls;i++) sret_slot_walk(n->dinit[i],cur);
}

static const char* binop_insn(const char*op){
    /* & | ^ << >> 已由 N_BIN 本征直发接管（TAND/TOR/XOR/SLL 系，2026-11-22
     * 方言拍板）——落到这里即路由 bug。历史上这里曾硬报错「二进制位运算需要
     * 软件模拟」（方案 A 时代口径）；更早还有 `<<` 直译 SLL 致 x<<1=×3 的坑，
     * 本征化后 ×3^k 恰是定义，不再是坑。 */
    if(!strcmp(op,"&")||!strcmp(op,"|")||!strcmp(op,"^")||
       !strcmp(op,"<<")||!strcmp(op,">>")){
        fprintf(stderr,"internal: 位运算/移位 %s 应由 N_BIN 本征直发生成，不该走到 binop_insn\n",op);
        exit(2);
    }
    static const char*m[]={
        "+","ADD","-","SUB","*","MUL","/","DIV","%","REM",NULL};
    for(int i=0;m[i];i+=2) if(!strcmp(op,m[i])) return m[i+1];
    return NULL;
}
/* 关系运算：dst = (a op b) 0/1 */
static void emit_rel_u(const char*op,int dst,int a,int b,int uns);
static void emit_rel(const char*op,int dst,int a,int b){
    emit_rel_u(op,dst,a,b,0);
}
static void emit_rel_u(const char*op,int dst,int a,int b,int uns){
    const char*D=regname(dst),*A=regname(a),*B=regname(b);
    const char*SL = uns?"SLTU":"SLT";
    if(!strcmp(op,"<"))       emitf("%s %s, %s, %s",SL,D,A,B);
    else if(!strcmp(op,">=")){ emitf("%s %s, %s, %s",SL,D,A,B); emitf("SLTIU %s, %s, 1",D,D); }
    else if(!strcmp(op,">"))  emitf("%s %s, %s, %s",SL,D,B,A);
    else if(!strcmp(op,"<=")){ emitf("%s %s, %s, %s",SL,D,B,A); emitf("SLTIU %s, %s, 1",D,D); }
    else if(!strcmp(op,"==")){ emitf("SUB %s, %s, %s",D,A,B); emitf("SLTIU %s, %s, 1",D,D); }
    else if(!strcmp(op,"!=")){ emitf("SUB %s, %s, %s",D,A,B); emitf("SLTU %s, zero, %s",D,D); }
    else too_deep(1);
}

/* 具名 syscall -> 编号 */
static int syscall_no(const char*name){
    static const char*sys[]={"write","read","read8","write8","exit","brk","getcwd","openat","close",
        "lseek","readv","writev","newfstatat","fstat","exit_group","set_tid_address",
        "futex","clock_gettime","sched_yield","rt_sigaction","rt_sigprocmask",
        "rt_sigreturn","uname","getpid","gettid","munmap","mmap","mprotect","getrandom",NULL};
    static const int num[]={64,63,5000,5001,93,214,17,56,57,62,65,66,79,80,94,96,98,113,124,
        134,135,139,160,172,178,215,222,226,278};
    for(int i=0;sys[i];i++) if(!strcmp(name,sys[i])) return num[i];
    return -1;
}

/* 函数标签 -> 指令地址（tryte）；不在函数表返回 -1。
 * 标签行在 OUT 里不占指令槽，普通指令按顺序计数。 */
static int fn_label_addr(const char*name){
    for(int i=0;i<NFN;i++) if(!strcmp(FNS[i]->s,name)){
        int idx=0;
        for(int k=0;k<NOUT;k++){
            const char*L=OUT[k];
            size_t len=strlen(L);
            if(len>0&&L[len-1]==':'){
                char tmp[128]; snprintf(tmp,sizeof(tmp),"%.*s",(int)(len-1),L);
                if(!strcmp(tmp,name)) return idx*4;
                continue;
            }
            idx++;
        }
    }
    return -1;
}
/* 名字是否已知为函数：本单元有体（fn_label_addr 可数）或导入体（FNS 条目
 * fbody==NULL，标签在 TVO_OUT 拼入 OUT 末尾）。导入函数的地址引用走符号
 * &name，汇编期绑定——gen 期 TVO_OUT 尚未拼入 OUT，烘焙计数必然失准（A4），
 * 且 peephole 删指令会前移标签（cmp_char 44 vs 40 实证）——一律符号发射。 */
static int is_known_fn(const char*name){
    if(fn_label_addr(name)>=0) return 1;
    for(int k=0;k<NFN;k++) if(!strcmp(FNS[k]->s,name)) return 1;
    const char*r=alias_follow(name);
    if(r){ for(int k=0;k<NFN;k++) if(!strcmp(FNS[k]->s,r)) return 1; }
    return 1==0;
}
/* 间接调用目标：把"被调地址"装入 reg。
 * n->s 形如 "f(...)"（直接函数指针调用）或 "(*fp)(...)"（指针解引用）。
 * - 裸名字是已知函数 -> load_imm 其指令下标地址（JAL 的目标值）
 * - (*fp) -> 求值 fp 变量（里面是函数地址） */
static void load_mem(int dst,int base,int off,const char*ty);   /* forward：实现在下 */
static void eval_target_into(int reg,const char*callee){
    const char*p=callee;
    if(p[0]=='('&&p[1]=='*'){                   /* (*fp) 形态 */
        char name[128]={0};
        const char*q=p+2;
        int i=0;
        while(*q&&*q!=')'&&i<127) name[i++]=*q++;
        name[i]=0;
        LocVar*v=findloc(name);
        if(v){ load_mem(reg,8,v->off,v->ty); return; }
        fprintf(stderr,"UNSUPPORTED: 间接调用目标 '%s' 不是局部变量\n",name);
        exit(2);
    }
    /* 裸名字：截到 '(' 前的函数名 */
    char name[128]={0};
    int i=0;
    while(p[i]&&p[i]!='('&&i<127){ name[i]=p[i]; i++; }
    name[i]=0;
    /* 已知函数：地址 = 指令下标*4（PC 是 tryte 地址）。
     * 函数标签在 assemble 阶段才解析 —— 这里 emit 一个伪指令让
     * 汇编器查标签。asm_line 支持 "LUI rd, %label" 吗？不支持。
     * 最简做法：emit "LUI reg, <label>"，assemble_all 里的 asm_line
     * 对 LUI 的立即数走 parse_int —— 会失败。
     * 改为：查 FNS 拿到函数标签在 OUT 里的行号 —— 行号在 assemble 前
     * 就是地址（标签行不占指令）。直接扫 OUT 找 "<name>:" 的指令下标。 */
    int addr=-1,idx=0;
    for(int k=0;k<NOUT;k++){
        const char*L=OUT[k];
        size_t len=strlen(L);
        if(len>0&&L[len-1]==':'){
            char tmp[128]; snprintf(tmp,sizeof(tmp),"%.*s",(int)(len-1),L);
            if(!strcmp(tmp,name)) { addr=idx*4; break; }
            continue;                             /* 标签行不占指令 */
        }
        idx++;                                    /* 普通指令 */
    }
    if(addr>=0){ load_imm(reg,addr); return; }
    /* 不是本地函数：可能是全局变量里存的函数指针 */
    Global*g=global_find(name);
    if(g){ emitf("ADDI %s, zero, &%s",regname(SCRATCH),g->name); load_mem(reg,SCRATCH,0,g->ty); return; }
    fprintf(stderr,"UNSUPPORTED: 间接调用目标 '%s' 未知\n",name);
    exit(2);
}

/* 求值 n，结果放在返回的寄存器里；进入时 TDN=d0，返回后 TDN=d0+1 且结果 = TPOOL[d0] */
/* ===================== 类型推导与左值寻址 ===================== */
static int eval(Node*n);          /* forward */
static void gen_brfalse(Node*c,const char*lbl);   /* forward：定义在 gen_stmt 之前 */
static void gen_brtrue(Node*c,const char*lbl);    /* forward */

/* dst = src * c（c>=2），src 保持不被修改。
 * VM 里一条 MUL 约 70ns、一条 ADD 约 5ns，所以小常量用"加倍累加"展开远快于乘法：
 *   c=3 -> dst=src; dst+=dst; dst+=src        (2 条 ADD)
 *   c=9 -> dst=src; 2x; 4x; 8x; +=src         (4 条 ADD)
 * 调用者必须保证 dst != src（src 是只读的常驻寄存器时尤其重要）。 */
static void mul_const_into(int dst,int src,long long c){
    emitf("ADDI %s, %s, 0",regname(dst),regname(src));      /* dst = src */
    long long k=1;
    while(k*2<=c){ emitf("ADD %s, %s, %s",regname(dst),regname(dst),regname(dst)); k*=2; }
    while(k<c){    emitf("ADD %s, %s, %s",regname(dst),regname(dst),regname(src)); k++; }
}
/* 字面量是否真的落在 long long 内（num 是截断值，用 numw 校验） */
static int num_fits_ll(Node*n){ return n->numw==(__int128)n->num; }
/* 字面量整数取值（用于移位量这类必须是编译期常量的位置）。
 * 顺带折叠一元负号：`-1` 在 AST 里是 N_UN('-', N_NUM)，不折叠就当成变量移位量，
 * 于是 `x << -1` 会静默走运行时路径（按 0 次处理），把 UB 藏起来。 */
static int node_const_int(Node*n,long long*out){
    if(!n) return 0;
    if(n->k==N_NUM&&num_fits_ll(n)){ *out=n->num; return 1; }
    if(n->k==N_UN&&n->s&&!strcmp(n->s,"-")&&n->e&&n->e->k==N_NUM&&num_fits_ll(n->e)){
        *out=-n->e->num; return 1;
    }
    return 0;
}
/* 展开后是否仍划算（MUL≈70ns vs ADD≈5ns，最多容 12 条） */
static int mul_chain_cost(long long c){
    long long ac=c<0?-c:c; int n=1;      /* 1 条 ADDI 拷贝 */
    long long k=1;
    while(k*2<=ac){ n++; k*=2; }
    n += (int)(ac-k);
    return n;
}
static char* ty_cat(const char*a,const char*b){
    char*out=malloc(strlen(a)+strlen(b)+1); strcpy(out,a); strcat(out,b); return out;
}
static int fn_is_variadic(const char*name){
    for(int i=0;i<NFNRET;i++) if(!strcmp(FNEYS[i],name)) return FNVARGS[i];
    return 0;
}
static const char* fn_ret_type(const char*name){
    for(int i=0;i<NFNRET;i++) if(!strcmp(FNEYS[i],name)) return FNRETS[i];
    return "int";
}
/* 表达式类型（尽力而为；未知一律按 int 处理，与 C 的隐式 int 兼容） */
static char* expr_type(Node*n){
    if(!n) return "int";
    switch(n->k){
    case N_NUM: return "int";
    case N_STR: return "char*";
    case N_CAST: return n->s ? n->s : "int";   /* cast 后类型=目标类型 */
    case N_COMPOUND: return n->s ? n->s : "int";
    case N_VAR: { LocVar*v=findloc(n->s);
        if(!v){ Global*g=global_find(n->s); if(g) return g->ty; }
        return v&&v->ty?v->ty:"int"; }
    case N_INDEX: {
        char*bt=expr_type(n->l);
        return is_array(bt)?array_elem(bt):strdup((char*)pointee(bt));
    }
    /* 说明：is_array 已排除 int[3]*，指针走 pointee 分支 */
    case N_MEMBER: {
        Node*b=n->l;
        char*bt=expr_type(b);
        if(n->arrow) bt=(char*)pointee(bt);
        Member*m=member_find(bt,n->s);
        return m&&m->ty?m->ty:"int";
    }
    case N_DEREF: return (char*)pointee(expr_type(n->e));
    case N_ADDR:  return ty_cat(expr_type(n->e),"*");
    case N_ASSIGN:
    case N_PREINC: return expr_type(n->k==N_ASSIGN?n->l:n->e);
    case N_POSTINC: return expr_type(n->e);
    case N_CALL:  return (char*)fn_ret_type(n->s);
    case N_UN:
        if(n->e && !strcmp(n->s,"~") && is_tinty(expr_type(n->e)))
          return expr_type(n->e);            /* ~ 保持 Kleene 系类型 */
        return "int";
    case N_BIN: {
        const char*op=n->s;
        char*lt=expr_type(n->l), *rt=expr_type(n->r);
        if(!strcmp(op,"&")||!strcmp(op,"|")){
            if(is_tinty(lt) && is_tinty(rt))
              return (!strcmp(lt,"__tint")||!strcmp(rt,"__tint"))?"__tint":"__tbool";
        }
        if(!strcmp(op,"+")||!strcmp(op,"-")){
            if(is_ptr(lt)&&!is_ptr(rt)) return lt;
            if(is_ptr(rt)&&!is_ptr(lt)) return rt;
            if(is_array(lt)) return ty_cat(array_elem(lt),"*");
        }
        return "int";
    }
    default: return "int";
    }
}
static void load_mem(int dst,int base,int off,const char*ty){
    chk_off(off);
    /* 载入按符号性选指令：signed char 用 LB（符号扩展），其余 char 用 LBU（零扩展）。
     * 与 RISC-V 语义一致——LB/LH 符号扩展、LBU/LHU 零扩展。 */
    if(is_char_ty(ty))
        emitf("%s %s, %d(%s)", char_signed(ty)?"LB":"LBU", regname(dst),off,regname(base));
    else if(!strcmp(ty,"__tint2"))
        emitf("LW4 %s, %d(%s)",regname(dst),off,regname(base));   /* sx20 符号扩展 */
    else if(!strcmp(ty,"__tbool"))
        emitf("LB %s, %d(%s)",regname(dst),off,regname(base));    /* 有符号字节容器（精确往返） */
    else emitf("LW %s, %d(%s)",regname(dst),off,regname(base));
}
static void store_mem(int src,int base,int off,const char*ty){
    chk_off(off);
    if(is_char_ty(ty)) emitf("SB %s, %d(%s)",regname(src),off,regname(base));
    else if(!strcmp(ty,"__tint2"))
        emitf("SW4 %s, %d(%s)",regname(src),off,regname(base));   /* mod 3^20 截断 */
    else if(!strcmp(ty,"__tbool"))
        emitf("SB %s, %d(%s)",regname(src),off,regname(base));
    else emitf("SW %s, %d(%s)",regname(src),off,regname(base));
}
static void load_glob(int dst,Global*g){
    /* 数据地址符号化（2026-11-22 §5.7）：烘焙常量在跨单元重排后错位（ecvt IN[40]
       实测踩烂库全局）；&name 在汇编期绑定到合并后的新槽 */
    emitf("ADDI %s, zero, &%s",regname(SCRATCH),g->name);
    load_mem(dst,SCRATCH,0,g->ty);
}
static void store_glob(int src,Global*g){
    emitf("ADDI %s, zero, &%s",regname(SCRATCH),g->name);
    store_mem(src,SCRATCH,0,g->ty);
}
/* 全串替换：old 字面量所有词边界命中处替换为 new；无命中返回 NULL。
 * 用于 TRVO 导入体的字符串符号改名（&__str_k → &__strimp_N）；
 * 标识符字符跟随时不替换，防 xy__str_1 误改（tvo_relabel 同款边界）。 */
static char*str_replace_all(const char*line,const char*old,const char*new){
    size_t lo=strlen(old),ln=strlen(new);
    const char*p=line; int hits=0;
    while((p=strstr(p,old))){
        if(p>line&&(isalnum((unsigned char)p[-1])||p[-1]=='_')){p++;continue;}
        if(isalnum((unsigned char)p[lo])||p[lo]=='_'){p++;continue;}
        hits++; p+=lo;
    }
    if(!hits) return NULL;
    char*out=malloc(strlen(line)+hits*(ln>lo?ln-lo:lo-ln)+2);
    char*w=out; const char*q=line;
    p=line;
    while((p=strstr(p,old))){
        if(p>line&&(isalnum((unsigned char)p[-1])||p[-1]=='_')){p++;continue;}
        if(isalnum((unsigned char)p[lo])||p[lo]=='_'){p++;continue;}
        size_t skip=p-q;
        memcpy(w,q,skip); w+=skip;
        memcpy(w,new,ln); w+=ln;
        p+=lo; q=p;
    }
    strcpy(w,q);
    return out;
}
/* 把"元素的起始地址"算进寄存器并返回 */
static int addr_of(Node*n){
    Node*lv = (n->k==N_ADDR)? n->e : n;
    if(lv->k==N_VAR){
        LocVar*v=findloc(lv->s);
        if(!v){
            Global*g=global_find(lv->s);
            if(g){ int r=tacq(); emitf("ADDI %s, zero, &%s",regname(r),g->name); return r; }
            {   /* &func 取函数地址豁免；真未声明 → 报错（静默地址 0 = 零页写）。
                   地址走符号 ADDI（汇编期绑定，含导入函数）——烘焙常量会被
                   peephole 前移变陈旧（cmp_char 44 vs 40 实证） */
                if(is_known_fn(lv->s)){ int r=tacq(); emitf("ADDI %s, zero, &%s",regname(r),lv->s); return r; }
            }
            fprintf(stderr,"UNSUPPORTED: undeclared identifier '%s'\n",lv->s);
            exit(2);
        }
        if(!v->spilled){
            fprintf(stderr,"UNSUPPORTED: address of register-allocated local '%s'\n",lv->s);
            exit(2);
        }
        { int r=tacq();
        /* &x：x 的栈槽地址。指针变量也一样——&p 要的是 p 自身的
         * 槽地址（char**），不是 p 的值（那是 *p / N_DEREF 的语义，
         * 在 eval N_DEREF 分支处理）。 */
        chk_off(v->off);
        emitf("ADDI %s, s0, %d",regname(r),v->off);
        return r; }
    }
    if(lv->k==N_DEREF){ int a=eval(lv->e); return rwcopy(a); }   /* *p 的地址= p 的值，不能改写 p */
    if(lv->k==N_INDEX){
        char*bt=expr_type(lv->l);
        char*et=is_array(bt)?array_elem(bt):strdup((char*)pointee(bt));
        int es=ty_bytes(et);
        int base = is_array(bt)? addr_of(lv->l) : rwcopy(eval(lv->l));   /* 指针变量要独立一份 */
        int dbase = TDN;                        /* base 之后的水位 = 结果所在深度+1 */
        int idx  = eval(lv->r);
        if(es!=1){
            load_imm(SCRATCH,es);
            if(in_tpool(idx)) emitf("MUL %s, %s, %s",regname(idx),regname(idx),regname(SCRATCH));
            else {                              /* 索引直接来自变量寄存器，缩放要用临时 */
                int t=tacq();
                emitf("MUL %s, %s, %s",regname(t),regname(idx),regname(SCRATCH));
                idx=t;
            }
        }
        emitf("ADD %s, %s, %s",regname(base),regname(base),regname(idx));
        /* 按标记回退水位：索引是表达式才占过临时，是变量则根本没占，
           用 TDN-- 会把 base 一起释放掉，导致上层复用 base 的寄存器。 */
        TDN=dbase;
        return base;
    }
    if(lv->k==N_MEMBER){
        char*bt=expr_type(lv->l);
        int base;
        if(lv->arrow){ bt=(char*)pointee(bt); base=rwcopy(eval(lv->l)); }
        else if(is_array(bt)) base=addr_of(lv->l);
        else base=addr_of(lv->l);
        Member*m=member_find(bt,lv->s);
        if(!m){ fprintf(stderr,"UNSUPPORTED: unknown member '%s'\n",lv->s); exit(2); }
        if(m->off) emitf("ADDI %s, %s, %d",regname(base),regname(base),m->off);
        return base;
    }
    return eval(n);
}

static int eval(Node*n){
    if(!n){ int r=tacq(); emitf("ADDI %s, zero, 0",regname(r)); return r; }
    int d0=TDN;
    switch(n->k){
    case N_COMPOUND: {
        /* 复合字面量：成员初始化写入帧内匿名缓冲，表达式值=缓冲首地址
         * （struct 表达式求值=首地址语义，与 sret 返回一致） */
        StructDef*sd=struct_find(n->s);
        if(!sd){ fprintf(stderr,"UNSUPPORTED: 复合字面量类型 '%s' 不是 struct\n",n->s); exit(2); }
        int base=SRET_BASE+n->sret_off;
        for(int i=0;i<sd->nm && i<n->e->nitems;i++){
            Node*it=n->e->items[i];
            int sub=0;
            gen_init_at(sd->m[i].ty, base+sd->m[i].off, it, &sub);
        }
        int r=tacq();
        emitf("ADDI %s, s0, %d",regname(r),base);
        return r;
    }
    case N_NUM: {
        for(int i=0;i<CC_N;i++) if(CC_V[i]==n->numw) return CC_R[i];  /* 已预装：只读共享 */
        int r=tacq(); load_imm128(r,n->numw); return r;
    }
    case N_STR: { int r=tacq(); emitf("ADDI %s, zero, &__str_%d",regname(r),n->strsym); return r; }
    case N_VAR: {
        LocVar*v=findloc(n->s);
        /* 全局优先级：局部遮蔽全局（C 作用域规则）。
         * 数组/结构体全局：值=地址；标量/指针全局：按类型 LW/LB。 */
        if(!v){
            Global*g=global_find(n->s);
            if(g){
                if(is_array(g->ty)||struct_find(g->ty)) return addr_of(n);
                int r=tacq();
                load_glob(r,g);
                return r;
            }
            {   /* 函数名 -> 函数地址（函数指针语义，供 __libc_start_main(main) 等）。
                   地址走符号 ADDI（汇编期绑定，含导入函数）——烘焙常量会被
                   peephole 前移变陈旧（cmp_char 44 vs 40 实证） */
                if(is_known_fn(n->s)){ int r=tacq(); emitf("ADDI %s, zero, &%s",regname(r),n->s); return r; }
            }
        }
        /* 数组名求值 = 数组首地址（C 的退化规则）；结构体同理 —— 结构体右值的
         * "值"就是首地址，真正按值拷贝由赋值/传参/返回这些消费方自己做。 */
        if(v&&(is_array(v->ty)||struct_find(v->ty))) return addr_of(n);
        /* 常驻寄存器里的变量直接返回其寄存器，省掉 `ADDI t, s, 0` 这次拷贝。
           结果因此是只读的，上级若要写必须先 dstof()。 */
        if(v&&!v->spilled&&v->reg>0) return v->reg;
        int r=tacq();
        if(!v){
            /* 诚实报错（2026-11-22，未声明标识符专项）：未知符号静默零页
               比编译失败危险得多（P4 实测 IN[k] 未声明 → 全 ±0 错码）。
               函数名豁免已在上方 fn_label_addr 分支完成；隐式声明函数的
               调用走 N_CALL 名字直发 + 链接期 scan_undef，不经此路径。 */
            fprintf(stderr,"UNSUPPORTED: undeclared identifier '%s'\n",n->s);
            exit(2);
        }
        load_mem(r,8,v->off,v->ty);                            /* 按类型选 LB/LBU/LW */
        return r;
    }
    case N_UN: {
        int a=eval(n->e);
        int d=dstof(a);                                  /* -x 不能原地改写 x */
        if(!strcmp(n->s,"-")){
            if(is_tinty(expr_type(n->e))){
                fprintf(stderr,"UNSUPPORTED: __tint has no unary '-' (Kleene system)\n");
                exit(2);
            }
            emitf("SUB %s, zero, %s",regname(d),regname(a));
        }
        else if(!strcmp(n->s,"!")) emitf("SLTIU %s, %s, 1",regname(d),regname(a));
        else if(!strcmp(n->s,"~")){
            emitf("TNOT %s, %s",regname(d),regname(a));    /* 本征：2-t，~0=-1 */
        }
        return d;
    }
    case N_BIN: {
        const char*op=n->s;
        if(!strcmp(op,"&&")||!strcmp(op,"||")){          /* C 短路语义 */
            int a=eval(n->l);
            int d=dstof(a);
            emitf("SLTU %s, zero, %s",regname(d),regname(a));   /* bool(x) = (x != 0)，一条搞定 */
            char*shortl=newlbl("short"),*endl=newlbl("logic");
            emitf("%s %s, zero, %s", !strcmp(op,"&&")?"BEQ":"BNE", regname(d), shortl);
            int b=eval(n->r);
            int bd=dstof(b);
            emitf("SLTU %s, zero, %s",regname(bd),regname(b));
            if(bd!=d) emitf("ADDI %s, %s, 0",regname(d),regname(bd));
            emitf("JAL zero, %s",endl);
            lbl(shortl);
            emitf("ADDI %s, zero, %d",regname(d), !strcmp(op,"&&")?0:1);
            lbl(endl);
            TDN=d0+1;
            return d;
        }
        /* 常量乘法强度削减：小常量展开成加法链，避开 70ns 的硬件乘法 */
        if(!strcmp(op,"*")&&(n->l->k==N_NUM||n->r->k==N_NUM)){
            Node*nm=n->r->k==N_NUM?n->r:n->l;
            Node*vr=n->r->k==N_NUM?n->l:n->r;
            long long c=nm->num;
            if(num_fits_ll(nm)&&c!=0&&c!=1&&c!=-1&&vr->k!=N_NUM&&mul_chain_cost(c)<=12){
                int a=eval(vr);
                int d=tacq();                          /* 必须与 a 分开：a 的原值还要用 */
                mul_const_into(d,a,c<0?-c:c);
                if(c<0) emitf("SUB %s, zero, %s",regname(d),regname(d));
                if(d!=TPOOL[d0]){ emitf("ADDI %s, %s, 0",regname(TPOOL[d0]),regname(d)); d=TPOOL[d0]; }
                TDN=d0+1;
                return d;
            }
        }
        char*lt=expr_type(n->l), *rt=expr_type(n->r);
        {   /* __tint/__tbool Kleene 门控（批B，2026-11-23）：与 tri-tcc C1 同口径。
               允许 & |（本征直发即 TAND/TOR）与 ==/!=（位型全等）；拒绝
               ^ < > <= >= + - * / % << >>；混 int 报错（==0/!=0 常量豁免）。
               && || 走 int 真值路径（与 tri-tcc 同，不入契约）。 */
            int ltint=is_tinty(lt), rtint=is_tinty(rt);
            if(ltint&&rtint){
                if(strcmp(op,"&")&&strcmp(op,"|")&&strcmp(op,"==")&&strcmp(op,"!=")){
                    fprintf(stderr,"UNSUPPORTED: __tint has no '%s' (Kleene system: no xor/order/arithmetic)\n",op);
                    exit(2);
                }
            } else if(ltint!=rtint){
                int zero_side = (n->l->k==N_NUM && n->l->numw==0)
                             || (n->r->k==N_NUM && n->r->numw==0);
                if(!((!strcmp(op,"==")||!strcmp(op,"!=")) && zero_side)){
                    fprintf(stderr,"UNSUPPORTED: implicit mixing of int and __tint/__tbool is not allowed\n");
                    exit(2);
                }
            }
        }
        int a=eval(n->l);
        int b=eval(n->r);
        /* 目标寄存器优先复用本次表达式的私有临时；两侧都是常驻寄存器
           （`a * b` 这种纯变量运算）时才新取一个。 */
        int d = in_tpool(a)? a : (in_tpool(b)? b : tacq());
        if(d!=TPOOL[d0]){                    /* 维持"结果落在 TPOOL[d0]"的不变量 */
            emitf("ADDI %s, %s, 0",regname(TPOOL[d0]),regname(d));
            d=TPOOL[d0];
        }
        if(!strcmp(op,"<<")||!strcmp(op,">>")){
            /* 三值本征移位（2026-11-22 方言拍板）：<< = ×3^k（SLLI），>> =
               ÷3^k 向零截断（SRAI，≡ 除法）或 raw ÷3^k（SRLI，无符号）。
               有符号性只看**左操作数**：移位量的符号性无关紧要，
               而 char/uchar 提升成 int 后必然非负，按有符号处理结果一致。 */
            int uns = ty_uns(lt);
            int isl = op[0]=='<';
            long long k;
            if(node_const_int(n->r,&k)){
                if(k<0){ fprintf(stderr,"UNSUPPORTED: 移位量为负 %lld（C 语义未定义）\n",k); exit(2); }
                if(k>4096){ fprintf(stderr,"UNSUPPORTED: 移位量 %lld 过大\n",k); exit(2); }
                if(isl) emit_shl_k(d,a,k); else emit_shr_k(d,a,k,uns);
            } else {
                /* 变量移位：单条原生指令（移位量硬件取 mod 3^7） */
                if(isl) emitf("SLL %s, %s, %s",regname(d),regname(a),regname(b));
                else emitf(uns?"SRL %s, %s, %s":"SRA %s, %s, %s",regname(d),regname(a),regname(b));
            }
            TDN=d0+1;
            return d;
        }
        if((!strcmp(op,"+")||!strcmp(op,"-"))&&is_ptr(lt)&&!is_ptr(rt)){
            int es=ty_bytes(pointee(lt));
            if(es!=1&&!in_tpool(b)){         /* 偏移量在常驻寄存器里，不能原地缩放 */
                load_imm(SCRATCH,es);
                emitf("MUL %s, %s, %s",regname(d),regname(b),regname(SCRATCH));
                emitf("%s %s, %s, %s",op[0]=='+'?"ADD":"SUB",regname(d),regname(a),regname(d));
                TDN=d0+1; return d;
            }
            if(es!=1){ load_imm(SCRATCH,es); emitf("MUL %s, %s, %s",regname(b),regname(b),regname(SCRATCH)); }
        } else if(!strcmp(op,"+")&&is_ptr(rt)&&!is_ptr(lt)){
            int es=ty_bytes(pointee(rt));
            if(es!=1&&!in_tpool(a)){
                load_imm(SCRATCH,es);
                emitf("MUL %s, %s, %s",regname(d),regname(a),regname(SCRATCH));
                emitf("ADD %s, %s, %s",regname(d),regname(d),regname(b));
                TDN=d0+1; return d;
            }
            if(es!=1){ load_imm(SCRATCH,es); emitf("MUL %s, %s, %s",regname(a),regname(a),regname(SCRATCH)); }
        } else if(!strcmp(op,"-")&&is_ptr(lt)&&is_ptr(rt)){
            int es=ty_bytes(pointee(lt));
            emitf("SUB %s, %s, %s",regname(d),regname(a),regname(b));
            if(es!=1){ load_imm(SCRATCH,es); emitf("DIV %s, %s, %s",regname(d),regname(d),regname(SCRATCH)); }
            TDN=d0+1;
            return d;
        }
        if(!strcmp(op,"&")||!strcmp(op,"|")||!strcmp(op,"^")){
            /* 三值本征：TAND(min)/TOR(max)/XOR(mod-3 加)，单条直发 */
            emitf("%s %s, %s, %s", op[0]=='&'?"TAND":op[0]=='|'?"TOR":"XOR",
                  regname(d),regname(a),regname(b));
            TDN=d0+1;
            return d;
        }
        int uns = ty_uns(lt)||ty_uns(rt);
        const char*ins=binop_insn(op);
        if(ins){
            if(uns&&!strcmp(ins,"DIV")) ins="DIVU";
            else if(uns&&!strcmp(ins,"REM")) ins="REMU";
            emitf("%s %s, %s, %s",ins,regname(d),regname(a),regname(b));
        }
        else emit_rel_u(op,d,a,b,uns);
        TDN=d0+1;                                        /* 释放右操作数 */
        return d;
    }
    case N_ASSIGN: {
        Node*lv=n->l;
        StructDef*sd=struct_find(expr_type(lv));
        if(sd){                                          /* 结构体整块拷贝 */
            int dst=addr_of(lv);
            int src=addr_of(n->r);
            for(int o=0;o<sd->size;o+=9){
                emitf("LW %s, %d(%s)",regname(SCRATCH),o,regname(src));
                emitf("SW %s, %d(%s)",regname(SCRATCH),o,regname(dst));
            }
            int r=tacq();
            if(dst!=r) emitf("ADDI %s, %s, 0",regname(r),regname(dst));
            return r;
        }
        /* —— 目标驱动求值：把右侧直接算进左值自己的常驻寄存器 ——
           只处理最常见且无副作用的形态（常量 / x op y / 另一个变量），
           其余仍走通用路径，行为完全不变。 */
        if(lv->k==N_VAR&&n->r){
            LocVar*tg=findloc(lv->s);
            Node*r=n->r;
            if(tg&&!tg->spilled&&tg->reg>0&&!is_ptr(expr_type(lv))){
                const char*R=regname(tg->reg);
                if(r->k==N_NUM){ load_imm128(tg->reg,r->numw); return tg->reg; }
                /* x = x ± 小常量 -> 一条 ADDI */
                if(r->k==N_BIN&&r->l&&r->l->k==N_VAR&&!strcmp(r->l->s,lv->s)&&
                   r->r&&r->r->k==N_NUM&&num_fits_ll(r->r)&&(!strcmp(r->s,"+")||!strcmp(r->s,"-"))){
                    long long c=r->r->num; if(!strcmp(r->s,"-")) c=-c;
                    if(c>=-1093&&c<=1093){ emitf("ADDI %s, %s, %lld",R,R,c); return tg->reg; }
                }
                /* x = x * 小常量：加法链比 MUL 快一个数量级 */
                if(r->k==N_BIN&&!strcmp(r->s,"*")&&r->l&&r->l->k==N_VAR&&
                   !strcmp(r->l->s,lv->s)&&r->r&&r->r->k==N_NUM){
                    long long c=r->r->num;
                    if(num_fits_ll(r->r)&&c!=0&&c!=1&&c!=-1&&mul_chain_cost(c)<=12){
                        int t=tacq();
                        mul_const_into(t,tg->reg,c<0?-c:c);
                        if(c<0) emitf("SUB %s, zero, %s",regname(t),regname(t));
                        emitf("ADDI %s, %s, 0",R,regname(t));
                        TDN=0;
                        return tg->reg;
                    }
                }
                /* x = x op y（只取无符号/移位歧义的 + -；& | ^ 走通用路径的适配层） */
                if(r->k==N_BIN&&r->l&&r->l->k==N_VAR&&!strcmp(r->l->s,lv->s)&&r->r){
                    const char*op=r->s;
                    if(!strcmp(op,"+")||!strcmp(op,"-")){
                        int b=eval(r->r);
                        emitf("%s %s, %s, %s",binop_insn(op),R,R,regname(b));
                        TDN=0;
                        return tg->reg;
                    }
                }
                /* x = 另一个常驻变量 */
                if(r->k==N_VAR){
                    LocVar*sv=findloc(r->s);
                    if(sv&&!sv->spilled&&sv->reg>0&&!is_array(sv->ty)){
                        if(sv->reg!=tg->reg) emitf("ADDI %s, %s, 0",R,regname(sv->reg));
                        return tg->reg;
                    }
                }
            }
        }
        int v=eval(n->r);                                /* 值（也可能是左值地址的来源） */
        int d1=TDN;
        if(lv->k==N_VAR){
            LocVar*l=findloc(lv->s);
            Global*gg=!l?global_find(lv->s):NULL;
            if(gg) store_glob(v,gg);
            else if(!l){
                /* 赋值目的未声明：store 静默丢弃比零页更隐蔽，必须报错 */
                fprintf(stderr,"UNSUPPORTED: undeclared identifier '%s'\n",lv->s);
                exit(2);
            }
            else if(l->spilled){ store_mem(v,8,l->off,l->ty); }
            else if(l->reg!=v) emitf("ADDI %s, %s, 0",regname(l->reg),regname(v));
        } else if(lv->k==N_DEREF){
            char*ty=(char*)pointee(expr_type(lv->e));
            int pa=eval(lv->e);
            store_mem(v,pa,0,ty);
        } else if(lv->k==N_INDEX||lv->k==N_MEMBER){
            char*ty=expr_type(lv);
            int pa=addr_of(lv);
            store_mem(v,pa,0,ty);
        } else {
            int pa=eval(lv);
            emitf("SW %s, 0(%s)",regname(v),regname(pa));
        }
        TDN=d1;                                          /* 丢弃地址临时，保留值 */
        return v;
    }
    case N_POSTINC: {
        /* 后缀：表达式值为【旧值】，副作用是 +d 写回。 */
        Node*lv=n->e; int d=(int)n->num;
        int oldv;
        if(lv->k==N_VAR){
            LocVar*l=findloc(lv->s);
            oldv=tacq();
            if(!l){
                Global*gg=global_find(lv->s);
                if(!gg){
                    fprintf(stderr,"UNSUPPORTED: undeclared identifier '%s'\n",lv->s);
                    exit(2);
                }
                {
                    load_glob(oldv,gg);
                    int r2=tacq();
                    emitf("ADDI %s, %s, %d",regname(r2),regname(oldv),d);
                    store_glob(r2,gg);
                    return oldv;
                }
            } else if(!l->spilled){
                emitf("ADDI %s, %s, 0",regname(oldv),regname(l->reg));
                emitf("ADDI %s, %s, %d",regname(l->reg),regname(l->reg),d);
            } else {
                load_mem(oldv,8,l->off,l->ty);
                int r2=tacq();
                emitf("ADDI %s, %s, %d",regname(r2),regname(oldv),d);
                store_mem(r2,8,l->off,l->ty);
            }
            return oldv;
        }
        int pa=eval(lv);
        oldv=tacq();
        emitf("LW %s, 0(%s)",regname(oldv),regname(pa));
        int r2=tacq();
        emitf("ADDI %s, %s, %d",regname(r2),regname(oldv),d);
        emitf("SW %s, 0(%s)",regname(r2),regname(pa));
        return oldv;
    }
    case N_PREINC: {
        Node*lv=n->e; int d=(int)n->num;
        if(lv->k==N_VAR){
            LocVar*l=findloc(lv->s);
            int r=tacq();
            if(!l){
                Global*gg=global_find(lv->s);
                if(!gg){
                    fprintf(stderr,"UNSUPPORTED: undeclared identifier '%s'\n",lv->s);
                    exit(2);
                }
                load_glob(r,gg);
                emitf("ADDI %s, %s, %d",regname(r),regname(r),d);
                store_glob(r,gg); return r;
            } else if(!l->spilled){
                emitf("ADDI %s, %s, %d",regname(l->reg),regname(l->reg),d);
                if(l->reg!=r) emitf("ADDI %s, %s, 0",regname(r),regname(l->reg));
            } else {
                load_mem(r,8,l->off,l->ty);
                emitf("ADDI %s, %s, %d",regname(r),regname(r),d);
                store_mem(r,8,l->off,l->ty);
            }
            return r;
        }
        int pa=eval(lv); int r=tacq();
        emitf("LW %s, 0(%s)",regname(r),regname(pa));
        emitf("ADDI %s, %s, %d",regname(r),regname(r),d);
        emitf("SW %s, 0(%s)",regname(r),regname(pa));
        return r;
    }
    case N_DEREF: {
        char*ty=(char*)pointee(expr_type(n->e));
        int a=eval(n->e);
        int d=dstof(a);
        load_mem(d,a,0,ty);
        return d;
    }
    case N_ADDR: return addr_of(n->e);
    case N_CALL: {
        int nargs=n->nargs;
        int sn=syscall_no(n->s);
        /* __builtin_tri_lit(x, a)：Post 文字运算直发（2026-11-23 内建，与
           tri-tcc 同名同语义；ternary-isa-design.md §6.1）。a 编译期常量
           0..2；超界/非常量诚实报错（exit 2 口径）。结果类型同 x（词级 int）。 */
        if(!strcmp(n->s,"__builtin_tri_lit")){
            Node *xa;
            if(nargs!=2){ fprintf(stderr,"UNSUPPORTED: __builtin_tri_lit expects 2 args\n"); exit(2); }
            xa=n->args[1];
            if(xa->k!=N_NUM || xa->numw<0 || xa->numw>2){
                fprintf(stderr,"UNSUPPORTED: __builtin_tri_lit expects constant a in 0..2\n");
                exit(2);
            }
            {
                int r=eval(n->args[0]);
                int d=tacq();
                emitf("LIT%d %s, %s, zero",(int)xa->numw,regname(d),regname(r));
                /* 结果在临时寄存器而非 a0——return 语境的 A0_DIRECT 必须清零，
                   否则 return-gen 跳过 a0 拷贝（return 直返 0 实证）。 */
                A0_DIRECT = 0;
                return d;
            }
        }
        /* 间接调用：(*fp)(...) / (fn)(...) 形态，n->s=="(*target)"，
         * 目标表达式在 n->e。返回类型按 int（无签名信息）。 */
        int indirect = (n->s[0]=='(') && strstr(n->s,"target")!=NULL;
        /* 返回 struct 的调用走 sret：a0 = 本帧里那块返回缓冲的地址，
         * 实参寄存器窗口整体后移一位；调用完成后 a0 会把缓冲地址带回来。 */
        int sret = (sn<0) && struct_find(fn_ret_type(n->s))!=NULL;
        int abase = sret?1:0;
        int nreg  = 8-abase;
        /* "return f(...) 结果直留 a0"这个标记只对本层调用有效。实参里还有嵌套调用时
         * 内层调用会跟着跳过 `ADDI r, a0, 0`，把寄存器池里一个失效的旧值当返回值存下去。
         * 实测（修复前）：`return f(g(3),g(4))` 返回 0 而不是 34，且编译期毫无提示。
         * 2026-11-19 由契约测试的 f(g(),h()) 用例查出来。 */
        int direct_here = A0_DIRECT;
        A0_DIRECT = 0;
        /* 1) 活跃临时全部落栈：被调者会破坏 t 系列寄存器。索引=深度，嵌套不会互相覆盖。 */
        for(int i=0;i<d0;i++) emitf("SW %s, %d(s0)",regname(TPOOL[i]),CSAVE_OFF+i*9);
        /* 快路径：实参全是常量或标量变量、不超过寄存器窗口且非变参时，
           直接装进 a 寄存器，省掉"先写暂存区再读回"的两趟访存。 */
        int fast = (sn<0)&&nargs<=nreg&&!fn_is_variadic(n->s);
        for(int i=0;i<nargs&&fast;i++){
            Node*a=n->args[i];
            if(a->k==N_NUM) continue;
            /* struct 实参必须传地址，不能读第一个字，故不进快路径 */
            if(a->k==N_VAR){ LocVar*v=findloc(a->s); if(v&&!is_array(v->ty)&&!struct_find(v->ty)) continue; }
            fast=0;
        }
        if(fast){
            for(int i=0;i<nargs;i++){
                Node*a=n->args[i];
                if(a->k==N_NUM){ load_imm128(10+abase+i,a->numw); continue; }
                LocVar*v=findloc(a->s);
                if(!v && getenv("TRV_ASM_DEBUG"))
                  fprintf(stderr,"[asm-debug] fast-arg %d: 名字 %s findloc 未中\n",i,a->s);
                if(!v){
                    /* 函数名实参 → 符号 &name（fn-ptr 传参，导入函数同支持）；
                       真未声明 → 诚实报错（原静默 ADDI 0 是零页风险） */
                    if(is_known_fn(a->s))
                        emitf("ADDI a%d, zero, &%s",abase+i,a->s);
                    else{
                        fprintf(stderr,"UNSUPPORTED: undeclared identifier '%s'\n",a->s);
                        exit(2);
                    }
                }
                else if(!v->spilled&&v->reg>0){ if(v->reg!=10+abase+i) emitf("ADDI a%d, %s, 0",abase+i,regname(v->reg)); }
                else { chk_off(v->off); emitf("LW a%d, %d(s0)",abase+i,v->off); }
            }
            if(sret){ chk_off(SRET_BASE+n->sret_off); emitf("ADDI a0, s0, %d",SRET_BASE+n->sret_off); }
            if(indirect){
                /* 间接调用：目标表达式装临时寄存器再 JALR */
                int tr=eval(n->e);
                emitf("JALR ra, 0(%s)",regname(tr));
            } else {
                /* 被调名三级解析（同慢路径）：
                 * 1) FNS 有体 → 直发 JAL；
                 * 2) 局部/形参/全局同名（fn-ptr 变量）→ 读槽 JALR；
                 * 3) 原型登记名 / 隐式声明（C89，跨单元后链）→ 符号直发 JAL。
                 * 勿走 eval(N_VAR)——未声明闸门会 exit(2)，切断 C89 隐式
                 * 声明的跨单元调用（musl_wide 批量 29 失败实证）。 */
                int known = 0;                 /* 1=FNS 直发 2=符号直发 3=fn-ptr 变量 */
                { int k; for(k=0;k<NFN;k++) if(!strcmp(FNS[k]->s,n->s)){known=1;break;} }
                if(!known && (findloc(n->s)||global_find(n->s))) known=3;
                if(!known) known=2;
                if(known==3){
                    Node*v=nnew(N_VAR); v->s=n->s;
                    int tr=eval(v);
                    emitf("JALR ra, 0(%s)",regname(tr));
                } else emitf("JAL ra, %s",n->s);
            }
        } else {
            /* 2) 实参逐个求值后暂存（嵌套调用会破坏 a0-a7，故不能直接放进 a 寄存器）。
                struct 实参求值结果就是首地址（C 的按值传参由被调方入口拷贝实现）。 */
            for(int i=0;i<nargs;i++){
                int r=eval(n->args[i]);
                emitf("SW %s, %d(s0)",regname(r),ARG_OFF+i*9);
                TDN=d0;
            }
            /* 变参或被调函数形参超过寄存器窗口：参数块在帧内连续排布，块基址进 t6。
               块放在帧内（而不是 sp 之下）是为了不与已求值的实参暂存区互相踩踏。 */
            int need_block = (sn<0) && (fn_is_variadic(n->s)||nargs>nreg);
            if(need_block) emitf("ADDI t6, s0, %d",ARG_OFF);
            for(int i=0;i<nargs&&i<nreg;i++) emitf("LW a%d, %d(s0)",abase+i,ARG_OFF+i*9);
            if(sn>=0){ load_imm(A7,sn); emitf("ECALL"); }
            else {
                if(sret){ chk_off(SRET_BASE+n->sret_off); emitf("ADDI a0, s0, %d",SRET_BASE+n->sret_off); }
                if(indirect){
                    int tr=eval(n->e);
                    emitf("JALR ra, 0(%s)",regname(tr));
                } else {
                    /* 被调名三级解析（2026-11-22 未声明标识符专项重构）：
                     * 1) FNS 有体 → 直发 JAL（本编译单元可见定义）；
                     * 2) 局部/形参/全局同名 → 函数指针语义，读变量槽 JALR
                     *    （形参 fn-ptr 调用，如 qsort 的 cmp——不得被原型抢注）；
                     * 3) 原型登记名 / 隐式声明（C89）→ 符号直发 JAL：
                     *    .trvo 文本导出在导入期由被调方定义解析；
                     *    可执行链接由 scan_undef 兜底报 undefined reference。
                     * 坑实录：旧路径对 FNS 查无的名字合成 N_VAR 走静默兜底——
                     * 原型-only 调用（qsort_tri 的 memcpy）被编译成消失/跳错
                     * 目标的垃圾码，测试未踩到该分支故长期潜伏。 */
                    int known = 0;                 /* 1=FNS 直发 2=符号直发 3=fn-ptr 变量 */
                    { int k; for(k=0;k<NFN;k++) if(!strcmp(FNS[k]->s,n->s)){known=1;break;} }
                    if(!known && (findloc(n->s)||global_find(n->s))) known=3;
                    if(!known) known=2;            /* 原型登记名 / 隐式声明 */
                    if(known==3){
                        Node*v=nnew(N_VAR); v->s=n->s;
                        int tr=eval(v);
                        emitf("JALR ra, 0(%s)",regname(tr));
                    } else emitf("JAL ra, %s",n->s);
                }
            }
        }
        /* 3) 恢复活跃临时，结果取回 */
        A0_DIRECT = direct_here;                 /* 本层才是"直通 a0"的对象 */
        for(int i=0;i<d0;i++) emitf("LW %s, %d(s0)",regname(TPOOL[i]),CSAVE_OFF+i*9);
        int r=tacq();
        if(!A0_DIRECT) emitf("ADDI %s, a0, 0",regname(r));   /* return f() 时 a0 已是结果 */
        return r;
    }
    case N_VASTART: {
        LocVar*ab=findloc("__argbase");
        if(!ab){ fprintf(stderr,"UNSUPPORTED: va_start outside variadic function\n"); exit(2); }
        int pa=addr_of(n->e);
        int r=tacq();
        chk_off(ab->off);
        emitf("LW %s, %d(s0)",regname(r),ab->off);
        if(NFIXED) emitf("ADDI %s, %s, %d",regname(r),regname(r),NFIXED*9);
        emitf("SW %s, 0(%s)",regname(r),regname(pa));
        emitf("ADDI %s, zero, 0",regname(r));
        return r;
    }
    case N_VAARG: {
        int pa=addr_of(n->e);
        int c=tacq(), v=tacq();
        emitf("LW %s, 0(%s)",regname(c),regname(pa));
        load_mem(v,c,0,n->s);
        emitf("ADDI %s, %s, 9",regname(c),regname(c));
        emitf("SW %s, 0(%s)",regname(c),regname(pa));
        emitf("ADDI %s, %s, 0",regname(TPOOL[d0]),regname(v));
        TDN=d0+1;
        return TPOOL[d0];
    }
    case N_VAEND: { int r=tacq(); emitf("ADDI %s, zero, 0",regname(r)); return r; }
    case N_INDEX:
    case N_MEMBER: {
        char*ty=expr_type(n);
        int pa=addr_of(n);
        if(struct_find(ty)) return pa;            /* 成员本身是结构体：值是首地址 */
        int d=dstof(pa);
        if(strcmp(ty,"int")) load_mem(d,pa,0,ty);
        else emitf("LW %s, 0(%s)",regname(d),regname(pa));
        return d;
    }
    case N_CAST:
        /* (type)cast：TriRV 标量全部 45-trit 等宽，cast 是类型标注。
         * 例外是窄化目标 —— musl 常用形态是
         * 指针 cast 与 (unsigned) 取模，指针 cast 无操作；
         * 1-tryte（char/__tbool）mod 243 归一（signed REM ≡ mod+sx5）；
         * __tint2（C2 方案 A）mod 3^20 + sx20（与 tri-gen/py 同口径）。 */
        if(n->s && !strncmp(n->s,"char",4) && !strchr(n->s,'*')){
            int v=eval(n->e);
            int d=dstof(v);
            load_imm(SCRATCH,243);          /* R 型：立即数先进寄存器 */
            /* 源必须是 v（操作数所在寄存器）——dstof 可能新配 d，d,d 读空槽
               （return 语境常量直落 a0=x10、d=tacq=x5，REM x5,x5 读垃圾实证） */
            emitf("REM %s, %s, %s",regname(d),regname(v),regname(SCRATCH));
            return d;
        }
        if(n->s && !strcmp(n->s,"__tbool") && !strchr(n->s,'*')){
            int v=eval(n->e);
            int d=dstof(v);
            load_imm(SCRATCH,243);          /* signed REM ≡ mod 243 + sx5 */
            emitf("REM %s, %s, %s",regname(d),regname(v),regname(SCRATCH));
            return d;
        }
        if(n->s && !strcmp(n->s,"__tint2") && !strchr(n->s,'*')){
            /* 常数装载不得用 SCRATCH（多段自覆写，见 load_imm128 守卫）——
               双临时 f/g 承载 3^20 与半阈值。 */
            int v=eval(n->e);
            int f=tacq();
            int g=tacq();
            int d=dstof(v);
            load_imm(f,3486784401LL);       /* 3^20 */
            emitf("REMU %s, %s, %s",regname(d),regname(v),regname(f));
            load_imm(g,1743392201LL);       /* 半阈值+1 */
            emitf("SLTU %s, %s, %s",regname(f),regname(d),regname(g));
            emitf("SLTIU %s, %s, 1",regname(f),regname(f));   /* d>=half → 负标志 */
            load_imm(g,3486784401LL);       /* g 复用：重装 3^20 */
            emitf("MUL  %s, %s, %s",regname(f),regname(f),regname(g));
            emitf("SUB  %s, %s, %s",regname(d),regname(d),regname(f));
            TDN=d0+1;                       /* 回收 f/g 槽，d 保留为结果 */
            return d;
        }
        return eval(n->e);
    case N_DOWHILE:
    case N_IF: {
        /* 表达式层的三目 cond ? a : b（parse_expr 产 N_IF）。
         * 语句层的 if/else 也走这里（gen_stmt 直接处理，不进 eval）。
         * 生成：cond 假 -> else；then；跳末尾；else：els。 */
        char*els=newlbl("tern_els"),*end=newlbl("tern_end");
        gen_brfalse(n->cond,els);
        /* 结果统一落 TPOOL[d0]：两边求值后显式搬运。
         * 注意 eval 可能返回常驻寄存器（不占临时池），此时 dstof 会
         * tacq 一个未初始化的临时——必须先 ADDI 到 TPOOL[d0] 再用。 */
        int d=TPOOL[d0];
        TDN=d0+1;                          /* 预留结果槽 */
        int a=eval(n->then);
        if(a!=d) emitf("ADDI %s, %s, 0",regname(d),regname(a));
        emitf("JAL zero, %s",end);
        lbl(els);
        int b=eval(n->els);
        if(b!=d) emitf("ADDI %s, %s, 0",regname(d),regname(b));
        lbl(end);
        TDN=d0+1;
        return d;
    }
    default:
        too_deep(2);
        return -1;
    }
}

/* 初始化列表：把 {…} 铺到 s0+off 开始的连续槽位。
   slot 是本层"已填充的标量槽"游标，用于 C 的扁平初始化语义。 */
static const char* innermost_elem(const char*ty){
    const char*t2=ty;
    while(is_array(t2)) t2=array_elem(t2);
    return t2;
}
static void gen_init_at(const char*ty,int off,Node*init,int*slot);

static void gen_init_list_node(const char*ty,int off,Node*init,int*slot){
    StructDef*sd=struct_find(ty);
    if(sd){                                  /* 结构体：按成员顺序填充 */
        int k=0;
        for(int i=0;i<sd->nm;i++){
            if(k>=init->nitems) break;
            Node*it=init->items[k++];
            int sub=0;
            if(it->k==N_INITLIST) gen_init_list_node(sd->m[i].ty, off+sd->m[i].off, it, &sub);
            else { int c=0; gen_init_at(sd->m[i].ty, off+sd->m[i].off, it, &c); }
        }
        return;
    }
    if(is_array(ty)){                        /* 数组：子列表归位，标量按扁平填充 */
        char*et=array_elem(ty); int es=ty_bytes(et);
        int count=array_count(ty);
        int idx=*slot;
        int all_scalar=1;
        for(int i=0;i<init->nitems;i++) if(init->items[i]->k==N_INITLIST) all_scalar=0;
        if(all_scalar){
            /* C 扁平初始化：连续标量按最内层元素大小铺满整个数组 */
            const char*el=innermost_elem(ty); int els=ty_bytes(el);
            for(int i=0;i<init->nitems;i++){
                int c=0; gen_init_at(el, off+(*slot)*els, init->items[i], &c); (*slot)++;
            }
            return;
        }
        for(int i=0;i<init->nitems;i++){
            Node*it=init->items[i];
            if(it->k==N_INITLIST){
                int sub=0;
                gen_init_list_node(et, off+idx*es, it, &sub);
                idx++;
            } else if(is_array(et)){
                int sub=0;
                gen_init_list_node(et, off+idx*es, it, &sub);
                idx++;
            } else {
                int c=0; gen_init_at(et, off+idx*es, it, &c); idx++;
            }
            if(idx>=count) break;
        }
        *slot=idx;
        return;
    }
    int c=0; gen_init_at(ty, off, init, &c);   /* 标量：单槽 */
}

static void gen_init_at(const char*ty,int off,Node*init,int*slot){
    if(init&&init->k==N_INITLIST){ gen_init_list_node(ty,off,init,slot); return; }
    if(is_array(ty)){
        const char*el=innermost_elem(ty); int es=ty_bytes(el);
        store_mem(eval(init), 8, off + (*slot)*es, el);
        (*slot)++;
        return;
    }
    TDN=0; int r=eval(init);
    store_mem(r,8,off,ty);
    TDN=0;
}

/* 生成"cond 为假就跳到 lbl"的最短序列。
 * 关系运算的取反不需要先算出 0/1 再取反——把取反直接融进分支条件：
 *   i <= n  =>  SLT t, n, i ; BNE t, zero, end   （n<i 则退出）
 * 比 "SLT t,i,n ; SLTIU t,t,1 ; BEQ t,zero,end" 少一条，循环每轮都省。 */
static void gen_brfalse(Node*c,const char*lbl){
    TDN=0;
    if(c&&c->k==N_BIN){
        const char*op=c->s;
        int rel = !strcmp(op,"<")||!strcmp(op,">")||!strcmp(op,"<=")||
                  !strcmp(op,">=")||!strcmp(op,"==")||!strcmp(op,"!=");
        if(rel){
            char*lt=expr_type(c->l),*rt=expr_type(c->r);
            int uns=ty_uns(lt)||ty_uns(rt);
            const char*SL=uns?"SLTU":"SLT";
            int a=eval(c->l);
            int b=eval(c->r);
            int d=in_tpool(a)? a : (in_tpool(b)? b : tacq());
            if(d!=TPOOL[0]){ emitf("ADDI %s, %s, 0",regname(TPOOL[0]),regname(d)); d=TPOOL[0]; }
            if(!strcmp(op,"<")){        emitf("%s %s, %s, %s",SL,regname(d),regname(a),regname(b)); emitf("BEQ %s, zero, %s",regname(d),lbl); }
            else if(!strcmp(op,">=")){  emitf("%s %s, %s, %s",SL,regname(d),regname(a),regname(b)); emitf("BNE %s, zero, %s",regname(d),lbl); }
            else if(!strcmp(op,">")){   emitf("%s %s, %s, %s",SL,regname(d),regname(b),regname(a)); emitf("BEQ %s, zero, %s",regname(d),lbl); }
            else if(!strcmp(op,"<=")){  emitf("%s %s, %s, %s",SL,regname(d),regname(b),regname(a)); emitf("BNE %s, zero, %s",regname(d),lbl); }
            else if(!strcmp(op,"==")){  emitf("SUB %s, %s, %s",regname(d),regname(a),regname(b));       emitf("BNE %s, zero, %s",regname(d),lbl); }
            else {                      emitf("SUB %s, %s, %s",regname(d),regname(a),regname(b));       emitf("BEQ %s, zero, %s",regname(d),lbl); }
            TDN=0;
            return;
        }
    }
    int v=eval(c);
    emitf("BEQ %s, zero, %s",regname(v),lbl);
    TDN=0;
}
/* gen_brtrue：条件为真跳 lbl（do-while 尾部判断用）。
 * 与 gen_brfalse 同构：关系运算直接取反分支，其余 SLTIU+BEQ 翻转。 */
static void gen_brtrue(Node*c,const char*lbl){
    TDN=0;
    if(c&&c->k==N_BIN){
        const char*op=c->s;
        int rel = !strcmp(op,"<")||!strcmp(op,">")||!strcmp(op,"<=")||
                  !strcmp(op,">=")||!strcmp(op,"==")||!strcmp(op,"!=");
        if(rel){
            char*lt=expr_type(c->l),*rt=expr_type(c->r);
            int uns=ty_uns(lt)||ty_uns(rt);
            const char*SL=uns?"SLTU":"SLT";
            int a=eval(c->l);
            int b=eval(c->r);
            int d=in_tpool(a)? a : (in_tpool(b)? b : tacq());
            if(d!=TPOOL[0]){ emitf("ADDI %s, %s, 0",regname(TPOOL[0]),regname(d)); d=TPOOL[0]; }
            if(!strcmp(op,"<")){        emitf("%s %s, %s, %s",SL,regname(d),regname(a),regname(b)); emitf("BNE %s, zero, %s",regname(d),lbl); }
            else if(!strcmp(op,">=")){  emitf("%s %s, %s, %s",SL,regname(d),regname(a),regname(b)); emitf("BEQ %s, zero, %s",regname(d),lbl); }
            else if(!strcmp(op,">")){   emitf("%s %s, %s, %s",SL,regname(d),regname(b),regname(a)); emitf("BNE %s, zero, %s",regname(d),lbl); }
            else if(!strcmp(op,"<=")){  emitf("%s %s, %s, %s",SL,regname(d),regname(b),regname(a)); emitf("BEQ %s, zero, %s",regname(d),lbl); }
            else if(!strcmp(op,"==")){  emitf("SUB %s, %s, %s",regname(d),regname(a),regname(b));       emitf("BEQ %s, zero, %s",regname(d),lbl); }
            else {                      emitf("SUB %s, %s, %s",regname(d),regname(a),regname(b));       emitf("BNE %s, zero, %s",regname(d),lbl); }
            TDN=0;
            return;
        }
    }
    int v=eval(c);
    emitf("BNE %s, zero, %s",regname(v),lbl);
    TDN=0;
}

static int ends_with_return(Node*n){
    if(!n) return 0;
    if(n->k==N_RETURN) return 1;
    if(n->k==N_BLOCK&&n->nitems>0) return ends_with_return(n->body[n->nitems-1]);
    return 0;
}

static void gen_stmt(Node*n){
    if(!n) return;
    switch(n->k){
    case N_BLOCK: for(int i=0;i<n->nitems;i++) gen_stmt(n->body[i]); break;
    case N_LABEL: lbl(n->s); break;
    case N_GOTO:  emitf("JAL zero, %s",n->s); break;
    case N_SWITCH: {
        /* switch 生成：cond 求值 → 逐 case BEQ 跳转；无匹配跳 default/end；
         * case 体顺序生成（fall-through）；break 落 end。 */
        int cv=eval(n->cond);
        TDN=0;
        char*end=newlbl("swend");
        GROW(LOOPEND,CAPLOOP,NLOOP,char*); LOOPEND[NLOOP]=end; NLOOP++;
        /* 建 case 值→标签 映射 */
        int ncase=0; char*deflbl=0;
        for(int i=0;i<n->nitems;i++){
            if(n->items[i]->k==N_CASE) ncase++;
            else if(n->items[i]->k==N_DEFAULT) deflbl=newlbl("swdef");
        }
        char**clbl=malloc(sizeof(char*)*(size_t)(ncase?ncase:1));
        { int k=0;
          for(int i=0;i<n->nitems;i++) if(n->items[i]->k==N_CASE)
              clbl[k++]=newlbl("case"); }
        /* 分发 */
        int k=0;
        for(int i=0;i<n->nitems;i++){
            if(n->items[i]->k==N_CASE){
                int t2=tacq();
                emitf("ADDI %s, zero, %d",regname(t2),(int)n->items[i]->num);
                emitf("SUB %s, %s, %s",regname(t2),regname(cv),regname(t2));
                emitf("BEQ %s, zero, %s",regname(t2),clbl[k]);
                k++;
            }
        }
        emitf("JAL zero, %s",deflbl?deflbl:end);
        /* 体：N_CASE 输出对应标签，N_DEFAULT 输出 default 标签 */
        k=0;
        for(int i=0;i<n->nitems;i++){
            Node*it=n->items[i];
            if(it->k==N_CASE){ lbl(clbl[k]); k++; }
            else if(it->k==N_DEFAULT){ lbl(deflbl); }
            else gen_stmt(it);
        }
        lbl(end);
        NLOOP--;
        break;
    }
    case N_DECL:
        for(int i=0;i<n->ndecls;i++){
            if(!n->dinit[i]) continue;
            LocVar*v=findloc(n->dnames[i]); if(!v) continue;
            if(n->dinit[i]->k==N_INITLIST){
                if(v->spilled){ int slot=0; gen_init_at(v->ty, v->off, n->dinit[i], &slot); }
                else {                                   /* 标量 {x}：取第一个元素 */
                    Node*first=n->dinit[i];
                    while(first&&first->k==N_INITLIST&&first->nitems>0) first=first->items[0];
                    TDN=0; int r=eval(first);
                    if(v->reg!=r) emitf("ADDI %s, %s, 0",regname(v->reg),regname(r));
                    TDN=0;
                }
                continue;
            }
            if(!v->spilled&&n->dinit[i]->k==N_NUM){
                load_imm128(v->reg,n->dinit[i]->numw); TDN=0; continue;
            }
            TDN=0; int r=eval(n->dinit[i]);
            if(v->spilled){
                if(struct_find(v->ty)){          /* struct 初始化 = 从源地址整块拷入 */
                    int dst=tacq();
                    emitf("ADDI %s, s0, %d",regname(dst),v->off);
                    copy_words(dst,r,ty_bytes(v->ty));
                } else store_mem(r,8,v->off,v->ty);
            }
            else if(v->reg!=r) emitf("ADDI %s, %s, 0",regname(v->reg),regname(r));
            TDN=0;
        }
        break;
    case N_EXPR: TDN=0;
        for(Node*s=n->expr; s; s=s->els){ TDN=0; eval(s); }
        TDN=0; break;
    case N_IF: {
        if(!n->els){ char*end=newlbl("endif");
            gen_brfalse(n->cond,end); gen_stmt(n->then); lbl(end);
        } else { char*els=newlbl("else"),*end=newlbl("endif");
            gen_brfalse(n->cond,els);
            gen_stmt(n->then); emitf("JAL zero, %s",end);
            lbl(els); gen_stmt(n->els); lbl(end);
        }
        break;
    }
    case N_WHILE: {
        char*start=newlbl("while"),*end=newlbl("end");
        lbl(start); gen_brfalse(n->cond,end);
        GROW(LOOPEND,CAPLOOP,NLOOP,char*); GROW(LOOPCONT,CAPLOOPC,NLOOP,char*);
        LOOPEND[NLOOP]=end; LOOPCONT[NLOOP]=start; NLOOP++;
        gen_stmt(n->stmt); NLOOP--;
        emitf("JAL zero, %s",start); lbl(end); break;
    }
    case N_DOWHILE: {
        /* do body while(cond); —— 体先执行，条件放尾部判断。
         * continue 跳到条件判断处（cont 标签）。 */
        char*start=newlbl("do"),*cont=newlbl("cont"),*end=newlbl("end");
        lbl(start);
        GROW(LOOPEND,CAPLOOP,NLOOP,char*); GROW(LOOPCONT,CAPLOOPC,NLOOP,char*);
        LOOPEND[NLOOP]=end; LOOPCONT[NLOOP]=cont; NLOOP++;
        gen_stmt(n->stmt); NLOOP--;
        lbl(cont);
        gen_brtrue(n->cond,start);
        lbl(end); break;
    }
    case N_FOR: {
        TDN=0;
        if(n->init){
            if(n->init->k==N_DECL) gen_stmt(n->init);
            else for(Node*s=n->init; s; s=s->els){ TDN=0; eval(s); }
        }
        TDN=0;
        char*start=newlbl("for"),*cont=newlbl("cont"),*end=newlbl("end");
        lbl(start);
        if(n->cond) gen_brfalse(n->cond,end);
        GROW(LOOPEND,CAPLOOP,NLOOP,char*); GROW(LOOPCONT,CAPLOOPC,NLOOP,char*);
        LOOPEND[NLOOP]=end; LOOPCONT[NLOOP]=cont; NLOOP++;
        gen_stmt(n->stmt); lbl(cont);
        if(n->step){
            /* 逗号 step 链：沿 els 逐个求值（值丢弃） */
            for(Node*s=n->step; s; s=s->els){ TDN=0; eval(s); TDN=0; }
        }
        NLOOP--;
        emitf("JAL zero, %s",start); lbl(end); break;
    }
    case N_RETURN:
        TDN=0;
        if(FUNC_SRET && n->expr){
            /* 返回 struct：把值拷进调用方给的缓冲，再把缓冲地址带回去。
             * struct 类型表达式求值 = 首地址，直接 addr_of 即可覆盖变量/成员/调用三种形态。 */
            int src=addr_of(n->expr);
            int dst=tacq();
            chk_off(LOC[FUNC_SRET_LOC].off);
            emitf("LW %s, %d(s0)",regname(dst),LOC[FUNC_SRET_LOC].off);
            copy_words(dst,src,FUNC_RET_BYTES);
            emitf("ADDI a0, %s, 0",regname(dst));
            TDN=0;
        } else if(n->expr&&n->expr->k==N_CALL
                  && strcmp(n->expr->s,"__builtin_tri_lit")!=0){    /* return f(...)：结果直接留在 a0。
                       __builtin_tri_lit 结果在临时寄存器（gen 期直发，非 a0 ABI），
                       走下方通用拷贝路径——结构性误判实证：return 直返 0。 */
            A0_DIRECT=1; eval(n->expr); A0_DIRECT=0;
        } else if(n->expr){ int r=eval(n->expr); if(r!=10) emitf("ADDI a0, %s, 0",regname(r)); }
        else emitf("ADDI a0, zero, 0");
        TDN=0;
        if(FUNC_EPILOGUE) emitf("JAL zero, %s",FUNC_EPILOGUE);
        else emitf("JALR zero, 0(ra)");
        break;
    case N_BREAK: if(NLOOP>0) emitf("JAL zero, %s",LOOPEND[NLOOP-1]); break;
    case N_CONT:  if(NLOOP>0) emitf("JAL zero, %s",LOOPCONT[NLOOP-1]); break;
    default: break;
    }
}

static void collect_decls(Node*n){
    if(!n) return;
    switch(n->k){
    case N_DECL:
        for(int i=0;i<n->ndecls;i++){
            if(findloc(n->dnames[i])) continue;
            LocVar*v=loc_new(); v->name=n->dnames[i]; v->ty=n->dtypes[i];
            int isarr; ty_size(v->ty,&isarr);
            /* 1-tryte 整数必须落栈：赋值要按 tryte 截断（SB mod 243），读取要按符号性
             * 扩展（LB/LBU）。放在寄存器里就没有这个归一化点，signed char c=200
             * 会原样返回 200 而不是 -43。 */
            v->spilled = isarr || struct_find(v->ty)!=NULL || !strcmp(v->ty,"va_list")
                       || is_char_ty(v->ty);
        }
        /* 初始化式里可能取址/调用，需要继续扫描（&x 必须把 x 赶到栈上） */
        for(int i=0;i<n->ndecls;i++) collect_decls(n->dinit[i]);
        break;
    case N_BLOCK: for(int i=0;i<n->nitems;i++) collect_decls(n->body[i]); break;
    case N_IF: collect_decls(n->cond);collect_decls(n->then);collect_decls(n->els); break;
    case N_WHILE: collect_decls(n->cond);collect_decls(n->stmt); break;
    case N_DOWHILE: collect_decls(n->cond);collect_decls(n->stmt); break;
    case N_FOR: collect_decls(n->init);collect_decls(n->cond);collect_decls(n->step);collect_decls(n->stmt); break;
    case N_EXPR: collect_decls(n->expr); break;
    case N_RETURN: collect_decls(n->expr); break;
    /* 取址的局部变量必须落栈：寄存器里的变量没有地址 */
    case N_ADDR:
        if(n->e&&n->e->k==N_VAR){ LocVar*v=findloc(n->e->s); if(v) v->spilled=1; }
        collect_decls(n->e);
        break;
    case N_ASSIGN: collect_decls(n->l); collect_decls(n->r); break;
    case N_BIN: collect_decls(n->l); collect_decls(n->r); break;
    case N_UN: collect_decls(n->e); break;
    case N_PREINC: collect_decls(n->e); break;
    case N_SWITCH: collect_decls(n->cond); for(int i=0;i<n->nitems;i++) collect_decls(n->items[i]); break;
    case N_POSTINC: collect_decls(n->e); break;
    case N_DEREF: collect_decls(n->e); break;
    case N_CAST: collect_decls(n->e); break;   /* *(int*)&x：取址藏在 cast 里，漏扫则 x 留寄存器 → 取址 UNSUPPORTED */
    case N_INDEX: collect_decls(n->l); collect_decls(n->r); break;
    case N_MEMBER: collect_decls(n->l); break;
    case N_CALL: for(int i=0;i<n->nargs;i++) collect_decls(n->args[i]); break;
    case N_VASTART: case N_VAARG: case N_VAEND: collect_decls(n->e); break;
    default: break;
    }
}

/* 判断表达式/语句里是否含"真实函数调用"（非 syscall）。syscall 用 ECALL 不改 ra。 */
static int is_syscall_name(const char*s){
    static const char*sys[]={"write","read","read8","write8","exit","brk","getcwd","openat","close",
        "lseek","readv","writev","newfstatat","fstat","exit_group","set_tid_address",
        "futex","clock_gettime","sched_yield","rt_sigaction","rt_sigprocmask",
        "rt_sigreturn","uname","getpid","gettid","munmap","mmap","mprotect","getrandom",NULL};
    for(int i=0;sys[i];i++) if(!strcmp(s,sys[i])) return 1;
    return 0;
}
static int max_args(Node*n){
    if(!n) return 0;
    int m=0;
    if(n->k==N_CALL&&n->nargs>m) m=n->nargs;
    Node*all[]={n->l,n->r,n->e,n->cond,n->then,n->els,n->init,n->step,n->stmt,n->expr};
    for(int i=0;i<10;i++){ int v=max_args(all[i]); if(v>m) m=v; }
    if(n->body) for(int i=0;i<n->nitems;i++){ int v=max_args(n->body[i]); if(v>m) m=v; }
    if(n->args) for(int i=0;i<n->nargs;i++){ int v=max_args(n->args[i]); if(v>m) m=v; }
    if(n->items) for(int i=0;i<n->nitems;i++){ int v=max_args(n->items[i]); if(v>m) m=v; }
    if(n->dinit) for(int i=0;i<n->ndecls;i++){ int v=max_args(n->dinit[i]); if(v>m) m=v; }
    return m;
}
static int has_any_call(Node*n){
    if(!n) return 0;
    if(n->k==N_CALL) return 1;                 /* 含 syscall：ECALL 同样吃 a0-a7 */
    Node*all[]={n->l,n->r,n->e,n->cond,n->then,n->els,n->init,n->step,n->stmt,n->expr};
    for(int i=0;i<10;i++) if(has_any_call(all[i])) return 1;
    if(n->body) for(int i=0;i<n->nitems;i++) if(has_any_call(n->body[i])) return 1;
    if(n->args) for(int i=0;i<n->nargs;i++) if(has_any_call(n->args[i])) return 1;
    if(n->items) for(int i=0;i<n->nitems;i++) if(has_any_call(n->items[i])) return 1;
    if(n->dinit) for(int i=0;i<n->ndecls;i++) if(has_any_call(n->dinit[i])) return 1;
    return 0;
}
static int has_call(Node*n){
    if(!n) return 0;
    if(n->k==N_CALL) return !is_syscall_name(n->s);
    Node*all[]={n->l,n->r,n->e,n->cond,n->then,n->els,n->init,n->step,n->stmt,n->expr};
    for(int i=0;i<10;i++) if(has_call(all[i])) return 1;
    if(n->body) for(int i=0;i<n->nitems;i++) if(has_call(n->body[i])) return 1;
    if(n->args) for(int i=0;i<n->nargs;i++) if(has_call(n->args[i])) return 1;
    if(n->items) for(int i=0;i<n->nitems;i++) if(has_call(n->items[i])) return 1;
    if(n->dinit) for(int i=0;i<n->ndecls;i++) if(has_call(n->dinit[i])) return 1;
    return 0;
}
static void gen_func(Node*f){
    NLOC=0; memset(USEDREG,0,sizeof(USEDREG)); NLOOP=0; CURFN=f->s; NFIXED=f->nparams;
    FUNC_EPILOGUE=newlbl("epi");
    int anycall=has_any_call(f->fbody);
    int hascall=has_call(f->fbody);
    /* sret：返回 struct 的函数多一个隐藏参数 a0 = 调用方给的返回缓冲地址。
     * 参数寄存器的编号整体后移一位（a1..a7），与 Python 后端一致。 */
    FUNC_SRET = struct_find(f->rettype)!=NULL;
    FUNC_RET_BYTES = FUNC_SRET ? ty_bytes(f->rettype) : 0;
    int abase = FUNC_SRET?1:0;
    pp_set(!anycall&&!f->variadic, f->nparams);   /* 必须在寄存器分配之前定池 */
    for(int i=0;i<f->nparams;i++){
        LocVar*v=loc_new(); v->name=f->params[i]; v->ty=f->ptypes[i];
        int isarr; ty_size(v->ty,&isarr);
        /* 形参同理：char 类形参必须在入口按 tryte 归一化，故落栈后用 LB/LBU 读回。
         * struct 形参按值传：入口从 aN（= 源地址）逐字拷进本地副本，之后它就是
         * 一个普通结构体局部变量 —— 修改与调用方完全隔离。 */
        v->spilled = isarr || is_char_ty(v->ty) || struct_find(v->ty)!=NULL
                     || !strcmp(v->ty,"__tint2") || !strcmp(v->ty,"__tbool");   /* 存取即窄化：必须落槽（同 char） */
        /* 叶子函数没有调用会踩 a0-a7，形参可以直接留在它到达时的寄存器里，
           省掉 `ADDI sN, aM, 0` 这次搬运。池从 a_nparams 起，不会与它们冲突。 */
        if(!anycall&&!isarr&&!is_char_ty(v->ty)&&!struct_find(v->ty)&&i<8-abase){
            v->reg=10+abase+i; reg_take(v->reg);
        }
    }
    if(f->variadic){ LocVar*vb=loc_new(); vb->name="__argbase"; vb->ty="int"; vb->spilled=1; }
    FUNC_SRET_LOC=-1;
    if(FUNC_SRET){ LocVar*vb=loc_new(); vb->name="__sretptr"; vb->ty="int*"; vb->spilled=1;
                   FUNC_SRET_LOC=NLOC-1; }   /* 必须排在形参之后：下面按 LOC[i] 索引形参 */
    collect_decls(f->fbody);
    /* __tint2 局部强制落槽（2026-11-23 C2 方案 A）：存取即窄化语义只在
       SW4/LW4 路径上，寄存器驻留值会绕过截断/符号扩展（x=4000000000
       寄存器驻留直比 513215599 失败实证）——与 char/struct 同款处理 */
    for(int i=0;i<NLOC;i++)
      if(!LOC[i].spilled && (!strcmp(LOC[i].ty,"__tint2") || !strcmp(LOC[i].ty,"__tbool")))
        LOC[i].spilled=1;   /* 存取即窄化口径：tint2/tbool 必经 LW4|LB / SW4|SB */
    for(int i=0;i<NLOC;i++){
        if(LOC[i].spilled){
            if(LOC[i].reg>0){ USEDREG[LOC[i].reg]=0; LOC[i].reg=0; }   /* 取址后已落栈，归还寄存器 */
            continue;
        }
        if(LOC[i].reg>0) continue;                                    /* 形参已就地占用 a 寄存器 */
        int r=reg_alloc_persistent();
        if(r<0) LOC[i].spilled=1; else LOC[i].reg=r;
    }
    int spill=0; for(int i=0;i<NLOC;i++) if(LOC[i].spilled){ LOC[i].off=spill; spill+=ty_size(LOC[i].ty,NULL); }
    /* 位运算/移位不再占任何 callee-saved 工作寄存器（2026-11-22 本征直发，
       方案 A 的 s7-s11 + s2-s6/s1 抢占机制退役）——工作寄存器需求归零。 */
    /* 大常量预装到空闲的 callee-saved 寄存器（仅叶子函数：那里 s 系是空的） */
    CC_N=0;
    collect_fn_consts(f->fbody);
    if(!PP_SAVED&&CC_N>0){
        static int scout[6]={9,22,23,24,25,26};
        int got=0;
        for(int i=0;i<6&&got<CC_N;i++)
            if(!USEDREG[scout[i]]){ USEDREG[scout[i]]=1; CC_R[got++]=scout[i]; }
        CC_N=got;                       /* 寄存器不够就少缓存几个 */
    } else CC_N=0;
    /* 收集实际用到的、需要保存的寄存器（caller-saved 池无需保存） */
    int sregs[16],ns=0;
    if(PP_SAVED){
        for(int k=0;k<PP_N;k++) if(USEDREG[PP_POOL[k]]) sregs[ns++]=PP_POOL[k];
    } else {
        for(int k=0;k<CC_N;k++) sregs[ns++]=CC_R[k];   /* 常量缓存用的 s 寄存器同样要存 */
    }
    int need_frame = hascall || spill>0 || ns>0;
    if(getenv("TRV_DUMP_CC")) fprintf(stderr,"[cc] %s: CC_N=%d ns=%d spill=%d hascall=%d need_frame=%d\n",f->s,CC_N,ns,spill,hascall,need_frame);
    /* 帧布局: s0 = 原sp+18; 原sp[0]=old fp, [9]=ra; saved s 寄存器从 s0+0 起;
     * spill 在 saved 之后。sp 抬升量须覆盖到 spill 顶部。 */
    int savbase=0;
    int spillbase=ns*9;
    /* 跨调用区：7 个临时保存槽（按深度索引）+ 8 个实参暂存槽 */
    CSAVE_OFF = spillbase + spill;
    ARG_OFF   = CSAVE_OFF + 6*9;
    int narg_slots = 8; { int ma=max_args(f->fbody); if(ma>narg_slots) narg_slots=ma+2; }
    /* sret 缓冲跟在实参暂存区之后：每个返回 struct 的调用点各占一块。 */
    /* sret_slot_walk 无条件走：除调用返回缓冲外，复合字面量
     * (TYPE){...} 的匿名临时缓冲也在此分配（与调用无关）。 */
    int sret_bytes=0; sret_slot_walk(f->fbody,&sret_bytes);
    /* 无调用的叶子函数：实参暂存区/跨调用区不存在，sret/compound
     * 缓冲直接从 CSAVE_OFF 起（不必留 ARG 区） */
    SRET_BASE = hascall ? (ARG_OFF + narg_slots*9) : CSAVE_OFF;
    int call_bytes = (hascall ? (6*9 + narg_slots*9) : 0) + sret_bytes;
    int framesize=18+ns*9+spill+call_bytes;   /* sp 抬到帧顶 */
    if(need_frame){
        emitf("SW s0, 0(sp)");
        if(hascall) emitf("SW ra, 9(sp)");    /* 叶子函数不调别人，ra 不会被覆盖 */
        emitf("ADDI s0, sp, 18");
        for(int k=0;k<ns;k++) emitf("SW %s, %d(s0)",regname(sregs[k]),savbase+k*9);
        if(framesize>0){
            if(framesize<=1093) emitf("ADDI sp, sp, %d",framesize);   /* 小帧一步到位 */
            else { load_imm(SCRATCH,framesize); emitf("ADD sp, sp, %s",regname(SCRATCH)); }
        }
    }
    if(getenv("TRV_DUMP_LOC")) for(int i=0;i<NLOC;i++)
        fprintf(stderr,"[loc] %-8s ty=%-10s sp=%d off=%d reg=%d\n",LOC[i].name,LOC[i].ty,LOC[i].spilled,LOC[i].off,LOC[i].reg);
    /* spill 偏移改为相对 s0 */
    if(need_frame) for(int i=0;i<NLOC;i++) if(LOC[i].spilled) LOC[i].off += spillbase;
    /* 变参函数：参数块基址在 t6，存入隐藏槽供 va_start/va_arg 使用 */
    if(f->variadic){ LocVar*vb=findloc("__argbase");
        if(vb){ chk_off(vb->off); emitf("SW t6, %d(s0)",vb->off); } }
    /* sret：a0 是调用方给的返回缓冲地址。后面的参数拷贝只用临时寄存器，不碰 a0。 */
    if(FUNC_SRET){ chk_off(LOC[FUNC_SRET_LOC].off); emitf("SW a0, %d(s0)",LOC[FUNC_SRET_LOC].off); }
    TDN=0;
    /* 参数寄存器窗口：sret 占掉 a0，只剩 a1..a7。多出来的从参数块 t6 + i*9 取
     * （块内索引不随 sret 平移，与调用方的存放位置一一对应）。 */
    for(int i=8-abase;i<f->nparams;i++){
        LocVar*v=&LOC[i]; chk_off(i*9);
        int r=tacq(); emitf("LW %s, %d(t6)",regname(r),i*9);
        if(struct_find(v->ty)){                   /* 块里放的是源地址：拷进本地副本 */
            int dst=tacq(); chk_off(v->off);
            emitf("ADDI %s, s0, %d",regname(dst),v->off);
            copy_words(dst,r,ty_bytes(v->ty));
        }
        else if(v->spilled) store_mem(r,8,v->off,v->ty);
        else emitf("ADDI %s, %s, 0",regname(v->reg),regname(r));
        TDN=0;
    }
    for(int i=0;i<CC_N;i++) load_imm128(CC_R[i],(__int128)CC_V[i]);   /* 大常量只装一次 */
    /* 参数装载（在帧建立后） */
    TDN=0;
    for(int i=0;i<f->nparams&&i<8-abase;i++){
        LocVar*v=&LOC[i];
        if(struct_find(v->ty)){
            /* 按值传参：aN 里是源地址，逐字拷进本地副本后就是普通结构体局部变量，
             * 之后的成员读写与取址全都走既有路径，调用方完全看不到这里的变化。 */
            int dst=tacq(), src=tacq(); chk_off(v->off);
            emitf("ADDI %s, s0, %d",regname(dst),v->off);
            emitf("ADDI %s, a%d, 0",regname(src),i+abase);
            copy_words(dst,src,ty_bytes(v->ty));
            TDN=0;
        }
        else if(v->spilled){
            /* 形参落栈时也按类型写：char 形参用 SB 把入参截到 1 tryte，
             * 后续 LB/LBU 读回时符号性才与 C 一致；__tint2 形参同理——
             * SW4 把入参截/符号化到 20-trit（实参寄存器传的是全宽词，
             * 不截则 callee 读到未窄化值）。 */
            if(is_char_ty(v->ty) || !strcmp(v->ty,"__tint2") || !strcmp(v->ty,"__tbool")){
                int r=tacq(); emitf("ADDI %s, a%d, 0",regname(r),i+abase);
                store_mem(r,8,v->off,v->ty); TDN=0;
            } else { chk_off(v->off); emitf("SW a%d, %d(s0)",i+abase,v->off); }
        }
        else if(v->reg!=10+abase+i) emitf("ADDI %s, a%d, 0",regname(v->reg),i+abase);
    }
    TDN=0;
    gen_stmt(f->fbody);
    if(!ends_with_return(f->fbody)) emitf("ADDI a0, zero, 0");   /* 仅兜底路径需要 */
    lbl(FUNC_EPILOGUE);
    if(need_frame){
        for(int k=0;k<ns;k++) emitf("LW %s, %d(s0)",regname(sregs[k]),savbase+k*9);
        if(hascall) emitf("LW ra, -9(s0)");
        emitf("ADDI sp, s0, -18");
        emitf("LW s0, 0(sp)");
    }
    emitf("JALR zero, 0(ra)");
}

/* ===================== 汇编器：文本 -> 20-trit 机器码 ===================== */
typedef struct { char*name; int idx; } Label;
static Label *LABS; static int NLAB, CAPLAB;
static void add_label(const char*n,int idx){
    GROW(LABS,CAPLAB,NLAB,Label);
    LABS[NLAB].name=strdup(n); LABS[NLAB].idx=idx; NLAB++;
}
/* 弱别名表（2026-11-22 A6 最小弱符号）：ALIAS a → b（#pragma weak a = b）。
 * 语义=名称映射：引用 a 在汇编期解析到 b 的地址；若合并符号空间另有 a 的
 * 强定义（如平台自带 strchr），add_label 先到先得于 a 本名 → 强定义自然
 * 覆盖别名（后者仅在无强定义时由引用按别名命中）。musl weak_alias 形态。
 * 全局符号/弱全局变量不支持（记账）。 */
static char**ALIAS_NAMES; static char**ALIAS_REAL;
static int NALIAS, CAPALIAS_N, CAPALIAS_R;   /* 双容量独立（共享 cap 的 GROW 对会漏分配——实证） */
static void alias_add(const char*a,const char*b){
    GROW(ALIAS_NAMES,CAPALIAS_N,NALIAS,sizeof(char*));
    GROW(ALIAS_REAL,CAPALIAS_R,NALIAS,sizeof(char*));
    ALIAS_NAMES[NALIAS]=strdup(a); ALIAS_REAL[NALIAS]=strdup(b); NALIAS++;
}
static const char*alias_follow(const char*n){
    for(int i=0;i<NALIAS;i++) if(!strcmp(ALIAS_NAMES[i],n)) return ALIAS_REAL[i];
    return NULL;
}
static int find_label(const char*n){
    for(int i=0;i<NLAB;i++) if(!strcmp(LABS[i].name,n)) return LABS[i].idx;
    const char*r=alias_follow(n);                       /* 弱别名链式跟随 */
    if(r){ for(int i=0;i<NLAB;i++) if(!strcmp(LABS[i].name,r)) return LABS[i].idx; }
    return -1;
}
static int find_label_exact(const char*n){ for(int i=0;i<NLAB;i++) if(!strcmp(LABS[i].name,n)) return LABS[i].idx; return -1; }

static int need_reg(void){ return 0; }

/* 解析 "LUI t0, 9" / "ADDI a0, t1, 3" / "LW t0, 0(sp)" / "SW t2, 4(sp)" / "JAL ra, main" / "BEQ t0, zero, _L1" */
static uint32_t asm_line(const char*line,int idx){
    /* 去前导空格 */
    while(*line==' '||*line=='\t') line++;
    char op[32],r1[32],r2[32],r3[32];
    char b1[64],b2[64],b3[64];
    memset(b1,0,sizeof(b1));memset(b2,0,sizeof(b2));memset(b3,0,sizeof(b3));
    int nf=sscanf(line,"%31s",op); if(nf<1) return 0;
    /* 符号地址预处理（2026-11-22）：&name → 标签地址（idx*4 tryte），任意指令
       操作数通用。动机：函数指针取址若烘焙 gen 期常量，peephole 删指令后陈旧
       （qsort case8 cmp_char 44 vs 40 实证）；符号化在汇编期一次绑定。
       仅支持单 ADDI 装载（地址 < 3^13）；跨单元大地址分段符号化留链接器专项。 */
    char fixed[512]; fixed[0]=0;
    {
        const char*amp=strchr(line,'&');
        if(amp){
            char sym[128]; snprintf(sym,sizeof(sym),"%s",amp+1);
            char*end=sym; while(*end&&!isspace((unsigned char)*end)&&*end!=',')end++; *end=0;
            int la=find_label(sym);
            if(la<0){ fprintf(stderr,"[asm] unknown symbol &%s (line: %s)\n",sym,line); return 0; }
            char repl[64]; snprintf(repl,sizeof(repl),"%d",la*4);
            size_t off=(size_t)(amp-line);
            snprintf(fixed,sizeof(fixed),"%.*s%s%s",(int)off,line,repl,amp+1+strlen(sym));
            line=fixed;                          /* 后续切分用替换后的行 */
        }
    }
    /* 用逗号切分操作数（注意 0(sp) 含括号无逗号，安全） */
    snprintf(CURASM,sizeof(CURASM),"%s",line);
    const char*rest=line+strlen(op); while(*rest==' ')rest++;
    char*ops[3]={b1,b2,b3}; int no=0; const char*p=rest;
    while(*p&&no<3){ while(*p==' ')p++; const char*q=p; while(*q&&*q!=',')q++;
        int L=q-p; memcpy(ops[no],p,L); ops[no][L]=0; no++; if(*q==','){p=q+1;}else break; }
    for(int i=0;i<no;i++){ char*t=ops[i]; while(*t==' ')t++; memmove(ops[i],t,strlen(t)+1); }

    InstDef*d=find_inst(op);
    if(!d){ fprintf(stderr,"[asm] unknown mnemonic: %s (line: %s)\n",op,line); return 0; }
    /* 无操作数系统指令 */
    if(!strcmp(op,"ECALL")) return encI(O_SYSTEM,0,0,0,0);
    if(!strcmp(op,"EBREAK")) return encI(O_SYSTEM,0,0,1,0);

    if(d->fmt==0){ /* R: op rd, rs1, rs2 */
        /* TNOT 特判：{11,funct=0,funct2=2} → encR(op, f2=2, rs2=0, rs1, funct=0, rd)。
           与 tri-isa.h TRI_ENC_R(TNOT_OP, TNOT_FUNCT2=2, 0, a, TNOT_FUNCT=0, d)
           逐参对齐——funct 走 2-trit 槽（trits7-8）、funct2 走 3-trit 槽
           （trits17-19），勿按直觉换位（换位即编成 {11,2,0}=TSEXT，~ 全废）。 */
        if(!strcmp(op,"TNOT")) return encR(O_TLOGIC,2,0,find_reg(ops[1]),0,find_reg(ops[0]));
        int rd=find_reg(ops[0]),rs1=find_reg(ops[1]),rs2=find_reg(ops[2]);
        return encR(d->op,d->f2,rs2,rs1,d->fu,rd);
    }
    if(d->fmt==1){ /* I: op rd, rs1, imm  or  LW rd, imm(rs1) */
        int rd=find_reg(ops[0]);
        /* 坑实录：装载助记符硬编码清单——LW4 漏名即 putf(-1) 崩（同 TNOT 缺
           ITAB 形；新指令要点两处名：ITAB + 本清单） */
        if(!strcmp(op,"LW")||!strcmp(op,"LH")||!strcmp(op,"LB")||!strcmp(op,"LBU")||!strcmp(op,"LHU")||!strcmp(op,"LW4")||!strcmp(op,"JALR")){
            /* imm(rs1) */
            char*par=strchr(ops[1],'('); long imm=0; int rs1=0;
            if(par){ *par=0; imm=atol(ops[1]); char rr[32]; snprintf(rr,sizeof(rr),"%s",par+1);
                     char*rp=strchr(rr,')'); if(rp)*rp=0; rs1=find_reg(rr); *par='('; }
            return encI(d->op,(int)imm,rs1,d->fu,rd);
        }
        long imm=atol(ops[2]);
        return encI(d->op,(int)imm,find_reg(ops[1]),d->fu,rd);
    }
    if(d->fmt==2){ /* S/B */
        if(!strcmp(op,"SW")||!strcmp(op,"SH")||!strcmp(op,"SB")||!strcmp(op,"SW4")){
            int rs2=find_reg(ops[0]);
            char*par=strchr(ops[1],'('); long imm=0; int rs1=0;
            if(par){ *par=0; imm=atol(ops[1]); char rr[32]; snprintf(rr,sizeof(rr),"%s",par+1);
                     char*rp=strchr(rr,')'); if(rp)*rp=0; rs1=find_reg(rr); *par='('; }
            return encS(d->op,(int)imm,rs2,rs1,d->fu);
        }
        /* branch: op rs1, rs2, label */
        int rs1=find_reg(ops[0]),rs2=find_reg(ops[1]);
        int lt=find_label(ops[2]); int imm=(lt>=0)?(lt-idx):atoi(ops[2]);
        return encS(d->op,imm,rs2,rs1,d->fu);
    }
    if(d->fmt==3){ /* U/J */
        int rd=find_reg(ops[0]);
        if(!strcmp(op,"JAL")){
            int lt=find_label(ops[1]);
            if(getenv("TRV_LABEL_DEBUG") && !strcmp(ops[1],"ecvt"))
              fprintf(stderr,"[ecvt-call] idx=%d lt=%d\n",idx,lt);
            int imm=(lt>=0)?(lt-idx):atoi(ops[1]);
            if(lt<0 && getenv("TRV_ASM_DEBUG"))
              fprintf(stderr,"[asm-debug] idx=%d JAL %s 未命中（imm 落 atoi=%d）；LABS=%d 项\n",idx,ops[1],imm,NLAB);
            return encU(O_JAL,imm,rd);
        }
        return encU(d->op,atoi(ops[1]),rd);
    }
    if(d->fmt==5){ /* TC 压缩（T10d）：C-ADDI reg_s, imm7 等五类
        * reg_s = 静态子集索引 0-7（映射表 _C_REGS），imm7 = 7-trit 立即数
        * 编码：t0-t2=23, t3-t4=subformat, t5-t7=reg_s, t8-t14=imm7 */
        int reg_s = atoi(ops[0]);
        int imm = imm7ok(atoi(ops[1]));
        uint32_t v = 0;
        v = putf(v, 0, 2, O_TC);          /* opcode 23 */
        v = putf(v, 3, 4, d->fu);         /* subformat */
        v = putf(v, 5, 7, reg_s);
        v = putf(v, 8, 14, imm);
        return v;
    }
    return 0;
}

/* ============ TRVO 中间格式导入 ============
 * -l xxx.trvo：读回先前导出的目标文件。函数汇编体进独立缓冲 TVO_OUT
 * （两遍 gen 会清 OUT，TVO_OUT 在 assemble_all 之前统一 append），
 * 函数登记 FNS（fbody=NULL 标记"体已在 TVO_OUT"，gen_func 跳过），
 * 标签计数续接（LBLBASE），全局/字符串地址由既有分配器统一排布。 */
static char **TVO_OUT; static int NTVO, CAPTVO;
static int nstr_imp_records;   /* 导入 STR 计数：改名映射 __str_k → __strimp_N */
static int TVO_SEQ;                        /* 导入单元序号：标签改写 _L<n> -> _X<seq>_<n> */
/* 把行内所有 _L<数字> token 改写为 _X<seq>_<数字>（定义与引用同形，全改）。
 * 标签出现在两种位置：行首 "LABEL:" 与跳转目标操作数。数字边界用
 * 非字母数字判定，_L1 与 _L12 不会互混。 */
static char*tvo_relabel(const char*line){
    size_t cap=strlen(line)*2+32;
    char*out=malloc(cap); size_t w=0;
    for(size_t i=0;line[i];){
        if(line[i]=='_'&&line[i+1]=='L'&&isdigit((unsigned char)line[i+2])){
            size_t j=i+2;
            while(isdigit((unsigned char)line[j])) j++;
            /* 前一个字符不能是标识符字符（避免 xy_L1 误改） */
            int prev_id=(i>0)&&(isalnum((unsigned char)line[i-1])||line[i-1]=='_');
            if(!prev_id){
                int orign=atoi(line+i+2);        /* 原编号，保留进新名 */
                w+=(size_t)snprintf(out+w,cap-w,"_X%d_%d",TVO_SEQ,orign);
                i=j; continue;
            }
        }
        out[w++]=line[i++];
    }
    out[w]=0;
    return out;
}
static void tvo_import(const char*path){
    FILE*fp=fopen(path,"r"); if(!fp){perror(path);exit(1);}
    char line[1024];
    if(!fgets(line,sizeof line,fp)||strncmp(line,"TRVO",4)!=0){
        fprintf(stderr,"link error: %s is not TRVO\n",path); exit(1);
    }
    int lblbase=0;
    /* LBLBASE 死码清理（2026-11-22 A5b）：主流程 LBL=0 重置令导入端续接失效
     * （防冲突实际靠 tvo_relabel 的 _X<seq>_ 改写）。格式字段保留兼容旧文件，
     * 导入端不再累加。 */
    /* 第一遍：先扫 FUNC 边界数，给 OUT 行预留单元前缀改写需要原行 */
    while(fgets(line,sizeof line,fp)){
        if(!strncmp(line,"GLOBW ",6)){
            /* 弱全局（2026-11-22 A6 顺延）：主单元已有强初始化（AST init 非空）
               → 弱定义与 hex 数据整体让位（不注册回填，防覆盖强值）；
               否则同 GLOB（弱默认生效）。 */
            char name[128],ty[128],hex[1024]={0};
            if(sscanf(line,"GLOBW %127s %127s %1023s",name,ty,hex)>=2){
                Global*exg=global_find(name);
                if(!(exg && exg->init)){
                    Global*g=global_add(name,ty,ty_bytes(ty),NULL);
                    if(hex[0]){
                        GROW(TVO_GNAME,CAPTGN,NTG,sizeof(char*));
                        GROW(TVO_GHEX,CAPTGH,NTG,sizeof(char*));
                        TVO_GNAME[NTG]=strdup(name); TVO_GHEX[NTG]=strdup(hex); NTG++;
                    }
                    (void)g;
                }
            }
        } else if(!strncmp(line,"GLOB ",5)){
            char name[128],ty[128],hex[1024];
            hex[0]=0;
            if(sscanf(line,"GLOB %127s %127s %1023s",name,ty,hex)>=2){
                Global*exg=global_find(name);
                if(exg && exg->init && hex[0]){
                    fprintf(stderr,"link error: duplicate global '%s'（主单元有初始化，导入单元亦有数据）\n",name);
                    exit(1);
                }
                Global*g=global_add(name,ty,ty_bytes(ty),NULL);
                if(hex[0]){
                    TVO_GNAME=grow(TVO_GNAME,&CAPTGN,NTG+1,sizeof(char*));
                    TVO_GHEX=grow(TVO_GHEX,&CAPTGH,NTG+1,sizeof(char*));
                    TVO_GNAME[NTG]=strdup(name);
                    TVO_GHEX[NTG]=strdup(hex);
                    NTG++;
                }
                (void)g;
            }
        } else if(!strncmp(line,"ALIAS ",6)){
            char an[128],rn[128];
            if(sscanf(line,"ALIAS %127s %127s",an,rn)==2) alias_add(an,rn);
        } else if(!strncmp(line,"STR ",4)){
            int len; if(sscanf(line,"STR %d",&len)==1){
                char *buf=malloc((size_t)len+1);
                const char*hex=line+4; while(*hex==' ')hex++;
                while(*hex>='0'&&*hex<='9')hex++;      /* 跳过长度字段 */
                while(*hex==' ')hex++;                  /* 长度后空格 */
                for(int k=0;k<len;k++){
                    unsigned b; sscanf(hex+2*k,"%2x",&b); buf[k]=(char)b;
                }
                /* 字符串符号化（2026-11-22 §5.7）：导入单元的第 k 条 STR 对应
                   其代码里的 &__str_k 引用；合并后 STRSEG 下标 = NSTR（追加序），
                   新符号名 __strimp_<NSTR>。STRBASE 同步预留，防与主单元字符串
                   重叠（主 assign_strings 从推进后的 STRBASE 起分配）。 */
                {   char from[64],to[64];
                    snprintf(from,sizeof(from),"&__str_%d",nstr_imp_records);
                    snprintf(to,sizeof(to),"&__strimp_%d",NSTR);
                    for(int li=0;li<NTVO;li++){
                        char*nw=str_replace_all(TVO_OUT[li],from,to);
                        if(nw){ TVO_OUT[li]=nw; }
                    }
                    nstr_imp_records++;
                }
                GROW(STRSEG,CAPSTR,NSTR,char*); GROW(STRLEN,CAPSTRLEN,NSTR,int);
                STRSEG[NSTR]=buf; STRLEN[NSTR]=len; NSTR++;
            }
        } else if(!strncmp(line,"FUNC ",5)){
            char fname[128]; int np=0;
            if(sscanf(line,"FUNC %127s %d",fname,&np)!=2) continue;
            {   /* 体首行插函数标签：assemble_all 的标签扫描靠它定位 */
                char lab[136]; snprintf(lab,sizeof(lab),"%s:",fname);
                GROW(TVO_OUT,CAPTVO,NTVO,char*); TVO_OUT[NTVO++]=strdup(lab);
            }
            TVO_SEQ++;                         /* 每个导入单元一个标签命名空间 */
            for(int k=0;k<NFN;k++) if(!strcmp(FNS[k]->s,fname)){
                fprintf(stderr,"link error: duplicate function '%s' (%s)\n",fname,path);
                exit(1);
            }
            Node*f=nnew(N_FUNC); f->rettype=strdup("int"); f->s=strdup(fname);
            f->ptypes=malloc(sizeof(char*)*32); f->params=malloc(sizeof(char*)*32);
            f->fbody=NULL;                   /* 体在 TVO_OUT */
            while(fgets(line,sizeof line,fp)){
                if(!strncmp(line,"PARAM ",6)){
                    int pi; char pn[128],pt[128];
                    if(sscanf(line,"PARAM %d %127s %127s",&pi,pn,pt)==3){
                        f->params[pi]=strdup(pn); f->ptypes[pi]=strdup(pt);
                        if(pi+1>f->nparams) f->nparams=pi+1;
                    }
                } else if(!strncmp(line,"LINE ",5)){
                    char *src_line=strdup(line+5);
                    size_t L=strlen(src_line);
                    while(L&&(src_line[L-1]=='\n'||src_line[L-1]=='\r')) src_line[--L]=0;
                    GROW(TVO_OUT,CAPTVO,NTVO,char*); TVO_OUT[NTVO++]=tvo_relabel(src_line);
                } else if(!strncmp(line,"ENDFUNC",7)) break;
            }
            FNS=grow(FNS,&CAPFN,NFN,sizeof(Node*)); FNS[NFN++]=f;
        } else if(!strncmp(line,"END",3)) break;
    }
    fclose(fp);
}

/* ============ 链接期未定义引用检测 ============
 * 拼接完成后（所有翻译单元的 FNS 已合表），逐函数扫调用点：
 * callee 既非 syscall、又无定义、又不是本函数形参（函数指针形参走
 * 间接调用路径）、也不是全局变量 —— 就是未定义引用。
 * 对齐 gcc 链接器的 undefined reference 报错。 */
static const char **UNDEF_PARAMS; static int NUNDEF_PARAMS;
static void scan_undef_node(Node*n,int *nundef,char (*names)[64]){
    if(!n) return;
    switch(n->k){
    case N_CALL:
        if(!is_syscall_name(n->s) && strncmp(n->s,"(*target)",9)!=0){
            int known=0;
            /* __builtin_tri_lit：gen 期直发内建（2026-11-23），非外部符号——
               本遍历与 gen 各走各的 AST（双点白名单教训，同 tinycc
               tri_patch_symbol/tri_sym_final），漏一点即 undefined reference。 */
            if(!strcmp(n->s,"__builtin_tri_lit")) known=1;
            const char*real=alias_follow(n->s);
            for(int i=0;i<NFN;i++) if(!strcmp(FNS[i]->s,n->s)){known=1;break;}
            if(!known && real){
                for(int i=0;i<NFN;i++) if(!strcmp(FNS[i]->s,real)){known=1;break;}
            }
            if(!known){
                for(int i=0;i<NUNDEF_PARAMS;i++)
                    if(!strcmp(UNDEF_PARAMS[i],n->s)){known=1;break;}
            }
            if(!known && global_find(n->s)) known=1;   /* 全局变量（函数指针表） */
            /* 扫描运行于 gen_func 之后：findloc 恰覆盖本函数形参+局部——
               fn-ptr 局部（如 int (*lp)(int)）作被调目标由此合法化 */
            if(!known && findloc(n->s)) known=1;
            if(!known && (*nundef)<60){
                snprintf(names[*nundef],64,"%s",n->s);
                (*nundef)++;
            }
        }
        for(int i=0;i<n->nargs;i++) scan_undef_node(n->args[i],nundef,names);
        return;
    }
    Node*all[]={n->l,n->r,n->e,n->cond,n->then,n->els,n->init,n->step,n->stmt,n->expr};
    for(int i=0;i<10;i++) scan_undef_node(all[i],nundef,names);
    if(n->body) for(int i=0;i<n->nitems;i++) scan_undef_node(n->body[i],nundef,names);
    if(n->args) for(int i=0;i<n->nargs;i++) scan_undef_node(n->args[i],nundef,names);
    if(n->items) for(int i=0;i<n->nitems;i++) scan_undef_node(n->items[i],nundef,names);
    if(n->dinit) for(int i=0;i<n->ndecls;i++) scan_undef_node(n->dinit[i],nundef,names);
}

/* 窥孔优化：基本块内 ADDI 复制链传播 ============
 * ADDI rd, rs, 0 是寄存器复制。在基本块内维护 canonical[rd]=rs，
 * 把后续对 rd 的源引用替换为 rs，消除冗余 MOVE。
 * 遇到 label / 跳转 / 非复制指令即清空对应链（保守，保证语义）。
 */
static int parse_mov(const char*line,int*rd,int*rs){
    while(*line==' '||*line=='\t')line++;
    if(strncmp(line,"ADDI",4)||!isspace((unsigned char)line[4])) return 0;
    line+=4; while(*line==' '||*line=='\t')line++;
    char a[32]={0},b[32]={0},c[32]={0};
    char*dst[3]={a,b,c}; int k=0;
    while(*line&&k<3){
        const char*q=line; while(*q&&*q!=',')q++;
        int L=q-line; if(L>=32)L=31; memcpy(dst[k],line,L); dst[k][L]=0; k++;
        char*t=dst[k-1]; while(*t==' '||*t=='\t')t++; memmove(dst[k-1],t,strlen(t)+1);
        if(!*q)break; line=q+1;
    }
    if(k<3) return 0;
    if(strcmp(c,"0")&&strcmp(c,"zero")) return 0;
    int d=find_reg(a),s=find_reg(b);
    if(d<0||s<0||d==s) return 0;
    *rd=d;*rs=s; return 1;
}
static void peephole(void){
    int canon[32]; for(int i=0;i<32;i++)canon[i]=-1;
    char **NEW=malloc(sizeof(char*)*(NOUT?NOUT:1)); int NNEW=0;
    for(int i=0;i<NOUT;i++){
        char*line=OUT[i];
        char*s=line; while(*s==' '||*s=='\t')s++;
        /* 标签或控制流指令：清空复制链 */
        if(*s=='_'||*s=='.'||strchr(s,':')){ for(int k=0;k<32;k++)canon[k]=-1; NEW[NNEW++]=line; continue; }
        char op[32]; sscanf(s,"%31s",op);
        if(!strcmp(op,"JAL")||!strcmp(op,"JALR")||!strcmp(op,"BEQ")||!strcmp(op,"BNE")||
           !strcmp(op,"ECALL")||!strcmp(op,"BLT")||!strcmp(op,"BGE")){
            for(int k=0;k<32;k++)canon[k]=-1; NEW[NNEW++]=line; continue;
        }
        int rd,rs;
        if(parse_mov(line,&rd,&rs)){
            /* canonical 源：若 rs 有规范源，用之 */
            int src = (canon[rs]>=0)?canon[rs]:rs;
            /* 删除自复制 ADDI r,r,0 */
            if(src==rd){ continue; }
            char nl[128]; snprintf(nl,sizeof(nl),"ADDI %s, %s, 0",regname(rd),regname(src));
            NEW[NNEW++]=strdup(nl);
            canon[rd]=src;
        } else {
            /* 非复制指令：把指令中的源寄存器替换为规范源（保守：只替换 t0/t1/t2 且格式简单）。
               这里为安全起见不做通用替换，仅清空被写寄存器的链。 */
            int wr=-1;
            char a[32]; if(sscanf(s,"%31s %31s",op,a)>=2){ wr=find_reg(a); }
            if(wr>=0) canon[wr]=-1;
            NEW[NNEW++]=line;
        }
    }
    OUT=NEW; NOUT=NNEW;
}

/* 两遍汇编 */
/* ---- 符号地址装载展开（2026-11-22 §5.7，链接器首案核心） ----
 * LINE 里的 `&name` 地址引用（数据全局/函数）由 assemble_all 在汇编期展开为
 * load_imm128 等价的分段装载序列（1~N 条）。两遍（计数/编码）走同一生成器，
 * 标签下标保持一致。动机：数据地址（如 qs_tmp_wide@2511）超出 ADDI 的 IMM7
 * （±1093），且烘焙 gen 期常量会被跨单元重排/peephole 弄错位——符号化 +
 * 汇编期展开一次解决。 */
static int sym_emit_seg(uint32_t*prog,int idx,int rd,long long n){
    if(n<0){ idx=sym_emit_seg(prog,idx,rd,-n);
             if(prog) prog[idx]=encR(O_OP,1,rd,0,0,rd);   /* SUB rd, zero, rd */
             return idx+1; }
    long long h=n/2187, lo=n%2187; const long long HALF7=(2187-1)/2;
    if(lo>HALF7){ lo-=2187; h++; }
    if(h==0){ if(prog) prog[idx]=encI(O_OPIMM,(int)lo,0,0,rd); return idx+1; }
    if(prog) prog[idx]=encU(O_LUI,(int)h,rd); idx++;              /* LUI rd, h */
    if(lo){ if(prog) prog[idx]=encI(O_OPIMM,(int)lo,rd,0,rd); idx++; }  /* ADDI rd, rd, lo */
    return idx;
}
static int sym_emit(uint32_t*prog,int idx,int rd,long long v){
    if(v==0){ if(prog) prog[idx]=encI(O_OPIMM,0,0,0,rd); return idx+1; }
    const long long B=3486784401LL; const long long HALFB=(B-1)/2;
    long long segs[8]; int ns=0; long long m=v;
    while(m){ long long seg=m%B; m/=B; if(seg>HALFB){seg-=B;m++;} segs[ns++]=seg; }
    idx=sym_emit_seg(prog,idx,rd,segs[ns-1]);
    for(int i=ns-2;i>=0;i--){
        if(prog) prog[idx]=encI(O_OPIMM,20,rd,4,rd); idx++;       /* SLLI rd, rd, 20 */
        idx=sym_emit_seg(prog,idx,SCRATCH,segs[i]);
        if(prog) prog[idx]=encR(O_OP,0,SCRATCH,rd,0,rd); idx++;   /* ADD rd, rd, tp */
    }
    return idx;
}
/* 从汇编行解析 &sym 的值与目的寄存器（操作数一）；成功返回 0 */
static int sym_parse(const char*line,char*sym,int symcap,long long*val,int*rd){
    const char*amp=strchr(line,'&'); if(!amp) return -1;
    snprintf(sym,symcap,"%s",amp+1);
    char*e2=sym; while(*e2&&!isspace((unsigned char)*e2)&&*e2!=',')e2++; *e2=0;
    int la=find_label(sym);
    if(la>=0){ *val=(long long)la*4; }
    else if(!strncmp(sym,"__strimp_",9)||!strncmp(sym,"__str_",6)){
        /* 字符串符号（2026-11-22 §5.7 字符串符号化）：__str_<k>（主单元）/
           __strimp_<j>（导入单元，经改名映射）——k/j = STRSEG 下标，
           地址 = STRSEG_START + 前缀和 STRLEN[0..k-1] */
        int k=-1;
        if(!strncmp(sym,"__strimp_",9)) k=atoi(sym+9);
        else k=atoi(sym+6);
        if(k<0||k>NSTR) return -1;
        long long a=STRSEG_START;
        for(int t=0;t<k&&t<NSTR;t++) a+=STRLEN[t];
        *val=a;
    }
    else { Global*gd=global_find(sym); if(!gd) return -1; *val=gd->addr; }
    {   /* 目的寄存器 = 助记符后的第一个操作数 */
        char op[32]; if(sscanf(line,"%31s",op)!=1) return -1;
        const char*p=line+strlen(op); while(*p==' '||*p=='\t')p++;
        char rn[32]; int k=0;
        while(*p&&*p!=','&&*p!=' '&&*p!='\t'&&k<31) rn[k++]=*p++;
        rn[k]=0;
        *rd=find_reg(rn);
    }
    return *rd>=0?0:-1;
}

static uint32_t* assemble_all(int*pn){
    /* 三阶段（2026-11-22 §5.7）：
     * 0) 定点迭代：标签收集 + &符号行展开计数。&引用可前向（如主程序引用
     *    导入单元的函数标签），首轮未命中按保守上界 7 条预占；LABS 每轮
     *    完整重建，前向引用次轮即可解析，迭代至 idx 稳定（实测 2~3 轮）。
     * 1) 编码：LABS 已完整，&行精确展开（条数=计数轮的最终值）。 */
    int idx=0, prev=-1, rounds=0;
    while(idx!=prev && rounds<10){
        prev=idx; idx=0; NLAB=0; rounds++;
        for(int i=0;i<NOUT;i++){
            char line[512]; strncpy(line,OUT[i],sizeof(line)-1); line[sizeof(line)-1]=0;
            char*s=line; while(*s==' '||*s=='\t')s++;
            if(*s&&strchr(s,':')){ char*colon=strchr(s,':'); *colon=0; add_label(s,idx); *colon=':';
                /* 该行只有标签 */
                char*t=colon+1; while(*t==' ')t++; if(*t) idx++;
            } else if(*s&&strchr(s,'&')){
                char sym[128]; long long v; int rd;
                if(sym_parse(s,sym,sizeof(sym),&v,&rd))
                    idx += 7;                     /* 未命中（首轮前向引用）：保守上界 */
                else
                    idx = sym_emit(NULL,idx,rd,v); /* 仅计数 */
            } else if(*s) idx++;
        }
    }
    *pn=idx;
    if(getenv("TRV_LABEL_DEBUG")){
        FILE*lf=fopen("/tmp/labs.dump","w");
        for(int i=0;i<NLAB;i++) fprintf(lf,"%s %d\n",LABS[i].name,LABS[i].idx);
        fclose(lf);
    }
    uint32_t*prog=calloc(idx?idx:1,sizeof(uint32_t));
    idx=0;
    for(int i=0;i<NOUT;i++){
        char line[512]; strncpy(line,OUT[i],sizeof(line)-1); line[sizeof(line)-1]=0;
        char*s=line; while(*s==' '||*s=='\t')s++;
        if(*s&&strchr(s,':')){ char*colon=strchr(s,':'); *colon=':';
            char*t=colon+1; while(*t==' ')t++; if(!*t) continue; s=t;
        }
        if(*s&&strchr(s,'&')){
            char sym[128]; long long v; int rd;
            if(sym_parse(s,sym,sizeof(sym),&v,&rd)){
                fprintf(stderr,"[asm] unknown symbol &%s\n",sym);
                return NULL;
            }
            idx = sym_emit(prog,idx,rd,v);
            continue;
        }
        if(*s){ prog[idx]=asm_line(s,idx); idx++; }
    }
    return prog;
}

/* 分配字符串常量地址 */
static void strings_walk(Node*n){
    if(!n) return;
    if(n->k==N_STR){
        n->straddr=STRBASE; STRBASE+=n->slen+1;       /* 段数据含结尾 NUL */
        n->strsym=NSTR;                               /* 符号 = STRSEG 下标（跨单元符号化）*/
        GROW(STRSEG,CAPSTR,NSTR,char*); GROW(STRLEN,CAPSTRLEN,NSTR,int);
        {   char*buf=malloc((size_t)n->slen+1);
            memcpy(buf,n->s,(size_t)n->slen); buf[n->slen]=0;
            STRSEG[NSTR]=buf; STRLEN[NSTR]=n->slen+1; NSTR++; }
        return;
    }
    Node*all[]={n->l,n->r,n->e,n->cond,n->then,n->els,n->init,n->step,n->stmt,n->expr};
    for(int i=0;i<10;i++) strings_walk(all[i]);   /* N_DOWHILE: cond/stmt 已覆盖 */
    if(n->body) for(int i=0;i<n->nitems;i++) strings_walk(n->body[i]);
    if(n->args) for(int i=0;i<n->nargs;i++) strings_walk(n->args[i]);
    if(n->items) for(int i=0;i<n->nitems;i++) strings_walk(n->items[i]);
    if(n->dinit) for(int i=0;i<n->ndecls;i++) strings_walk(n->dinit[i]);
}
/* ===================== 能力闸门 =====================
 * 编译器只承诺它真正实现的语义。对未实现特性（数组寻址、结构体成员、
 * 变参、栈传参...）必须显式拒绝——返回非零退出码，由统一后端回退到
 * Python 参考实现。宁可慢，也不能静默生成错误代码。
 */
static int ty_is_array(const char*ty){ return ty && strchr(ty,'[')!=NULL; }
static int ty_is_struct_val(const char*ty){
    return ty && !strncmp(ty,"struct ",7) && ty[strlen(ty)-1]!='*';
}
/* gate_check（位运算/移位工作寄存器预扫描）已于 2026-11-22 随方案 A 退役：
 * 本征直发零工作寄存器，无需按函数预扫 AST 抢占 callee-saved。 */

static void put_u32(FILE*f,uint32_t v){ fputc(v&0xff,f);fputc((v>>8)&0xff,f);fputc((v>>16)&0xff,f);fputc((v>>24)&0xff,f); }

int main(int argc,char**argv){
    { __int128 p=1; for(int i=0;i<45;i++) p*=3; TRV_P45V=p;   /* 3^45（模数本体） */
      { __int128 w=1; for(int b=0;b<9;b++){ TRV_WT[b]=w; w*=243; } }  /* W[b]=3^(5b) 低->高 */
    }
    if(argc<3){ fprintf(stderr,"usage: %s <src.c> <out.trv> [out.asm] [-l lib.c ...]\n",argv[0]); return 1; }
    /* 多文件（mini 链接器）：-l 库文件与主文件按序拼接成一个翻译单元。
     * C 的链接语义中最常用的"先原型后定义 + 重复定义检测"由此获得：
     *   - 顺序：命令行出现顺序（-l 库在前、主文件在后也支持）
     *   - 重复函数定义：parse_program 检测 FNS 重名 -> 报错
     *   - 调用先于定义：两遍代码生成兜底（前向引用已解决） */
    static char **TRVO_PATHS; static int NTVOP, CAPTVOP;   /* 待导入的 .trvo */
    {   /* 拼接所有源文件 */
        long total=0;
        int *sizes=malloc(sizeof(int)*(size_t)argc);
        char **bufs=malloc(sizeof(char*)*(size_t)argc);
        int nf=0;
        for(int ai=1;ai<argc;ai++){
            if(!strcmp(argv[ai],"-l")){ ai++;
                if(ai>=argc){ fprintf(stderr,"-l needs file\n"); return 1; }
            } else if(ai!=1) continue;   /* 只认 argv[1]（主文件）与 -l */
            {   /* .trvo 输入：不入拼接缓冲，登记延后导入 */
                size_t L=strlen(argv[ai]);
                if(L>5&&!strcmp(argv[ai]+L-5,".trvo")){
                    TRVO_PATHS=grow(TRVO_PATHS,&CAPTVOP,NTVOP,sizeof(char*));
                    TRVO_PATHS[NTVOP++]=strdup(argv[ai]);
                    continue;
                }
            }
            FILE*fp=fopen(argv[ai],"r"); if(!fp){perror(argv[ai]);return 1;}
            fseek(fp,0,SEEK_END); long sz=ftell(fp); fseek(fp,0,SEEK_SET);
            char*src=malloc((size_t)sz+2);
            fread(src,1,(size_t)sz,fp); src[sz]=0; fclose(fp);
            if(sz==0||src[sz-1]!='\n'){ src[sz]='\n'; sz++; }   /* 拼接边界保证 */
            bufs[nf]=src; sizes[nf]=(int)sz; total+=sz; nf++;
        }
        char*src=malloc((size_t)total+1); long off=0;
        for(int i=0;i<nf;i++){ memcpy(src+off,bufs[i],(size_t)sizes[i]); off+=sizes[i]; }
        src[total]=0;
        lex(src); IPOS=0;
        parse_program();
    }
    /* 能力闸门：未实现特性直接拒绝，交由统一后端回退到 Python 参考实现。
     * struct 按值传参/返回已于 2026-11-19 实现（见 gen_func 的 prologue 拷贝与
     * N_RETURN 的 sret 拷贝），不再拦截。位运算/移位工作寄存器闸门
     * （gate_check 循环）已于 2026-11-22 随本征直发退役。 */
    /* A5a（2026-11-22）：导入体 LINE 的 JAL 符号引用同样受 UNDEF 检测——
     * 闭包导出不携带缺失上游时，链接期诚实报错（旧行为：导入体不扫描，
     * 缺失引用静默通过 → 运行期跳空）。syscall 内建豁免。 */
    for(int k2=0;k2<NTVO;k2++){
        const char*t=TVO_OUT[k2];
        if(!strncmp(t,"JAL ra, ",8)){
            const char*fn=t+8;
            int known=0;
            { int q; for(q=0;q<NFN;q++) if(!strcmp(FNS[q]->s,fn)){known=1;break;} }
            if(!known && syscall_no((char*)fn)<0){
                fprintf(stderr,"link error: undefined reference to '%s' (in imported body)\n",fn);
                exit(1);
            }
        }
    }

    /* 函数名表 */
    for(int i=0;i<NFN;i++){
        GROW(FNRETS,CAPFNRET,NFNRET,char*); GROW(FNVARGS,CAPFNVARGS,NFNRET,int);
        GROW(FNEYS,CAPFNEYS,NFNRET,char*);
        FNRETS[NFNRET]=FNS[i]->rettype; FNVARGS[NFNRET]=FNS[i]->variadic; FNEYS[NFNRET]=FNS[i]->s; NFNRET++;
    }
    /* TRVO 目标文件导入：FNS/GLOBS/STRSEG 追加（在地址分配之前，
     * 让导入的全局/字符串参与统一布局；函数汇编体在 assemble 前 append）。 */
    for(int ti=0;ti<NTVOP;ti++) tvo_import(TRVO_PATHS[ti]);

    /* 全局变量：地址分配 -> 字符串段接在其后 -> 数据生成（依赖字符串地址） */
    assign_glob_addrs();
    STRSEG_START = STRBASE;               /* 记录字符串段写出的起点 */
    /* 字符串地址分配：先扫全局初始化表达式（指针 -> 字符串字面量），
       再扫所有函数体 */
    for(int i=0;i<NGLOB;i++) if(GLOBS[i].init){
        strings_walk(GLOBS[i].init);
    }
    for(int i=0;i<NFN;i++){ strings_walk(FNS[i]->fbody); }
    /* C2 内存映射纪律（2026-11-22）：数据区顶（全局+字符串末尾）不得越过栈基址。
       旧行为：静默编译 → 栈帧与静态数据重叠 → 运行期互相踩踏（静默错码）。
       布局：数据 200..STACK_BASE-1（GBASE=200 起全局、字符串接后），栈向上。 */
    if (STRBASE > TRI_STACK_BASE) {
        fprintf(stderr,
                "UNSUPPORTED: 数据区顶 %d 越过栈基址 %d——全局/字符串过大 "
                "（内存映射：数据 %d..%d，栈 %d+；需内存映射升级或缩小静态数据）\n",
                STRBASE, TRI_STACK_BASE, GBASE, TRI_STACK_BASE - 1, TRI_STACK_BASE);
        exit(2);
    }
    gen_glob_data();
    NOUT=0; LBL=0;
    /* 是否有用户定义的 __libc_start_main（决定 crt0 走 musl 链还是直连 main） */
    int have_lsm=0;
    for(int i=0;i<NFN;i++) if(!strcmp(FNS[i]->s,"__libc_start_main")) have_lsm=1;
    /* 第一遍的 _start（占位）；第二遍在上面循环里重新 emit。
     * 进程信息在零页固定地址（本 VM 栈向上生长，sp 惯例会立刻被第一帧覆盖，
     * 这是栈方向决定的必要偏差）：argc@0 argv@9（argv 数组首地址，元素是
     * 指针，数组本身在 VM 构造的镜像里） */
    emitf("_start:");
    if(have_lsm){
        /* TriRV 形态（与 musl 签名的差异见 trirv_crt.c 头注释）：
         * __libc_start_main(argc, argv) —— main 在同一编译单元，
         * lsm 直接 JAL main，不需要 main 指针参数（避免了
         * "main 地址"这个编译期前向引用问题）。 */
        emitf("LW a0, 0(zero)");            /* argc */
        emitf("LW a1, 9(zero)");            /* argv 数组地址 */
        emitf("JAL ra, __libc_start_main");
        emitf("ADDI a7, zero, 93");
        emitf("ECALL");
    } else {
        /* 直连链：也按 RISC-V main 约定传 argc/argv（envp 不传，兼容旧程序） */
        emitf("LW a0, 0(zero)");            /* argc */
        emitf("LW a1, 9(zero)");            /* argv */
        emitf("JAL ra, main");
        emitf("ADDI a7, zero, 93");
        emitf("ECALL");
    }
    /* 两遍生成：函数地址（fn_label_addr）依赖 OUT 里的标签行号，
     * 而 __libc_start_main 这类前向引用在第一遍时 main 还没生成。
     * 第一遍：_start + 每函数"标签+1条返回"占位，只为锁定标签行号；
     * 第二遍：清空重生成（此时 fn_label_addr 能查到正确地址）。 */
    for(int pass=0;pass<2;pass++){
        if(pass==1){ NOUT=0; LBL=0;
            emitf("_start:");                  /* _start 也得重来（占用指令下标） */
            if(have_lsm){
                emitf("LW a0, 0(zero)");
                emitf("LW a1, 9(zero)");
                emitf("JAL ra, __libc_start_main");
                emitf("ADDI a7, zero, 93");
                emitf("ECALL");
            } else {
                emitf("LW a0, 0(zero)");
                emitf("LW a1, 9(zero)");
                emitf("JAL ra, main");
                emitf("ADDI a7, zero, 93");
                emitf("ECALL");
            }
        }
        for(int i=0;i<NFN;i++){
            if(pass==1 && !FNS[i]->fbody) continue;  /* TRVO 导入：标签+体在 TVO_OUT */
            emitf("%s:",FNS[i]->s);
            if(pass==0) emitf("JALR zero, 0(ra)");   /* 占位：至少 1 条指令 */
            else if(!FNS[i]->fbody){
                emitf("JALR zero, 0(ra)");           /* TRVO 单元独编（无导入）时占位 */
            } else {
                gen_func(FNS[i]);
                /* 链接期未定义引用检测：此刻 findloc 恰好覆盖本函数的
                   形参+局部（gen_func 刚填完）。N_CALL 的 callee 四查：
                   FNS / syscall / 本函数形参（间接调用豁免）/ 全局。
                   导出 TRVO（目标文件）时跳过——目标文件允许 UNDEF，
                   检测留给最终产出可执行 .trv 的链接期。 */
                {
                    int is_trvo = strlen(argv[2])>5 && !strcmp(argv[2]+strlen(argv[2])-5,".trvo");
                    if(!is_trvo){
                        static char undef_names[64][64];
                        int nundef=0;
                        const char **pset=malloc(sizeof(char*)*(size_t)(FNS[i]->nparams?FNS[i]->nparams:1));
                        for(int p=0;p<FNS[i]->nparams;p++) pset[p]=FNS[i]->params[p];
                        UNDEF_PARAMS=pset; NUNDEF_PARAMS=FNS[i]->nparams;
                        scan_undef_node(FNS[i]->fbody,&nundef,undef_names);
                        if(nundef){
                            for(int u=0;u<nundef;u++)
                                fprintf(stderr,"link error: undefined reference to '%s' (in %s)\n",
                                        undef_names[u],FNS[i]->s);
                            exit(1);
                        }
                        free(pset);
                    }
                }
            }
        }
    }
    drop_jump_to_next();
    /* ---- T10d TC 压缩 peephole（TRV_COMPACT=1 启用）----
     * ADDI rd, rd, imm（rd ∈ {x10,x11,x12,x13,x8,x9,x14,x15}，|imm|≤1093）
     * → C-ADDI <subset_idx>, <imm>——语义等价，码长 4→3 tryte。
     * 其他四类（LW/SW/BEQ/BNE）后续补齐。 */
    if(getenv("TRV_COMPACT")){
        /* TC 子集 ABI 名 ↔ 子集索引 + 寄存器号（与 trirv_vm.c _exec_c 的
           _C_REGS 映射一致：0→x10/a0, 1→x11/a1, 2→x12/a2, 3→x13/a3,
           4→x8/s0, 5→x9/s1, 6→x14, 7→x15） */
        static const char*tc_names[]={"a0","a1","a2","a3","s0","s1","x14","x15"};
        for(int i=0;i<NOUT;i++){
            char rdbuf[16],rsbuf[16];int imm;
            if(sscanf(OUT[i],"ADDI %15[^,], %15[^,], %d",rdbuf,rsbuf,&imm)!=3)continue;
            int tidx=-1;
            for(int k=0;k<8;k++) if(!strcmp(rdbuf,tc_names[k])){tidx=k;break;}
            if(tidx<0||strcmp(rdbuf,rsbuf))continue;   /* C-ADDI 要求 rd==rs1 */
            if(imm<-1093||imm>1093)continue;
            char buf[64];snprintf(buf,sizeof(buf),"C-ADDI %d, %d",tidx,imm);
            OUT[i]=strdup(buf);
        }
    }
    /* TRVO 导入的汇编体：两遍 gen 已定稿，append 到 OUT 末尾
     * （assemble_all 的标签扫描会自动解析它们之间的引用）。
     * 必须在 asm dump / peephole / assemble_all 之前。 */
    for(int i=0;i<NTVO;i++) OUT=grow(OUT,&CAPOUT,NOUT,sizeof(char*)),OUT[NOUT++]=TVO_OUT[i];
    if(getenv("TRV_DUMP_ASM")){ for(int i=0;i<NOUT;i++) fprintf(stderr,"%s\n",OUT[i]); }
    /* 第三个参数（可选）：把汇编文本写入文件，供统一后端/差分测试读取，
       避免与 stderr 上的诊断信息混流。 */
    if(argc>=4){
        FILE*af=fopen(argv[3],"w");
        if(af){ for(int i=0;i<NOUT;i++) fprintf(af,"%s\n",OUT[i]); fclose(af); }
    }
    /* 窥孔 copy-propagation 原型：目前正确性未达交付标准（跨指令寄存器状态维护
       不完整，个别程序会破坏循环终止），默认关闭。仅在 TRV_PEEP=1 时启用供研究。
       寄存器式代码生成本身已消除绝大部分栈搬运，是主要收益来源。 */
    if(getenv("TRV_PEEP")) peephole();
    int n; uint32_t*prog=assemble_all(&n);

    /* TRVO 导出模式：输出文件以 .trvo 结尾时写中间格式（可再链接）。
     * 文本行式：FUNC/PARAM/LINE/GLOB/STR/LBLBASE/END。 */
    if(strlen(argv[2])>5 && !strcmp(argv[2]+strlen(argv[2])-5,".trvo")){
        FILE*out=fopen(argv[2],"wb"); if(!out){perror("out");return 1;}
        fprintf(out,"TRVO 1\n");
        fprintf(out,"LBLBASE %d\n",LBL);          /* 标签计数续接用 */
        for(int i=0;i<NGLOB;i++){
            fprintf(out,"%s %s %s ",GLOBS[i].weak?"GLOBW":"GLOB",GLOBS[i].name,GLOBS[i].ty);
            /* 初值字节 hex（gen_glob_data 后 data 有效；坑实录：曾不导初值——
               导入端 global_add(init=NULL) 全零，strtod_tri 的 SD_P34=3^34
               等常量全丢 → raw=0。真实管线此前全走源码拼接未触发。） */
            if(GLOBS[i].data)
                for(int b=0;b<GLOBS[i].size;b++)
                    fprintf(out,"%02x",GLOBS[i].data[b]);
            fprintf(out,"\n");
        }
        for(int i=0;i<NSTR;i++){
            fprintf(out,"STR %d ",STRLEN[i]);
            for(int k=0;k<STRLEN[i];k++) fprintf(out,"%02x",(unsigned char)STRSEG[i][k]);
            fprintf(out,"\n");
        }
        for(int i=0;i<NALIAS;i++)
            fprintf(out,"ALIAS %s %s\n",ALIAS_NAMES[i],ALIAS_REAL[i]);
        for(int i=0;i<NFN;i++){
            /* A5a 闭包转发导出（2026-11-22）：导入函数（fbody==NULL）连同
             * 本单元函数一并导出——.trvo 链可传递，下游一次 -l 拿到完整闭包。
             * 若下游同时 -l 上游原件 → duplicate function 诚实报错（安全网）。
             * TRV_NO_FORWARD=1 恢复旧行为（平铺半闭包）。 */
            if(!FNS[i]->fbody && getenv("TRV_NO_FORWARD")) continue;
            fprintf(out,"FUNC %s %d\n",FNS[i]->s,FNS[i]->nparams);
            for(int p=0;p<FNS[i]->nparams;p++)
                fprintf(out,"PARAM %d %s %s\n",p,FNS[i]->params[p],FNS[i]->ptypes[p]);
            /* 该函数的汇编行：OUT 里从 "name:" 之后到下一个顶层标签之前 */
            int in_fn=0;
            for(int k=0;k<NOUT;k++){
                const char*L=OUT[k];
                size_t len=strlen(L);
                if(len>0&&L[len-1]==':'){
                    char tmp[128]; snprintf(tmp,sizeof(tmp),"%.*s",(int)(len-1),L);
                    /* 边界只认【函数名】标签；局部标签 _L* 照抄为 LINE */
                    int is_fn_boundary=0;
                    for(int q=0;q<NFN;q++) if(!strcmp(FNS[q]->s,tmp)){is_fn_boundary=1;break;}
                    if(in_fn && is_fn_boundary) break;      /* 下一个函数开始 */
                    if(!in_fn && !strcmp(tmp,FNS[i]->s)){ in_fn=1; continue; }
                    if(!in_fn) continue;
                    /* 函数体内局部标签：原样 LINE */
                }
                if(in_fn) fprintf(out,"LINE %s\n",L);
            }
            fprintf(out,"ENDFUNC\n");
        }
        fprintf(out,"END\n");
        fclose(out);
        fprintf(stderr,"[trirv_cc.c] exported TRVO %s (fns=%d globs=%d strs=%d)\n",argv[2],NFN,NGLOB,NSTR);
        return 0;
    }
    /* 写 TRV1 */
    FILE*out=fopen(argv[2],"wb"); if(!out){perror("out");return 1;}
    fwrite("TRV1",1,4,out);
    put_u32(out,1); put_u32(out,n);
    {   /* 段数 = 字符串段 + 非零初始化的全局段 */
        int nseg=NSTR;
        for(int i=0;i<NGLOB;i++){ Global*g=&GLOBS[i];
            int nz=0; for(int b=0;b<g->size;b++) if(g->data[b]) nz++;
            if(nz) nseg++; }
        put_u32(out,nseg);
    }
    for(int i=0;i<n;i++) put_u32(out,prog[i]);
    /* 字符串段：每个地址一个 segment（起点 = 分配前的 STRBASE，不是当前值） */
    {
        int addr=STRSEG_START;
        for(int i=0;i<NSTR;i++){
            put_u32(out,addr); put_u32(out,STRLEN[i]);
            fwrite(STRSEG[i],1,STRLEN[i],out);
            addr+=STRLEN[i];
        }
    }
    /* 全局变量段（跳过全零：内存镜像天然为 0，省文件体积） */
    for(int i=0;i<NGLOB;i++){
        Global*g=&GLOBS[i];
        int nz=0; for(int b=0;b<g->size;b++) if(g->data[b]) nz++;
        if(!nz) continue;
        put_u32(out,g->addr); put_u32(out,g->size);
        fwrite(g->data,1,(size_t)g->size,out);
    }
    fclose(out);
    fprintf(stderr,"[trirv_cc.c] compiled %s: %d instructions, %d strings, %d globals -> %s\n",argv[1],n,NSTR,NGLOB,argv[2]);
    return 0;
}


