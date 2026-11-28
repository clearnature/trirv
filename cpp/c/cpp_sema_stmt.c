/* cpp_sema_stmt.c —— S2c：check 主循环/check_stmt 全分支/check_return/
 * rewrite_implicit_this（0.1.5）。诊断码 E0/N8/K1/K2/R1 与 Python 恒等。 */
#include "cpp_sema.h"
#include "cpp_mangle.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

CType *sema_check_expr(Sema *, Node *, Scope *, int);
void sema_check_stmt(Sema *, Node *, Scope *, int);
void sema_check_args(Sema *, Node *args, Node *meth, Scope *, int is_const);

/* ---- Scope ---- */
static Scope *scope_new(Scope *parent) {
    Scope *sc = calloc(1, sizeof(Scope));
    sc->parent = parent;
    return sc;
}

CType *sema_sc_find(Scope *sc, const char *name) {
    for (Scope *s = sc; s; s = s->parent)
        for (int i = 0; i < s->nvars; i++)
            if (!strcmp(s->vars[i].name, name)) return s->vars[i].ty;
    return NULL;
}

void sema_sc_declare(Scope *sc, const char *name, CType *ty) {
    if (sc->nvars < 64) {
        sc->vars[sc->nvars].name = strdup(name);
        sc->vars[sc->nvars].ty = ty;
        sc->nvars++;
    }
}

/* ---- rewrite_implicit_this：裸 name(args) 在方法内= this 调用 ---- */
extern Sema *GS;   /* 全局当前 Sema（单 TU 单线程——rw 家族上下文） */
static int name_in_visible(ClassInfo *ci, const char *name) {
    for (; ci; ) {
        for (Node *m = ci->methods; m; m = m->next)
            if (!strcmp(m->s, name)) return 1;
        ci = sema_find_class(GS, ci->base);
    }
    return 0;
}

static void rw_expr(Node *e, ClassInfo *vis_ci);
static void rw_stmt(Node *s, ClassInfo *vis_ci) {
    if (!s) return;
    switch (s->k) {
    case NK_VARDECL: if (s->a) rw_expr(s->a, vis_ci); break;
    case NK_CTORSTMT:
        for (Node *a = s->items; a; a = a->next) rw_expr(a, vis_ci);
        break;
    case NK_EXPRSTMT: rw_expr(s->a, vis_ci); break;
    case NK_RETURN:   if (s->a) rw_expr(s->a, vis_ci); break;
    case NK_ASSIGN:
        if (s->a->k == NK_FREECALL && vis_ci &&
            name_in_visible(vis_ci, s->a->s)) {
            Node *mc = ast_new(NK_METHODCALL, s->line);
            mc->s = strdup(s->a->s); mc->items = s->a->items;
            Node *th = ast_new(NK_THIS, s->line);
            mc->a = th;
            s->a = mc;
        } else rw_expr(s->a, vis_ci);
        rw_expr(s->b, vis_ci);
        break;
    case NK_IF:
        rw_expr(s->a, vis_ci); rw_stmt(s->b, vis_ci);
        if (s->c) rw_stmt(s->c, vis_ci);
        break;
    case NK_WHILE:
        rw_expr(s->a, vis_ci); rw_stmt(s->b, vis_ci);
        break;
    case NK_FOR:
        if (s->a) rw_stmt(s->a, vis_ci);
        if (s->b) rw_expr(s->b, vis_ci);
        if (s->c) rw_expr(s->c, vis_ci);
        rw_stmt(s->d, vis_ci);
        break;
    case NK_BLOCK:
        for (Node *x = s->items; x; x = x->next) rw_stmt(x, vis_ci);
        break;
    default: break;
    }
}

static void rw_expr(Node *e, ClassInfo *vis_ci) {
    if (!e) return;
    switch (e->k) {
    case NK_BIN:  rw_expr(e->a, vis_ci); rw_expr(e->b, vis_ci); break;
    case NK_UN:   rw_expr(e->a, vis_ci); break;
    case NK_ASSIGN:
        if (e->a->k == NK_FREECALL && vis_ci &&
            name_in_visible(vis_ci, e->a->s)) {
            Node *mc = ast_new(NK_METHODCALL, e->line);
            mc->s = strdup(e->a->s); mc->items = e->a->items;
            Node *th = ast_new(NK_THIS, e->line);
            mc->a = th;
            e->a = mc;
        } else rw_expr(e->a, vis_ci);
        rw_expr(e->b, vis_ci);
        break;
    case NK_MEMBER: rw_expr(e->a, vis_ci); break;
    case NK_METHODCALL:
        rw_expr(e->a, vis_ci);
        for (Node *a = e->items; a; a = a->next) rw_expr(a, vis_ci);
        break;
    case NK_FREECALL:
        for (Node *a = e->items; a; a = a->next) rw_expr(a, vis_ci);
        break;
    case NK_NAMEDCAST: rw_expr(e->a, vis_ci); break;
    default: break;
    }
}

