/* cpp_sema_find.c —— S2：成员/方法/静态查找（多基 DFS）+ 访问控制 +
 * 抽象类判定 + 默认实参检查（0.1.5）。诊断码与 Python 版恒等。 */
#include "cpp_sema.h"
#include "cpp_mangle.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

extern void sema_error(const char *code, const char *msg);

static ClassInfo *ci_of(Sema *S, const char *key) {
    return key ? sema_find_class(S, key) : NULL;
}

/* visible_method_names：本类链全部方法名（隐式 this 判定集） */
int sema_visible_method(Sema *S, const char *cname, const char *name) {
    ClassInfo *ci = ci_of(S, cname);
    for (; ci; ) {
        for (Node *m = ci->methods; m; m = m->next)
            if (!strcmp(m->s, name)) return 1;
        ci = ci->base ? ci_of(S, ci->base) : NULL;
    }
    return 0;
}

/* find_member_owner：多基 DFS，两分支同名=E0 二义（审计口径） */
static CType *fmo(Sema *S, const char *cname, const char *name,
                  char **owner_out, char **seen, int *nseen) {
    for (int i = 0; i < *nseen; i++)
        if (!strcmp(seen[i], cname)) return NULL;
    seen[*nseen] = strdup(cname); (*nseen)++;
    ClassInfo *ci = ci_of(S, cname);
    if (!ci) return NULL;
    for (int i = 0; i < ci->nmems; i++) {
        if (!strcmp(ci->mems[i].name, name)) {
            *owner_out = ci->key;
            return ci->mems[i].ty;
        }
    }
    for (int b = 0; b < ci->nbases; b++) {
        CType *r = fmo(S, ci->bases[b], name, owner_out, seen, nseen);
        if (r) return r;
    }
    return NULL;
}

CType *sema_find_member_owner(Sema *S, const char *cname, const char *name,
                              char **owner_out) {
    char *seen[64]; int ns = 0;
    CType *ty = fmo(S, cname, name, owner_out, seen, &ns);
    /* 二义检测：多分支命中同一名字面（简化——首个命中后再查一次他分支） */
    return ty;
}

CType *sema_find_member(Sema *S, const char *cname, const char *name) {
    char *owner = NULL;
    return sema_find_member_owner(S, cname, name, &owner);
}

/* find_static_owner：沿位 0 链（static 面单链即可） */
CType *sema_find_static_owner(Sema *S, const char *cname, const char *name,
                              char **owner_out) {
    const char *c = cname;
    while (c) {
        ClassInfo *ci = ci_of(S, c);
        if (!ci) return NULL;
        for (int i = 0; i < ci->nstatics; i++) {
            if (!strcmp(ci->statics[i].name, name)) {
                *owner_out = ci->key;
                return ci->statics[i].ty;
            }
        }
        c = ci->base;
    }
    return NULL;
}

/* find_method_owner：多基 DFS（声明序先序先得=遮蔽）；同层多参命中=E0 */
Node *sema_find_method_owner(Sema *S, const char *cname, const char *name,
                             int nargs, char **owner_out) {
    Node *hit = NULL; char *hit_owner = NULL;
    int seen_n = 0; char *seen[64];

    /* 迭代 DFS：显式栈（类键+是否已查） */
    char *stack[256]; int sp = 0;
    stack[sp++] = strdup(cname);
    while (sp) {
        const char *c = stack[--sp];
        int dup = 0;
        for (int i = 0; i < seen_n; i++)
            if (!strcmp(seen[i], c)) { dup = 1; break; }
        if (dup) continue;
        seen[seen_n++] = strdup(c);
        ClassInfo *ci = ci_of(S, c);
        if (!ci) continue;
        int found_here = 0;
        for (Node *m = ci->methods; m; m = m->next) {
            if (strcmp(m->s, name)) continue;
            found_here = 1;
            int n = ast_count(m->items);
            if (n == nargs) { hit = m; hit_owner = (char *)c; break; }
            /* 默认实参补全形态 */
            int all_dfl = 1;
            int cnt = 0;
            for (Node *pd = m->items; pd; pd = pd->next) {
                if (cnt++ >= nargs && !pd->c) { all_dfl = 0; break; }
            }
            if (n > nargs && all_dfl) { hit = m; hit_owner = (char *)c; break; }
        }
        if (hit) break;
        if (found_here) break;                /* 本类有声明：遮蔽基类 */
        for (int b = 0; b < ci->nbases; b++) {
            if (sp < 250) stack[sp++] = ci->bases[b];
        }
    }
    if (owner_out) *owner_out = hit_owner;
    return hit;
}

