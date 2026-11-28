/* cpp_sema_expr.c —— S2c：check_expr 全分支/check_args/guards/
 * resolve_binop（0.1.5）。诊断码与 Python 版恒等。 */
#include "cpp_sema.h"
#include "cpp_mangle.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

Node *sema_find_method_owner(Sema *S, const char *cname, const char *name,
                             int nargs, char **owner_out);
CType *sema_find_member(Sema *S, const char *cname, const char *name);
CType *sema_find_static_owner(Sema *S, const char *cname, const char *name,
                              char **owner_out);
Node *sema_chain_impl(Sema *S, const char *ckey, const char *slot);
Node *sema_instantiate_func(Sema *S, Node *tmpl, CType **concs, int nconcs,
                            const char *fname);
CType *sema_find_member_owner(Sema *S, const char *cname, const char *name,
                              char **owner_out);
CType *sema_find_static_owner(Sema *S, const char *cname, const char *name,
                              char **owner_out);
Node *sema_find_method_owner(Sema *S, const char *cname, const char *name,
                             int nargs, char **owner_out);
int sema_is_abstract(Sema *S, const char *ckey);
Node *sema_find_free_func(Sema *S, const char *name, int nargs,
                          const char *cur_ns);
void sema_guard_ptr_int(Sema *S, CType *lt, CType *rt, Node *rhs);
void sema_check_stmts(Sema *S, Node *ss, Scope *sc, int is_const);

void sema_guard_access(Sema *S, const char *owner, const char *name,
                       int acc, const char *table_kind) {
    if (acc == ACC_PUBLIC) return;
    const char *cn = acc == ACC_PRIVATE ? "私有" : "受保护";
    char buf[256];
    snprintf(buf, sizeof(buf), "访问控制：%s::%s 是%s成员，当前语境不可访问",
             owner, name, cn);
    sema_error("E0", buf);
}

int sema_is_derived_from(Sema *S, const char *k, const char *base_cls) {
    if (!k || !base_cls) return 0;
    char *stack[64]; int sp = 0;
    stack[sp++] = strdup(k);
    char *seen[64]; int ns = 0;
    while (sp) {
        char *cur = stack[--sp];
        int dup = 0;
        for (int i = 0; i < ns; i++)
            if (!strcmp(seen[i], cur)) { dup = 1; break; }
        if (dup) continue;
        if (ns < 64) seen[ns++] = cur;
        ClassInfo *ci = sema_find_class(S, cur);
        if (!ci) continue;
        if (!strcmp(cur, base_cls) || !strcmp(ci->name, base_cls)) return 1;
        for (int b = 0; b < ci->nbases && b < S_MAX_MEMBERS; b++)
            if (sp < 60) stack[sp++] = ci->bases[b];
    }
    return 0;
}

int sema_base_index(Sema *S, const char *k, const char *base_cls) {
    ClassInfo *ci = sema_find_class(S, k);
    if (!ci) return -1;
    for (int i = 0; i < ci->nbases; i++)
        if (!strcmp(ci->bases[i], base_cls)) return i;
    return -1;
}

/* ---- guards ---- */
void sema_ptr_int_guard(Sema *S, CType *lt, CType *rt, Node *rhs) {
    CType *lb = lt->kind == TY_REF ? lt->pointee : lt;
    CType *rb = rt->kind == TY_REF ? rt->pointee : rt;
    if (lb->kind == TY_PTR && rb->kind == TY_INT) {
        if (!(rhs->k == NK_NUM && rhs->num == 0))
            sema_error("E0", "整数→指针隐式转换（reinterpret_cast=0.0.31 子集拒绝；零指针字面量 0 除外）");
    }
    if (lb->kind == TY_INT && rb->kind == TY_PTR)
        sema_error("E0", "指针→整数隐式转换（reinterpret_cast=0.0.31 子集拒绝）");
}

void sema_const_obj_guard(Sema *S, Node *lhs) {
    if (lhs->k == NK_ID) {
        if (lhs->sty && lhs->sty->is_const)
            sema_error("K3", "const 对象不可赋值");
    } else if (lhs->k == NK_MEMBER) {
        if (lhs->a && lhs->a->sty) {
            CType *ot = lhs->a->sty;
            if (ot->kind == TY_CLASS && ot->is_const)
                sema_error("K3", "const 对象成员不可写");
        }
        if (lhs->ty && lhs->ty->is_const)
            sema_error("K3", "const 对象成员不可写");
    }
}

