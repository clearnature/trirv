/* cpp_parser_class.c —— classdef/templatedef/外联成员（0.1.4）。
 * 对齐 cpp_parser.py classdef 分支序与 templatedef 三分支。
 * 访问级记 f1 低 2 位（ACC_*）；标志位 F_CONST/F_STATIC/...。 */
#include "cpp_parser.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>


static Node *p_class_body(const char *kind, const char *name) {
    int acc = !strcmp(kind, "class") ? ACC_PRIVATE : ACC_PUBLIC;
    char *base0 = NULL;
    Node *bases = NULL;
    if (P_accept("PUNCT", ":")) {
        for (;;) {
            P_expect("KW", "public");
            const char *b = p_qual_name(P_expect("ID", NULL)->val);
            Node *bn = ast_new(NK_BASE, 0);
            bn->s = strdup(b);
            bn->next = bases; bases = bn;         /* 前插=反序，遍历时反转 */
            if (!P_accept("PUNCT", ",")) break;
        }
    }
    if (P_accept("PUNCT", ";")) {
        Node *cd = ast_new(NK_CLASSDECL, 0);
        cd->s = strdup(name); cd->forward = 1;
        /* bases 反序 → 声明序 */
        Node *rev = NULL;
        for (Node *b = bases; b; b = b->next) {
            Node *c = ast_new(NK_BASE, 0); c->s = strdup(b->s);
            c->next = rev; rev = c;
        }
        cd->items = rev;
        return cd;
    }
    P_expect("PUNCT", "{");
    Node *cd = ast_new(NK_CLASSDECL, 0);
    cd->s = strdup(name);
    /* bases 声明序链 */
    Node *rev = NULL;
    for (Node *b = bases; b; b = b->next) {
        Node *c = ast_new(NK_BASE, 0); c->s = strdup(b->s);
        c->next = rev; rev = c;
    }
    cd->items = rev;                              /* 暂存 bases 声明序 */
    while (!P_accept("PUNCT", "}")) {
        PTok *t = P_peek(0);
        if (t->kind == (const char *)"KW" &&
            (!strcmp(t->val, "public") || !strcmp(t->val, "private") ||
             !strcmp(t->val, "protected"))) {
            P_next();
            P_expect("PUNCT", ":");
            acc = !strcmp(t->val, "public") ? ACC_PUBLIC :
                  !strcmp(t->val, "private") ? ACC_PRIVATE : ACC_PROTECTED;
            continue;
        }
        if (t->kind == (const char *)"KW" && !strcmp(t->val, "virtual")) {
            P_next();
            CType *ty = p_parse_type();
            const char *nm = P_expect("ID", NULL)->val;
            P_expect("PUNCT", "(");
            Node *params = p_parse_params();
            P_expect("PUNCT", ")");
            int is_const = P_accept("KW", "const") ? F_CONST : 0;
            Node *md = ast_new(NK_METHOD, t->line);
            md->s = strdup(nm); md->ty = ty; md->items = params;
            md->f1 = acc | is_const | F_VIRTUAL;
            if (P_accept("PUNCT", "=")) {         /* 0.0.27 纯虚 = 0 */
                PTok *z = P_expect("NUM", NULL);
                if (strcmp(z->val, "0"))
                    parse_err(t->line, "纯虚声明限 = 0");
                md->f1 |= F_PURE;
                P_expect("PUNCT", ";");
            } else {
                md->b = p_parse_block();
            }
            md->next = cd->items; cd->items = md; /* 反序收集 */
            continue;
        }
        if (t->kind == (const char *)"KW" && !strcmp(t->val, "static")) {
            P_next();
            CType *ty = p_parse_type();
            if (P_peek(0)->kind == (const char *)"KW" &&
                !strcmp(P_peek(0)->val, "operator")) {
                P_next();
                PTok *nxt = P_next();
                if (strcmp(nxt->val, "new") && strcmp(nxt->val, "delete"))
                    parse_err(t->line,
                              "static 后仅支持 operator new/delete");
                P_expect("PUNCT", "(");
                Node *params = p_parse_params();
                P_expect("PUNCT", ")");
                Node *md = ast_new(NK_METHOD, t->line);
                char buf[64]; snprintf(buf, sizeof(buf), "operator %s",
                                       nxt->val);
                md->s = strdup(buf); md->ty = ty; md->items = params;
                md->f2 = 1; md->s2 = strdup(nxt->val); md->f1 = acc;
                md->b = p_parse_block();
                md->next = cd->items; cd->items = md;
                continue;
            }
            const char *nm = P_expect("ID", NULL)->val;
            if (P_peek(0)->kind == (const char *)"PUNCT" &&
                !strcmp(P_peek(0)->val, "(")) {
                Node *md = p_methoddef_static(nm, ty, acc);
                md->next = cd->items; cd->items = md;
            } else {
                Node *sd = ast_new(NK_STATDECL, t->line);
                sd->s = strdup(nm); sd->ty = ty; sd->f1 = acc;
                P_expect("PUNCT", ";");
                sd->next = cd->items; cd->items = sd;
            }
            continue;
        }
        if (t->kind == (const char *)"KW" && !strcmp(t->val, "explicit")) {
            P_next();
            Node *ct = p_ctordef(name, 1);
            ct->next = cd->items; cd->items = ct;
            continue;
        }
        if (t->kind == (const char *)"ID" && !strcmp(t->val, name) &&
            P_peek(1)->kind == (const char *)"PUNCT" &&
            !strcmp(P_peek(1)->val, "(")) {
            Node *ct = p_ctordef(name, 0);
            ct->next = cd->items; cd->items = ct;
            continue;
        }
        if (t->kind == (const char *)"KW" && !strcmp(t->val, "int")) {
            if ((P_peek(1)->kind == (const char *)"ID" &&
                 P_peek(2)->kind == (const char *)"PUNCT" &&
                 !strcmp(P_peek(2)->val, "(")) ||
                (P_peek(1)->kind == (const char *)"PUNCT" &&
                 !strcmp(P_peek(1)->val, "&")) ||
                (P_peek(1)->kind == (const char *)"KW" &&
                 !strcmp(P_peek(1)->val, "operator"))) {
                Node *md = p_methoddef(acc);
                md->next = cd->items; cd->items = md;
            } else {
                Node *md = p_memberdecl(acc);
                md->next = cd->items; cd->items = md;
            }
            continue;
        }
        if ((t->kind == (const char *)"KW" &&
             (!strcmp(t->val, "char") || !strcmp(t->val, "void") ||
              !strcmp(t->val, "bool") || !strcmp(t->val, "const"))) ||
            (t->kind == (const char *)"KW" && !strcmp(t->val, "operator"))) {
            Node *md = p_methoddef(acc);
            md->next = cd->items; cd->items = md;
            continue;
        }
        if (t->kind == (const char *)"ID" &&
            P_peek(1)->kind == (const char *)"PUNCT" &&
            !strcmp(P_peek(1)->val, "*")) {
            Node *md = p_memberdecl(acc);          /* B* p; 指针成员 */
            md->next = cd->items; cd->items = md;
            continue;
        }
        if ((t->kind == (const char *)"ID" &&
             P_peek(1)->kind == (const char *)"ID" &&
             P_peek(2)->kind == (const char *)"PUNCT" &&
             !strcmp(P_peek(2)->val, "(")) ||
            (t->kind == (const char *)"ID" &&
             P_peek(1)->kind == (const char *)"KW" &&
             !strcmp(P_peek(1)->val, "operator"))) {
            Node *md = p_methoddef(acc);
            md->next = cd->items; cd->items = md;
            continue;
        }
        if (t->kind == (const char *)"KW" &&
            (!strcmp(t->val, "class") || !strcmp(t->val, "struct"))) {
            Node *inner = p_classdef(NULL, NULL);   /* 嵌套类冒泡（0.0.22） */
            char key[256];
            snprintf(key, sizeof(key), "%s%s%s", cd->ns ? cd->ns : "",
                     cd->ns ? "::" : "", name);
            inner->nested_in = strdup(key);
            inner->ns = strdup(key);
            inner->next = cd->items; cd->items = inner;
            continue;
        }
        if (t->kind == (const char *)"KW" && !strcmp(t->val, "friend")) {
            P_next(); p_parse_type();
            P_expect("ID", NULL);
            P_expect("PUNCT", ";");                 /* friend：语义 no-op */
            continue;
        }
        if (t->kind == (const char *)"ID" &&
            P_peek(1)->kind == (const char *)"ID" &&
            (P_peek(2)->kind == (const char *)"PUNCT" &&
             (!strcmp(P_peek(2)->val, ";") || !strcmp(P_peek(2)->val, "=") ||
              !strcmp(P_peek(2)->val, "[")))) {
            Node *md = p_memberdecl(acc);           /* 类类型成员 T v; */
            md->next = cd->items; cd->items = md;
            continue;
        }
        if (t->kind == (const char *)"PUNCT" && !strcmp(t->val, "~")) {
            P_next();
            const char *dn = P_expect("ID", NULL)->val;
            if (strcmp(dn, name))
                parse_err(t->line, "析构名与类不符");
            P_expect("PUNCT", "("); P_expect("PUNCT", ")");
            Node *dt = ast_new(NK_DTOR, t->line);
            dt->b = p_parse_block();
            dt->next = cd->items; cd->items = dt;
            continue;
        }
        parse_err(t->line, "类体内不支持");
    }
    P_expect("PUNCT", ";");
    /* 反序收集 → 声明序链 */
    Node *rev2 = NULL;
    for (Node *m = cd->items; m; m = m->next) {
        Node *head = malloc(sizeof(Node));
        *head = *m; head->next = rev2; rev2 = head;
    }
    cd->items = rev2;
    return cd;
}

Node *p_classdef(const char *kind, const char *name) {
    char kbuf[16], nbuf[256];
    if (!kind) kind = strcpy(kbuf, P_next()->val);
    if (!name) name = strcpy(nbuf, P_expect("ID", NULL)->val);
    return p_class_body(kind, name);
}
