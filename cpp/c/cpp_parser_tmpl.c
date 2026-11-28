/* cpp_parser_tmpl.c —— 模板解析：templatedef 三分支 + 外联成员归属
 * （0.1.4）。对齐 cpp_parser.py templatedef/_ext_class_member。
 * NK_TCLASS 槽：s=name a=decl(ClassDecl) items=tvars 链（NK_PARAM:
 * s=name, ty=dfl|NULL）b=ext_methods 链 c=ext_ctors 链 d=specializations
 * 链（NK_BASE: s=flat, a=ClassDecl）。 */
#include "cpp_parser.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

Node *p_templatedef(void) {
    P_expect("KW", "template");
    P_expect("PUNCT", "<");
    char *tvars[16];
    CType *dfls[16];
    int ntvars = 0;
    for (;;) {
        if (P_peek(0)->kind == (const char *)"PUNCT" &&
            !strcmp(P_peek(0)->val, ">"))
            break;                                /* template<> = 全特化头 */
        P_expect("KW", "typename");
        const char *vn = P_expect("ID", NULL)->val;
        CType *dfl = P_accept("PUNCT", "=") ? p_parse_type() : NULL;
        if (ntvars < 16) {
            tvars[ntvars] = strdup(vn);
            dfls[ntvars] = dfl;
        }
        ntvars++;
        if (!P_accept("PUNCT", ",")) break;
    }
    P_expect("PUNCT", ">");
    PTok *t = P_peek(0);
    if (t->kind == (const char *)"KW" &&
        (!strcmp(t->val, "class") || !strcmp(t->val, "struct"))) {
        /* 0.0.41 类模板：template<typename T> class X {...} */
        const char *kind = P_next()->val;
        const char *cname = P_expect("ID", NULL)->val;
        if (P_accept("PUNCT", ":"))
            parse_err(t->line, "类模板继承=0.0.43 多继承面，暂不支持");
        if (ntvars == 0 && P_peek(0)->kind == (const char *)"PUNCT" &&
            !strcmp(P_peek(0)->val, "<")) {
            /* 0.0.42 全特化：template<> class Desc<int> {...} */
            P_next();
            char flat[512];
            snprintf(flat, sizeof(flat), "%s__", cname);
            int first = 1;
            for (;;) {
                CType *a = p_parse_type();
                char tmp[256];
                snprintf(tmp, sizeof(tmp), "%s", type_name(a));
                for (char *q = tmp; *q; q++) if (*q == ':') *q = '_';
                if (!first) strncat(flat, "__", sizeof(flat) - strlen(flat) - 1);
                strncat(flat, tmp, sizeof(flat) - strlen(flat) - 1);
                first = 0;
                if (!P_accept("PUNCT", ",")) break;
            }
            P_expect("PUNCT", ">");           /* 0.1.1c 补漏（曾致 期望{ 得>） */
            Node *ct = NULL;
            for (int i = 0; i < P_nctempls; i++)
                if (!strcmp(P_ctempls[i].name, cname)) { ct = P_ctempls[i].ct; break; }
            if (!ct)
                parse_err(t->line, "全特化须在主模板之后");
            if (P_peek(0)->kind == (const char *)"PUNCT" &&
                !strcmp(P_peek(0)->val, ";")) {
                P_next();
                return ct;                        /* 特化声明（须完整定义） */
            }
            Node *cd = p_classdef(kind, cname);
            Node *sp = ast_new(NK_BASE, 0);
            sp->s = strdup(flat); sp->a = cd;
            sp->next = ct->d; ct->d = sp;         /* specializations 链 */
            return ct;
        }
        Node *cd = p_classdef(kind, cname);
        Node *node = ast_new(NK_TCLASS, t->line);
        node->s = strdup(cname); node->a = cd;
        for (int i = ntvars - 1; i >= 0; i--) {   /* tvars 链（声明序） */
            Node *tv = ast_new(NK_PARAM, 0);
            tv->s = strdup(tvars[i]); tv->ty = dfls[i];
            tv->next = node->items; node->items = tv;
        }
        node->b = node->c = node->d = NULL;       /* ext/specializations 链头 */
        cc_tmpl_reg(cname, node);                 /* 外联定义归模板（0.0.41） */
        return node;
    }
    CType *ty = p_parse_type();
    PTok *t2 = P_peek(0);
    if (t2->kind == (const char *)"PUNCT" && !strcmp(t2->val, "::")) {
        /* 0.0.41 ctor 外联：Box<T>::Box(...)（ty=parse_type 吃的 Box<T>） */
        P_next();
        char cls_short[256];
        snprintf(cls_short, sizeof(cls_short), "%s", ty->name);
        char *dd = strstr(cls_short, "__");
        if (dd) *dd = 0;
        PTok *t3 = P_peek(0);
        if (t3->kind != (const char *)"ID" || strcmp(t3->val, cls_short))
            parse_err(t->line, "外联 ctor 形态限 类名::类名(...)");
        Node *ct = p_ctordef(t3->val, 0);         /* ctordef 自行消费 ID+体 */
        return p_ext_class_member(tvars, ntvars, ty->name, 0, ct);
    }
    if (t2->kind == (const char *)"ID" &&
        P_peek(1)->kind == (const char *)"PUNCT" &&
        !strcmp(P_peek(1)->val, "<")) {
        /* 0.0.41 方法外联：T Box<T>::get(...)（ty=ret） */
        CType *cls_ty = p_parse_type();
        P_expect("PUNCT", "::");
        const char *nm = P_expect("ID", NULL)->val;
        P_expect("PUNCT", "(");
        Node *params = p_parse_params();
        P_expect("PUNCT", ")");
        int is_const = P_accept("KW", "const") ? F_CONST : 0;
        Node *md = ast_new(NK_METHOD, t->line);
        md->s = strdup(nm); md->ty = ty; md->items = params;
        md->f1 = is_const; md->b = p_parse_block();
        return p_ext_class_member(tvars, ntvars, cls_ty->name, 1, md);
    }
    const char *nm = P_expect("ID", NULL)->val;
    P_expect("PUNCT", "(");
    Node *params = p_parse_params();
    P_expect("PUNCT", ")");
    if (P_accept("KW", "const"))
        parse_err(t->line, "模板函数无 const（无 this 可限定）");
    Node *body = p_parse_block();
    Node *fd = ast_new(NK_TFUNC, t->line);
    fd->s = strdup(nm); fd->ty = ty; fd->items = params; fd->b = body;
    for (int i = ntvars - 1; i >= 0; i--) {       /* tvars 链（声明序） */
        Node *tv = ast_new(NK_PARAM, 0);
        tv->s = strdup(tvars[i]); tv->ty = dfls[i];
        tv->next = fd->c; fd->c = tv;
    }
    return fd;
}