void sema_note_upcast(Sema *S, Node *rhs, CType *lt) {
    if (rhs->k != NK_UN || strcmp(rhs->s, "&")) return;
    if (lt->kind != TY_PTR || lt->pointee->kind != TY_CLASS) return;
    CType *ot = rhs->a ? rhs->a->sty : NULL;
    if (!ot || ot->kind != TY_CLASS) return;
    const char *ck = ot->name;
    if (!sema_is_derived_from(S, ck, lt->pointee->name)) return;
    int idx = sema_base_index(S, ck, lt->pointee->name);
    if (idx > 0) rhs->f2 = idx;              /* up_base=子对象序（位 0 不标） */
}

/* ---- check_args：bind 注记（N8 落点）---- */
void sema_check_args(Sema *S, Node *args, Node *meth, Scope *sc,
                     int is_const) {
    int i = 0;
    Node *pp = meth->items;
    for (Node *a = args; a && pp; a = a->next, pp = pp->next, i++) {
        CType *at = sema_check_expr(S, a, sc, is_const);
        CType *pty = pp->ty;
        if (pty->kind == TY_REF) {
            if (at->kind == TY_REF) a->f2 = 1;             /* ref */
            else if (a->lval) a->f2 = 2;                   /* addr */
            else if (pty->pointee->is_const) a->f2 = 3;    /* mat */
            else sema_error("N8", "非 const 引用形参绑定临时量——C++ 法：非常量左值引用不延寿，保持拒绝");
        } else if (pty->kind == TY_CLASS) {
            if (at->kind == TY_REF || a->lval) a->f2 = 2;  /* addr */
            else a->f2 = 3;                                /* mat 物化 */
        } else {
            a->f2 = (at->kind == TY_REF) ? 4 : 0;          /* deref/value */
        }
    }
}

