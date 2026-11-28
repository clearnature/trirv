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
