/* cpp_lexer.c —— 词法层实现（0.1.3）。逐条镜像 cpp_lexer.py 语义：
 * - 显式长度遍历（NUL 字节=普通非法字符，非终止符——Python len(src) 同口径）
 * - PUNCTS 最长前缀（&& 先于 &，<<= 先于 <<）
 * - 块注释跨行计 line；未终止=lex_error
 * - 字符串保引号，\ 跳下一字符；未终止=lex_error
 * - bytes>=0x80 视作字母（unicode alpha 近似——已文档化边界） */
#include "cpp_lexer.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <ctype.h>

static const char *KEYWORDS[] = {
    "class", "struct", "public", "private", "protected", "virtual",
    "int", "char", "void", "bool", "const", "unsigned", "static",
    "return", "if", "else", "while", "for", "new", "delete", "this",
    "namespace", "template", "typename", "try", "catch", "throw", "extern",
    "explicit", "static_cast", "dynamic_cast", "reinterpret_cast",
    "const_cast", "true", "false", "nullptr", "enum", "operator", "friend",
    NULL,
};

static const char *PUNCTS[] = {
    "...", "::", "->", "++", "--", "<<", ">>", "<=", ">=", "==", "!=",
    "&&", "||", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=",
    "<<=", ">>=", NULL,
};

static void lex_error(int line, const char *what) {
    fprintf(stderr, "L%d: %s\n", line, what);
    exit(2);
}

static int is_id_start(unsigned char c) {
    return isalpha(c) || c == '_' || c >= 0x80;
}

static int is_id_cont(unsigned char c) {
    return isalnum(c) || c == '_' || c >= 0x80;
}

static int is_kw(const char *w) {
    for (int i = 0; KEYWORDS[i]; i++)
        if (!strcmp(w, KEYWORDS[i])) return 1;
    return 0;
}

static char *substr_dup(const char *s, int i, int j) {
    char *b = malloc((size_t)(j - i) + 1);
    memcpy(b, s + i, (size_t)(j - i));
    b[j - i] = 0;
    return b;
}

Tok *cpp_lex(const char *src, int srclen, int *n_out) {
    int cap = 64, n = 0, i = 0, line = 1;
    Tok *toks = malloc(sizeof(Tok) * (size_t)cap);
    while (i < srclen) {
        unsigned char c = (unsigned char)src[i];
        if (c == '\n') { line++; i++; continue; }
        if (c == ' ' || c == '\t' || c == '\r') { i++; continue; }
        if (c == '/' && i + 1 < srclen && src[i + 1] == '/') {
            while (i < srclen && src[i] != '\n') i++;
            continue;
        }
        if (c == '/' && i + 1 < srclen && src[i + 1] == '*') {
            int j = i + 2;
            while (j + 1 < srclen && !(src[j] == '*' && src[j + 1] == '/')) {
                if (src[j] == '\n') line++;
                j++;
            }
            if (j + 1 >= srclen) lex_error(line, "未闭合块注释");
            i = j + 2;
            continue;
        }
        if (c == '"') {
            int j = i + 1;
            while (j < srclen && src[j] != '"') {
                if (src[j] == '\\') j++;
                j++;
            }
            if (j >= srclen) lex_error(line, "未闭合字符串");
            if (n == cap) { cap *= 2; toks = realloc(toks, sizeof(Tok) * (size_t)cap); }
            toks[n].kind = "STR"; toks[n].val = substr_dup(src, i, j + 1);
            toks[n].line = line; n++;
            i = j + 1;
            continue;
        }
        if (isdigit(c)) {
            int j = i;
            while (j < srclen && (is_id_cont((unsigned char)src[j])
                                  || src[j] == '.')) j++;
            if (n == cap) { cap *= 2; toks = realloc(toks, sizeof(Tok) * (size_t)cap); }
            toks[n].kind = "NUM"; toks[n].val = substr_dup(src, i, j);
            toks[n].line = line; n++;
            i = j;
            continue;
        }
        if (is_id_start(c)) {
            int j = i;
            while (j < srclen && is_id_cont((unsigned char)src[j])) j++;
            char *w = substr_dup(src, i, j);
            if (n == cap) { cap *= 2; toks = realloc(toks, sizeof(Tok) * (size_t)cap); }
            toks[n].kind = is_kw(w) ? "KW" : "ID";
            toks[n].val = w; toks[n].line = line; n++;
            i = j;
            continue;
        }
        int matched = 0;
        for (int pi = 0; PUNCTS[pi]; pi++) {
            size_t pl = strlen(PUNCTS[pi]);
            if (i + (int)pl <= srclen && !strncmp(src + i, PUNCTS[pi], pl)) {
                if (n == cap) { cap *= 2; toks = realloc(toks, sizeof(Tok) * (size_t)cap); }
                toks[n].kind = "PUNCT"; toks[n].val = strdup(PUNCTS[pi]);
                toks[n].line = line; n++;
                i += (int)pl;
                matched = 1;
                break;
            }
        }
        if (matched) continue;
        if (c != 0 && strchr("+-*/%<>=!&|^~?:.,;()[]{}", c)) {   /* strchr(NUL)=终符陷阱 */
            if (n == cap) { cap *= 2; toks = realloc(toks, sizeof(Tok) * (size_t)cap); }
            toks[n].kind = "PUNCT";
            toks[n].val = malloc(2); toks[n].val[0] = (char)c; toks[n].val[1] = 0;
            toks[n].line = line; n++;
            i++;
            continue;
        }
        lex_error(line, "非法字符");
    }
    if (n == cap) { cap++; toks = realloc(toks, sizeof(Tok) * (size_t)cap); }
    toks[n].kind = "EOF"; toks[n].val = strdup(""); toks[n].line = line; n++;
    *n_out = n;
    return toks;
}

void free_tokens(Tok *toks, int n) {
    for (int i = 0; i < n; i++) free(toks[i].val);
    free(toks);
}
