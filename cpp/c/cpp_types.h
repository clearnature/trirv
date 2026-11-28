/* cpp_types.h —— C 移植类型层 ADT（0.1.2）。
 * 对齐 cpp/src/cpp_types.py：非法组合在构造层不可表达——
 * TypeError_ 对应 C 侧 ctype_fail()（编译器不变式违反=abort）。
 * L7：常量集中 cpp_c_constants.h。 */
#ifndef CPP_TYPES_H
#define CPP_TYPES_H

#include <stddef.h>

typedef enum {
    TY_VOID, TY_INT, TY_CHAR, TY_BOOL, TY_CLASS, TY_REF, TY_PTR
} CTypeKind;

typedef struct CType {
    CTypeKind kind;
    int is_const;            /* REF: 派生自 pointee（不可独立设） */
    struct CType *pointee;   /* REF/PTR（owned） */
    char *name;              /* CLASS（owned，可含 :: 限定键） */
} CType;

/* 构造函数（不变式在此强制） */
CType *ctype_int(int is_const);
CType *ctype_char(int is_const);
CType *ctype_bool(int is_const);
CType *ctype_void(void);                 /* const void 不可表达（无形参） */
CType *ctype_class(const char *name, int is_const);   /* 匿名类不可表达 */
CType *ctype_ref(CType *pointee);        /* ref-of-ref 不可表达 */
CType *ctype_ptr(CType *pointee);

/* 投影（lower/mangle 边界层专用；库内一律保持 CType*） */
const char *ctype_kind_name(CType *ty);
const char *type_name(CType *ty);        /* 'int'/'char'/'bool'/'void'/类名 */
CType *ctype_copy(CType *ty);            /* 深拷贝（模板 _subst 用） */
int ctype_equal(CType *a, CType *b);     /* 结构相等 */
int ctype_is_ref(CType *ty);

/* 不变式违反出口（编译器 bug=abort，非用户错误） */
void ctype_fail(const char *msg) __attribute__((noreturn));

/* 自检（0.1.2 TDD：ADT 不变式 7 项 Python 版的 C 镜像） */
int ctype_selftest(void);

#endif /* CPP_TYPES_H */
