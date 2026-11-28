/* cpp_sema_canon.c —— S2：canon_ty/canon_stmt/canon_expr（0.1.5）。
 * TClass 短名→限定键（ns 内确有此名）；Node 全遍历 switch。 */
#include "cpp_sema.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

void sema_canon_expr(Sema *S, Node *e, const char *ns);

void sema_canon_ty(Sema *S, CType *ty, const char *ns) {
    if (!ty) return;
    if (ty->kind == TY_REF) { sema_canon_ty(S, ty->pointee, ns); return; }
    if (ty->kind == TY_CLASS && !strstr(ty->name, "::")) {
        const char *r = sema_resolve_class(S, ty->name, ns);
        if (r) ty->name = strdup(r);
    }
}

void sema_canon_stmt(Sema *S, Node *s, const char *ns) {
    if (!s) return;
    switch (s->k) {
    case NK_VARDECL:
        sema_canon_ty(S, s->ty, ns);
        if (s->a) sema_canon_expr(S, s->a, ns);
        break;
    case NK_CTORSTMT: {
        const char *r = sema_resolve_class(S, s->s, ns);
        if (r) s->s = strdup(r);
        for (Node *a = s->items; a; a = a->next)
            sema_canon_expr(S, a, ns);
        break;
    }
    case NK_COPYINIT: {
        const char *r = sema_resolve_class(S, s->s, ns);
        if (r) s->s = strdup(r);
        sema_canon_expr(S, s->a, ns);
        break;
    }
    case NK_DELETESTMT: sema_canon_expr(S, s->a, ns); break;
    case NK_EXPRSTMT:   sema_canon_expr(S, s->a, ns); break;
    case NK_RETURN:     if (s->a) sema_canon_expr(S, s->a, ns); break;
    case NK_IF:
        sema_canon_expr(S, s->a, ns);
        sema_canon_stmt(S, s->b, ns);
        if (s->c) sema_canon_stmt(S, s->c, ns);
        break;
    case NK_WHILE:
        sema_canon_expr(S, s->a, ns);
        sema_canon_stmt(S, s->b, ns);
        break;
    case NK_FOR:
        if (s->a) sema_canon_stmt(S, s->a, ns);
        if (s->b) sema_canon_expr(S, s->b, ns);
        if (s->c) sema_canon_expr(S, s->c, ns);
        sema_canon_stmt(S, s->d, ns);
        break;
    case NK_BLOCK:
        for (Node *x = s->items; x; x = x->next)
            sema_canon_stmt(S, x, ns);
        break;
    default: break;
    }
}

void sema_canon_expr(Sema *S, Node *e, const char *ns) {
    if (!e) return;
    switch (e->k) {
    case NK_NEW: {
        const char *r = sema_resolve_class(S, e->s, ns);
        if (r) e->s = strdup(r);
        for (Node *a = e->items; a; a = a->next)
            sema_canon_expr(S, a, ns);
        break;
    }
    case NK_METHODCALL:
        sema_canon_expr(S, e->a, ns);
        for (Node *a = e->items; a; a = a->next)
            sema_canon_expr(S, a, ns);
        break;
    case NK_FREECALL: {
        const char *r = sema_resolve_class(S, e->s, ns);
        if (r) e->s = strdup(r);          /* CtorExpr 类名→键（lower flat） */
        for (Node *a = e->items; a; a = a->next)
            sema_canon_expr(S, a, ns);
        break;
    }
    case NK_BIN:
        sema_canon_expr(S, e->a, ns);
        sema_canon_expr(S, e->b, ns);
        break;
    case NK_UN:   sema_canon_expr(S, e->a, ns); break;
    case NK_ASSIGN:
        sema_canon_expr(S, e->a, ns);
        sema_canon_expr(S, e->b, ns);
        break;
    case NK_MEMBER: sema_canon_expr(S, e->a, ns); break;
    default: break;                       /* Num/StrLit/Id/This：叶子 */
    }
}
