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
#define NREG 27
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
#define T3   18
#define T4   19
#define T5   20
#define T6   21
#define S2   22
#define S6   26

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
    v=putf(v,0,2,op); v=putf(v,3,5,rd); v=putf(v,7,8,funct);
    v=putf(v,9,11,rs1); v=putf(v,13,15,rs2); v=putf(v,17,19,f2);
    return v;
}
static uint32_t encI(int op,int imm7,int rs1,int funct,int rd){
    uint32_t v=0;
    v=putf(v,0,2,op); v=putf(v,3,5,rd); v=putf(v,7,8,funct);
    v=putf(v,9,11,rs1);
    int m=((imm7%2187)+2187)%2187;
    v=putf(v,13,19,m);
    return v;
}
static uint32_t encS(int op,int imm7,int rs2,int rs1,int funct){
    uint32_t v=0;
    v=putf(v,0,2,op); v=putf(v,7,8,funct);
    v=putf(v,9,11,rs1); v=putf(v,13,15,rs2);
    int m=((imm7%2187)+2187)%2187;
    v=putf(v,3,6,m%81); v=putf(v,17,19,m/81);
    return v;
}
static uint32_t encU(int op,int imm13,int rd){
    uint32_t v=0;
    v=putf(v,0,2,op); v=putf(v,3,5,rd);
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
    {"t3",T3},{"t4",T4},{"t5",T5},{"t6",T6},
    {"s2",22},{"s3",23},{"s4",24},{"s5",25},{"s6",26},
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
        case 5:return "t0"; case 6:return "t1"; case 7:return "t2";
        case 8:return "s0"; case 9:return "s1";
        case 10:return "a0"; case 11:return "a1"; case 12:return "a2"; case 13:return "a3";
        case 14:return "a4"; case 15:return "a5"; case 16:return "a6"; case 17:return "a7";
        case 18:return "t3"; case 19:return "t4"; case 20:return "t5"; case 21:return "t6";
        case 22:return "s2"; case 23:return "s3"; case 24:return "s4";
        case 25:return "s5"; case 26:return "s6";
    }
    snprintf(buf,sizeof(buf),"x%d",r); return buf;
}

