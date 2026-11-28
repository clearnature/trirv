/* cpp_parser_expr.c —— 表达式层（0.1.4）：优先级爬升/primary/
 * _try_tcall/_try_tspec 差分面。深度帽 L6：primary >45（校准实证）。 */
#include "cpp_parser.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

extern int P_prim_depth;
static const char *BINLV[][3] = {
    {"||", NULL}, {"&&", NULL}, {"|", NULL}, {"^", NULL}, {"&", NULL},
    {"==", "!=", NULL}, {"<", ">", "<=", ">=", NULL},
    {"<<", ">>", NULL}, {"+", "-", NULL}, {"*", "/", "%", NULL},
};
#define NBINLV 10

static Node *p_exprlist_chain(void) {
    Node *head = NULL, *tail = NULL;
    if (P_peek(0)->kind == (const char *)"PUNCT" &&
        !strcmp(P_peek(0)->val, ")"))
        return NULL;                      /* 空实参 f()（Python args=[] 同口径） */
    for (;;) {
        Node *e = p_parse_expr();
        if (tail) tail->next = e; else head = e;
        for (; tail && tail->next; tail = tail->next) {}
        if (!tail) tail = e;
        if (!P_accept("PUNCT", ",")) break;
    }
    return head;
}

static Node *p_binary(int lv);
Node *p_primary_pub(void);

static Node *p_unary(void) {
    PTok *t = P_peek(0);
    if (t->kind == (const char *)"PUNCT" &&
        (!strcmp(t->val, "!") || !strcmp(t->val, "-") ||
         !strcmp(t->val, "+"))) {
        P_next();
        Node *n = ast_new(NK_UN, t->line);
        n->s = strdup(t->val); n->a = p_unary();
        return n;
    }
    if (t->kind == (const char *)"PUNCT" && !strcmp(t->val, "&")) {
        P_next();                         /* 消费 &（缺它=无限递归） */
        Node *n = ast_new(NK_UN, t->line);
        n->s = strdup("&"); n->a = p_unary();   /* 0.0.21 &lvalue */
        return n;
    }
    if (t->kind == (const char *)"PUNCT" && !strcmp(t->val, "*")) {
        P_next();
        Node *n = ast_new(NK_UN, t->line);
        n->s = strdup("*"); n->a = p_unary();   /* 0.0.23 解引用 */
        return n;
    }
    /* postfix */
    Node *e = NULL;
    {
        /* primary 内联入口：深度帽在 p_primary */
        e = p_primary_pub();
    }
    for (;;) {
        PTok *t = P_peek(0);
        if (t->kind == (const char *)"PUNCT" &&
            (!strcmp(t->val, ".") || !strcmp(t->val, "->"))) {
            P_next();
            const char *nm = P_expect("ID", NULL)->val;
            if (P_accept("PUNCT", "(")) {
                Node *args = p_exprlist_chain();
                P_expect("PUNCT", ")");
                Node *mc = ast_new(NK_METHODCALL, t->line);
                mc->a = e; mc->s = strdup(nm); mc->items = args;
                e = mc;
            } else {
                Node *m = ast_new(NK_MEMBER, t->line);
                m->a = e; m->s = strdup(nm);
                m->f1 = !strcmp(t->val, "->");
                e = m;
            }
            continue;
        }
        if (t->kind == (const char *)"PUNCT" && !strcmp(t->val, "++"))
            parse_err(t->line, "后缀 ++ 暂不支持（i += 1 替代）");
        if (t->kind == (const char *)"PUNCT" && !strcmp(t->val, "--"))
            parse_err(t->line, "后缀 -- 暂不支持");
        return e;
    }
}

Node *p_primary_pub(void);

static Node *p_primary_body(void);

Node *p_primary_pub(void) {
    P_prim_depth++;
    if (P_prim_depth > 45) {              /* 0.1.1 G4（校准实证 ~18 帧/层） */
        P_prim_depth--;
        parse_err(0, "表达式嵌套过深（>45）——子集边界");
    }
    Node *n = p_primary_body();
    P_prim_depth--;
    return n;
}

