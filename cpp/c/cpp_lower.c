/* cpp_lower.c —— 最简可验证 emit（0.1.6 S4a）：
 * 类=struct+TI / 方法=函数 / ctor=函数 / main。
 * 语料子集：test01（类+方法+ctor+main+表达式+控制流）。
 * 不覆盖：EH/模板/多继承（S5/S6 扩展）。 */
#include "cpp_sema.h"
#include "cpp_mangle.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <stdarg.h>

static char *OUT;
static size_t OUTN, OUTCAP;

static void emit(const char *text) {
    size_t l = strlen(text);
    while (OUTN + l + 1 > OUTCAP) {
        OUTCAP = OUTCAP ? OUTCAP * 2 : 65536;
        OUT = realloc(OUT, OUTCAP);
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

void lower_reset_out(void) {
    OUT = calloc(1, 65536);
    OUTN = 0; OUTCAP = 65536;
}

const char *lower_out(void) { return OUT ? OUT : ""; }

const char *lower_flat(const char *n) {
    static char buf[256];
    snprintf(buf, sizeof(buf), "%s", n);
    for (char *q = buf; *q; q++) if (*q == ':') *q = '_';
    return buf;
}

static const char *ty_c(CType *ty) {
    static char buf[512];
    if (ty->kind == TY_PTR) {
        const char *inner = ty_c(ty->pointee);
        snprintf(buf, sizeof(buf), "%s *", inner);
        return buf;
    }
    if (ty->kind == TY_CLASS)
        snprintf(buf, sizeof(buf), "%sstruct %s",
                 ty->is_const ? "const " : "", lower_flat(ty->name));
    else
        snprintf(buf, sizeof(buf), "%s%s",
                 ty->is_const ? "const " : "",
                 ty->kind == TY_INT ? "int" : ty->kind == TY_CHAR ? "char"
                 : ty->kind == TY_BOOL ? "bool" : "void");
    return buf;
}

/* ---- 表达式发射 ---- */
static const char *l_expr(Node *e, Scope *sc);

/* 方法调用接收者 */
static const char *l_recv(Node *o, Scope *sc) {
    if (o->k == NK_THIS) return "this_";
    if (o->k == NK_ID) {
        if (o->ref_kind == RK_MEMBER) {
            static char buf[512];
            snprintf(buf, sizeof(buf), "this_->%s", o->s);
            return buf;
        }
        CType *t = sema_sc_find(sc, o->s);
        if (t && (t->kind == TY_PTR || t->kind == TY_REF))
            return o->s;                     /* 指针/引用本体 */
        return o->s;                          /* 对象名（caller 加 &） */
    }
    return l_expr(o, sc);
}

/* args 链 → 逗号分隔 C 表达式 */
static void l_args(Node *args, Scope *sc, char *out, size_t cap) {
    out[0] = 0;
    for (Node *a = args; a; a = a->next) {
        if (out[0]) strncat(out, ", ", cap - strlen(out) - 1);
        strncat(out, l_expr(a, sc), cap - strlen(out) - 1);
    }
}

static const char *l_expr(Node *e, Scope *sc) {
    static char buf[4096];
    switch (e->k) {
    case NK_NUM:
        snprintf(buf, sizeof(buf), "%ld", e->num);
        return buf;
    case NK_STRLIT:
        snprintf(buf, sizeof(buf), "%s", e->s);
        return buf;
    case NK_ID:
        if (e->ref_kind == RK_STATIC && e->owner) {
            snprintf(buf, sizeof(buf), "%s_%s", lower_flat(e->owner), e->s);
            return buf;
        }
        snprintf(buf, sizeof(buf), "%s", e->s);
        return buf;
    case NK_THIS:
        return "this_";
    case NK_MEMBER: {
        const char *obj = l_expr(e->a, sc);
        snprintf(buf, sizeof(buf), "%s.%s", obj, e->s);
        return buf;
    }
    case NK_METHODCALL: {
        Node *meth = e->sig;
        if (!meth) { buf[0] = 0; return buf; }
        /* 接收者：对象=&name / ptr=name / this=this_ */
        const char *recv = l_recv(e->a, sc);
        snprintf(buf, sizeof(buf), "%s(%s)", meth->mname, recv);
        return buf;
    }
    case NK_FREECALL: {
        char args[2048] = "";
        l_args(e->items, sc, args, sizeof(args));
        snprintf(buf, sizeof(buf), "%s(%s)",
                 e->sig ? e->sig->mname : e->s, args);
        return buf;
    }
    case NK_BIN:
        snprintf(buf, sizeof(buf), "(%s %s %s)",
                 l_expr(e->a, sc), e->s, l_expr(e->b, sc));
        return buf;
    case NK_UN:
        snprintf(buf, sizeof(buf), "(%s%s)", e->s, l_expr(e->a, sc));
        return buf;
    case NK_ASSIGN:
        snprintf(buf, sizeof(buf), "%s = %s",
                 l_expr(e->a, sc), l_expr(e->b, sc));
        return buf;
    case NK_NAMEDCAST:
        snprintf(buf, sizeof(buf), "(%s)(%s)",
                 ty_c(e->ty), l_expr(e->a, sc));
        return buf;
    case NK_NEW: {
        char args[2048] = "";
        l_args(e->items, sc, args, sizeof(args));
        snprintf(buf, sizeof(buf), "new %s%s%s",
                 e->s, args[0] ? "(" : "", args);
        return buf;
    }
    default:
        snprintf(buf, sizeof(buf), "/* ?? %d */", (int)e->k);
        return buf;
    }
}

/* ---- 语句发射 ---- */
static void l_stmt(Node *s, Scope *sc, int is_const);
static void l_stmts(Node *body, Scope *sc, int is_const) {
    for (Node *ss = body; ss; ss = ss->next)
        l_stmt(ss, sc, is_const);
}

static void l_stmt(Node *s, Scope *sc, int is_const) {
    switch (s->k) {
    case NK_VARDECL:
        emitf("%s %s", ty_c(s->ty), s->s);
        if (s->a) emitf(" = %s", l_expr(s->a, sc));
        emitf(";\n");
        break;
    case NK_CTORSTMT:
        emitf("struct %s %s;\n", s->s, s->s2);
        if (s->sig && s->sig->mname) {
            emitf("%s(&%s", s->sig->mname, s->s2);
            for (Node *a = s->items; a; a = a->next)
                emitf(", %s", l_expr(a, sc));
            emitf(");\n");
        }
        break;
    case NK_COPYINIT:
        emitf("%s %s = %s;\n", ty_c(s->ty), s->s2,
              s->a ? l_expr(s->a, sc) : "0");
        break;
    case NK_EXPRSTMT:
        emitf("%s;\n", l_expr(s->a, sc));
        break;
    case NK_RETURN:
        if (s->a) emitf("return %s;\n", l_expr(s->a, sc));
        else emitf("return;\n");
        break;
    case NK_IF:
        emitf("if (%s) ", l_expr(s->a, sc));
        l_stmt(s->b, sc, is_const);
        if (s->c) { emitf("else "); l_stmt(s->c, sc, is_const); }
        break;
    case NK_WHILE:
        emitf("while (%s) ", l_expr(s->a, sc));
        l_stmt(s->b, sc, is_const);
        break;
    case NK_FOR:
        emitf("for (");
        if (s->a) { l_stmt(s->a, sc, is_const); }
        emitf("; ");
        if (s->b) emitf("%s", l_expr(s->b, sc));
        emitf("; ");
        if (s->c) emitf("%s", l_expr(s->c, sc));
        emitf(") ");
        l_stmt(s->d, sc, is_const);
        break;
    case NK_BLOCK:
        emitf("{\n");
        l_stmts(s->items, sc, is_const);
        emitf("}\n");
        break;
    case NK_THROW:
        if (s->a) emitf("throw %s;\n", l_expr(s->a, sc));
        else emitf("throw;\n");
        break;
    case NK_TRY:
        emitf("try ");
        l_stmt(s->b, sc, is_const);
        for (Node *arm = s->items; arm; arm = arm->next) {
            emitf("catch (...) ");
            l_stmt(arm->b, sc, is_const);
        }
        break;
    case NK_DELETESTMT:
        emitf("delete %s;\n", l_expr(s->a, sc));
        break;
    default: break;
    }
}
