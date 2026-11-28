/* cpp_parser_stmt.c —— 语句层：block/stmt/forstmt（0.1.4）。
 * 深度帽 L6：块 >150（包装式计数防平级累加——F8f 教训）。 */
#include "cpp_parser.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

extern int P_blk_depth, P_prim_depth;

Node *p_parse_block(void) {
    if (++P_blk_depth > 150) {            /* 0.1.1 G4：诚实拒绝（校准实证） */
        P_blk_depth--;
        parse_err(P_peek(0)->line, "块嵌套过深（>150）——子集边界");
    }
    Node *blk = ast_new(NK_BLOCK, P_peek(0)->line);
    Node *tail = NULL;
    P_expect("PUNCT", "{");
    while (!P_accept("PUNCT", "}")) {
        Node *st = p_parse_stmt();
        if (tail) tail->next = st; else blk->items = st;
        for (; tail && tail->next; tail = tail->next) {}
        if (!tail) tail = st;
    }
    P_blk_depth--;
    return blk;
}

static Node *p_forstmt(void) {
    PTok *t0 = P_peek(0);
    P_expect("KW", "for");
    P_expect("PUNCT", "(");
    Node *init = NULL;
    if (P_peek(0)->kind == (const char *)"KW" &&
        (!strcmp(P_peek(0)->val, "const") || !strcmp(P_peek(0)->val, "int") ||
         !strcmp(P_peek(0)->val, "char"))) {
        CType *ty = p_parse_type();
        const char *nm = P_expect("ID", NULL)->val;
        Node *ie = P_accept("PUNCT", "=") ? p_parse_expr() : NULL;
        init = ast_new(NK_VARDECL, t0->line);
        init->s = strdup(nm); init->ty = ty; init->a = ie;
    } else if (!(P_peek(0)->kind == (const char *)"PUNCT" &&
                 !strcmp(P_peek(0)->val, ";"))) {
        init = ast_new(NK_EXPRSTMT, t0->line);
        init->a = p_parse_expr();
    }
    P_expect("PUNCT", ";");
    Node *cond = (P_peek(0)->kind == (const char *)"PUNCT" &&
                  !strcmp(P_peek(0)->val, ";")) ? NULL : p_parse_expr();
    P_expect("PUNCT", ";");
    Node *step = (P_peek(0)->kind == (const char *)"PUNCT" &&
                  !strcmp(P_peek(0)->val, ")")) ? NULL : p_parse_expr();
    P_expect("PUNCT", ")");
    Node *f = ast_new(NK_FOR, t0->line);
    f->a = init; f->b = cond; f->c = step; f->d = p_parse_stmt();
    return f;
}

