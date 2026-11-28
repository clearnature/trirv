/* cpp_sema.h —— C 移植语义层（0.1.5 S1：Sema 态/ClassInfo/
 * build_classes 三 Phase/build_funcs）。诊断码与 Python 版恒等（E0/K3...），
 * 文案类别恒等。 */
#ifndef CPP_SEMA_H
#define CPP_SEMA_H

#include "cpp_ast.h"
#include "cpp_types.h"

#define S_MAX_CLASSES 256
#define S_MAX_FUNCS 256
#define S_MAX_MEMBERS 64

typedef struct ClassInfo {
    char *key, *name;
    char *base;                            /* 位 0 基类键 */
    char *bases[S_MAX_MEMBERS];            /* 多基键（声明序） */
    int nbases;
    struct { char *name; CType *ty; int acc; } mems[S_MAX_MEMBERS];
    int nmems;
    struct { char *name; CType *ty; int acc; } statics[S_MAX_MEMBERS];
    int nstatics;
    Node *methods;                         /* 声明序链（NK_METHOD） */
    Node *ctors;                           /* 链（NK_CTOR） */
    Node *dtor;                            /* NK_DTOR | NULL */
    char *vslots[S_MAX_MEMBERS];
    int nvslots;
    int has_op_new, has_op_delete;
    int incomplete;
} ClassInfo;


typedef struct Scope Scope;
struct ScopeEnt { char *name; CType *ty; };
struct Scope {
    Scope *parent;
    struct ScopeEnt vars[64];
    int nvars;
};

typedef struct {
    ClassInfo *classes[S_MAX_CLASSES];
    int nclasses;
    Node *funcs[S_MAX_FUNCS];
    int nfuncs;
    Node *decls_list;
    int ndefls;
    Node *templates[S_MAX_CLASSES];
    int ntemplates;
    Node *class_templates[S_MAX_CLASSES];
    int nclass_templates;
    char *cls_insts[S_MAX_CLASSES];
    int ncls_insts;
    Node *cur_cls;
    char *cur_ns;
    Node *cur_method;                      /* 检查上下文（NK_METHOD） */
    int eh_flag, cur_catch;                /* EH 门控/catch 深度（0.0.35/36） */
    Node *fn_dtors;                        /* RAII 对链（NK_INIT） */
    Node *gdecls;                          /* 全局链（NK_VARDECL） */
    struct ScopeEnt gvars[64]; int ngvars;
    struct { char *cls, *nm; CType *ty; } sdefs[64]; int nsdefs;
} Sema;

void sema_init(Sema *S);
void sema_build_classes(Sema *S, Node *classes);
void sema_build_funcs(Sema *S, Node *funcs);
const char *sema_resolve_class(Sema *S, const char *name, const char *ns);
ClassInfo *sema_find_class(Sema *S, const char *key);
void sema_error(const char *code, const char *msg) __attribute__((noreturn));
extern Sema *GS;
void sema_check_all(Sema *S, Node *tu);
void sema_check_stmts(Sema *S, Node *ss, Scope *sc, int is_const);    /* check 主循环（0.1.5 S2） */
extern Sema *GS;                           /* rw 家族全局上下文（单 TU） */
Node *sema_chain_impl(Sema *S, const char *ckey, const char *slot);
void sema_fn_dtor_push(Sema *S, const char *var, const char *ckey);
void sema_guard_ptr_int(Sema *S, CType *lt, CType *rt, Node *rhs);
int sema_is_abstract(Sema *S, const char *ckey);
CType *sema_check_expr(Sema *S, Node *e, Scope *sc, int is_const);
void sema_check_args(Sema *S, Node *args, Node *meth, Scope *sc, int is_const);
CType *sema_sc_find(Scope *sc, const char *name);
void sema_sc_declare(Scope *sc, const char *name, CType *ty);

#endif /* CPP_SEMA_H */
