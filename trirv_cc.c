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
static uint32_t encI(int op,int imm7,int rs1,int funct,int rd){
    uint32_t v=0;
    v=putf(v,0,2,op); v=putf(v,3,6,rd); v=putf(v,7,8,funct);
    v=putf(v,9,12,rs1);
    int m=((imm7%2187)+2187)%2187;
    v=putf(v,13,19,m);
    return v;
}
static uint32_t encS(int op,int imm7,int rs2,int rs1,int funct){
    uint32_t v=0;
    v=putf(v,0,2,op); v=putf(v,7,8,funct);
    v=putf(v,9,12,rs1); v=putf(v,13,16,rs2);
    int m=((imm7%2187)+2187)%2187;
    v=putf(v,3,6,m%81); v=putf(v,17,19,m/81);
    return v;
}
static uint32_t encU(int op,int imm13,int rd){
    uint32_t v=0;
    v=putf(v,0,2,op); v=putf(v,3,6,rd);
    int m=((imm13%1594323)+1594323)%1594323;
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
    {"LBU",O_LOAD,3,0,1},{"LHU",O_LOAD,4,0,1},
    {"SB",O_STORE,0,0,2},{"SH",O_STORE,1,0,2},{"SW",O_STORE,2,0,2},
    {"BEQ",O_BRANCH,0,0,2},{"BNE",O_BRANCH,1,0,2},{"BLT",O_BRANCH,2,0,2},
    {"BGE",O_BRANCH,3,0,2},{"BLTU",O_BRANCH,4,0,2},{"BGEU",O_BRANCH,5,0,2},
    {"JAL",O_JAL,0,0,3},{"JALR",O_JALR,0,0,1},{"LUI",O_LUI,0,0,3},
    {"SEQ",O_TLOGIC,3,0,0},{"SNE",O_TLOGIC,3,1,0},  /* 占位, 用 SUB+SLTIU 代替 */
    {"ECALL",O_SYSTEM,0,0,1},
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
    static const char*kw[]={"int","char","void","unsigned","signed","struct","va_list",
        "if","else","while","for","return","break","continue",NULL};
    for(int i=0;kw[i];i++) if(!strcmp(s,kw[i])) return 1;
    return 0;
}
static void lex(const char*src){
    const char*p=src;
    while(*p){
        if(isspace((unsigned char)*p)){p++;continue;}
        if(p[0]=='/'&&p[1]=='/'){ while(*p&&*p!='\n')p++; continue; }
        if(p[0]=='/'&&p[1]=='*'){ p+=2; while(*p&&!(p[0]=='*'&&p[1]=='/'))p++; if(*p)p+=2; continue; }
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
        static const char*ops[]={"...","==","!=","<=",">=","&&","||","<<",">>","++","--","->",NULL};
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
    N_PREINC,N_DEREF,N_ADDR,
    N_BLOCK,N_DECL,N_IF,N_WHILE,N_FOR,N_RETURN,N_EXPR,N_BREAK,N_CONT,
    N_FUNC,N_INITLIST,N_VASTART,N_VAARG,N_VAEND
} NodeKind;
typedef struct Node Node;
struct Node {
    NodeKind k;
    long long num;
    __int128 numw;      /* N_NUM 的全精度字面量 */
    char *s;            /* 名称/运算符/成员名 */
    int slen;           /* TK_STR 字节长度 */
    int straddr;
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
};
static Node* nnew(NodeKind k){
    Node*n=calloc(1,sizeof(Node)); n->k=k; return n;
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
static char* parse_type(void){
    if(!(atk(TK_KW)||atk(TK_ID))){ fprintf(stderr,"parse_type: bad token '%s'\n",cur()->s); exit(1); }
    char base[128];
    /* char 的符号性：RISC-V psABI 规定 char 默认无符号（GCC DEFAULT_SIGNED_CHAR=0），
     * 与 TriRV 的 tryte 值域 0..242 天然吻合。故 char/unsigned char 走 LBU 零扩展，
     * 只有显式 signed char 走 LB 符号扩展取 -121..121。 */
    if(accept("unsigned")){
        if(accept("char")) snprintf(base,sizeof(base),"uchar");
        else { accept("int"); snprintf(base,sizeof(base),"unsigned"); }
    } else if(accept("signed")){
        if(accept("char")) snprintf(base,sizeof(base),"schar");
        else { accept("int"); snprintf(base,sizeof(base),"int"); }
    } else if(accept("int")) snprintf(base,sizeof(base),"int");
    else if(accept("char")) snprintf(base,sizeof(base),"char");
    else if(accept("void")) snprintf(base,sizeof(base),"void");
    else if(accept("struct")){ char*nm=take()->s; snprintf(base,sizeof(base),"struct %s",nm); }
    else if(accept("va_list")) snprintf(base,sizeof(base),"va_list");
    else { snprintf(base,sizeof(base),"%s",take()->s); }
    int len=strlen(base); char*ty=malloc(256); strcpy(ty,base);
    while(at("*")){ take(); ty[len++]='*'; ty[len]=0; }
    return ty;
}

static Node* parse_primary(void){
    if(atk(TK_NUM)){ Token*tk=take(); Node*n=nnew(N_NUM); n->num=tk->num; n->numw=tk->numw; return n; }
    if(atk(TK_STR)){
        Token*t=take(); Node*n=nnew(N_STR); n->s=t->s; n->slen=t->slen; return n;
    }
    if(accept("(")){ Node*n=parse_expr(); expect(")"); return n; }
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
        Node*n=nnew(N_VAR); n->s=name; return n;
    }
    fprintf(stderr,"parse_primary: bad '%s'\n",cur()->s); exit(1);
}
static Node* parse_postfix(void){
    Node*n=parse_primary();
    for(;;){
        if(accept("[")){ Node*i=parse_expr(); expect("]"); Node*x=nnew(N_INDEX); x->l=n; x->r=i; n=x; }
        else if(at(".")||at("->")){
            int arrow = at("->"); take();
            char*mt=take()->s;
            Node*x=nnew(N_MEMBER); x->l=n; x->s=mt; x->arrow=arrow; n=x;
        }
        else if(accept("++")){ Node*x=nnew(N_PREINC); x->e=n; x->num=1; n=x; }
        else if(accept("--")){ Node*x=nnew(N_PREINC); x->e=n; x->num=-1; n=x; }
        else break;
    }
    return n;
}
static Node* parse_unary(void){
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
static Node* parse_expr(void){ return parse_assign(); }

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

static Node* parse_decl(void){
    /* 声明: type name[=init], name[=init], ... ; */
    char*ty=parse_type();
    Node*n=nnew(N_DECL);
    n->dnames=malloc(sizeof(char*)*64);
    n->dtypes=malloc(sizeof(char*)*64);
    n->dinit =malloc(sizeof(Node*)*64);
    do{
        char*name=take()->s;
        char*dty=ty;
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
    expect(";");
    return n;
}
static int is_decl_start(void){
    Token*t=cur();
    if(t->kind==TK_KW){
        static const char*dt[]={"int","char","void","unsigned","signed","struct","va_list",NULL};
        for(int i=0;dt[i];i++) if(!strcmp(t->s,dt[i])){
            /* struct X; 与 struct X f() 区分：先看后面 */
            return 1;
        }
    }
    return 0;
}

static Node* parse_stmt(void){
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
    if(accept("for")){
        expect("("); Node*n=nnew(N_FOR);
        if(!at(";")) n->init=is_decl_start()?parse_decl():parse_expr();
        expect(";");
        if(!at(";")) n->cond=parse_expr();
        expect(";");
        if(!at(")")) n->step=parse_expr();
        expect(")"); n->stmt=parse_stmt(); return n;
    }
    if(accept("return")){
        Node*n=nnew(N_RETURN);
        if(!at(";")) n->expr=parse_expr();
        expect(";"); return n;
    }
    if(accept("break")){ expect(";"); return nnew(N_BREAK); }
    if(accept("continue")){ expect(";"); return nnew(N_CONT); }
    if(is_decl_start()) return parse_decl();
    Node*n=nnew(N_EXPR); n->expr=parse_expr(); expect(";"); return n;
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
/* 真数组：带维度且不是"指向数组的指针"（int[3]* 是指针，大小仍是 9） */
static int is_array(const char*ty){
    return ty && strchr(ty,'[') && ty[strlen(ty)-1]!='*';
}
static const char* pointee(const char*ty);          /* forward */
static int ty_bytes(const char*ty);

static void struct_def(const char*name){
    StructDef sd={0}; sd.name=strdup(name);
    expect("{");
    int pos=0;
    while(!at("}")){
        char*mty=parse_type();
        char*mn=take()->s;
        while(accept("[")){                            /* 成员数组 */
            char buf[96]; snprintf(buf,sizeof(buf),"%s[%s]",mty,cur()->s);
            mty=strdup(buf); take(); expect("]");
        }
        int off=((pos+8)/9)*9;                          /* 成员起始 9 对齐 */
        pos=off+ty_bytes(mty);
        GROW(sd.m,sd.cap,sd.nm,Member);
        sd.m[sd.nm].name=mn; sd.m[sd.nm].ty=mty; sd.m[sd.nm].off=off; sd.nm++;
        expect(";");
    }
    expect("}"); expect(";");
    sd.size=((pos+8)/9)*9;
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
    StructDef*sd=struct_find(ty);
    if(sd) return sd->size;
    return 9;
}
static int ty_size(const char*ty,int *isarr){
    if(isarr) *isarr=is_array(ty)?1:0;
    return ty_bytes(ty);
}

static Node** FNS; static int NFN, CAPFN;

static void struct_def(const char*name);   /* forward：布局在类型系统一节实现 */

static void parse_program(void){
    while(!atk(TK_EOF)){
        if(accept("struct")){
            /* struct 定义：记录成员类型与 9 对齐偏移 */
            Token*nm=take();
            if(!at("{")){ fprintf(stderr,"UNSUPPORTED: struct as return type\n"); exit(2); }
            struct_def(nm->s);
            continue;
        }
        char*ret=parse_type();
        char*name=take()->s;
        expect("(");
        Node*f=nnew(N_FUNC); f->rettype=ret; f->s=name;
        f->ptypes=malloc(sizeof(char*)*32);
        f->params =malloc(sizeof(char*)*32);
        if(!at(")")){
            if(!(strcmp(ret,"void")==0&&at("void"))){
                do{
                    if(accept("...")){ f->variadic=1; break; }
                    char*pt=parse_type(); char*pn=take()->s;
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
    load_seg(reg, segs[ns-1]);
    for(int i=ns-2;i>=0;i--){
        emitf("SLLI %s, %s, 20",regname(reg),regname(reg));
        load_seg(SCRATCH, segs[i]);
        emitf("ADD %s, %s, %s",regname(reg),regname(reg),regname(SCRATCH));
    }
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

static const char* binop_insn(const char*op){
    static const char*m[]={
        "+","ADD","-","SUB","*","MUL","/","DIV","%","REM",
        "&","AND","|","OR","^","XOR","<<","SLL",">>","SRL",NULL};
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
    else if(!strcmp(op,">=")){ emitf("%s %s, %s, %s",SL,D,A,B); emitf("XORI %s, %s, 1",D,D); }
    else if(!strcmp(op,">"))  emitf("%s %s, %s, %s",SL,D,B,A);
    else if(!strcmp(op,"<=")){ emitf("%s %s, %s, %s",SL,D,B,A); emitf("XORI %s, %s, 1",D,D); }
    else if(!strcmp(op,"==")){ emitf("SUB %s, %s, %s",D,A,B); emitf("SLTIU %s, %s, 1",D,D); }
    else if(!strcmp(op,"!=")){ emitf("SUB %s, %s, %s",D,A,B); emitf("SLTIU %s, %s, 1",D,D); emitf("XORI %s, %s, 1",D,D); }
    else too_deep(1);
}

/* 具名 syscall -> 编号 */
static int syscall_no(const char*name){
    static const char*sys[]={"write","read","exit","brk","getcwd","openat","close",
        "lseek","readv","writev","newfstatat","fstat","exit_group","set_tid_address",
        "futex","clock_gettime","sched_yield","rt_sigaction","rt_sigprocmask",
        "rt_sigreturn","uname","getpid","gettid","munmap","mmap","mprotect","getrandom",NULL};
    static const int num[]={64,63,93,214,17,56,57,62,65,66,79,80,94,96,98,113,124,
        134,135,139,160,172,178,215,222,226,278};
    for(int i=0;sys[i];i++) if(!strcmp(name,sys[i])) return num[i];
    return -1;
}

/* 求值 n，结果放在返回的寄存器里；进入时 TDN=d0，返回后 TDN=d0+1 且结果 = TPOOL[d0] */
/* ===================== 类型推导与左值寻址 ===================== */
static int eval(Node*n);          /* forward */

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
    case N_VAR: { LocVar*v=findloc(n->s); return v&&v->ty?v->ty:"int"; }
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
    case N_CALL:  return (char*)fn_ret_type(n->s);
    case N_BIN: {
        const char*op=n->s;
        char*lt=expr_type(n->l), *rt=expr_type(n->r);
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
    else emitf("LW %s, %d(%s)",regname(dst),off,regname(base));
}
static void store_mem(int src,int base,int off,const char*ty){
    chk_off(off);
    if(is_char_ty(ty)) emitf("SB %s, %d(%s)",regname(src),off,regname(base));
    else emitf("SW %s, %d(%s)",regname(src),off,regname(base));
}
/* 把"元素的起始地址"算进寄存器并返回 */
static int addr_of(Node*n){
    Node*lv = (n->k==N_ADDR)? n->e : n;
    if(lv->k==N_VAR){
        LocVar*v=findloc(lv->s);
        int r=tacq();
        if(!v){ emitf("ADDI %s, zero, 0",regname(r)); return r; }
        if(!v->spilled){
            fprintf(stderr,"UNSUPPORTED: address of register-allocated local '%s'\n",lv->s);
            exit(2);
        }
        if(is_ptr(v->ty)){                      /* 指针变量：s0+off 里存的是地址，取出来 */
            chk_off(v->off);
            emitf("LW %s, %d(s0)",regname(r),v->off);
            return r;
        }
        chk_off(v->off);
        emitf("ADDI %s, s0, %d",regname(r),v->off);
        return r;
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
    case N_NUM: {
        for(int i=0;i<CC_N;i++) if(CC_V[i]==n->numw) return CC_R[i];  /* 已预装：只读共享 */
        int r=tacq(); load_imm128(r,n->numw); return r;
    }
    case N_STR: { int r=tacq(); load_imm(r,n->straddr); return r; }
    case N_VAR: {
        LocVar*v=findloc(n->s);
        /* 数组名求值 = 数组首地址（C 的退化规则） */
        if(v&&is_array(v->ty)) return addr_of(n);
        /* 常驻寄存器里的变量直接返回其寄存器，省掉 `ADDI t, s, 0` 这次拷贝。
           结果因此是只读的，上级若要写必须先 dstof()。 */
        if(v&&!v->spilled&&v->reg>0) return v->reg;
        int r=tacq();
        if(!v) emitf("ADDI %s, zero, 0",regname(r));           /* 未知符号兜底 */
        else load_mem(r,8,v->off,v->ty);                       /* 按类型选 LB/LBU/LW */
        return r;
    }
    case N_UN: {
        int a=eval(n->e);
        int d=dstof(a);                                  /* -x 不能原地改写 x */
        if(!strcmp(n->s,"-")) emitf("SUB %s, zero, %s",regname(d),regname(a));
        else if(!strcmp(n->s,"!")) emitf("SLTIU %s, %s, 1",regname(d),regname(a));
        else if(!strcmp(n->s,"~")) emitf("TNOT %s, %s",regname(d),regname(a));
        return d;
    }
    case N_BIN: {
        const char*op=n->s;
        if(!strcmp(op,"&&")||!strcmp(op,"||")){          /* C 短路语义 */
            int a=eval(n->l);
            int d=dstof(a);
            emitf("SLTIU %s, %s, 1",regname(d),regname(a));
            emitf("XORI %s, %s, 1",regname(d),regname(d));
            char*shortl=newlbl("short"),*endl=newlbl("logic");
            emitf("%s %s, zero, %s", !strcmp(op,"&&")?"BEQ":"BNE", regname(d), shortl);
            int b=eval(n->r);
            int bd=dstof(b);
            emitf("SLTIU %s, %s, 1",regname(bd),regname(b));
            emitf("XORI %s, %s, 1",regname(bd),regname(bd));
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
        int a=eval(n->l);
        int b=eval(n->r);
        /* 目标寄存器优先复用本次表达式的私有临时；两侧都是常驻寄存器
           （`a * b` 这种纯变量运算）时才新取一个。 */
        int d = in_tpool(a)? a : (in_tpool(b)? b : tacq());
        if(d!=TPOOL[d0]){                    /* 维持"结果落在 TPOOL[d0]"的不变量 */
            emitf("ADDI %s, %s, 0",regname(TPOOL[d0]),regname(d));
            d=TPOOL[d0];
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
        int uns = ty_uns(lt)||ty_uns(rt);
        const char*ins=binop_insn(op);
        if(ins){
            if(uns&&!strcmp(ins,"DIV")) ins="DIVU";
            else if(uns&&!strcmp(ins,"REM")) ins="REMU";
            else if(!strcmp(ins,"SRL")&&!uns) ins="SRA";     /* 有符号右移用算术移位 */
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
                /* x = x op y（只取无符号/移位歧义的 + - & | ^） */
                if(r->k==N_BIN&&r->l&&r->l->k==N_VAR&&!strcmp(r->l->s,lv->s)&&r->r){
                    const char*op=r->s;
                    if(!strcmp(op,"+")||!strcmp(op,"-")||
                       !strcmp(op,"&")||!strcmp(op,"|")||!strcmp(op,"^")){
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
            if(l&&l->spilled){ store_mem(v,8,l->off,l->ty); }
            else if(l&&l->reg!=v) emitf("ADDI %s, %s, 0",regname(l->reg),regname(v));
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
    case N_PREINC: {
        Node*lv=n->e; int d=(int)n->num;
        if(lv->k==N_VAR){
            LocVar*l=findloc(lv->s);
            int r=tacq();
            if(l&&!l->spilled){
                emitf("ADDI %s, %s, %d",regname(l->reg),regname(l->reg),d);
                if(l->reg!=r) emitf("ADDI %s, %s, 0",regname(r),regname(l->reg));
            } else if(l){
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
        /* 1) 活跃临时全部落栈：被调者会破坏 t 系列寄存器。索引=深度，嵌套不会互相覆盖。 */
        for(int i=0;i<d0;i++) emitf("SW %s, %d(s0)",regname(TPOOL[i]),CSAVE_OFF+i*9);
        /* 快路径：实参全是常量或标量变量、不超过 8 个且非变参时，
           直接装进 a 寄存器，省掉"先写暂存区再读回"的两趟访存。 */
        int fast = (sn<0)&&nargs<=8&&!fn_is_variadic(n->s);
        for(int i=0;i<nargs&&fast;i++){
            Node*a=n->args[i];
            if(a->k==N_NUM) continue;
            if(a->k==N_VAR){ LocVar*v=findloc(a->s); if(v&&!is_array(v->ty)) continue; }
            fast=0;
        }
        if(fast){
            for(int i=0;i<nargs;i++){
                Node*a=n->args[i];
                if(a->k==N_NUM){ load_imm128(10+i,a->numw); continue; }
                LocVar*v=findloc(a->s);
                if(!v) emitf("ADDI a%d, zero, 0",i);
                else if(!v->spilled&&v->reg>0){ if(v->reg!=10+i) emitf("ADDI a%d, %s, 0",i,regname(v->reg)); }
                else { chk_off(v->off); emitf("LW a%d, %d(s0)",i,v->off); }
            }
            emitf("JAL ra, %s",n->s);
        } else {
            /* 2) 实参逐个求值后暂存（嵌套调用会破坏 a0-a7，故不能直接放进 a 寄存器） */
            for(int i=0;i<nargs;i++){
                int r=eval(n->args[i]);
                emitf("SW %s, %d(s0)",regname(r),ARG_OFF+i*9);
                TDN=d0;
            }
            /* 变参或被调函数形参超过 8 个：参数块在帧内连续排布，块基址进 t6。
               块放在帧内（而不是 sp 之下）是为了不与已求值的实参暂存区互相踩踏。 */
            int need_block = (sn<0) && (fn_is_variadic(n->s)||nargs>8);
            if(need_block) emitf("ADDI t6, s0, %d",ARG_OFF);
            for(int i=0;i<nargs&&i<8;i++) emitf("LW a%d, %d(s0)",i,ARG_OFF+i*9);
            if(sn>=0){ load_imm(A7,sn); emitf("ECALL"); }
            else emitf("JAL ra, %s",n->s);
        }
        /* 3) 恢复活跃临时，结果取回 */
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
        int d=dstof(pa);
        if(strcmp(ty,"int")) load_mem(d,pa,0,ty);
        else emitf("LW %s, 0(%s)",regname(d),regname(pa));
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
 * 比 "SLT t,i,n ; XORI t,t,1 ; BEQ t,zero,end" 少一条，循环每轮都省。 */
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
            if(v->spilled){ store_mem(r,8,v->off,v->ty); }
            else if(v->reg!=r) emitf("ADDI %s, %s, 0",regname(v->reg),regname(r));
            TDN=0;
        }
        break;
    case N_EXPR: TDN=0; eval(n->expr); TDN=0; break;
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
    case N_FOR: {
        TDN=0;
        if(n->init){ if(n->init->k==N_DECL) gen_stmt(n->init); else eval(n->init); }
        TDN=0;
        char*start=newlbl("for"),*cont=newlbl("cont"),*end=newlbl("end");
        lbl(start);
        if(n->cond) gen_brfalse(n->cond,end);
        GROW(LOOPEND,CAPLOOP,NLOOP,char*); GROW(LOOPCONT,CAPLOOPC,NLOOP,char*);
        LOOPEND[NLOOP]=end; LOOPCONT[NLOOP]=cont; NLOOP++;
        gen_stmt(n->stmt); lbl(cont);
        if(n->step){ TDN=0; eval(n->step); TDN=0; }
        NLOOP--;
        emitf("JAL zero, %s",start); lbl(end); break;
    }
    case N_RETURN:
        TDN=0;
        if(n->expr&&n->expr->k==N_CALL){        /* return f(...)：结果直接留在 a0 */
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
    case N_DEREF: collect_decls(n->e); break;
    case N_INDEX: collect_decls(n->l); collect_decls(n->r); break;
    case N_MEMBER: collect_decls(n->l); break;
    case N_CALL: for(int i=0;i<n->nargs;i++) collect_decls(n->args[i]); break;
    case N_VASTART: case N_VAARG: case N_VAEND: collect_decls(n->e); break;
    default: break;
    }
}

/* 判断表达式/语句里是否含"真实函数调用"（非 syscall）。syscall 用 ECALL 不改 ra。 */
static int is_syscall_name(const char*s){
    static const char*sys[]={"write","read","exit","brk","getcwd","openat","close",
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
    pp_set(!anycall&&!f->variadic, f->nparams);   /* 必须在寄存器分配之前定池 */
    for(int i=0;i<f->nparams;i++){
        LocVar*v=loc_new(); v->name=f->params[i]; v->ty=f->ptypes[i];
        int isarr; ty_size(v->ty,&isarr);
        /* 形参同理：char 类形参必须在入口按 tryte 归一化，故落栈后用 LB/LBU 读回。 */
        v->spilled = isarr || is_char_ty(v->ty);
        /* 叶子函数没有调用会踩 a0-a7，形参可以直接留在它到达时的寄存器里，
           省掉 `ADDI sN, aM, 0` 这次搬运。池从 a_nparams 起，不会与它们冲突。 */
        if(!anycall&&!isarr&&!is_char_ty(v->ty)&&!struct_find(v->ty)&&i<8){
            v->reg=10+i; reg_take(v->reg);
        }
    }
    if(f->variadic){ LocVar*vb=loc_new(); vb->name="__argbase"; vb->ty="int"; vb->spilled=1; }
    collect_decls(f->fbody);
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
    int sregs[8],ns=0;
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
    int call_bytes = hascall ? (6*9 + narg_slots*9) : 0;
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
    /* 第 9 个起的形参在参数块里：t6 + i*9 */
    for(int i=8;i<f->nparams;i++){
        LocVar*v=&LOC[i]; chk_off(i*9);
        int r=tacq(); emitf("LW %s, %d(t6)",regname(r),i*9);
        if(v->spilled) store_mem(r,8,v->off,v->ty);
        else emitf("ADDI %s, %s, 0",regname(v->reg),regname(r));
        TDN=0;
    }
    for(int i=0;i<CC_N;i++) load_imm128(CC_R[i],(__int128)CC_V[i]);   /* 大常量只装一次 */
    /* 参数装载（在帧建立后） */
    for(int i=0;i<f->nparams&&i<8;i++){
        LocVar*v=&LOC[i];
        if(v->spilled){
            /* 形参落栈时也按类型写：char 形参用 SB 把入参截到 1 tryte，
             * 后续 LB/LBU 读回时符号性才与 C 一致。 */
            if(is_char_ty(v->ty)){
                int r=tacq(); emitf("ADDI %s, a%d, 0",regname(r),i);
                store_mem(r,8,v->off,v->ty); TDN=0;
            } else { chk_off(v->off); emitf("SW a%d, %d(s0)",i,v->off); }
        }
        else if(v->reg!=10+i) emitf("ADDI %s, a%d, 0",regname(v->reg),i);
    }
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
static int find_label(const char*n){ for(int i=0;i<NLAB;i++) if(!strcmp(LABS[i].name,n)) return LABS[i].idx; return -1; }

static int need_reg(void){ return 0; }

/* 解析 "LUI t0, 9" / "ADDI a0, t1, 3" / "LW t0, 0(sp)" / "SW t2, 4(sp)" / "JAL ra, main" / "BEQ t0, zero, _L1" */
static uint32_t asm_line(const char*line,int idx){
    /* 去前导空格 */
    while(*line==' '||*line=='\t') line++;
    char op[32],r1[32],r2[32],r3[32];
    char b1[64],b2[64],b3[64];
    memset(b1,0,sizeof(b1));memset(b2,0,sizeof(b2));memset(b3,0,sizeof(b3));
    int nf=sscanf(line,"%31s",op); if(nf<1) return 0;
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
        if(!strcmp(op,"TNOT")) return encR(O_TLOGIC,2,0,0,find_reg(ops[1]),find_reg(ops[0]));
        int rd=find_reg(ops[0]),rs1=find_reg(ops[1]),rs2=find_reg(ops[2]);
        return encR(d->op,d->f2,rs2,rs1,d->fu,rd);
    }
    if(d->fmt==1){ /* I: op rd, rs1, imm  or  LW rd, imm(rs1) */
        int rd=find_reg(ops[0]);
        if(!strcmp(op,"LW")||!strcmp(op,"LH")||!strcmp(op,"LB")||!strcmp(op,"LBU")||!strcmp(op,"LHU")||!strcmp(op,"JALR")){
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
        if(!strcmp(op,"SW")||!strcmp(op,"SH")||!strcmp(op,"SB")){
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
            int lt=find_label(ops[1]); int imm=(lt>=0)?(lt-idx):atoi(ops[1]);
            return encU(O_JAL,imm,rd);
        }
        return encU(d->op,atoi(ops[1]),rd);
    }
    return 0;
}

/* ============ 窥孔优化：基本块内 ADDI 复制链传播 ============
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
static uint32_t* assemble_all(int*pn){
    NLAB=0;
    /* 第一遍：标签 -> 指令索引 */
    int idx=0;
    for(int i=0;i<NOUT;i++){
        char line[512]; strncpy(line,OUT[i],sizeof(line)-1); line[sizeof(line)-1]=0;
        char*s=line; while(*s==' '||*s=='\t')s++;
        if(*s&&strchr(s,':')){ char*colon=strchr(s,':'); *colon=0; add_label(s,idx); *colon=':';
            /* 该行只有标签 */
            char*t=colon+1; while(*t==' ')t++; if(*t) idx++;
        } else if(*s) idx++;
    }
    *pn=idx;
    uint32_t*prog=calloc(idx?idx:1,sizeof(uint32_t));
    idx=0;
    for(int i=0;i<NOUT;i++){
        char line[512]; strncpy(line,OUT[i],sizeof(line)-1); line[sizeof(line)-1]=0;
        char*s=line; while(*s==' '||*s=='\t')s++;
        if(*s&&strchr(s,':')){ char*colon=strchr(s,':'); *colon=':';
            char*t=colon+1; while(*t==' ')t++; if(!*t) continue; s=t;
        }
        if(*s){ prog[idx]=asm_line(s,idx); idx++; }
    }
    return prog;
}

/* 分配字符串常量地址 */
static void assign_strings(Node*n){
    if(!n) return;
    if(n->k==N_STR){ n->straddr=STRBASE; STRBASE+=n->slen+1; return; }  /* 段数据含结尾 NUL */
    Node*all[]={n->l,n->r,n->e,n->cond,n->then,n->els,n->init,n->step,n->stmt,n->expr};
    for(int i=0;i<10;i++) assign_strings(all[i]);
    if(n->body) for(int i=0;i<n->nitems;i++) assign_strings(n->body[i]);
    if(n->args) for(int i=0;i<n->nargs;i++) assign_strings(n->args[i]);
    if(n->items) for(int i=0;i<n->nitems;i++) assign_strings(n->items[i]);
    if(n->dinit) for(int i=0;i<n->ndecls;i++) assign_strings(n->dinit[i]);
}
static void register_strings(Node*n){
    if(!n) return;
    if(n->k==N_STR){
        GROW(STRSEG,CAPSTR,NSTR,char*); GROW(STRLEN,CAPSTRLEN,NSTR,int);
        {   /* C 字符串以 NUL 结尾：段数据补一个 0 */ 
            char*buf=malloc((size_t)n->slen+1);
            memcpy(buf,n->s,(size_t)n->slen); buf[n->slen]=0;
            STRSEG[NSTR]=buf; STRLEN[NSTR]=n->slen+1; NSTR++;
        }
        return;
    }
    Node*all[]={n->l,n->r,n->e,n->cond,n->then,n->els,n->init,n->step,n->stmt,n->expr};
    for(int i=0;i<10;i++) register_strings(all[i]);
    if(n->body) for(int i=0;i<n->nitems;i++) register_strings(n->body[i]);
    if(n->args) for(int i=0;i<n->nargs;i++) register_strings(n->args[i]);
    if(n->items) for(int i=0;i<n->nitems;i++) register_strings(n->items[i]);
    if(n->dinit) for(int i=0;i<n->ndecls;i++) register_strings(n->dinit[i]);
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
static const char* GATE_WHY;

static void gate_check(Node*n){
    if(!n||GATE_WHY) return;
    switch(n->k){
    case N_DECL:
        for(int i=0;i<n->ndecls;i++) gate_check(n->dinit[i]);
        return;
    case N_INDEX:  gate_check(n->l); gate_check(n->r); return;
    case N_MEMBER: gate_check(n->l); return;
    case N_CALL:

        for(int i=0;i<n->nargs;i++) gate_check(n->args[i]);
        return;
    case N_BLOCK:  for(int i=0;i<n->nitems;i++) gate_check(n->body[i]); return;
    case N_IF:     gate_check(n->cond); gate_check(n->then); gate_check(n->els); return;
    case N_WHILE:  gate_check(n->cond); gate_check(n->stmt); return;
    case N_FOR:    gate_check(n->init); gate_check(n->cond); gate_check(n->step); gate_check(n->stmt); return;
    case N_RETURN: gate_check(n->expr); return;
    case N_EXPR:   gate_check(n->expr); return;
    case N_BIN:    gate_check(n->l); gate_check(n->r); return;
    case N_UN:     gate_check(n->e); return;
    case N_ASSIGN: gate_check(n->l); gate_check(n->r); return;
    case N_PREINC: gate_check(n->e); return;
    case N_DEREF:  gate_check(n->e); return;
    case N_ADDR:   gate_check(n->e); return;
    case N_VASTART: case N_VAARG: case N_VAEND: gate_check(n->e); return;
    default: return;
    }
}

static void put_u32(FILE*f,uint32_t v){ fputc(v&0xff,f);fputc((v>>8)&0xff,f);fputc((v>>16)&0xff,f);fputc((v>>24)&0xff,f); }

int main(int argc,char**argv){
    if(argc<3){ fprintf(stderr,"usage: %s <src.c> <out.trv> [out.asm]\n",argv[0]); return 1; }
    FILE*fp=fopen(argv[1],"r"); if(!fp){perror("open");return 1;}
    fseek(fp,0,SEEK_END); long sz=ftell(fp); fseek(fp,0,SEEK_SET);
    char*src=malloc(sz+1); fread(src,1,sz,fp); src[sz]=0; fclose(fp);
    lex(src); IPOS=0;
    parse_program();
    /* 能力闸门：未实现特性直接拒绝，交由统一后端回退到 Python 参考实现 */
    for(int i=0;i<NFN&&!GATE_WHY;i++){

        for(int k=0;k<FNS[i]->nparams;k++){
            if(ty_is_struct_val(FNS[i]->ptypes[k])){ GATE_WHY="struct param by value (未实现)"; break; }
        }
        if(ty_is_struct_val(FNS[i]->rettype)){ GATE_WHY="struct return by value (未实现)"; break; }
        gate_check(FNS[i]->fbody);
    }
    if(GATE_WHY){ fprintf(stderr,"UNSUPPORTED: %s\n",GATE_WHY); return 2; }
    /* 函数名表 */
    for(int i=0;i<NFN;i++){
        GROW(FNRETS,CAPFNRET,NFNRET,char*); GROW(FNVARGS,CAPFNVARGS,NFNRET,int);
        GROW(FNEYS,CAPFNEYS,NFNRET,char*);
        FNRETS[NFNRET]=FNS[i]->rettype; FNVARGS[NFNRET]=FNS[i]->variadic; FNEYS[NFNRET]=FNS[i]->s; NFNRET++;
    }
    /* 字符串地址分配（全局扫描所有函数体） */
    for(int i=0;i<NFN;i++){ assign_strings(FNS[i]->fbody); register_strings(FNS[i]->fbody); }
    NOUT=0; LBL=0;
    /* _start */
    emitf("_start:");
    emitf("JAL ra, main");
    emitf("ADDI a7, zero, 93");
    emitf("ECALL");
    for(int i=0;i<NFN;i++){ emitf("%s:",FNS[i]->s); gen_func(FNS[i]); }
    drop_jump_to_next();
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

    /* 写 TRV1 */
    FILE*out=fopen(argv[2],"wb"); if(!out){perror("out");return 1;}
    fwrite("TRV1",1,4,out);
    put_u32(out,1); put_u32(out,n); put_u32(out,NSTR);
    for(int i=0;i<n;i++) put_u32(out,prog[i]);
    /* 字符串段：每个地址一个 segment */
    {
        int addr=100;
        for(int i=0;i<NSTR;i++){
            put_u32(out,addr); put_u32(out,STRLEN[i]);
            fwrite(STRSEG[i],1,STRLEN[i],out);
            addr+=STRLEN[i];
        }
    }
    fclose(out);
    fprintf(stderr,"[trirv_cc.c] compiled %s: %d instructions, %d strings -> %s\n",argv[1],n,NSTR,argv[2]);
    return 0;
}


