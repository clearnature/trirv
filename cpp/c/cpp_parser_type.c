/* cpp_parser_type.c —— 类型解析：parse_type/_try_tspec/parse_params/
 * qual_name（0.1.4）。
 * parse_params 统一返回 N_PARAM 链（ty/s/c=默认实参 expr 或 NULL）——
 * defaults 是否存在由调用方扫链判断（0.0.11 方法默认实参面）。 */
#include "cpp_parser.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

const char *p_qual_name(const char *first) {
    /* ID (:: ID)* → "a::b"（strdup 串） */
    char *nm = strdup(first);
    while (P_peek(0)->kind == (const char *)"PUNCT" &&
           !strcmp(P_peek(0)->val, "::")) {
        P_next();
        const char *seg = P_expect("ID", NULL)->val;
        char *n2 = malloc(strlen(nm) + 2 + strlen(seg) + 1);
        strcpy(n2, nm); strcat(n2, "::"); strcat(n2, seg);
        free(nm);
        nm = n2;
    }
    return nm;
}

CType *p_parse_type(void) {
    int is_const = P_accept("KW", "const") ? 1 : 0;
    PTok *t = P_next();
    CType *ty = NULL;
    if (t->kind == (const char *)"KW" && !strcmp(t->val, "int"))
        ty = ctype_int(is_const);
    else if (t->kind == (const char *)"KW" && !strcmp(t->val, "char"))
        ty = ctype_char(is_const);
    else if (t->kind == (const char *)"KW" && !strcmp(t->val, "bool"))
        ty = ctype_bool(is_const);
    else if (t->kind == (const char *)"KW" && !strcmp(t->val, "void"))
        ty = ctype_void();
    else if (t->kind == (const char *)"ID") {
        if (!strcmp(t->val, "size_t")) {
            ty = ctype_int(is_const);         /* 0.0.30 别名 */
        } else {
            const char *qn = p_qual_name(t->val);
            ty = ctype_class(qn, is_const);   /* geo::Box 限定类型 */
            if (P_peek(0)->kind == (const char *)"PUNCT" &&
                !strcmp(P_peek(0)->val, "<")) {
                int ok = 0;
                CType *spec = p_try_tspec(ty, &ok);   /* 0.1.1 Box<int> */
                if (ok) ty = spec;
            }
        }
    } else {
        parse_err(t->line, "不支持的类型");   /* 走恢复栈（试探可回退） */
    }
    if (P_accept("PUNCT", "&"))
        ty = ctype_ref(ty);                   /* ref(ref) 构造层拒绝 */
    while (P_accept("PUNCT", "*"))
        ty = ctype_ptr(ty);
    return ty;
}

/* ID 后 <类型序列> → 平化实例类型（Box<int>→Box__int）；
 * 失配游标复原 ok=0（嵌套 Box<Box<int>>=子集边界拒绝——>> 单 token）。 */
CType *p_try_tspec(CType *base, int *ok) {
    int mark = P_i;
    *ok = 0;
    int lvl = P_try_push();                   /* 试探恢复点（ParseError 对应） */
    if (setjmp(*P_try_buf(lvl))) {
        P_try_pop();
        P_i = mark;
        return NULL;
    }
    if (!P_accept("PUNCT", "<")) { P_try_pop(); P_i = mark; return NULL; }
    CType *arr[64];
    int nargs = 0;
    for (;;) {
        CType *a = p_parse_type();
        if (nargs < 64) arr[nargs] = a;
        nargs++;
        if (!P_accept("PUNCT", ",")) break;
    }
    P_try_pop();
    if (nargs > 64 || !P_accept("PUNCT", ">")) {
        P_i = mark;
        return NULL;                          /* 形态失配——回退比较层 */
    }
    char flat[512] = {0};
    for (int i = 0; i < nargs; i++) {
        const char *seg = type_name(arr[i]);
        char tmp[256];
        snprintf(tmp, sizeof(tmp), "%s", seg);
        for (char *q = tmp; *q; q++) if (*q == ':') *q = '_';
        if (flat[0]) strncat(flat, "__", sizeof(flat) - strlen(flat) - 1);
        strncat(flat, tmp, sizeof(flat) - strlen(flat) - 1);
    }
    char nm[512];
    snprintf(nm, sizeof(nm), "%s__%s", base->name ? base->name : "", flat);
    *ok = 1;
    return ctype_class(nm, base->is_const);
}

/* params：N_PARAM 链（ty/s=name/c=默认实参|NULL），保持声明序 */
Node *p_parse_params(void) {
    Node *head = NULL, *tail = NULL;
    if (P_peek(0)->kind == (const char *)"PUNCT" &&
        !strcmp(P_peek(0)->val, ")"))
        return NULL;
    for (;;) {
        CType *ty = p_parse_type();
        const char *nm = P_expect("ID", NULL)->val;
        Node *d = P_accept("PUNCT", "=") ? p_parse_expr() : NULL;
        Node *pn = ast_new(NK_PARAM, 0);
        pn->ty = ty; pn->s = strdup(nm); pn->c = d;
        if (tail) tail->next = pn; else head = pn;
        tail = pn;
        if (!P_accept("PUNCT", ",")) break;
    }
    return head;
}
