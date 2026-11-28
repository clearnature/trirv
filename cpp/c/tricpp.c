/* tricpp.c —— C++ 前端 C 移植 driver（0.1.2 骨架→0.1.3 lex）。
 * 子命令形态：selftest-* 为各层自检/差分入口（TDD）；
 * input.cpp output.c 为转译主形态（0.1.12 集成）。
 * L8：产物落套件 tmp/，无 bin 缓存。 */
#include "cpp_types.h"
#include "cpp_lexer.h"
#include "cpp_parser.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static char *read_file(const char *path, int *len_out) {
    FILE *fp = fopen(path, "rb");
    if (!fp) { perror(path); exit(2); }
    fseek(fp, 0, SEEK_END);
    long n = ftell(fp);
    fseek(fp, 0, SEEK_SET);
    char *buf = malloc((size_t)n + 1);
    if (fread(buf, 1, (size_t)n, fp) != (size_t)n) {
        fprintf(stderr, "short read: %s\n", path);
        exit(2);
    }
    buf[n] = 0;
    fclose(fp);
    *len_out = (int)n;
    return buf;
}

int main(int argc, char **argv) {
    if (argc >= 2 && !strcmp(argv[1], "selftest-types"))
        return ctype_selftest();
    if (argc == 3 && !strcmp(argv[1], "selftest-parse")) {
        extern Node *cpp_parse_tu(const char *, int, int *);
        int len;
        FILE *fp = fopen(argv[2], "rb");
        if (!fp) { perror(argv[2]); return 2; }
        fseek(fp, 0, SEEK_END);
        len = (int)ftell(fp);
        fseek(fp, 0, SEEK_SET);
        char *buf = malloc((size_t)len + 1);
        if (fread(buf, 1, (size_t)len, fp) != (size_t)len) return 2;
        buf[len] = 0;
        fclose(fp);
        int hm = 0;
        cpp_parse_tu(buf, len, &hm);
        printf("parse OK (main=%d)\n", hm);
        return 0;
    }
    if (argc == 3 && !strcmp(argv[1], "selftest-lex")) {
        int len;
        char *src = read_file(argv[2], &len);
        int n;
        Tok *toks = cpp_lex(src, len, &n);
        for (int i = 0; i < n; i++)
            printf("%s\x1f%s\x1f%d\n", toks[i].kind, toks[i].val, toks[i].line);
        free_tokens(toks, n);
        free(src);
        return 0;
    }
    fprintf(stderr,
            "usage: tricpp selftest-types | selftest-lex file.cpp | "
            "input.cpp output.c (v0.1.3)\n");
    return 2;
}
