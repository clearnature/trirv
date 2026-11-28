/* cpp_sema_tmpl_check.c —— S3：函数模板实例化全套（0.1.5）。
 * instantiate=deepcopy+subst+重 check；FreeCall 模板分支=显式实参/
 * 按参数位推导（一致性 E0）。 */
#include "cpp_sema.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

Node *ast_deep_copy(Node *n);
CType *sema_check_expr(Sema *, Node *, Scope *, int);
void sema_check_stmts(Sema *, Node *, Scope *, int);
extern Sema *GS;

static CType *ty_flat_parse(const char *a) {
    /* 平化名反解（_tflat）：int/size_t/char/bool */
    if (!strcmp(a, "int") || !strcmp(a, "size_t")) return ctype_int(0);
    if (!strcmp(a, "char")) return ctype_char(0);
    if (!strcmp(a, "bool")) return ctype_bool(0);
    return NULL;
}

/* 实例名（{name}__{t1}__{t2}——::→_ 平化） */
static void inst_name_of(char *out, size_t cap, const char *fname,
                         CType **concs, int n) {
    snprintf(out, cap, "%s", fname);
    for (int i = 0; i < n; i++) {
        char seg[256];
        snprintf(seg, sizeof(seg), "%s", type_name(concs[i]));
        for (char *q = seg; *q; q++) if (*q == ':') *q = '_';
        strncat(out, "__", cap - strlen(out) - 1);
        strncat(out, seg, cap - strlen(out) - 1);
    }
}

/* subst_stmt：VarDecl.ty / CtorStmt.cls / New.cls / Try 遍历（同前缀限） */
static void subst_stmt_r(Sema *S, Node *s, const char *tvar, CType *conc,
                         const char *prefix, const char *inst_flat);

static void subst_expr_r(Sema *S, Node *e, const char *tvar, CType *conc,
                         const char *prefix, const char *inst_flat) {
    if (!e) return;
    if (e->k == NK_NEW && prefix && !strcmp(e->s, prefix) &&
        !strcmp(e->s, prefix)) {
        /* New 类名替换：cls == "{prefix}__{tvar}" → inst_flat */
    }
    if (e->k == NK_NEW && prefix) {
        char want[256];
        snprintf(want, sizeof(want), "%s__%s", prefix, tvar);
        if (!strcmp(e->s, want)) e->s = strdup(inst_flat);
    }
    if (e->k == NK_BIN) { subst_expr_r(S, e->a, tvar, conc, prefix, inst_flat);
                          subst_expr_r(S, e->b, tvar, conc, prefix, inst_flat); }
    if (e->k == NK_UN) subst_expr_r(S, e->a, tvar, conc, prefix, inst_flat);
    if (e->k == NK_ASSIGN) { subst_expr_r(S, e->a, tvar, conc, prefix, inst_flat);
                             subst_expr_r(S, e->b, tvar, conc, prefix, inst_flat); }
    if (e->k == NK_METHODCALL) { subst_expr_r(S, e->a, tvar, conc, prefix, inst_flat);
        for (Node *a = e->items; a; a = a->next) subst_expr_r(S, a, tvar, conc, prefix, inst_flat); }
    if (e->k == NK_FREECALL) {
        char want[256];
        snprintf(want, sizeof(want), "%s__%s", prefix, tvar);
        if (prefix && !strcmp(e->s, want)) e->s = strdup(inst_flat);
        for (Node *a = e->items; a; a = a->next) subst_expr_r(S, a, tvar, conc, prefix, inst_flat);
    }
    if (e->k == NK_MEMBER) subst_expr_r(S, e->a, tvar, conc, prefix, inst_flat);
    if (e->k == NK_NAMEDCAST) subst_expr_r(S, e->a, tvar, conc, prefix, inst_flat);
}

