/* cpp_parser.c —— 解析器核心：共享态/基元/parse_tu/extern_c/nsdef/
 * funcdef/maindef（0.1.4）。对齐 cpp_parser.py；错误=exit(2)。 */
#include "cpp_parser.h"
#include "cpp_lexer.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

PTok *P_toks;
int P_i, P_n;
int P_blk_depth, P_prim_depth;
static CTMEnt ctm_buf[256];
CTMEnt *P_ctempls = ctm_buf;
int P_nctempls = 0;
int PP_ERR;

static void p_extern_c(Node **funcs);
static void p_nsdef(Node **classes, Node **funcs);

PTok *P_peek(int k) {
    int i = P_i + k;
    return &P_toks[i < P_n ? i : P_n - 1];
}

PTok *P_next(void) { return &P_toks[P_i++]; }

int P_at(const char *kind, const char *val) {
    PTok *t = P_peek(0);
    if (strcmp(t->kind, kind)) return 0;
    return !val || !strcmp(t->val, val);
}

PTok *P_accept(const char *kind, const char *val) {
    if (P_at(kind, val)) return P_next();
    return NULL;
}

/* L3 试探恢复栈（_try_tcall/_try_tspec 的 try/except ParseError 对应物）：
 * parse_err 在栈非空时 longjmp 到最近试探点（游标由试探点 mark 复原） */
#include <setjmp.h>
static jmp_buf P_JBS[32];
static int P_NJB = 0;
int P_try_push(void) {
    if (P_NJB >= 32) return 0;
    P_NJB++;
    return P_NJB;                          /* 层号（1 起） */
}
void P_try_pop(void) { if (P_NJB > 0) P_NJB--; }
jmp_buf *P_try_buf(int lvl) { return &P_JBS[lvl - 1]; }

void parse_err(int line, const char *msg) {
    if (P_NJB > 0) longjmp(P_JBS[P_NJB - 1], 1);   /* 试探内=静默回退
                                                    * （Python except 同口径） */
    fprintf(stderr, "L%d: %s\n", line, msg);
    exit(2);
}

PTok *P_expect(const char *kind, const char *val) {
    PTok *t = P_next();
    if (strcmp(t->kind, kind) || (val && strcmp(t->val, val))) {
        fprintf(stderr, "L%d: 期望 %s，得 '%s'\n", t->line,
                val ? val : kind, t->val);
        exit(2);
    }
    return t;
}

/* ---- 顶层 ---- */
static Node *ch_tail(Node *head, Node *n) {
    /* 幂等尾插：同节点重复入链=自环（外联 return 的 TCLASS 曾三次
     * 入 templates 链——ct->next=ct 自环挂死，sample 实证） */
    if (!head) { n->next = NULL; return n; }
    Node *e = head;
    while (e->next) { if (e == n) return head; e = e->next; }
    if (e == n) return head;
    n->next = NULL;
    e->next = n;
    return head;
}

Node *cpp_parse_tu(const char *src, int srclen, int *has_main) {
    P_toks = cpp_lex(src, srclen, &P_n);
    P_i = 0;
    Node *classes = NULL, *funcs = NULL, *globals = NULL, *sdefs = NULL,
         *templates = NULL, *main_body = NULL;
    int dbg_it = 0;
    while (strcmp(P_peek(0)->kind, "EOF")) {
        if (++dbg_it % 1000 == 0)
            fprintf(stderr, "[dbg] it=%d i=%d peek=%s/%s\n", dbg_it, P_i,
                    P_peek(0)->kind, P_peek(0)->val);
        PTok *t = P_peek(0);
        if (t->kind == (const char *)"KW" &&
            (!strcmp(t->val, "int") || !strcmp(t->val, "char")) &&
            P_peek(1)->kind == (const char *)"ID" &&
            !strcmp(P_peek(2)->val, "::") && P_peek(2)->kind == (const char *)"PUNCT") {
            P_next();                                 /* int/char 吃掉 */
            const char *cn = P_expect("ID", NULL)->val;   /* 类名 */
            P_expect("PUNCT", "::");
            const char *nm = P_expect("ID", NULL)->val;
            Node *init = P_accept("PUNCT", "=") ? p_parse_expr() : NULL;
            P_expect("PUNCT", ";");
            CType *ty = !strcmp(t->val, "char") ? ctype_char(0) : ctype_int(0);
            Node *sd = ast_new(NK_STATDECL, t->line);
            sd->s2 = strdup(cn); sd->s = strdup(nm); sd->ty = ty; sd->a = init;
            sdefs = ch_tail(sdefs, sd);
            continue;
        }
        if (t->kind == (const char *)"KW" &&
            (!strcmp(t->val, "int") || !strcmp(t->val, "char")) &&
            P_peek(1)->kind == (const char *)"ID" &&
            (!strcmp(P_peek(2)->val, ";") || !strcmp(P_peek(2)->val, "="))) {
            CType *ty = !strcmp(t->val, "char") ? ctype_char(0) : ctype_int(0);
            P_next();
            const char *nm = P_expect("ID", NULL)->val;
            Node *init = P_accept("PUNCT", "=") ? p_parse_expr() : NULL;
            P_expect("PUNCT", ";");
            Node *g = ast_new(NK_VARDECL, t->line);
            g->s = strdup(nm); g->ty = ty; g->a = init;
            globals = ch_tail(globals, g);
            continue;
        }
        if (t->kind == (const char *)"KW" && !strcmp(t->val, "extern")) {
            p_extern_c(&funcs);
            continue;
        }
        if (t->kind == (const char *)"KW" && !strcmp(t->val, "template")) {
            Node *ft = p_templatedef();
            templates = ch_tail(templates, ft);
            continue;
        }
        if (t->kind == (const char *)"KW" && !strcmp(t->val, "namespace")) {
            p_nsdef(&classes, &funcs);
            continue;
        }
        if (t->kind == (const char *)"KW" &&
            (!strcmp(t->val, "class") || !strcmp(t->val, "struct"))) {
            Node *cd = p_classdef(NULL, NULL);
            if (cd) classes = ch_tail(classes, cd);
            continue;
        }
        if (t->kind == (const char *)"KW" && !strcmp(t->val, "int") &&
            !strcmp(P_peek(1)->val, "main")) {
            if (main_body) parse_err(t->line, "main 重定义");
            P_next(); P_next();
            P_expect("PUNCT", "("); P_expect("PUNCT", ")");
            main_body = p_parse_block();
            continue;
        }
        if (t->kind == (const char *)"KW" &&
            (!strcmp(t->val, "void") || !strcmp(t->val, "bool") ||
             !strcmp(t->val, "const") || !strcmp(t->val, "int") ||
             !strcmp(t->val, "char") || !strcmp(t->val, "operator"))) {
            Node *f = p_funcdef(0);
            funcs = ch_tail(funcs, f);
            continue;
        }
        if (t->kind == (const char *)"ID") {
            Node *f = p_funcdef(0);
            funcs = ch_tail(funcs, f);
            continue;
        }
        parse_err(t->line, "不支持的顶层声明");
    }
    if (!main_body)
        parse_err(P_peek(0)->line,
                  "缺 main（可执行 TU 必需——0.1.1 G5）");
    (void)templates; (void)sdefs; (void)globals; (void)classes;
    Node *tu = ast_new(NK_BLOCK, 1);
    tu->a = classes; tu->b = funcs; tu->c = globals; tu->d = sdefs;
    tu->items = templates; tu->num = 1;
    if (has_main) *has_main = 1;
    return tu;
}

