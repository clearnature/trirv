/* cpp_types.c —— 类型层 ADT 实现（0.1.2）。
 * 不变式（构造层不可表达，对齐 cpp_types.py ADT 纪律）：
 *   I1 ref-of-ref 拒绝（C++ 禁）
 *   I2 const void 不可表达（ctype_void 无形参）
 *   I3 匿名类拒绝（name 非空非空串）
 *   I4 REF is_const=pointee 派生 property，顶层 setter 拒绝
 *   I5 kind 投影仅边界层（type_name），库内保持 CType* */
#include "cpp_types.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <unistd.h>
#include <signal.h>
#include <sys/wait.h>

void ctype_fail(const char *msg) {
    fprintf(stderr, "type invariant violated: %s\n", msg);
    abort();
}

static CType *ctype_new(CTypeKind k) {
    CType *t = calloc(1, sizeof(CType));
    if (!t) ctype_fail("oom");
    t->kind = k;
    return t;
}

CType *ctype_int(int is_const) {
    CType *t = ctype_new(TY_INT);
    t->is_const = !!is_const;
    return t;
}

CType *ctype_char(int is_const) {
    CType *t = ctype_new(TY_CHAR);
    t->is_const = !!is_const;
    return t;
}

CType *ctype_bool(int is_const) {
    CType *t = ctype_new(TY_BOOL);
    t->is_const = !!is_const;
    return t;
}

CType *ctype_void(void) {
    return ctype_new(TY_VOID);            /* I2：无 const 形参 */
}

CType *ctype_class(const char *name, int is_const) {
    if (!name || !name[0])                /* I3：匿名类不可表达 */
        ctype_fail("anonymous class");
    CType *t = ctype_new(TY_CLASS);
    t->is_const = !!is_const;
    t->name = strdup(name);
    return t;
}

CType *ctype_ref(CType *pointee) {
    if (!pointee) ctype_fail("ref of NULL");
    if (pointee->kind == TY_REF)          /* I1：ref-of-ref 不可表达 */
        ctype_fail("ref-of-ref");
    CType *t = ctype_new(TY_REF);
    t->pointee = ctype_copy(pointee);
    t->is_const = pointee->is_const;      /* I4：派生 property */
    return t;
}

CType *ctype_ptr(CType *pointee) {
    if (!pointee) ctype_fail("ptr of NULL");
    CType *t = ctype_new(TY_PTR);
    t->pointee = ctype_copy(pointee);
    return t;
}

const char *ctype_kind_name(CType *ty) {
    switch (ty->kind) {
    case TY_VOID:  return "void";
    case TY_INT:   return "int";
    case TY_CHAR:  return "char";
    case TY_BOOL:  return "bool";
    case TY_CLASS: return "class";
    case TY_REF:   return "ref";
    case TY_PTR:   return "ptr";
    }
    ctype_fail("unknown kind");
}

const char *type_name(CType *ty) {
    if (ty->kind == TY_REF || ty->kind == TY_PTR)
        return type_name(ty->pointee);    /* 边界投影：解包 */
    if (ty->kind == TY_CLASS)
        return ty->name;
    return ctype_kind_name(ty);
}

CType *ctype_copy(CType *ty) {
    CType *t = ctype_new(ty->kind);
    t->is_const = ty->is_const;
    if (ty->pointee) t->pointee = ctype_copy(ty->pointee);
    if (ty->name) t->name = strdup(ty->name);
    return t;
}

int ctype_equal(CType *a, CType *b) {
    if (a->kind != b->kind || a->is_const != b->is_const) return 0;
    if (a->name && b->name) {
        if (strcmp(a->name, b->name)) return 0;
    } else if (a->name || b->name) {
        return 0;
    }
    if (a->pointee && b->pointee)
        return ctype_equal(a->pointee, b->pointee);
    return (a->pointee == NULL) == (b->pointee == NULL);
}

int ctype_is_ref(CType *ty) { return ty->kind == TY_REF; }

/* ---- 自检：ADT 不变式（Python 版 7 项的 C 镜像）---- */
static int st_pass = 0, st_fail = 0;
#define ST_CHECK(cond, tag) do { \
    if (cond) { st_pass++; } \
    else { st_fail++; printf("ST_FAIL %s\n", tag); } \
} while (0)

static void expect_abort(void (*fn)(void), const char *tag) {
    /* 不变式违反必须 abort——fork 子进程验证（防真 abort 带崩主测） */
    pid_t pid = fork();
    if (pid == 0) { fn(); _exit(0); }      /* 未 abort=不变式失效 */
    int st = 0;
    waitpid(pid, &st, 0);
    ST_CHECK(WIFSIGNALED(st) && WTERMSIG(st) == SIGABRT, tag);
}

static void f_ref_of_ref(void) { CType *r = ctype_ref(ctype_int(0));
    ctype_ref(r); (void)r; }
static void f_anon_class(void) { ctype_class("", 0); }

int ctype_selftest(void) {
    /* 1 基本型构造+相等 */
    CType *a = ctype_int(0), *b = ctype_int(0), *c = ctype_int(1);
    ST_CHECK(ctype_equal(a, b) && !ctype_equal(a, c), "int-eq");
    /* 2 void 无 const 形参 */
    CType *v = ctype_void();
    ST_CHECK(v->kind == TY_VOID && !v->is_const, "void-noconst");
    /* 3 匿名类拒绝 */
    expect_abort(f_anon_class, "anon-class-abort");
    /* 4 ref-of-ref 拒绝 */
    expect_abort(f_ref_of_ref, "ref-ref-abort");
    /* 5 ref const 派生自 pointee */
    CType *rc = ctype_ref(ctype_int(1));
    ST_CHECK(rc->is_const == 1, "ref-const-derived");
    /* 6 kind 投影（边界层） */
    CType *box = ctype_class("geo::Box", 0);
    CType *rb = ctype_ref(box);
    ST_CHECK(!strcmp(type_name(rb), "geo::Box"), "kind-proj");
    /* 7 结构相等含 ref/ptr 嵌套 */
    CType *p1 = ctype_ptr(ctype_class("Box", 0));
    CType *p2 = ctype_ptr(ctype_class("Box", 0));
    ST_CHECK(ctype_equal(p1, p2), "deep-eq");
    printf("types 自检：%d PASS / %d FAIL\n", st_pass, st_fail);
    return st_fail ? 1 : 0;
}
