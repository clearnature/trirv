/* cpp_parser.h —— C 移植解析器共享态与入口（0.1.4）。 */
#ifndef CPP_PARSER_H
#define CPP_PARSER_H

#include "cpp_ast.h"
#include "cpp_lexer.h"
#include <setjmp.h>

typedef struct { const char *kind; char *val; int line; } PTok;

extern PTok *P_toks;
extern int P_i, P_n;
extern int P_blk_depth, P_prim_depth;
typedef struct { char *name; Node *ct; } CTMEnt;
extern CTMEnt *P_ctempls;
extern int P_nctempls;
Node *p_templatedef(void);
Node *cc_tmpl_reg(const char *name, Node *ct);
Node *p_ext_class_member(char **tvars, int ntvars, const char *cls_flat,
                         int is_method, Node *member);

PTok *P_peek(int k);
PTok *P_next(void);
int P_at(const char *kind, const char *val);
PTok *P_accept(const char *kind, const char *val);
PTok *P_expect(const char *kind, const char *val);
void parse_err(int line, const char *msg);
int P_try_push(void);                       /* 试探恢复点（0=栈满） */
void P_try_pop(void);
jmp_buf *P_try_buf(int lvl);

Node *p_funcdef(int c_link);
Node *p_classdef(const char *kind, const char *name);
Node *p_ctordef(const char *clsname, int explicit_);
const char *p_op_name(void);
Node *p_methoddef(int acc);
Node *p_methoddef_static(const char *nm, CType *ty, int acc);
Node *p_memberdecl(int acc);
Node *p_parse_params(void);                  /* N_PARAM 链（c=默认实参|NULL） */
CType *p_parse_type(void);
CType *p_try_tspec(CType *base, int *ok);
const char *p_qual_name(const char *first);
Node *p_parse_block(void);
Node *p_parse_expr(void);
Node *p_parse_stmt(void);

#endif /* CPP_PARSER_H */
