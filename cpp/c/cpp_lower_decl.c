/* cpp_lower_decl.c —— S4/L-decl：flat/type_c/decl_class/TI/tiid/globals/
 * sdefs 发射（0.1.6）。对齐 cpp_lower.py 输出格式。 */
#include "cpp_lower.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <stdarg.h>

#define OUTCAP (1 << 16)
static char *OUT;
static size_t OUTN, OUTCAP_N;

void lower_reset_out(void) {
    OUT = calloc(1, OUTCAP);
    OUTCAP_N = OUTCAP;
    OUTN = 0;
}

const char *lower_out(void) { return OUT ? OUT : ""; }

static void emit(const char *text) {
    size_t l = strlen(text);
    while (OUTN + l + 1 > OUTCAP_N) {
        OUTCAP_N *= 2;
        OUT = realloc(OUT, OUTCAP_N);
    }
    memcpy(OUT + OUTN, text, l);
    OUTN += l;
    OUT[OUTN] = 0;
}

static void emitf(const char *fmt, ...) {
    char tmp[4096];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(tmp, sizeof(tmp), fmt, ap);
    va_end(ap);
    emit(tmp);
}

const char *lower_flat(const char *n) {
    static char buf[256];
    snprintf(buf, sizeof(buf), "%s", n);
    for (char *q = buf; *q; q++) if (*q == ':') *q = '_';
    return buf;
}

/* type_c：类型 → C 声明片（struct 扁平前缀；ptr 递归加 *；const 传递） */
const char *lower_type_c(CType *ty) {
    static char buf[512];
    if (ty->kind == TY_PTR)
        snprintf(buf, sizeof(buf), "%s *", lower_type_c(ty->pointee));
    else if (ty->kind == TY_CLASS)
        snprintf(buf, sizeof(buf), "%sstruct %s",
                 ty->is_const ? "const " : "", lower_flat(ty->name));
    else
        snprintf(buf, sizeof(buf), "%s%s",
                 ty->is_const ? "const " : "",
                 ty->kind == TY_INT ? "int" : ty->kind == TY_CHAR ? "char"
                 : ty->kind == TY_BOOL ? "bool" : "void");
    return buf;
}

/* decl_class：class → struct（基类嵌 __base 首成员；多基 __base{i}；
 * 虚根加 __vtbl+__owner）。对齐 Python decl_class。 */
void lower_decl_class(Sema *S, Node *cd) {
    ClassInfo *ci = sema_find_class(S, cd->s2 ? cd->s2 : cd->s);
    if (!ci) return;
    char cname[256];
    snprintf(cname, sizeof(cname), "%s", lower_flat(ci->key));
    char parts[2048] = "";
    int is_virt_root = ci->vslots && !ci->base;
    if (is_virt_root) {
        strncat(parts, "struct ", sizeof(parts) - strlen(parts) - 1);
        strncat(parts, cname, sizeof(parts) - strlen(parts) - 1);
        strncat(parts, "_v *__vtbl; void *__owner;", sizeof(parts) - strlen(parts) - 1);
    }
    for (int i = 0; i < ci->nbases; i++) {
        char piece[300];
        snprintf(piece, sizeof(piece), "struct %s %s;",
                 lower_flat(ci->bases[i]),
                 i == 0 ? "__base" : "__base{i}");
        /* __base{i} 字面替换 */
        char fixed[320];
        snprintf(fixed, sizeof(fixed), "%s", piece);
        if (i > 0) snprintf(fixed, sizeof(fixed), "struct %s __base%d;",
                            lower_flat(ci->bases[i]), i);
        strcpy(piece, fixed);
        strncat(parts, piece, sizeof(parts) - strlen(parts) - 1);
    }
    for (int i = 0; i < ci->nmems; i++) {
        strncat(parts, " ", sizeof(parts) - strlen(parts) - 1);
        strncat(parts, lower_type_c(ci->mems[i].ty), sizeof(parts) - strlen(parts) - 1);
        strncat(parts, " ", sizeof(parts) - strlen(parts) - 1);
        strncat(parts, ci->mems[i].name, sizeof(parts) - strlen(parts) - 1);
        strncat(parts, ";", sizeof(parts) - strlen(parts) - 1);
    }
    char decl[4096];
    if (parts[0])
        snprintf(decl, sizeof(decl), "struct %s { %s };", cname, parts);
    else
        snprintf(decl, sizeof(decl), "struct %s { };", cname);
    emit(decl);
    emit("\n");
}