static Node *p_primary_body(void) {
    PTok *t = P_next();
    if (t->kind == (const char *)"NUM") {
        const char *sv = t->val;
        if (strchr(sv, '.') || strchr(sv, 'e') || strchr(sv, 'E'))
            parse_err(t->line, "浮点字面量暂不支持");
        /* base-0 语义（0x/0b/前导 0）+ 45-trit 值域（0.1.1 G3） */
        const char *q = sv;
        int base = 10;
        if (q[0] == '0' && (q[1] == 'x' || q[1] == 'X')) { base = 16; q += 2; }
        else if (q[0] == '0' && (q[1] == 'b' || q[1] == 'B')) { base = 2; q += 2; }
        else if (q[0] == '0' && q[1] != 0) { base = 8; q++; }
        __int128 v = 0;                   /* 0.1.1 G3：__int128 累加（long long
                                           * 会先溢出——值域检查失效勘误） */
        for (; *q; q++) {
            int d;
            if (*q >= '0' && *q <= '9') d = *q - '0';
            else if (base == 16 && *q >= 'a' && *q <= 'f') d = *q - 'a' + 10;
            else if (base == 16 && *q >= 'A' && *q <= 'F') d = *q - 'A' + 10;
            else if (base == 10) d = *q - '0';
            else break;
            if (d >= base) break;
            v = v * base + d;
        }
        __int128 lim = 1;
        for (int i = 0; i < 45; i++) lim *= 3;
        if (v >= lim)
            parse_err(t->line, "字面量超 45-trit 字值域（<3^45）");
        Node *n = ast_new(NK_NUM, t->line); n->num = (long)v;
        return n;
    }
    if (t->kind == (const char *)"STR") {
        Node *n = ast_new(NK_STRLIT, t->line);
        n->s = strdup(t->val);
        return n;
    }
    if (t->kind == (const char *)"ID") {
        const char *nm = p_qual_name(t->val);   /* geo::area → 单一名 */
        /* 0.0.40 <T>(…) 形态（_try_tcall：失败游标复原——L3 恢复栈
         * 承载 ParseError 试探语义，p_parse_type 失败长跳至此） */
        {
            int mark = P_i;
            int lvl = P_try_push();
            if (setjmp(*P_try_buf(lvl))) {
                P_try_pop();
                P_i = mark;
            } else {
                if (!P_accept("PUNCT", "<")) {
                    P_try_pop(); P_i = mark;
                } else {
                    CType *arr[64];
                    int ntargs = 0;
                    for (;;) {
                        CType *a = p_parse_type();
                        if (ntargs < 64) arr[ntargs] = ctype_copy(a);
                        ntargs++;
                        if (!P_accept("PUNCT", ",")) break;
                    }
                    P_try_pop();
                    if (ntargs <= 64 && P_accept("PUNCT", ">") &&
                        P_peek(0)->kind == (const char *)"PUNCT" &&
                        !strcmp(P_peek(0)->val, "(")) {
                        P_next();
                        Node *args = p_exprlist_chain();
                        P_expect("PUNCT", ")");
                        CType **tarr = malloc(sizeof(CType *) * (size_t)ntargs);
                        for (int i = 0; i < ntargs; i++)
                            tarr[i] = ctype_copy(arr[i]);
                        Node *fc = ast_new(NK_FREECALL, t->line);
                        fc->s = strdup(nm); fc->items = args;
                        fc->targs = tarr; fc->ntargs = ntargs;
                        P_try_pop();          /* 0.1.5 成功路径 pop（泄漏曾致
                                              * 后续 parse_err 跳死帧挂死） */
                        return fc;
                    }
                    P_i = mark;           /* 失配复原回退比较层 */
                }
            }
        }
        if (P_peek(0)->kind == (const char *)"PUNCT" &&
            !strcmp(P_peek(0)->val, "(")) {
            P_next();
            Node *args = p_exprlist_chain();
            P_expect("PUNCT", ")");
            Node *fc = ast_new(NK_FREECALL, t->line);
            fc->s = strdup(nm); fc->items = args; fc->ntargs = -1;
            return fc;
        }
        Node *n = ast_new(NK_ID, t->line);
        n->s = strdup(nm);
        return n;
    }
    if (t->kind == (const char *)"KW" &&
        (!strcmp(t->val, "static_cast") || !strcmp(t->val, "reinterpret_cast") ||
         !strcmp(t->val, "const_cast") || !strcmp(t->val, "dynamic_cast"))) {
        P_expect("PUNCT", "<");
        CType *ty = p_parse_type();
        P_expect("PUNCT", ">");
        P_expect("PUNCT", "(");
        Node *e = p_parse_expr();
        P_expect("PUNCT", ")");
        Node *n = ast_new(NK_NAMEDCAST, t->line);
        n->s = strdup(t->val); n->ty = ty; n->a = e;
        return n;
    }
    if (t->kind == (const char *)"KW" && !strcmp(t->val, "this")) {
        Node *n = ast_new(NK_THIS, t->line);
        return n;
    }
    if (t->kind == (const char *)"KW" && !strcmp(t->val, "new")) {
        const char *cn = p_qual_name(P_expect("ID", NULL)->val);
        Node *args = NULL;
        if (P_accept("PUNCT", "(")) {
            args = p_exprlist_chain();
            P_expect("PUNCT", ")");
        }
        Node *n = ast_new(NK_NEW, t->line);
        n->s = strdup(cn); n->items = args;
        return n;
    }
    if (t->kind == (const char *)"KW" && !strcmp(t->val, "delete"))
        parse_err(t->line, "delete 是语句：delete p;（表达式位置不支持）");
    if (t->kind == (const char *)"PUNCT" && !strcmp(t->val, "(")) {
        Node *e = p_parse_expr();
        P_expect("PUNCT", ")");
        return e;
    }
    parse_err(t->line, "意外（primary）");
}

static Node *p_binary(int lv) {
    if (lv >= NBINLV) return p_unary();
    Node *l = p_binary(lv + 1);
    int n_ops = 0;
    for (;;) {
        PTok *t = P_peek(0);
        if (t->kind != (const char *)"PUNCT") break;
        int in = 0;
        for (int i = 0; BINLV[lv][i]; i++)
            if (!strcmp(t->val, BINLV[lv][i])) { in = 1; break; }
        if (!in) break;
        n_ops++;
        if (n_ops > 200)                  /* 0.1.1 G4：同层链长限制 */
            parse_err(t->line, "表达式链过长（>200 操作符）——子集边界");
        PTok *op = P_next();
        Node *b = ast_new(NK_BIN, t->line);
        b->s = strdup(op->val);
        b->a = l;
        b->b = p_binary(lv + 1);
        l = b;
    }
    return l;
}

Node *p_parse_expr(void) {
    Node *l = p_binary(0);
    if (P_accept("PUNCT", "=")) {
        Node *n = ast_new(NK_ASSIGN, 0);
        n->a = l; n->b = p_parse_expr();   /* 右结合 */
        return n;
    }
    return l;
}
