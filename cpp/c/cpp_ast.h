/* cpp_ast.h —— C 移植 AST（0.1.4）。通用槽 tagged union（cfront 式）：
 * k 决定各槽语义。对齐 cpp_parser.py 30+ 节点类的字段映射：
 *   Bin:s=op a=l b=r      Un:s=op a=e          Assign:a=lhs b=rhs
 *   If:a=cond b=then c=els  While:a=cond b=body  For:a=init b=cond c=step d=body
 *   Member:a=obj s=name f1=arrow   MethodCall:a=obj s=name items=args
 *   FreeCall:s=name items=args targs=types/ntargs(-1=无)
 *   New:s=cls items=args   CopyInit:s=cls s2=var a=init
 *   VarDecl:s=name ty a=init   CtorStmt:s=cls s2=var items=args
 *   Return/Throw:a=expr(NULL=缺)  ExprStmt:a  Block:items=stmts
 *   NamedCast:s=kind ty a=expr  DeleteStmt:a=expr
 *   Try:a=try_body items=N_CATCHARM(ty,s=cvar,a=block;catch(...)=ty NULL)
 *   N_DECL(局部):s=name ty a=init | N_CTORSTMT | N_EXPRSTMT
 *   FuncDecl:s=name ty=ret items=N_PARAM(ty,s) c=defaults b=body
 *            f1=c_link s2=ns
 *   Method:s=name ty=ret items=params c=defaults b=body
 *           f1=ACC|F_CONST|F_STATIC|F_VIRTUAL|F_PURE s2=is_special
 *   Ctor:items=params c=inits(N_INIT:s=tgt,items=args) b=body f1=explicit
 *   Dtor:b=body
 *   ClassDecl:s=name s2=base_child? bases=child 链(N_BASE:s)
 *             members=child 链（N_MEMBERDECL/N_STATDECL/NK_METHOD/
 *             NK_CTOR/NK_DTOR）ns/nested_in 记 f 槽外字串 s_ns
 *   TypeSeq:targs=types/ntargs（模板实参） */
#ifndef CPP_AST_H
#define CPP_AST_H

#include "cpp_types.h"

typedef enum {
    NK_NUM, NK_STRLIT, NK_ID, NK_THIS, NK_MEMBER, NK_METHODCALL,
    NK_FREECALL, NK_NEW, NK_DELETESTMT, NK_COPYINIT, NK_NAMEDCAST,
    NK_THROW, NK_TRY, NK_BIN, NK_UN, NK_ASSIGN, NK_VARDECL,
    NK_CTORSTMT, NK_EXPRSTMT, NK_BLOCK, NK_RETURN, NK_IF, NK_WHILE,
    NK_FOR, NK_CLASSDECL, NK_FUNCDECL, NK_METHOD, NK_CTOR, NK_DTOR,
    NK_TFUNC, NK_TCLASS, NK_PARAM, NK_INIT, NK_CATCHARM, NK_BASE,
    NK_MEMBERDECL, NK_STATDECL
} NKind;

/* 访问级（Method/MemberDecl f1 低 2 位） */
enum { ACC_PUBLIC = 0, ACC_PRIVATE = 1, ACC_PROTECTED = 2 };
/* 方法/构造标志位（f1 高位） */
enum { F_CONST = 4, F_STATIC = 8, F_VIRTUAL = 16, F_PURE = 32,
       F_EXPLICIT = 64 };

typedef struct Node Node;
struct Node {
    NKind k;
    int line;
    long num;
    int f1, f2;
    char *s, *s2;
    CType *ty;
    CType **targs;
    int ntargs;               /* -1=无显式模板实参 */
    Node *a, *b, *c, *d;
    Node *items;              /* 子链（next 链接） */
    int nitems;
    Node *next;               /* 兄弟链 */
    char *ns;                 /* 类/函数命名空间（0.0.13） */
    char *nested_in;          /* 外层类键（0.0.22） */
    int forward;              /* 前向声明 */
};

Node *ast_new(NKind k, int line);
Node *ast_chain(Node *head, Node *item);   /* items 链追加，返回新头计数无效 */
int ast_count(Node *chain);
const char *acc_name(int f1);              /* ACC_* → "public"/... */

/* 解析器（cpp_parser_*.c） */
Node *cpp_parse_tu(const char *src, int srclen, int *has_main);
                                           /* 失败=stderr+exit(2)；
                                              返回 TU 根（NK_BLOCK 形态：
                                              a=classes 链 b=funcs 链
                                              c=globals 链 d=sdefs 链
                                              child=templates 链，
                                              num=has_main 恒 1） */
Node *cpp_parse_stmt_pub(void);            /* stmt 入口（内部包装用） */
extern int PP_ERR;                          /* 解析错误标志（exit 前=1） */

#endif /* CPP_AST_H */