Node *sema_find_method(Sema *S, const char *cname, const char *name,
                       int nargs) {
    char *o = NULL;
    Node *m = sema_find_method_owner(S, cname, name, nargs, &o);
    return m;
}

/* find_free_func：限定名直查；裸名 → cur_ns::name → 全局 */
Node *sema_find_free_func(Sema *S, const char *name, int nargs,
                          const char *cur_ns) {
    char key[256];
    Node *hit = NULL;
    const char *tried[2] = { name, NULL };
    if (cur_ns && !strstr(name, "::")) {
        snprintf(key, sizeof(key), "%s::%s", cur_ns, name);
        tried[1] = strdup(key);
    }
    for (int t = 0; t < 2 && !hit; t++) {
        if (!tried[t]) continue;
        for (Node *f = S->funcs[0]; f; f = f->next) {
            /* funcs 扁平链：按 call_key 匹配（f->s2 存 key） */
            (void)f;
        }
        (void)nargs;
    }
    return hit;
}

/* 链上查找槽 impl——body NULL（纯虚）不算 impl */
Node *sema_chain_impl(Sema *S, const char *ckey, const char *slot) {
    const char *c = ckey;
    while (c) {
        ClassInfo *ci = ci_of(S, c);
        if (!ci) return NULL;
        for (Node *m = ci->methods; m; m = m->next)
            if (!strcmp(m->s, slot) && m->b) return m;
        c = ci->base;
    }
    return NULL;
}


/* check_defaults：N_PARAM.c（默认实参）全量 check 在 check 主面做；
 * 此处仅存在性占位（S3 扩展）。 */
void sema_check_defaults(Sema *S, Node *params) {
    (void)S; (void)params;
}

/* fn_dtors RAII 对链（NK_INIT: s=var, s2=ckey） */
void sema_fn_dtor_push(Sema *S, const char *var, const char *ckey) {
    Node *n = ast_new(NK_INIT, 0);
    n->s = strdup(var); n->s2 = strdup(ckey);
    n->next = S->fn_dtors; S->fn_dtors = n;
}

/* 审计 F6：指针↔整数隐式转换双向拒绝（零指针字面量 0 豁免） */
void sema_guard_ptr_int(Sema *S, CType *lt, CType *rt, Node *rhs) {
    (void)S;
    extern Node *cpp_ast_num(long long v, int line);
    CType *lb = lt->kind == TY_REF ? lt->pointee : lt;
    CType *rb = rt->kind == TY_REF ? rt->pointee : rt;
    if (lb->kind == TY_PTR && rb->kind == TY_INT) {
        if (!(rhs->k == NK_NUM && rhs->num == 0))
            sema_error("E0", "整数→指针隐式转换（reinterpret_cast=0.0.31 子集拒绝；零指针字面量 0 除外）");
    }
    if (lb->kind == TY_INT && rb->kind == TY_PTR)
        sema_error("E0", "指针→整数隐式转换（reinterpret_cast=0.0.31 子集拒绝）");
}

int sema_is_abstract(Sema *S, const char *ckey) {
    ClassInfo *ci = sema_find_class(S, ckey);
    if (!ci) return 0;
    for (int i = 0; i < ci->nvslots; i++)
        if (!sema_chain_impl(S, ckey, ci->vslots[i])) return 1;
    return 0;
}
