/* cpp_mangle.c —— Itanium 修饰实现（0.1.5）。对齐 cpp_mangle.py：
 * _ZN[K]<comps>E<enc>；K=const 成员；R/RK=ref；P=ptr；
 * 类=逐组件 <len>name；算子名=OP_ENC 无长度前缀。 */
#include "cpp_mangle.h"
#include "cpp_ast.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <ctype.h>

static const struct { const char *op, *enc; } OP_ENC[] = {
    {"=", "aS"}, {"+", "pl"}, {"==", "eq"}, {"-", "mi"}, {"*", "ml"},
    {"/", "dv"}, {"<", "lt"}, {">", "gt"}, {"<=", "le"}, {">=", "ge"},
    {"!=", "ne"}, {NULL, NULL},
};

static char M_BUF[512];

static void enc_type_r(char *out, size_t cap, CType *ty) {
    if (ty->kind == TY_REF) {
        strncat(out, ty->pointee->is_const ? "RK" : "R", cap - strlen(out));
        enc_type_r(out, cap, ty->pointee);
        return;
    }
    if (ty->kind == TY_PTR) {
        strncat(out, "P", cap - strlen(out));
        enc_type_r(out, cap, ty->pointee);
        return;
    }
    if (ty->kind == TY_CLASS) {
        /* geo::Box → 3geo3Box */
        char tmp[256];
        snprintf(tmp, sizeof(tmp), "%s", ty->name);
        char *seg = strtok(tmp, "::");
        while (seg) {
            char piece[280];
            snprintf(piece, sizeof(piece), "%zu%s", strlen(seg), seg);
            strncat(out, piece, cap - strlen(out));
            seg = strtok(NULL, "::");
        }
        return;
    }
    const char *m = ty->kind == TY_INT ? "i" : ty->kind == TY_CHAR ? "c"
                  : ty->kind == TY_BOOL ? "b" : ty->kind == TY_VOID ? "v"
                  : ty->kind;
    strncat(out, m, cap - strlen(out));
}

static const char *name_enc(const char *name) {
    static char buf[280];
    if (!strncmp(name, "operator", 8)) {
        const char *op = name + 8;
        for (int i = 0; OP_ENC[i].op; i++)
            if (!strcmp(op, OP_ENC[i].op)) return OP_ENC[i].enc;
        snprintf(buf, sizeof(buf), "%zu%s", strlen(op), op);
        return buf;
    }
    snprintf(buf, sizeof(buf), "%zu%s", strlen(name), name);
    return buf;
}

static void enc_components(char *out, size_t cap, const char *qname) {
    char tmp[256];
    snprintf(tmp, sizeof(tmp), "%s", qname);
    char *seg = strtok(tmp, "::");
    while (seg) {
        char piece[280];
        snprintf(piece, sizeof(piece), "%zu%s", strlen(seg), seg);
        strncat(out, piece, cap - strlen(out));
        seg = strtok(NULL, "::");
    }
}

char *mangle_method(const char *cls_name, Node *meth) {
    char out[512] = "_ZN";
    if (meth->f1 & F_CONST) strcpy(out, "_ZNK");
    enc_components(out + strlen(out), sizeof(out) - strlen(out), cls_name);
    strncat(out, name_enc(meth->s), sizeof(out) - strlen(out) - 1);
    strncat(out, "E", sizeof(out) - strlen(out) - 1);
    int has = 0;
    for (Node *p = meth->items; p; p = p->next) {
        enc_type_r(out, sizeof(out), p->ty);
        has = 1;
    }
    if (!has) strncat(out, "v", sizeof(out) - strlen(out) - 1);
    return strdup(out);
}

char *mangle_function(Node *f) {
    char out[512] = "";
    char enc[512] = "";
    int has = 0;
    for (Node *p = f->items; p; p = p->next) {
        enc_type_r(enc, sizeof(enc), p->ty);
        has = 1;
    }
    if (!has) strcpy(enc, "v");
    if (f->f1 & 1) return strdup(f->s);       /* c_link：裸名直出 */
    const char *nenc = name_enc(f->s);
    if (f->ns) {
        snprintf(out, sizeof(out), "_ZN%zu%s%sE%s", strlen(f->ns), f->ns,
                 nenc, enc);
    } else {
        snprintf(out, sizeof(out), "_Z%s%s", nenc, enc);
    }
    return strdup(out);
}