static void p_extern_c(Node **funcs) {
    P_expect("KW", "extern");
    PTok *st = P_expect("STR", NULL);
    if (strcmp(st->val, "\"C\"") && strcmp(st->val, "\"C \""))
        parse_err(st->line, "extern 语言链接仅支持 \"C\"");
    if (P_accept("PUNCT", "{")) {
        while (!P_accept("PUNCT", "}"))
            p_funcdef(1);
    } else {
        p_funcdef(1);
    }
}

static void p_nsdef(Node **classes, Node **funcs) {
    P_expect("KW", "namespace");
    PTok *nst = P_expect("ID", NULL);
    P_expect("PUNCT", "{");
    while (!P_accept("PUNCT", "}")) {
        PTok *t = P_peek(0);
        if (t->kind == (const char *)"KW" && !strcmp(t->val, "namespace"))
            parse_err(t->line, "嵌套命名空间=子集外（扁平化）");
        if (t->kind == (const char *)"KW" &&
            (!strcmp(t->val, "class") || !strcmp(t->val, "struct"))) {
            Node *cd = p_classdef(NULL, NULL);
            cd->ns = strdup(nst->val);
            cd->next = *classes; *classes = cd;
            continue;
        }
        if ((t->kind == (const char *)"KW" &&
             (!strcmp(t->val, "void") || !strcmp(t->val, "bool") ||
              !strcmp(t->val, "const") || !strcmp(t->val, "int") ||
              !strcmp(t->val, "char"))) || t->kind == (const char *)"ID") {
            Node *f = p_funcdef(0);
            f->ns = strdup(nst->val);
            f->next = *funcs; *funcs = f;
            continue;
        }
        parse_err(t->line, "命名空间内不支持");
    }
}

/* 自由函数：Type name(params) [{ body }]；extern "C" 允许原型 */
Node *p_funcdef(int c_link) {
    CType *ty = p_parse_type();
    PTok *t = P_peek(0);
    const char *nm;
    if (t->kind == (const char *)"KW" && !strcmp(t->val, "operator"))
        nm = p_op_name();
    else
        nm = P_expect("ID", NULL)->val;
    if (!strcmp(nm, "main"))
        parse_err(t->line, "main 必须是 int main()");
    P_expect("PUNCT", "(");
    Node *params = p_parse_params();
    P_expect("PUNCT", ")");
    if (P_accept("KW", "const"))
        parse_err(t->line, "自由函数无 const 后缀（const 仅限成员函数）");
    /* 审计 F2：自由函数默认实参诚实拒绝 */
    for (Node *p = params; p; p = p->next)
        if (p->c) parse_err(t->line, "自由函数默认实参暂不支持（方法默认实参=0.0.11）");
    Node *body = NULL;
    if (!strcmp(P_peek(0)->val, ";") && c_link) P_next();
    else body = p_parse_block();
    Node *fd = ast_new(NK_FUNCDECL, t->line);
    fd->s = strdup(nm); fd->ty = ty; fd->items = params; fd->b = body;
    fd->f1 = c_link ? 1 : 0;
    return fd;
}

Node *p_maindef(void) {                       /* 0.1.4：main 体经 parse_tu 内联 */
    P_expect("KW", "int");
    return p_parse_block();
}
