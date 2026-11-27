#!/usr/bin/env python3
"""transpiler v0.0.38 —— C++ -> C 降级器（sema 重构：五层架构落地）。

用法：python3 cpp/transpiler.py input.cpp output.c

五层（cpp/src/，对齐 ARCHITECTURE.md §一/§二，C 移植=逐层直译）：
  cpp_lexer   词法 token 流
  cpp_parser  递归下降 AST（子集边界=报错，静默错编比不支持更致命）
  cpp_sema    符号表 + Type{kind: VALUE|REF, is_const}（验收标准）
              N8 引用绑临时量拒绝 / K1 const 写成员 / K2 const 调非 const
  cpp_lower   Type 驱动的 C 形态决策（正则三遍退役）
  cpp_mangle  全 Itanium：_ZN[K]…E…（R/RK 喂点=sema Type）
  cpp_emit    C 源装配

v0.0.4–0.0.7 正则版教训（本版结构化根因）：
  &·& 碰撞 / -> 后误替换 / 对象实参加 & 全靠正则特设——引用语义
  只能靠符号表，正则三遍到此为止（用户拍板第 1 条）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from cpp_lexer import lex, LexError                    # noqa: E402
from cpp_parser import parse, ParseError               # noqa: E402
from cpp_sema import Sema, SemaError                   # noqa: E402
from cpp_lower import lower_tu                         # noqa: E402
from cpp_emit import emit                              # noqa: E402
from cpp_types import TypeError_                       # noqa: E402

VERSION = "0.0.38"


def transpile(src):
    classes, funcs, gdecls, sdefs, main = parse(src)
    sema = Sema()
    sema.check(classes, funcs, gdecls, sdefs, main)
    return emit(lower_tu(classes, funcs, gdecls, sdefs, main, sema.classes))


def main(argv):
    if len(argv) < 3:
        print(f"usage: transpiler.py input.cpp output.c  (v{VERSION})")
        return 1
    try:
        src = open(argv[1]).read()
        c_src = transpile(src)
    except (LexError, ParseError, SemaError, TypeError_) as e:
        print(f"[{type(e).__name__}] {e}", file=sys.stderr)
        return 2
    open(argv[2], "w").write(c_src)
    print(f"v{VERSION}: {argv[1]} -> {argv[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
