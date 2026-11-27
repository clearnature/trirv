#!/usr/bin/env python3
"""cpp_lexer —— Layer 1 词法：C++ 超集 token 流（五层架构，对齐 cpp_lexer.c 规划）。

token = (kind, value, line)：kind ∈ ID/NUM/STR/PUNCT/EOF。
超出子集的构造在此不拦（语法层报错），但注释/空白静默剥离。
"""
KEYWORDS = {
    "class", "struct", "public", "private", "protected", "virtual",
    "int", "char", "void", "bool", "const", "unsigned", "static",
    "return", "if", "else", "while", "for", "new", "delete", "this",
    "namespace", "template", "typename", "try", "catch", "throw", "extern",
    "explicit", "static_cast", "dynamic_cast", "reinterpret_cast",
    "const_cast", "throw", "try", "catch",
    "true", "false", "nullptr", "enum", "operator", "friend",
}

# 多字标点：长前缀优先匹配（&& 必须先于 &，-> 先于 -）
PUNCTS = [
    "...", "::", "->", "++", "--", "<<", ">>", "<=", ">=", "==", "!=",
    "&&", "||", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=",
    "<<=", ">>=",
]
SINGLES = set("+-*/%<>=!&|^~?:.,;()[]{}")


class LexError(Exception):
    pass


def lex(src):
    """C++ 源 → token 流。strip 注释与空白；字符串字面量保底。"""
    toks = []
    i, n, line = 0, len(src), 1
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        if c in " \t\r":
            i += 1
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            if j < 0:
                raise LexError(f"L{line}: 未闭合块注释")
            line += src.count("\n", i, j)
            i = j + 2
            continue
        if c == '"':
            j = i + 1
            while j < n and src[j] != '"':
                if src[j] == "\\":
                    j += 1
                j += 1
            if j >= n:
                raise LexError(f"L{line}: 未闭合字符串")
            toks.append(("STR", src[i:j + 1], line))
            i = j + 1
            continue
        if c.isdigit():
            j = i
            while j < n and (src[j].isalnum() or src[j] in "._"):
                j += 1
            toks.append(("NUM", src[i:j], line))
            i = j
            continue
        if c.isalpha() or c == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            w = src[i:j]
            toks.append(("KW" if w in KEYWORDS else "ID", w, line))
            i = j
            continue
        for p in PUNCTS:
            if src.startswith(p, i):
                toks.append(("PUNCT", p, line))
                i += len(p)
                break
        else:
            if c in SINGLES:
                toks.append(("PUNCT", c, line))
                i += 1
            else:
                raise LexError(f"L{line}: 非法字符 {c!r}")
    toks.append(("EOF", "", line))
    return toks