Node *p_parse_stmt(void) {
    PTok *t = P_peek(0);
    if (t->kind == (const char *)"PUNCT" && !strcmp(t->val, "{"))
        return p_parse_block();
    if (t->kind == (const char *)"KW" && !strcmp(t->val, "return")) {
        P_next();
        Node *e = (P_peek(0)->kind == (const char *)"PUNCT" &&
                   !strcmp(P_peek(0)->val, ";")) ? NULL : p_parse_expr();
        P_expect("PUNCT", ";");
        Node *n = ast_new(NK_RETURN, t->line); n->a = e;
        return n;
    }
    if (t->kind == (const char *)"KW" && !strcmp(t->val, "if")) {
        P_next();
        P_expect("PUNCT", "(");
        Node *c = p_parse_expr();
        P_expect("PUNCT", ")");
        Node *th = p_parse_stmt();
        Node *el = P_accept("KW", "else") ? p_parse_stmt() : NULL;
        Node *n = ast_new(NK_IF, t->line);
        n->a = c; n->b = th; n->c = el;
        return n;
    }
    if (t->kind == (const char *)"KW" && !strcmp(t->val, "while")) {
        P_next();
        P_expect("PUNCT", "(");
        Node *c = p_parse_expr();
        P_expect("PUNCT", ")");
        Node *n = ast_new(NK_WHILE, t->line);
        n->a = c; n->b = p_parse_stmt();
        return n;
    }
    if (t->kind == (const char *)"KW" && !strcmp(t->val, "throw")) {
        P_next();
        Node *e = (P_peek(0)->kind == (const char *)"PUNCT" &&
                   !strcmp(P_peek(0)->val, ";")) ? NULL : p_parse_expr();
        P_expect("PUNCT", ";");
        Node *n = ast_new(NK_THROW, t->line); n->a = e;
        return n;
    }
    if (t->kind == (const char *)"KW" && !strcmp(t->val, "try")) {
        P_next();
        Node *tbody = p_parse_block();
        Node *catches = NULL, *ctail = NULL;
        while (P_peek(0)->kind == (const char *)"KW" &&
               !strcmp(P_peek(0)->val, "catch")) {
            P_next();
            P_expect("PUNCT", "(");
            if (P_accept("PUNCT", "...")) {
                P_expect("PUNCT", ")");
                Node *arm = ast_new(NK_CATCHARM, t->line);
                arm->ty = NULL; arm->b = p_parse_block();
                if (ctail) ctail->next = arm; else catches = arm;
                ctail = arm;
                continue;
            }
            CType *cty = p_parse_type();
            const char *cvar = P_expect("ID", NULL)->val;
            P_expect("PUNCT", ")");
            Node *arm = ast_new(NK_CATCHARM, t->line);
            arm->ty = cty; arm->s = strdup(cvar); arm->b = p_parse_block();
            if (ctail) ctail->next = arm; else catches = arm;
            ctail = arm;
        }
        if (!catches)
            parse_err(t->line, "try 无 catch（0.1.1c 审计 F4：曾误收）");
        Node *n = ast_new(NK_TRY, t->line);
        n->a = tbody; n->items = catches;
        return n;
    }
    if (t->kind == (const char *)"KW" && !strcmp(t->val, "for"))
        return p_forstmt();
    if (t->kind == (const char *)"KW" && !strcmp(t->val, "delete")) {
        P_next();
        Node *e = p_parse_expr();
        P_expect("PUNCT", ";");
        Node *n = ast_new(NK_DELETESTMT, t->line); n->a = e;
        return n;
    }
    if (t->kind == (const char *)"KW" &&
        (!strcmp(t->val, "const") || !strcmp(t->val, "int") ||
         !strcmp(t->val, "char") || !strcmp(t->val, "void") ||
         !strcmp(t->val, "bool"))) {
        CType *ty = p_parse_type();       /* const int& x / int& x / void* p */
        const char *nm = P_expect("ID", NULL)->val;
        Node *init = P_accept("PUNCT", "=") ? p_parse_expr() : NULL;
        P_expect("PUNCT", ";");
        Node *n = ast_new(NK_VARDECL, t->line);
        n->s = strdup(nm); n->ty = ty; n->a = init;
        return n;
    }
    if (t->kind == (const char *)"ID" &&
        P_peek(1)->kind == (const char *)"PUNCT" &&
        !strcmp(P_peek(1)->val, "*")) {
        CType *ty = p_parse_type();       /* Box* p = ... */
        const char *nm = P_expect("ID", NULL)->val;
        Node *init = P_accept("PUNCT", "=") ? p_parse_expr() : NULL;
        P_expect("PUNCT", ";");
        Node *n = ast_new(NK_VARDECL, t->line);
        n->s = strdup(nm); n->ty = ty; n->a = init;
        return n;
    }
    if (t->kind == (const char *)"ID" && P_peek(1)->kind == (const char *)"PUNCT" &&
        !strcmp(P_peek(1)->val, "<")) {
        /* 0.1.1 类模板实例声明：Box<int> var; / var(args); / var = expr; */
        const char *cls = p_qual_name(P_next()->val);
        int ok = 0;
        CType *ty = p_try_tspec(ctype_class(cls, 0), &ok);
        if (!ok)
            parse_err(t->line, "语句级 < 后非类型形态（Box<int> var; 系=子集）");
        cls = ty->name;
        if (P_peek(0)->kind == (const char *)"ID" &&
            P_peek(1)->kind == (const char *)"PUNCT" &&
            !strcmp(P_peek(1)->val, "=")) {
            const char *var = P_next()->val;
            P_expect("PUNCT", "=");
            Node *init = p_parse_expr();
            P_expect("PUNCT", ";");
            Node *n = ast_new(NK_COPYINIT, t->line);
            n->s = strdup(cls); n->s2 = strdup(var); n->a = init;
            return n;
        }
        if (P_peek(0)->kind == (const char *)"ID" &&
            P_peek(1)->kind == (const char *)"PUNCT" &&
            !strcmp(P_peek(1)->val, ";")) {
            const char *var = P_next()->val;
            P_expect("PUNCT", ";");
            Node *n = ast_new(NK_CTORSTMT, t->line);
            n->s = strdup(cls); n->s2 = strdup(var);
            return n;
        }
        if (P_peek(0)->kind == (const char *)"ID" &&
            P_peek(1)->kind == (const char *)"PUNCT" &&
            !strcmp(P_peek(1)->val, "(")) {
            const char *var = P_next()->val;
            P_expect("PUNCT", "(");
            Node *args = NULL, *tail = NULL;
            if (!(P_peek(0)->kind == (const char *)"PUNCT" &&
                  !strcmp(P_peek(0)->val, ")"))) {
                for (;;) {
                    Node *e = p_parse_expr();
                    if (tail) tail->next = e; else args = e;
                    for (; tail && tail->next; tail = tail->next) {}
                    if (!tail) tail = e;
                    if (!P_accept("PUNCT", ",")) break;
                }
            }
            P_expect("PUNCT", ")");
            P_expect("PUNCT", ";");
            Node *n = ast_new(NK_CTORSTMT, t->line);
            n->s = strdup(cls); n->s2 = strdup(var); n->items = args;
            return n;
        }
        parse_err(t->line, "声明形态限 var;/var(args);/var = expr;");
    }
    if (t->kind == (const char *)"ID" && P_peek(1)->kind == (const char *)"ID" &&
        P_peek(2)->kind == (const char *)"PUNCT" &&
        !strcmp(P_peek(2)->val, "=")) {
        const char *cls = p_qual_name(P_next()->val);   /* X var = expr; */
        const char *var = P_next()->val;
        P_expect("PUNCT", "=");
        Node *init = p_parse_expr();
        P_expect("PUNCT", ";");
        Node *n = ast_new(NK_COPYINIT, t->line);
        n->s = strdup(cls); n->s2 = strdup(var); n->a = init;
        return n;
    }
    if (t->kind == (const char *)"ID" && P_peek(1)->kind == (const char *)"ID" &&
        P_peek(2)->kind == (const char *)"PUNCT" &&
        !strcmp(P_peek(2)->val, ";")) {
        const char *cls = p_qual_name(P_next()->val);   /* X var; 无参构造 */
        const char *var = P_next()->val;
        P_expect("PUNCT", ";");
        Node *n = ast_new(NK_CTORSTMT, t->line);
        n->s = strdup(cls); n->s2 = strdup(var);
        return n;
    }
    if (t->kind == (const char *)"ID" && P_peek(1)->kind == (const char *)"PUNCT" &&
        !strcmp(P_peek(1)->val, "::") && P_peek(2)->kind == (const char *)"ID" &&
        P_peek(3)->kind == (const char *)"ID" && P_peek(4)->kind == (const char *)"PUNCT" &&
        !strcmp(P_peek(4)->val, ";")) {
        const char *cls = p_qual_name(P_next()->val);
        const char *var = P_next()->val;
        P_expect("PUNCT", ";");
        Node *n = ast_new(NK_CTORSTMT, t->line);
        n->s = strdup(cls); n->s2 = strdup(var);
        return n;
    }
    if (t->kind == (const char *)"ID" && P_peek(1)->kind == (const char *)"ID" &&
        P_peek(2)->kind == (const char *)"PUNCT" && !strcmp(P_peek(2)->val, "(")) {
        const char *cls = p_qual_name(P_next()->val);   /* X var(args); */
        const char *var = P_next()->val;
        P_expect("PUNCT", "(");
        Node *args = NULL, *tail = NULL;
        if (!(P_peek(0)->kind == (const char *)"PUNCT" &&
              !strcmp(P_peek(0)->val, ")"))) {
            for (;;) {
                Node *e = p_parse_expr();
                if (tail) tail->next = e; else args = e;
                for (; tail && tail->next; tail = tail->next) {}
                if (!tail) tail = e;
                if (!P_accept("PUNCT", ",")) break;
            }
        }
        P_expect("PUNCT", ")");
        P_expect("PUNCT", ";");
        Node *n = ast_new(NK_CTORSTMT, t->line);
        n->s = strdup(cls); n->s2 = strdup(var); n->items = args;
        return n;
    }
    if (t->kind == (const char *)"ID" && P_peek(1)->kind == (const char *)"PUNCT" &&
        !strcmp(P_peek(1)->val, "::") && P_peek(2)->kind == (const char *)"ID" &&
        P_peek(3)->kind == (const char *)"ID" && P_peek(4)->kind == (const char *)"PUNCT" &&
        !strcmp(P_peek(4)->val, "(")) {
        const char *cls = p_qual_name(P_next()->val);
        const char *var = P_next()->val;
        P_expect("PUNCT", "(");
        Node *args = NULL, *tail = NULL;
        if (!(P_peek(0)->kind == (const char *)"PUNCT" &&
              !strcmp(P_peek(0)->val, ")"))) {
            for (;;) {
                Node *e = p_parse_expr();
                if (tail) tail->next = e; else args = e;
                for (; tail && tail->next; tail = tail->next) {}
                if (!tail) tail = e;
                if (!P_accept("PUNCT", ",")) break;
            }
        }
        P_expect("PUNCT", ")");
        P_expect("PUNCT", ";");
        Node *n = ast_new(NK_CTORSTMT, t->line);
        n->s = strdup(cls); n->s2 = strdup(var); n->items = args;
        return n;
    }
    Node *n = ast_new(NK_EXPRSTMT, t->line);
    n->a = p_parse_expr();
    P_expect("PUNCT", ";");
    return n;
}