void sema_rewrite_this(Sema *S, Node *body, const char *cls_key) {
    ClassInfo *ci = sema_find_class(S, cls_key);
    if (!ci) return;
    rw_stmt(body, ci);
}

/* ---- check_return：R1/K1（引用返回面）---- */
static void check_return(Sema *S, Node *meth, Node *expr) {
    if (!meth || !expr) return;
    if (meth->ty->kind == TY_REF) {
        if (expr->sty->kind == TY_REF) return;         /* 引用转发 */
        if (!expr->lval)
            sema_error("R1", "引用返回绑非左值——临时量物化=0.0.9 生命周期扩展");
        if (meth->f1 & F_CONST)
            sema_error("K1", "const 方法引用返回绑自身成员——写通道穿透 const");
    }
    /* VALUE 返回 ← REF 表达式：自动解引用（lower 落 *(…)） */
}

/* ---- 语句检查（全分支）---- */
void sema_check_stmt(Sema *S, Node *s, Scope *sc, int is_const) {
    switch (s->k) {
    case NK_VARDECL: {
        if (s->ty->kind == TY_REF) {
            if (!s->a) sema_error("E0", "引用声明需初始化器");
            sema_check_expr(S, s->a, sc, is_const);
            if (!s->ty->pointee->is_const && !s->a->lval &&
                s->a->sty->kind != TY_REF)
                sema_error("N8", "非 const 引用绑临时量——C++ 法：保持拒绝");
        } else if (s->a) {
            CType *it = sema_check_expr(S, s->a, sc, is_const);
            sema_guard_ptr_int(S, s->ty, it, s->a);   /* 审计 F6 */
        }
        sema_sc_declare(sc, s->s, s->ty);
        break;
    }
    case NK_CTORSTMT: {
        const char *ckey = sema_resolve_class(S, s->s, S->cur_ns);
        if (!ckey) sema_error("E0", "类未定义");
        if (sema_is_abstract(S, ckey))
            sema_error("E0", "抽象类不可实例化（含未覆盖的纯虚函数）");
        s->s = strdup(ckey);
        Node *hit = NULL; int nhit = 0;
        int nargs = ast_count(s->items);
        for (Node *c = sema_find_class(S, ckey)->ctors; c; c = c->next) {
            int n = ast_count(c->items);
            if (n == nargs || (n > nargs && c->c)) { hit = c; nhit++; }
        }
        if (nhit > 1) sema_error("E0", "构造歧义");
        if (!hit) sema_error("E0", "无匹配参构造");
        for (Node *a = s->items; a; a = a->next)
            sema_check_expr(S, a, sc, is_const);
        sema_sc_declare(sc, s->s2, ctype_class(ckey, 0));
        if (sema_find_class(S, ckey)->dtor)
            sema_fn_dtor_push(S, s->s2, ckey);     /* 0.0.37 RAII 追踪 */
        break;
    }
    case NK_THROW:
        S->eh_flag = 1;
        if (!s->a) {
            if (S->cur_catch == 0)
                sema_error("E0", "bare rethrow 须在 catch 内");
            break;
        }
        sema_check_expr(S, s->a, sc, is_const);
        if (s->a->sty->kind == TY_CLASS && s->a->lval)
            s->throw_cls = strdup(s->a->sty->name);  /* 0.0.38 类对象抛出 */
        else if (s->a->sty->kind != TY_INT && s->a->sty->kind != TY_CHAR)
            sema_error("E0", "throw 操作数限 int/char/类左值");
        break;
    case NK_TRY: {
        S->eh_flag = 1;
        sema_check_stmts(S, s->a, scope_new(sc), is_const);
        S->cur_catch++;
        for (Node *arm = s->items; arm; arm = arm->next) {
            Scope *csc = scope_new(sc);
            if (!arm->ty) {                        /* catch(...) */
                sema_check_stmts(S, arm->b, csc, is_const);
                continue;
            }
            if (arm->ty->kind == TY_REF) {
                if (arm->ty->pointee->kind != TY_CLASS)
                    sema_error("E0", "catch 类型限 int/char/类引用");
                sema_sc_declare(csc, arm->s, arm->ty);
            } else if (arm->ty->kind == TY_INT || arm->ty->kind == TY_CHAR) {
                sema_sc_declare(csc, arm->s, arm->ty);
            } else {
                sema_error("E0", "catch 类型限 int/char/类引用");
            }
            sema_check_stmts(S, arm->b, csc, is_const);
        }
        S->cur_catch--;
        break;
    }
    case NK_COPYINIT: {
        const char *ckey = sema_resolve_class(S, s->s, S->cur_ns);
        if (!ckey) sema_error("E0", "类未定义");
        if (sema_is_abstract(S, ckey)) sema_error("E0", "抽象类不可实例化");
        s->s = strdup(ckey);
        CType *rt = sema_check_expr(S, s->a, sc, is_const);
        CType *bt = rt->kind == TY_REF ? rt->pointee : rt;
        if (bt->kind != TY_CLASS || strcmp(bt->name, ckey)) {
            if (bt->kind == TY_INT || bt->kind == TY_CHAR) {
                Node *hits = NULL;
                for (Node *c = sema_find_class(S, ckey)->ctors; c; c = c->next)
                    if (ast_count(c->items) == 1) { c->next = NULL; hits = c; break; }
                if (!hits || hits->next)
                    sema_error("E0", "拷贝初始化无唯一转换构造");
                if (hits->f1 & F_EXPLICIT)
                    sema_error("E0", "explicit 构造禁止隐式转换");
                s->conv = hits; s->f2 = 0;         /* user_copy=0 */
                s->ty = ctype_class(ckey, 0);
                sema_sc_declare(sc, s->s2, s->ty);
                break;
            }
            sema_error("E0", "拷贝初始化类型不匹配");
        }
        s->f2 = 1;                                 /* user_copy（有拷贝 ctor） */
        s->ty = ctype_class(ckey, 0);
        if (sema_find_class(S, ckey)->dtor)
            sema_fn_dtor_push(S, s->s2, ckey);     /* 0.0.37 RAII */
        if ((s->f2 & 1) && s->a && !s->a->lval)
            sema_error("E0", "用户拷贝构造仅支持左值源（右值物化=后续版本）");
        sema_sc_declare(sc, s->s2, s->ty);
        break;
    }
    case NK_DELETESTMT: {
        CType *et = sema_check_expr(S, s->a, sc, is_const);
        if (et->kind != TY_PTR || et->pointee->kind != TY_CLASS)
            sema_error("E0", "delete 操作数须为对象指针");
        s->ty = et;
        ClassInfo *di = sema_find_class(S, et->pointee->name);
        s->s2 = (di && di->dtor) ? di->key : NULL;
        s->f2 = (di && di->has_op_delete) ? 1 : 0;
        break;
    }
    case NK_EXPRSTMT:
        sema_check_expr(S, s->a, sc, is_const);
        break;
    case NK_RETURN:
        if (s->a) sema_check_expr(S, s->a, sc, is_const);
        check_return(S, S->cur_method, s->a);
        break;
    case NK_IF:
        sema_check_expr(S, s->a, sc, is_const);
        sema_check_stmt(S, s->b, sc, is_const);
        if (s->c) sema_check_stmt(S, s->c, sc, is_const);
        break;
    case NK_WHILE:
        sema_check_expr(S, s->a, sc, is_const);
        sema_check_stmt(S, s->b, sc, is_const);
        break;
    case NK_FOR:
        if (s->a) sema_check_stmt(S, s->a, sc, is_const);
        if (s->b) sema_check_expr(S, s->b, sc, is_const);
        if (s->c) sema_check_expr(S, s->c, sc, is_const);
        sema_check_stmt(S, s->d, sc, is_const);
        break;
    case NK_BLOCK: {
        Scope *inner = scope_new(sc);
        for (Node *x = s->items; x; x = x->next)
            sema_check_stmt(S, x, inner, is_const);
        break;
    }
    default:
        sema_error("E0", "未知语句");
    }
}

void sema_check_stmts(Sema *S, Node *ss, Scope *sc, int is_const) {
    for (Node *s = ss; s; s = s->next)
        sema_check_stmt(S, s, sc, is_const);
}
