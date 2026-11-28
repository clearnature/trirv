/* cpp_lower.h —— C 移植 lower 层接口（0.1.6）。
 * AST（sema 后）→ C 文本；对齐 cpp_lower.py 输出格式。 */
#ifndef CPP_LOWER_H
#define CPP_LOWER_H

#include "cpp_ast.h"
#include "cpp_sema.h"

/* 输出缓冲（TU 全量装配后一次写出——emit 拼接面） */
void lower_reset_out(void);
const char *lower_out(void);

/* 各发射面（S4 逐块移植） */
void lower_emit_structs(Sema *S, Node *classes);      /* L-decl */
void lower_emit_globals(Sema *S, Node *gdecls, Node *sdefs);
void lower_emit_funcs(Sema *S, Node *classes, Node *funcs);
void lower_emit_main(Sema *S, Node *main_body);

/* 表达式/语句（stmt/expr 直译——递归互调） */
const char *lower_expr(Node *e, int ctx_ref, Scope *sc);
const char *lower_stmt_text(Node *s, Scope *sc, int is_const);
const char *lower_flat(const char *n);

#endif /* CPP_LOWER_H */
