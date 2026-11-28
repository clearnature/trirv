/* cpp_sema_build.c —— S1：build_classes 三 Phase + build_funcs（0.1.5）。
 * 诊断码与 Python 版恒等（E0...）；错误=stderr+exit(2)。 */
#include "cpp_sema.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

void sema_error(const char *code, const char *msg) {
    fprintf(stderr, "%s: %s\n", code, msg);
    exit(2);
}

void sema_init(Sema *S) {
    memset(S, 0, sizeof(*S));
}

ClassInfo *sema_find_class(Sema *S, const char *key) {
    for (int i = 0; i < S->nclasses; i++)
        if (!strcmp(S->classes[i]->key, key)) return S->classes[i];
    return NULL;
}

static ClassInfo *classinfo_new(const char *name, const char *key) {
    ClassInfo *ci = calloc(1, sizeof(ClassInfo));
    ci->name = strdup(name);
    ci->key = strdup(key);
    return ci;
}

/* Phase A：键注册（ns 类 → ns::name） */
static void phase_a(Sema *S, Node *classes) {
    for (Node *cd = classes; cd; cd = cd->next) {
        char key[256];
        snprintf(key, sizeof(key), "%s%s%s", cd->ns ? cd->ns : "",
                 cd->ns ? "::" : "", cd->s);
        ClassInfo *prev = sema_find_class(S, key);
        if (prev) {
            if (!prev->incomplete || cd->forward)
                sema_error("E0", "类重复定义");
        }
        cd->s2 = strdup(key);                  /* key 存 s2（phase_c 用） */
        if (!prev) {
            ClassInfo *ci = classinfo_new(cd->s, key);
            ci->incomplete = cd->forward ? 1 : 0;
            S->classes[S->nclasses++] = ci;
        }
    }
}

const char *sema_resolve_class(Sema *S, const char *name, const char *ns) {
    char key[256];
    if (sema_find_class(S, name)) return name;
    if (ns) {
        snprintf(key, sizeof(key), "%s::%s", ns, name);
        if (sema_find_class(S, key)) return strdup(key);
    }
    if (S->cur_cls) {
        const char *k = S->cur_cls->s;
        snprintf(key, sizeof(key), "%s::%s", k, name);
        if (sema_find_class(S, key)) return strdup(key);
    }
    return NULL;                               /* 0.0.41 惰性实例化=S5 */
}

/* Phase B：基类解析（键/自继承拒绝）；canon（ns 短名→键）恒等退化版
 * ——canon_stmt 完整移植=S3（ns 语料 test13 在 S3 差分验证）。 */
static void phase_b(Sema *S, Node *classes) {
    for (Node *cd = classes; cd; cd = cd->next) {
        const char *ns = cd->ns;
        const char *bkeys[64]; int nb = 0;
        for (Node *b = cd->items; b; b = b->next) {
            if (b->k != NK_BASE) continue;    /* 只挑基类槽（items 混链） */
            const char *bkey = sema_resolve_class(S, b->s, ns);
            if (!bkey) sema_error("E0", "基类未定义");
            if (!strcmp(bkey, cd->s))
                sema_error("E0", "类继承自身（审计 F1）");
            bkeys[nb++] = bkey;
        }
        ClassInfo *ci = sema_find_class(S, cd->s);
        if (ci) {
            ci->nbases = nb;
            for (int i = 0; i < nb; i++) ci->bases[i] = strdup(bkeys[i]);
            if (nb) ci->base = ci->bases[0];
            if (cd->forward) ci->incomplete = 1;
        }
    }
}

/* Phase C：ClassInfo 成员/方法/ctor/dtor/vslots 构建 */
static void phase_c(Sema *S, Node *classes) {
    for (Node *cd = classes; cd; cd = cd->next) {
        if (cd->forward) continue;
        ClassInfo *ci = sema_find_class(S, cd->s2 ? cd->s2 : cd->s);
        if (!ci) continue;
        for (Node *m = cd->items; m; m = m->next) {
            if (m->k == NK_MEMBERDECL) {
                ci->mems[ci->nmems].name = strdup(m->s);
                ci->mems[ci->nmems].ty = m->ty;
                ci->mems[ci->nmems].acc = m->f1 & 3;
                ci->nmems++;
            } else if (m->k == NK_STATDECL) {
                ci->statics[ci->nstatics].name = strdup(m->s);
                ci->statics[ci->nstatics].ty = m->ty;
                ci->statics[ci->nstatics].acc = m->f1 & 3;
                ci->nstatics++;
            }
        }
        Node *dt = NULL;
        for (Node *m = cd->items; m; m = m->next) {
            if (m->k == NK_METHOD) {
                if (!strcmp(m->s, ci->name)) continue;   /* ctor 形态不入 methods */
                if (m->f1 & F_VIRTUAL) {
                    int dup = 0;
                    for (int i = 0; i < ci->nvslots; i++)
                        if (!strcmp(ci->vslots[i], m->s)) { dup = 1; break; }
                    if (!dup && ci->nvslots < S_MAX_MEMBERS)
                        ci->vslots[ci->nvslots++] = m->s;
                }
            }
            if (m->k == NK_DTOR) dt = dt ? dt : m;
        }
        ci->dtor = dt;
        (void)dt;
    }
}

Sema *GS = NULL;                           /* rw 家族全局上下文（单 TU） */

void sema_build_classes(Sema *S, Node *classes) {
    /* Phase A 时把 key 存入 ClassDecl.s2（phase_c 用 key 查 info） */
    phase_a(S, classes);
    for (Node *cd = classes; cd; cd = cd->next) {
        ClassInfo *ci = sema_find_class(S, cd->s);
        if (ci) cd->s2 = ci->key;
    }
    phase_b(S, classes);
    phase_c(S, classes);
}

void sema_build_funcs(Sema *S, Node *funcs) {
    for (Node *f = funcs; f; f = f->next) {
        /* 审计 F7：签名类型未定义 → E0（诚实于声明处） */
        for (Node *p = f->items; p; p = p->next) {
            if (p->ty && p->ty->kind == TY_CLASS &&
                !sema_resolve_class(S, p->ty->name, f->ns))
                sema_error("E0", "形参类型未定义");
        }
        if (f->ty && f->ty->kind == TY_CLASS &&
            !sema_resolve_class(S, f->ty->name, f->ns))
            sema_error("E0", "返回类型未定义");
        S->funcs[S->nfuncs++] = f;
    }
}
