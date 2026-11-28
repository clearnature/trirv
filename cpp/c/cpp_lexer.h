/* cpp_lexer.h —— C 移植词法层（0.1.3）。对齐 cpp/src/cpp_lexer.py。
 * token=(kind,val,line)，kind ∈ ID/NUM/STR/PUNCT/KW/EOF；
 * 注释/空白剥离；错误=lex_error（exit(2)，L{n} 同类别文案）。
 * 差分口径：与 Python 侧逐 token 恒等（显式长度遍历——NUL 不终止；
 * bytes>=0x80 视作字母——unicode alpha 近似，已文档化边界）。 */
#ifndef CPP_LEXER_H
#define CPP_LEXER_H

typedef struct {
    const char *kind;
    char *val;          /* owned */
    int line;
} Tok;

Tok *cpp_lex(const char *src, int srclen, int *n_out);
void free_tokens(Tok *toks, int n);

#endif /* CPP_LEXER_H */
