/* cpp_parser_method.c —— 方法/构造/算子/成员声明（0.1.4）。 */
#include "cpp_parser.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

const char *p_op_name(void) {
    P_expect("KW", "operator");
    PTok *t = P_next();
    if (t->kind == (const char *)"PUNCT" &&
        (!strcmp(t->val, "+") || !strcmp(t->val, "=") ||
         !strcmp(t->val, "==") || !strcmp(t->val, "-") ||
         !strcmp(t->val, "<") || !strcmp(t->val, ">") ||
         !strcmp(t->val, "!="))) {
        char buf[32]; snprintf(buf, sizeof(buf), "operator%s", t->val);
        return strdup(buf);
    }
    if (t->kind == (const char *)"KW" &&
        (!strcmp(t->val, "new") || !strcmp(t->val, "delete"))) {
        char buf[32]; snprintf(buf, sizeof(buf), "operator %s", t->val);
        return strdup(buf);
    }
    parse_err(t->line, "操作符子集外（限算术：= + == - < > !=）");
}

Node *p_methoddef(int acc) {
    PTok *t0 = P_peek(0);
    CType *ty = p_parse_type();
    const char *nm;
    int special = 0;
    if (P_peek(0)->kind == (const char *)"KW" &&
        !strcmp(P_peek(0)->val, "operator")) {
        nm = p_op_name();
        if (!strcmp(nm, "operator new") || !strcmp(nm, "operator delete")) {
            P_expect("PUNCT", "(");
            Node *params = p_parse_params();
            P_expect("PUNCT", ")");
            if (P_accept("KW", "const"))
                parse_err(t0->line, "operator new/delete 无 const");
            Node *md = ast_new(NK_METHOD, t0->line);
            md->s = strdup(nm); md->ty = ty; md->items = params;
            md->b = p_parse_block();
            md->f2 = 1; md->s2 = strdup(strrchr(nm, ' ') + 1);
            md->f1 = acc;
            (void)special;
            return md;
        }
    } else {
        nm = P_expect("ID", NULL)->val;
    }
    P_expect("PUNCT", "(");
    Node *params = p_parse_params();
    P_expect("PUNCT", ")");
    int is_const = P_accept("KW", "const") ? F_CONST : 0;
    Node *body = NULL;
    if (P_accept("PUNCT", ";")) {
        body = NULL;                              /* 方法声明（外联定义） */
    } else {
        body = p_parse_block();
    }
    Node *md = ast_new(NK_METHOD, t0->line);
    md->s = strdup(nm); md->ty = ty; md->items = params; md->b = body;
    md->f1 = acc | is_const;
    return md;
}

Node *p_methoddef_static(const char *nm, CType *ty, int acc) {
    P_expect("PUNCT", "(");
    Node *params = p_parse_params();
    P_expect("PUNCT", ")");
    if (P_accept("KW", "const"))
        parse_err(0, "static 方法无 const（无 this 可限定）");
    Node *md = ast_new(NK_METHOD, 0);
    md->s = strdup(nm); md->ty = ty; md->items = params; md->b = p_parse_block();
    md->f1 = acc | F_STATIC;
    return md;
}

Node *p_ctordef(const char *clsname, int explicit_) {
    P_expect("ID", clsname);
    P_expect("PUNCT", "(");
    Node *params = p_parse_params();
    P_expect("PUNCT", ")");
    Node *inits = NULL, *itail = NULL;
    Node *body = NULL;
    if (P_accept("PUNCT", ":")) {
        for (;;) {
            const char *tgt = P_expect("ID", NULL)->val;
            P_expect("PUNCT", "(");
            Node *args = NULL, *atail = NULL;
            if (!(P_peek(0)->kind == (const char *)"PUNCT" &&
                  !strcmp(P_peek(0)->val, ")"))) {
                for (;;) {
                    Node *e = p_parse_expr();
                    if (atail) atail->next = e; else args = e;
                    for (; atail && atail->next; atail = atail->next) {}
                    if (!atail) atail = e;
                    if (!P_accept("PUNCT", ",")) break;
                }
            }
            P_expect("PUNCT", ")");
            Node *in = ast_new(NK_INIT, 0);
            in->s = strdup(tgt); in->items = args;
            if (itail) itail->next = in; else inits = in;
            itail = in;
            if (!P_accept("PUNCT", ",")) break;
        }
        body = p_parse_block();
    } else if (P_accept("PUNCT", ";")) {
        body = NULL;                              /* ctor 声明（外联定义） */
    } else {
        body = p_parse_block();
    }
    Node *cd = ast_new(NK_CTOR, 0);
    cd->items = params; cd->c = inits; cd->b = body;
    cd->f1 = explicit_ ? F_EXPLICIT : 0;
    return cd;
}

Node *p_memberdecl(int acc) {
    /* 成员声明：Type name(, name)* ; —— 成员链（f1=访问级） */
    Node *head = NULL, *tail = NULL;
    CType *ty = p_parse_type();
    for (;;) {
        const char *nm = P_expect("ID", NULL)->val;
        Node *m = ast_new(NK_MEMBERDECL, 0);
        m->s = strdup(nm); m->ty = ctype_copy(ty); m->f1 = acc;
        if (tail) tail->next = m; else head = m;
        tail = m;
        if (!P_accept("PUNCT", ",")) break;
    }
    P_expect("PUNCT", ";");
    return head;
}