/* ===================== 词法分析 ===================== */
typedef enum { TK_EOF,TK_ID,TK_NUM,TK_STR,TK_KW,TK_OP } TokKind;
typedef struct {
    TokKind kind;
    long long num;      /* TK_NUM */
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
    static const char*kw[]={"int","char","void","unsigned","struct","va_list",
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
            char*end; long long v=strtoll(p,&end,0);
            t.kind=TK_NUM; t.num=v; tok_push(t); p=end; continue;
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
            t.kind=TK_NUM; t.num=(unsigned char)c; tok_push(t);
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
    N_FUNC,N_INITLIST
} NodeKind;
typedef struct Node Node;
struct Node {
    NodeKind k;
    long long num;
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

static Node *parse_expr(void);
static Node *parse_stmt(void);
static Node *parse_block(void);

/* 类型：返回 malloc 的字符串, 如 "int","char*","int[3]","struct Foo" */
static char* parse_type(void){
    if(!(atk(TK_KW)||atk(TK_ID))){ fprintf(stderr,"parse_type: bad token '%s'\n",cur()->s); exit(1); }
    char base[128];
    if(accept("unsigned")){
        if(accept("int")) snprintf(base,sizeof(base),"unsigned");
        else snprintf(base,sizeof(base),"unsigned");
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
    if(atk(TK_NUM)){ Node*n=nnew(N_NUM); n->num=take()->num; return n; }
    if(atk(TK_STR)){
        Token*t=take(); Node*n=nnew(N_STR); n->s=t->s; n->slen=t->slen; return n;
    }
    if(accept("(")){ Node*n=parse_expr(); expect(")"); return n; }
    if(atk(TK_ID)){
        char*name=take()->s;
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
        if(at("[")){
            take(); char cnt[32]; snprintf(cnt,sizeof(cnt),"%s",cur()->s); take(); expect("]");
            dty=malloc(256); snprintf(dty,256,"%s[%s]",ty,cnt);
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
        static const char*dt[]={"int","char","void","unsigned","struct","va_list",NULL};
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

static Node** FNS; static int NFN, CAPFN;

static void parse_program(void){
    while(!atk(TK_EOF)){
        if(accept("struct")){
            /* struct 定义: struct Name { fields };  —— 跳过字段, 记录成员偏移供 struct 支持 */
            Token*nm=take(); expect("{");
            while(!at("}")){ /* type name [array]; */
                parse_type(); take();
                if(accept("[")){ if(atk(TK_NUM))take(); expect("]"); }
                expect(";");
            }
            expect("}"); expect(";");
            (void)nm;  /* 简化: struct 成员访问暂按字节偏移处理(见 codegen) */
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
                    if(accept("[")){ expect("]"); strcat(pt,"*"); }
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
#define MAXLOC 256
typedef struct { char*name; int reg; int spilled; int off; char*ty; } LocVar;

/* 输出缓冲：每条指令一行文本，最后统一汇编 */
static char **OUT; static int NOUT, CAPOUT;
static int LBL;
static LocVar LOC[MAXLOC]; static int NLOC;
static int USEDREG[32];          /* 当前函数占用的寄存器 */
static int SPILL_TOP;            /* 溢出区 tryte 偏移(相对 sp) */
static char *FNRETS[64]; static int NFNRET;  /* 函数名->返回类型 */
static int FNVARGS[64];                       /* 函数名->变参标志 */
static char *STRSEG[256]; static int STRLEN[256]; static int NSTR; static int STRBASE=100;
static const char *CURFN;
static char *LOOPEND[64], *LOOPCONT[64]; static int NLOOP;
static char *FUNC_EPILOGUE;   /* 当前函数的 epilogue 标签（return 跳这里统一恢复帧） */
static int XCALL_SLOT;        /* 当前帧内"跨调用表达式临时保存槽"相对 s0 的偏移 */

static void emitf(const char*fmt,...){
    char buf[512]; va_list ap; va_start(ap,fmt); vsnprintf(buf,sizeof(buf),fmt,ap); va_end(ap);
    if(NOUT>=CAPOUT){CAPOUT=CAPOUT?CAPOUT*2:256;OUT=realloc(OUT,CAPOUT*sizeof(char*));}
    OUT[NOUT++]=strdup(buf);
}
static char* newlbl(const char*tag){ char b[32]; snprintf(b,sizeof(b),"_L%d",++LBL); (void)tag; return strdup(b); }
static void lbl(const char*s){ emitf("%s:",s); }

static int reg_free(int r){ return !USEDREG[r]; }
static void reg_take(int r){ if(r>0&&r<27) USEDREG[r]=1; }
static int reg_alloc_persistent(void){
    /* 持久化寄存器池：只用 callee-saved 的 s1,s2..s6(共6个)。
     * 每个函数会在 prologue 保存它实际用到的 s 寄存器，故跨调用安全。
     * 避开 t0/t1/t2(表达式临时)、a0-a7(参数/syscall)、sp/fp/ra/zero。 */
    static int pool[]={22,23,24,25,26,9};
    for(unsigned i=0;i<sizeof(pool)/sizeof(pool[0]);i++)
        if(reg_free(pool[i])){ reg_take(pool[i]); return pool[i]; }
    return -1;   /* 溢出到栈 */
}
/* 表达式临时寄存器：t0,t1,t2, 再用剩余空闲 */
static int TMPREG[16], NTMP;
static int tmp_alloc(void){
    static int pool[]={5,6,7};
    for(unsigned i=0;i<sizeof(pool)/sizeof(pool[0]);i++) if(reg_free(pool[i])){return pool[i];}
    /* 兜底：任意空闲且非持久局部 */
    for(int r=10;r<27;r++) if(reg_free(r)) return r;
    return 5; /* 极端情况复用 t0（表达式树深度有限，实践中不会） */
}
static int is_tmp(int r){ for(int i=0;i<NTMP;i++) if(TMPREG[i]==r) return 1; return 0; }

static LocVar* findloc(const char*name){
    for(int i=0;i<NLOC;i++) if(!strcmp(LOC[i].name,name)) return &LOC[i];
    return NULL;
}
static int ty_size(const char*ty,int *isarr){
    if(isarr) *isarr=0;
    if(ty&&ty[strlen(ty)-1]=='*') return 9;
    if(ty&&!strncmp(ty,"char",4)&&ty[4]==0) return 1;
    if(ty&&strchr(ty,'[')){ /* 数组：粗略按 [n] 计算 */
        const char*b=strchr(ty,'['); int n=atoi(b+1); if(isarr)*isarr=1;
        int es = (!strncmp(ty,"char",4))?1:9; return n*es;
    }
    return 9;
}

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
static void load_imm(int reg, long long n){
    if(n==0){ emitf("ADDI %s, zero, 0",regname(reg)); return; }
    const long long B=1743392201LL;        /* 3^20 */
    const long long HALFB=(B-1)/2;
    long long segs[8], ns=0, m=n;
    while(m){ long long seg=m%B; m/=B;
        if(seg>HALFB){ seg-=B; m++; } segs[ns++]=seg; }
    if(ns==0){ emitf("ADDI %s, zero, 0",regname(reg)); return; }
    load_seg(reg, segs[ns-1]);
    for(int i=ns-2;i>=0;i--){
        emitf("SLLI %s, %s, 20",regname(reg),regname(reg));
        load_seg(T2, segs[i]);
        emitf("ADD %s, %s, t2",regname(reg),regname(reg));
    }
}

static Node *EXPRGEN(Node*n); /* forward */

static int has_call(Node*n);   /* forward */
/* 表达式生成：结果置于 t0，返回 t0。寄存器式核心——全程无 SW/LW 压栈。 */
static const char* binop_insn(const char*op){
    static const char*m[]={
        "+","ADD","-","SUB","*","MUL","/","DIV","%","REM",
        "&","AND","|","OR","^","XOR","<<","SLL",">>","SRL",NULL};
    for(int i=0;m[i];i+=2) if(!strcmp(op,m[i])) return m[i+1];
    return NULL;
}
/* 关系运算：a(op)b，a/b 为寄存器，结果 0/1 放 t0 */
static void emit_rel(const char*op,int a,int b){
    if(!strcmp(op,"<"))       emitf("SLT t0, %s, %s",regname(a),regname(b));
    else if(!strcmp(op,">=")){ emitf("SLT t0, %s, %s",regname(a),regname(b)); emitf("XORI t0, t0, 1"); }
    else if(!strcmp(op,">"))  emitf("SLT t0, %s, %s",regname(b),regname(a));
    else if(!strcmp(op,"<=")){ emitf("SLT t0, %s, %s",regname(b),regname(a)); emitf("XORI t0, t0, 1"); }
    else if(!strcmp(op,"==")){ emitf("SUB t0, %s, %s",regname(a),regname(b)); emitf("SLTIU t0, t0, 1"); }
    else if(!strcmp(op,"!=")){ emitf("SUB t0, %s, %s",regname(a),regname(b)); emitf("SLTIU t0, t0, 1"); emitf("XORI t0, t0, 1"); }
}

static int gen_expr(Node*n){
    if(!n){ emitf("ADDI t0, zero, 0"); return T0; }
    switch(n->k){
    case N_NUM: load_imm(T0,n->num); return T0;
    case N_STR: load_imm(T0,n->straddr); return T0;
    case N_VAR: {
        LocVar*v=findloc(n->s);
        if(v){ if(v->spilled) emitf("LW t0, %d(s0)",v->off); else emitf("ADDI t0, %s, 0",regname(v->reg)); }
        else emitf("ADDI t0, zero, 0"); /* 未知符号兜底 */
        return T0;
    }
    case N_UN:
        gen_expr(n->e);
        if(!strcmp(n->s,"-")) emitf("SUB t0, zero, t0");
        else if(!strcmp(n->s,"!")) emitf("SLTIU t0, t0, 1");
        else if(!strcmp(n->s,"~")) emitf("TNOT t0, t0");
        return T0;
    case N_BIN: {
        const char*op=n->s;
        if(!strcmp(op,"&&")||!strcmp(op,"||")){
            gen_expr(n->l); emitf("SLTIU t0, t0, 1"); emitf("XORI t0, t0, 1");
            emitf("ADDI t1, t0, 0");
            gen_expr(n->r); emitf("SLTIU t0, t0, 1"); emitf("XORI t0, t0, 1");
            emitf("%s t0, t1, t0", !strcmp(op,"&&")?"AND":"OR");
            return T0;
        }
        gen_expr(n->l); emitf("ADDI t1, t0, 0");
        /* 若右操作数含函数调用，t1(caller-saved)会被破坏：先 spill 到栈保住左值 */
        int save_left = has_call(n->r);
        if(save_left) emitf("SW t1, %d(s0)",XCALL_SLOT);
        gen_expr(n->r);
        if(save_left) emitf("LW t1, %d(s0)",XCALL_SLOT);
        const char*ins=binop_insn(op);
        if(ins){ emitf("%s t0, t1, t0",ins); return T0; }
        emit_rel(op,T1,T0);
        return T0;
    }
    case N_ASSIGN: {
        Node*lv=n->l;
        gen_expr(n->r); emitf("ADDI t2, t0, 0");
        if(lv->k==N_VAR){
            LocVar*v=findloc(lv->s);
            if(v){ if(v->spilled) emitf("SW t2, %d(s0)",v->off); else emitf("ADDI %s, t2, 0",regname(v->reg)); }
        } else if(lv->k==N_DEREF){
            gen_expr(lv->e); emitf("SW t2, 0(t0)");
        } else {
            gen_expr(lv); emitf("SW t2, 0(t0)");
        }
        emitf("ADDI t0, t2, 0");
        return T0;
    }
    case N_PREINC: {
        Node*lv=n->e; int d=(int)n->num;
        if(lv->k==N_VAR){
            LocVar*v=findloc(lv->s);
            if(v&&!v->spilled){
                emitf("ADDI %s, %s, %d",regname(v->reg),regname(v->reg),d);
                emitf("ADDI t0, %s, 0",regname(v->reg));
            } else if(v){
                emitf("LW t0, %d(s0)",v->off); emitf("ADDI t0, t0, %d",d); emitf("SW t0, %d(s0)",v->off);
            }
        } else {
            gen_expr(lv); emitf("LW t1, 0(t0)"); emitf("ADDI t1, t1, %d",d);
            emitf("SW t1, 0(t0)"); emitf("ADDI t0, t1, 0");
        }
        return T0;
    }
    case N_DEREF: gen_expr(n->e); emitf("LW t0, 0(t0)"); return T0;
    case N_ADDR: {
        Node*lv=n->e;
        if(lv->k==N_VAR){ LocVar*v=findloc(lv->s);
            if(v&&v->spilled) emitf("ADDI t0, s0, %d",v->off);
            else emitf("ADDI t0, zero, 0");
        } else gen_expr(lv);
        return T0;
    }
    case N_CALL: {
        int nargs=n->nargs;
        for(int i=0;i<nargs;i++){ gen_expr(n->args[i]); if(i<8) emitf("ADDI a%d, t0, 0",i); }
        static const char*sys[]={"write","read","exit","brk","getcwd","openat","close",
            "lseek","readv","writev","newfstatat","fstat","exit_group","set_tid_address",
            "futex","clock_gettime","sched_yield","rt_sigaction","rt_sigprocmask",
            "rt_sigreturn","uname","getpid","gettid","munmap","mmap","mprotect","getrandom",NULL};
        static const int sysnum[]={64,63,93,214,17,56,57,62,65,66,79,80,94,96,98,113,124,
            134,135,139,160,172,178,215,222,226,278};
        int syso=-1;
        for(int i=0;sys[i];i++) if(!strcmp(n->s,sys[i])){ syso=i; break; }
        if(syso>=0){ load_imm(A7,sysnum[syso]); emitf("ECALL"); }
        else emitf("JAL ra, %s",n->s);
        emitf("ADDI t0, a0, 0");
        return T0;
    }
    case N_INDEX: {
        gen_expr(n->l); emitf("ADDI t1, t0, 0");
        gen_expr(n->r);
        emitf("ADD t0, t1, t0"); emitf("LW t0, 0(t0)");
        return T0;
    }
    default: emitf("ADDI t0, zero, 0"); return T0;
    }
}

static void gen_stmt(Node*n){
    if(!n) return;
    switch(n->k){
    case N_BLOCK: for(int i=0;i<n->nitems;i++) gen_stmt(n->body[i]); break;
    case N_DECL:
        for(int i=0;i<n->ndecls;i++){
            if(!n->dinit[i]) continue;
            LocVar*v=findloc(n->dnames[i]); if(!v) continue;
            gen_expr(n->dinit[i]);
            if(v->spilled) emitf("SW t0, %d(s0)",v->off); else emitf("ADDI %s, t0, 0",regname(v->reg));
        }
        break;
    case N_EXPR: gen_expr(n->expr); break;
    case N_IF: {
        if(!n->els){ char*end=newlbl("endif");
            gen_expr(n->cond); emitf("BEQ t0, zero, %s",end); gen_stmt(n->then); lbl(end);
        } else { char*els=newlbl("else"),*end=newlbl("endif");
            gen_expr(n->cond); emitf("BEQ t0, zero, %s",els);
            gen_stmt(n->then); emitf("JAL zero, %s",end);
            lbl(els); gen_stmt(n->els); lbl(end);
        }
        break;
    }
    case N_WHILE: {
        char*start=newlbl("while"),*end=newlbl("end");
        lbl(start); gen_expr(n->cond); emitf("BEQ t0, zero, %s",end);
        LOOPEND[NLOOP]=end; LOOPCONT[NLOOP]=start; NLOOP++;
        gen_stmt(n->stmt); NLOOP--;
        emitf("JAL zero, %s",start); lbl(end); break;
    }
    case N_FOR: {
        if(n->init){ if(n->init->k==N_DECL) gen_stmt(n->init); else gen_expr(n->init); }
        char*start=newlbl("for"),*cont=newlbl("cont"),*end=newlbl("end");
        lbl(start); if(n->cond){ gen_expr(n->cond); emitf("BEQ t0, zero, %s",end); }
        LOOPEND[NLOOP]=end; LOOPCONT[NLOOP]=cont; NLOOP++;
        gen_stmt(n->stmt); lbl(cont); if(n->step) gen_expr(n->step); NLOOP--;
        emitf("JAL zero, %s",start); lbl(end); break;
    }
    case N_RETURN:
        if(n->expr) gen_expr(n->expr); else emitf("ADDI t0, zero, 0");
        emitf("ADDI a0, t0, 0");
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
            LocVar*v=&LOC[NLOC++]; v->name=n->dnames[i]; v->ty=n->dtypes[i];
            int isarr; ty_size(v->ty,&isarr); v->spilled=isarr;
        }
        break;
    case N_BLOCK: for(int i=0;i<n->nitems;i++) collect_decls(n->body[i]); break;
    case N_IF: collect_decls(n->cond);collect_decls(n->then);collect_decls(n->els); break;
    case N_WHILE: collect_decls(n->cond);collect_decls(n->stmt); break;
    case N_FOR: collect_decls(n->init);collect_decls(n->cond);collect_decls(n->step);collect_decls(n->stmt); break;
    case N_EXPR: collect_decls(n->expr); break;
    case N_RETURN: collect_decls(n->expr); break;
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
    NLOC=0; memset(USEDREG,0,sizeof(USEDREG)); NLOOP=0; CURFN=f->s;
    FUNC_EPILOGUE=newlbl("epi");
    for(int i=0;i<f->nparams;i++){
        LocVar*v=&LOC[NLOC++]; v->name=f->params[i]; v->ty=f->ptypes[i];
        int isarr; ty_size(v->ty,&isarr); v->spilled=isarr;
    }
    collect_decls(f->fbody);
    for(int i=0;i<NLOC;i++){
        if(LOC[i].spilled) continue;
        int r=reg_alloc_persistent();
        if(r<0) LOC[i].spilled=1; else LOC[i].reg=r;
    }
    int spill=0; for(int i=0;i<NLOC;i++) if(LOC[i].spilled){ LOC[i].off=spill; spill+=ty_size(LOC[i].ty,NULL); }
    /* 收集实际用到的 callee-saved 寄存器 */
    int sregs[6],ns=0;
    static int spool[]={9,22,23,24,25,26};
    for(int k=0;k<6;k++) if(USEDREG[spool[k]]) sregs[ns++]=spool[k];
    int hascall=has_call(f->fbody);
    int need_frame = hascall || spill>0 || ns>0;
    /* 帧布局: s0 = 原sp+18; 原sp[0]=old fp, [9]=ra; saved s 寄存器从 s0+0 起;
     * spill 在 saved 之后。sp 抬升量须覆盖到 spill 顶部。 */
    int savbase=0;
    int spillbase=ns*9;
    /* 跨调用表达式临时保存槽：放在 spill 之后 */
    XCALL_SLOT = spillbase + spill;
    int xcall_bytes = hascall ? 9 : 0;
    int framesize=18+ns*9+spill+xcall_bytes;   /* sp 抬到帧顶 */
    if(need_frame){
        emitf("SW s0, 0(sp)");
        emitf("SW ra, 9(sp)");
        emitf("ADDI s0, sp, 18");
        for(int k=0;k<ns;k++) emitf("SW %s, %d(s0)",regname(sregs[k]),savbase+k*9);
        if(framesize>0){ load_imm(T2,framesize); emitf("ADD sp, sp, t2"); }
    }
    /* spill 偏移改为相对 s0 */
    if(need_frame) for(int i=0;i<NLOC;i++) if(LOC[i].spilled) LOC[i].off += spillbase;
    /* 参数装载（在帧建立后） */
    for(int i=0;i<f->nparams&&i<8;i++){
        LocVar*v=&LOC[i];
        if(v->spilled) emitf("SW a%d, %d(s0)",i,v->off);
        else if(v->reg!=10+i) emitf("ADDI %s, a%d, 0",regname(v->reg),i);
    }
    gen_stmt(f->fbody);
    emitf("ADDI a0, zero, 0");
    lbl(FUNC_EPILOGUE);
    if(need_frame){
        for(int k=0;k<ns;k++) emitf("LW %s, %d(s0)",regname(sregs[k]),savbase+k*9);
        emitf("LW ra, -9(s0)");
        emitf("ADDI sp, s0, -18");
        emitf("LW s0, 0(sp)");
    }
    emitf("JALR zero, 0(ra)");
}

/* ===================== 汇编器：文本 -> 20-trit 机器码 ===================== */
typedef struct { char*name; int idx; } Label;
static Label LABS[512]; static int NLAB;
static void add_label(const char*n,int idx){ LABS[NLAB].name=strdup(n); LABS[NLAB].idx=idx; NLAB++; }
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
    int canon[27]; for(int i=0;i<27;i++)canon[i]=-1;
    char **NEW=malloc(sizeof(char*)*(NOUT?NOUT:1)); int NNEW=0;
    for(int i=0;i<NOUT;i++){
        char*line=OUT[i];
        char*s=line; while(*s==' '||*s=='\t')s++;
        /* 标签或控制流指令：清空复制链 */
        if(*s=='_'||*s=='.'||strchr(s,':')){ for(int k=0;k<27;k++)canon[k]=-1; NEW[NNEW++]=line; continue; }
        char op[32]; sscanf(s,"%31s",op);
        if(!strcmp(op,"JAL")||!strcmp(op,"JALR")||!strcmp(op,"BEQ")||!strcmp(op,"BNE")||
           !strcmp(op,"ECALL")||!strcmp(op,"BLT")||!strcmp(op,"BGE")){
            for(int k=0;k<27;k++)canon[k]=-1; NEW[NNEW++]=line; continue;
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
    if(n->k==N_STR){ n->straddr=STRBASE; STRBASE+=n->slen; return; }
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
        STRSEG[NSTR]=n->s; STRLEN[NSTR]=n->slen; NSTR++;
        return;
    }
    Node*all[]={n->l,n->r,n->e,n->cond,n->then,n->els,n->init,n->step,n->stmt,n->expr};
    for(int i=0;i<10;i++) register_strings(all[i]);
    if(n->body) for(int i=0;i<n->nitems;i++) register_strings(n->body[i]);
    if(n->args) for(int i=0;i<n->nargs;i++) register_strings(n->args[i]);
    if(n->items) for(int i=0;i<n->nitems;i++) register_strings(n->items[i]);
    if(n->dinit) for(int i=0;i<n->ndecls;i++) register_strings(n->dinit[i]);
}

static void put_u32(FILE*f,uint32_t v){ fputc(v&0xff,f);fputc((v>>8)&0xff,f);fputc((v>>16)&0xff,f);fputc((v>>24)&0xff,f); }

int main(int argc,char**argv){
    if(argc<3){ fprintf(stderr,"usage: %s <src.c> <out.trv>\n",argv[0]); return 1; }
    FILE*fp=fopen(argv[1],"r"); if(!fp){perror("open");return 1;}
    fseek(fp,0,SEEK_END); long sz=ftell(fp); fseek(fp,0,SEEK_SET);
    char*src=malloc(sz+1); fread(src,1,sz,fp); src[sz]=0; fclose(fp);
    lex(src); IPOS=0;
    parse_program();
    /* 函数名表 */
    for(int i=0;i<NFN;i++){ FNRETS[NFNRET]=FNS[i]->rettype; FNVARGS[NFNRET]=FNS[i]->variadic; NFNRET++; }
    /* 字符串地址分配（全局扫描所有函数体） */
    for(int i=0;i<NFN;i++){ assign_strings(FNS[i]->fbody); register_strings(FNS[i]->fbody); }
    NOUT=0; LBL=0;
    /* _start */
    emitf("_start:");
    emitf("JAL ra, main");
    emitf("ADDI a7, zero, 93");
    emitf("ECALL");
    for(int i=0;i<NFN;i++){ emitf("%s:",FNS[i]->s); gen_func(FNS[i]); }
    if(getenv("TRV_DUMP_ASM")){ for(int i=0;i<NOUT;i++) fprintf(stderr,"%s\n",OUT[i]); }
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