static void subst_stmt_r(Sema *S, Node *s, const char *tvar, CType *conc,
                         const char *prefix, const char *inst_flat) {
    if (!s) return;
    switch (s->k) {
    case NK_VARDECL:
        if (s->ty && s->ty->kind == TY_CLASS && !strcmp(s->ty->name, tvar))
            s->ty = ctype_copy(conc);
        if (s->a) subst_expr_r(S, s->a, tvar, conc, prefix, inst_flat);
        break;
    case NK_CTORSTMT: {
        char want[256];
        snprintf(want, sizeof(want), "%s__%s", prefix, tvar);
        if (prefix && !strcmp(s->s, want)) s->s = strdup(inst_flat);
        for (Node *a = s->items; a; a = a->next)
            subst_expr_r(S, a, tvar, conc, prefix, inst_flat);
        break;
    }
    case NK_EXPRSTMT: subst_expr_r(S, s->a, tvar, conc, prefix, inst_flat); break;
    case NK_RETURN:   if (s->a) subst_expr_r(S, s->a, tvar, conc, prefix, inst_flat); break;
    case NK_IF:
        subst_expr_r(S, s->a, tvar, conc, prefix, inst_flat);
        subst_stmt_r(S, s->b, tvar, conc, prefix, inst_flat);
        if (s->c) subst_stmt_r(S, s->c, tvar, conc, prefix, inst_flat);
        break;
    case NK_WHILE:
        subst_expr_r(S, s->a, tvar, conc, prefix, inst_flat);
        subst_stmt_r(S, s->b, tvar, conc, prefix, inst_flat);
        break;
    case NK_FOR:
        if (s->a) subst_stmt_r(S, s->a, tvar, conc, prefix, inst_flat);
        if (s->b) subst_expr_r(S, s->b, tvar, conc, prefix, inst_flat);
        if (s->c) subst_expr_r(S, s->c, tvar, conc, prefix, inst_flat);
        subst_stmt_r(S, s->d, tvar, conc, prefix, inst_flat);
        break;
    case NK_BLOCK:
        for (Node *x = s->items; x; x = x->next)
            subst_stmt_r(S, x, tvar, conc, prefix, inst_flat);
        break;
    case NK_TRY:
        subst_stmt_r(S, s->a, tvar, conc, prefix, inst_flat);
        for (Node *arm = s->items; arm; arm = arm->next)
            subst_stmt_r(S, arm->b, tvar, conc, prefix, inst_flat);
        break;
    case NK_NAMEDCAST: subst_expr_r(S, s->a, tvar, conc, prefix, inst_flat); break;
    case NK_DELETESTMT: subst_expr_r(S, s->a, tvar, conc, prefix, inst_flat); break;
    default: break;
    }
}

/* 方法/函数体级 subst：ret/params/defaults + 体 */
static void subst_decl(Sema *S, Node *m, const char *tvar, CType *conc,
                       const char *prefix, const char *inst_flat) {
    if (m->ty && m->ty->kind == TY_CLASS && !strcmp(m->ty->name, tvar))
        m->ty = ctype_copy(conc);
    for (Node *p = m->items; p; p = p->next)
        if (p->ty && p->ty->kind == TY_CLASS && !strcmp(p->ty->name, tvar))
            p->ty = ctype_copy(conc);
    for (Node *ss = m->b; ss; ss = ss->next)
        subst_stmt_r(S, ss, tvar, conc, prefix, inst_flat);
}

/* 函数模板实例化（S3）：deepcopy+subst+重 check+funcs 注册。
 * 返回实例 NK_TFUNC/MethodDecl 形态节点（funcs 链成员）。 */
Node *sema_instantiate_func(Sema *S, Node *tmpl, CType **concs, int nconcs,
                            const char *fname) {
    char inst_name[256];
    inst_name_of(inst_name, sizeof(inst_name), fname, concs, nconcs);
    for (int i = 0; i < S->nfuncs; i++)
        if (S->funcs[i]->mname && !strcmp(S->funcs[i]->mname, inst_name))
            return S->funcs[i];                /* 缓存复用（每类型一实例） */
    Node *clone = ast_deep_copy(tmpl->b);      /* TFUNC.b = decl */
    clone->s = strdup(inst_name);
    clone->ns = NULL;
    Node *tv = tmpl->c;                        /* tvars 链（NK_PARAM: s=name） */
    for (int i = 0; i < nconcs && tv; i++, tv = tv->next) {
        subst_decl(S, clone, tv->s, concs[i], fname, inst_name);
    }
    clone->mname = strdup(inst_name);          /* 约定名（单态化） */
    /* 重 check（cur_method=实例，scope 声明 params） */
    Node *saved_cls = S->cur_cls;
    const char *saved_ns = S->cur_ns;
    S->cur_cls = NULL; S->cur_ns = NULL;
    Scope *sc = calloc(1, sizeof(Scope));
    for (Node *p = clone->items; p; p = p->next)
        sema_sc_declare(sc, p->s, p->ty);
    S->fn_dtors = NULL;
    sema_check_stmts(S, clone->b, sc, 0);
    S->cur_cls = saved_cls; S->cur_ns = saved_ns;
    if (S->nfuncs < S_MAX_FUNCS) S->funcs[S->nfuncs++] = clone;
    return clone;
}
