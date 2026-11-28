/* cpp_mangle.h —— Itanium 名字修饰 C 移植（0.1.5）。对齐 cpp_mangle.py。 */
#ifndef CPP_MANGLE_H
#define CPP_MANGLE_H

#include "cpp_types.h"
#include "cpp_ast.h"

/* meth: NK_METHOD 节点（ty=ret/items=params/f1=F_CONST 位）；cls=类键 */
char *mangle_method(const char *cls_name, Node *meth);
/* f: NK_FUNCDECL 节点（c_link=裸名直出；ns 内 _ZN..E） */
char *mangle_function(Node *f);

#endif
