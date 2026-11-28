/* cpp_ast.c —— AST 节点构造（0.1.4）。 */
#include "cpp_ast.h"
#include "cpp_parser.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

Node *ast_new(NKind k, int line) {
    Node *n = calloc(1, sizeof(Node));
    if (!n) { fprintf(stderr, "oom\n"); exit(1); }
    n->k = k;
    n->line = line;
    n->ntargs = -1;               /* 默认无显式模板实参 */
    return n;
}

int ast_count(Node *chain) {
    int c = 0;
    for (Node *x = chain; x; x = x->next) c++;
    return c;
}

const char *acc_name(int f1) {
    switch (f1 & 3) {
    case ACC_PRIVATE:   return "private";
    case ACC_PROTECTED: return "protected";
    default:            return "public";
    }
}

Node *cc_tmpl_reg(const char *name, Node *ct) {
    P_ctempls[P_nctempls].name = strdup(name);
    P_ctempls[P_nctempls].ct = ct;
    P_nctempls++;
    return ct;
}

Node *cpp_ast_num(long long v, int line) {
    Node *n = ast_new(NK_NUM, line);
    n->num = (long)v;
    return n;
}

/* ast_deep_copy —— 通用递归深拷贝（0.1.5 S3 模板实例化的 deepcopy 对应物）。
 * sema 期字段（sig/sty/owner 等）不拷——重 check 重建。 */
static CType *ty_deep(CType *t) {
    if (!t) return NULL;
    return ctype_copy(t);                  /* ctype_copy 已是深拷贝 */
}

Node *ast_deep_copy(Node *n) {
    if (!n) return NULL;
    Node *c = malloc(sizeof(Node));
    *c = *n;                                  /* 浅拷贝标量/链表头指针 */
    if (n->s) c->s = strdup(n->s);
    if (n->s2) c->s2 = strdup(n->s2);
    if (n->ty) c->ty = ty_deep(n->ty);
    if (n->targs) {                            /* 类型实参数组深拷 */
        c->targs = malloc(sizeof(CType *) * (size_t)(n->ntargs > 0 ? n->ntargs : 1));
        for (int i = 0; i < n->ntargs; i++) c->targs[i] = ty_deep(n->targs[i]);
    }
    if (n->a) c->a = ast_deep_copy(n->a);
    if (n->b) c->b = ast_deep_copy(n->b);
    if (n->c) c->c = ast_deep_copy(n->c);
    if (n->d) c->d = ast_deep_copy(n->d);
    /* items 链逐元素拷贝（deep_copy 递归已截 next——尾置 NULL 兜底） */
    if (n->items) {
        Node *src = n->items, *tail = NULL;
        c->items = NULL;
        while (src) {
            Node *cp = ast_deep_copy(src);
            cp->next = NULL;
            if (tail) { tail->next = cp; tail = cp; }
            else { c->items = cp; tail = cp; }
            src = src->next;
        }
    }
    c->next = NULL;                            /* 兄弟链不拷（由容器重建） */
    return c;
}