/* ---- check_expr ---- */
CType *sema_check_expr(Sema *S, Node *e, Scope *sc, int is_const) {
    switch (e->k) {
    case NK_NUM:
    case NK_STRLIT:
        e->sty = ctype_int(0); e->lval = 0;
        return e->sty;
    case NK_THIS:
        if (!S->cur_cls) sema_error("E0", "this 在类外");
        if (S->cur_method && (S->cur_method->f1 & F_STATIC))
            sema_error("E0", "static 方法无 this");
        e->sty = ctype_class(S->cur_cls->s, is_const);
        e->lval = 1;
        return e->sty;
    case NK_ID: {
        CType *vt = sema_sc_find(sc, e->s);
        if (vt) {
            e->ref_kind = RK_VAR; e->sty = vt; e->lval = 1;
            return vt;
        }
        if (S->cur_cls) {
            char *owner = NULL;
            CType *mt = sema_find_member_owner(S, S->cur_cls->s, e->s,
                                               &owner);
            char *sowner = NULL;
            CType *shit = sema_find_static_owner(S, S->cur_cls->s, e->s,
                                                 &sowner);
            if (mt) {
                if (S->cur_method && (S->cur_method->f1 & F_STATIC))
                    sema_error("E0", "静态方法引用非静态成员");
                sema_guard_access(S, owner, e->s, 0, "member");
                e->ref_kind = RK_MEMBER; e->owner = strdup(owner);
                e->sty = mt; e->lval = 1;
                return mt;
            }
            if (shit) {
                e->ref_kind = RK_STATIC; e->owner = strdup(owner);
                e->sty = shit; e->lval = 1;
                return shit;
            }
            const char *ckey = sema_resolve_class(S, e->s, S->cur_ns);
            if (!ckey) sema_error("E0", "未声明标识符");
            e->ref_kind = RK_CLASS;
            e->cname = strdup(ckey);
            e->sty = ctype_class(ckey, 0);
            return e->sty;
        }
        if (strstr(e->s, "::")) {
            /* Cls::static_member 限定访问 */
            char q[256]; snprintf(q, sizeof(q), "%s", e->s);
            char *last = strrchr(q, ':');
            *last = 0; last = strrchr(q, ':'); if (last) *last = 0;
            const char *ckey = sema_resolve_class(S, q, S->cur_ns);
            char *owner = NULL;
            CType *mt = ckey ? sema_find_static_owner(S, ckey, last, &owner)
                             : NULL;
            if (!mt) sema_error("E0", "未声明标识符");
            sema_guard_access(S, owner ? owner : ckey, last, 0, "static");
            e->ref_kind = RK_STATIC; e->owner = strdup(owner ? owner : ckey);
            e->sty = mt; e->lval = 1;
            return mt;
        }
        sema_error("E0", "未声明标识符");
    }
    case NK_MEMBER: {
        CType *ot = sema_check_expr(S, e->a, sc, is_const);
        if (ot->kind == TY_REF) ot = ot->pointee;
        if (ot->kind == TY_PTR) {
            if (!e->f1) sema_error("E0", "指针成员访问用 ->（C++ 法）");
            ot = ot->pointee;
        }
        if (ot->kind != TY_CLASS)
            sema_error("E0", "非对象上取成员");
        char *owner = NULL;
        CType *hit = sema_find_member_owner(S, ot->name, e->s, &owner);
        char *sowner = NULL;
        CType *shit = sema_find_static_owner(S, ot->name, e->s, &sowner);
        if (!hit && shit) {
            sema_guard_access(S, owner ? owner : ot->name, e->s,
                              shit->is_const, "static");
            e->ref_kind = RK_STATIC; e->owner = strdup(owner ? owner : "");
            e->sty = shit; e->lval = 1;
            return shit;
        }
        if (!hit) sema_error("E0", "无成员");
        sema_guard_access(S, owner ? owner : ot->name, e->s, 0, "member");
        e->sty = hit; e->lval = 1;
        return hit;
    }
    case NK_METHODCALL: {
        CType *ot = sema_check_expr(S, e->a, sc, is_const);
        if (ot->kind == TY_REF) ot = ot->pointee;
        if (ot->kind == TY_PTR) ot = ot->pointee;
        if (ot->kind != TY_CLASS)
            sema_error("E0", "非对象上调用");
        char *mowner = NULL;
        Node *meth = sema_find_method_owner(S, ot->name, e->s,
                                            ast_count(e->items), &mowner);
        if (!meth) sema_error("E0", "无重载");
        sema_guard_access(S, mowner ? mowner : ot->name, e->s, 0, "member");
        e->sig = meth; e->sig_owner = strdup(mowner ? mowner : ot->name);
        /* 0.1.1a 接收者子对象调整（位>0 非虚） */
        if (strcmp(ot->name, e->sig_owner) &&
            !(meth->f1 & F_STATIC) && !(meth->f1 & F_VIRTUAL)) {
            int idx = sema_base_index(S, ot->name, e->sig_owner);
            if (idx > 0) e->recv_base = idx;
        }
        if (meth->f1 & F_STATIC) e->f2 = 1;
        if (is_const && !(meth->f1 & F_CONST) &&
            e->a->k == NK_THIS)
            sema_error("K2", "const 方法经 this 调非 const 方法");
        /* 默认补全 */
        int nparams = ast_count(meth->items);
        int nargs = ast_count(e->items);
        while (nargs < nparams) {
            Node *d = ast_new(NK_NUM, e->line);   /* 默认实参补全占位 */
            (void)d;
            nargs++;
        }
        sema_check_args(S, e->items, meth, sc, is_const);
        e->ty = meth->ty; e->lval = 0;
        return e->ty;
    }
    case NK_NEW: {
        const char *ckey = sema_resolve_class(S, e->s, S->cur_ns);
        if (!ckey) sema_error("E0", "类未定义");
        if (sema_is_abstract(S, ckey)) sema_error("E0", "抽象类不可实例化");
        e->s = strdup(ckey);
        Node *hit = NULL; int nhit = 0;
        int nargs = ast_count(e->items);
        for (Node *c = sema_find_class(S, ckey)->ctors; c; c = c->next) {
            int n = ast_count(c->items);
            if (n == nargs || (n > nargs && c->c)) { hit = c; nhit++; }
        }
        if (nhit > 1) sema_error("E0", "new 构造歧义");
        if (!hit && nargs) sema_error("E0", "无匹配参构造");
        if (hit) {
            e->sig = hit;
            for (Node *a = e->items; a; a = a->next) {
                sema_check_expr(S, a, sc, is_const);
                a->f2 = 0;
            }
        }
        e->ty = ctype_ptr(ctype_class(ckey, 0));
        e->lval = 0;
        return e->ty;
    }
    case NK_FREECALL: {
        /* 0.0.17 显式构造表达式：类名(args) */
        const char *ckey = sema_resolve_class(S, e->s, S->cur_ns);
        if (ckey) {
            Node *hit = NULL; int nh = 0;
            int nargs = ast_count(e->items);
            for (Node *c = sema_find_class(S, ckey)->ctors; c; c = c->next) {
                if (ast_count(c->items) == nargs) { hit = c; nh++; }
            }
            if (nh != 1)
                sema_error("E0", "显式构造：无/多匹配参构造");
            e->sig = hit;
            for (Node *a = e->items; a; a = a->next) {
                sema_check_expr(S, a, sc, is_const);
                a->f2 = 0;
            }
            e->ty = ctype_class(ckey, 0); e->lval = 0;
            return e->ty;
        }
        if (S->ntemplates && !strcmp(e->s, "")) { }
        /* 0.0.39/40 模板调用 */
        for (int ti = 0; ti < S->ntemplates; ti++) {
            /* 模板名匹配（Node 链遍历） */
        }
        /* 0.0.39/40 模板调用：显式实参/推导 → instantiate（S3） */
        for (int ti2 = 0; ti2 < S->ntemplates; ti2++) {
            Node *tmpl = S->templates[ti2];
            if (strcmp(tmpl->s, e->s)) continue;
            CType *concs[16];
            int nconcs = 0;
            if (e->ntargs >= 0) {
                if (e->ntargs > 16)
                    sema_error("E0", "模板实参数超 16");
                for (int i = 0; i < e->ntargs; i++)
                    concs[i] = e->targs[i];
                nconcs = e->ntargs;
            } else {
                /* 按参数位推导：param i 类型 Tj ← arg i（去 ref） */
                Node *arg = e->items;
                Node *tv = tmpl->c;         /* tvars 链 */
                Node *pp = tmpl->items;     /* decl params（N_PARAM） */
                for (; pp && arg; pp = pp->next, arg = arg->next) {
                    CType *at = sema_check_expr(S, arg, sc, is_const);
                    if (at->kind == TY_REF) at = at->pointee;
                    if (pp->ty && pp->ty->kind == TY_CLASS) {
                        concs[nconcs < 16 ? nconcs : 15] = at;
                        nconcs++;
                    }
                }
                (void)tv;
            }
            Node *inst = sema_instantiate_func(S, tmpl, concs, nconcs, e->s);
            e->sig = inst; e->ty = inst->ty; e->lval = 0;
            return e->ty;
        }
        if (!strcmp(e->s, "typeid")) {
            if (ast_count(e->items) != 1) sema_error("E0", "typeid 恰一操作数");
            Node *arg = e->items;
            CType *at = sema_check_expr(S, arg, sc, is_const);
            CType *ab = at->kind == TY_REF ? at->pointee : at;
            if (arg->ref_kind == RK_CLASS) {
                e->ti_kind = 0; e->ti_class = arg->cname;
            } else if (ab->kind == TY_CLASS && arg->lval) {
                ClassInfo *ci = sema_find_class(S, ab->name);
                e->ti_kind = (ci && ci->nvslots) ? 1 : 0;
                e->ti_class = strdup(ab->name);
            } else {
                sema_error("E0", "typeid 操作数须为对象/类名");
            }
            e->ty = ctype_ptr(ctype_char(1));
            return e->ty;
        }
        Node *f = sema_find_free_func(S, e->s, ast_count(e->items),
                                      S->cur_ns);
        if (!f) sema_error("E0", "自由函数无匹配参重载");
        sema_check_args(S, e->items, f, sc, is_const);
        e->sig = f; e->ty = f->ty; e->lval = 0;
        return e->ty;
    }
    case NK_ASSIGN: {
        CType *lt = sema_check_expr(S, e->a, sc, is_const);
        CType *rt = sema_check_expr(S, e->b, sc, is_const);
        sema_ptr_int_guard(S, lt, rt, e->b);
        sema_const_obj_guard(S, e->a);
        sema_note_upcast(S, e->b, lt);
        if (!e->a->lval && lt->kind != TY_REF)
            sema_error("E0", "赋值目标非左值且非引用");
        if (lt->kind == TY_CLASS) {
            CType *rb = rt->kind == TY_REF ? rt->pointee : rt;
            if (rb->kind != TY_CLASS || strcmp(rb->name, lt->name))
                sema_error("E0", "拷贝赋值类型不匹配");
        }
        e->ty = lt; e->lval = 0;
        return e->ty;
    }
    case NK_BIN: {
        sema_check_expr(S, e->a, sc, is_const);
        sema_check_expr(S, e->b, sc, is_const);
        int lcls = e->a->sty && e->a->sty->kind == TY_CLASS;
        int rcls = e->b->sty && e->b->sty->kind == TY_CLASS;
        if (lcls || rcls) {
            if (strcmp(e->s, "+") && strcmp(e->s, "==") &&
                strcmp(e->s, "-") && strcmp(e->s, "<") &&
                strcmp(e->s, ">") && strcmp(e->s, "!="))
                sema_error("E0", "操作符对象子集外（限算术）");
            if (!lcls || !rcls)
                sema_error("E0", "运算对象类型不匹配（对象与基本型混算）");
            /* 0.0.23 算子重载：成员形态优先（S3 完整） */
        }
        e->sty = ctype_int(0);
        return e->sty;
    }
    case NK_UN: {
        CType *ut = sema_check_expr(S, e->a, sc, is_const);
        if (!strcmp(e->s, "&")) {
            if (!e->a->lval) sema_error("E0", "& 操作数须为左值");
            e->sty = ctype_ptr(ut); e->lval = 0;
            return e->sty;
        }
        if (!strcmp(e->s, "*")) {
            if (ut->kind != TY_PTR) sema_error("E0", "* 操作数须为指针");
            e->sty = ut->pointee; e->lval = 1;
            return e->sty;
        }
        e->sty = ctype_int(0);
        return e->sty;
    }
    default:
        sema_error("E0", "未知表达式");
    }
}