/* 外联成员归模板：cls_flat 须= Name__T（tvars 恒等映射）；
 * 具体实参外联=ParseError；类模板未定义=ParseError。
 * is_method=1 → ext_methods(b 链)；0 → ext_ctors(c 链)。 */
Node *p_ext_class_member(char **tvars, int ntvars, const char *cls_flat,
                         int is_method, Node *member) {
    (void)tvars; (void)ntvars;
    char shortname[256];
    snprintf(shortname, sizeof(shortname), "%s", cls_flat);
    char *dd = strstr(shortname, "__");
    if (dd) *dd = 0;
    Node *ct = NULL;
    for (int i = 0; i < P_nctempls; i++)
        if (!strcmp(P_ctempls[i].name, shortname)) { ct = P_ctempls[i].ct; break; }
    if (!ct)
        parse_err(0, "类模板未定义（外联定义须在类模板之后）");
    char flat_tvars[512];
    snprintf(flat_tvars, sizeof(flat_tvars), "%s__", ct->s);
    for (Node *tv = ct->items; tv; tv = tv->next)
        strncat(flat_tvars, tv->s, sizeof(flat_tvars) - strlen(flat_tvars) - 1);
    if (strcmp(cls_flat, flat_tvars))
        parse_err(0, "外联定义限模板参数形态（具体实参外联=E0）");
    Node *wrap = ast_new(NK_BASE, 0);
    wrap->s = strdup(cls_flat); wrap->a = member;
    if (is_method) { wrap->next = ct->b; ct->b = wrap; }
    else { wrap->next = ct->c; ct->c = wrap; }
    return ct;
}
