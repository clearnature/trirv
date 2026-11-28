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

typedef struct {
    ClassInfo *classes[S_MAX_CLASSES];     /* 键表（key=info->key） */
    int nclasses;
    Node *funcs[S_MAX_FUNCS];              /* NK_FUNCDECL 链头数组（扁平） */
    int nfuncs;
    Node *decls_list;                      /* 全类链（含模板实例注入） */
    int ndefls;
    Node *templates[S_MAX_CLASSES];        /* NK_TFUNC 链 */
    int ntemplates;
    Node *class_templates[S_MAX_CLASSES];  /* NK_TCLASS */
    int nclass_templates;
    char *cls_insts[S_MAX_CLASSES];        /* 已实例化类键 */
    int ncls_insts;
    Node *cur_cls;                         /* NK_CLASSDECL（检查上下文） */
    char *cur_ns;
    char *cls_keys[S_MAX_CLASSES];         /* 类模板惰性实例化登记 */
} Sema;

void sema_init(Sema *S);
void sema_build_classes(Sema *S, Node *classes);
void sema_build_funcs(Sema *S, Node *funcs);
const char *sema_resolve_class(Sema *S, const char *name, const char *ns);
ClassInfo *sema_find_class(Sema *S, const char *key);
void sema_error(const char *code, const char *msg) __attribute__((noreturn));

#endif /* CPP_SEMA_H */
