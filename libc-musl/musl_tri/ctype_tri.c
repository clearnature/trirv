/* ctype_tri —— musl src/ctype 窄字符 15 文件的三值重写（ADR-38 M3）。
 *
 * 查表：ct_tab[c] 一字装每类 3^k 旗标（单 trit 掩码，& 测试语义成立——
 * 见 docs/C-TERNARY-PITFALLS.md）。上游实现是二进制惯用法，在三值方言
 * 静默算错（编译过、运行不崩、数值错——比崩溃危险）：
 *   isalpha: ((unsigned)c|32)-'a' < 26   —— 无符号视图已删 + TOR≠置位
 *   tolower: c | 32                       —— TOR 逐 trit max ≠ 置 bit5
 *   isascii: !(c & ~0x7f)                 —— TAND 掩码塌缩
 * 口径 = mbwc_tri（批次 7）：tryte 0..127 映射 ASCII，其余归 0。
 * 旗标分配（每类占一 trit，3 的幂）：upper=3 lower=9 digit=27 space=81
 * cntrl=243 punct=729 xdigit-AF=2187 xdigit-af=6561；alnum 掩码 = 39 = upper+lower+digit，xdigit 掩码 = 28458 = digit+AF+af（多 trit 掩码此处是「任一置位」测试，非取域）。 */

int ct_tab[128];
int ct_inited;

void ct_init(void) {
    int c;
    int f;
    if (ct_inited) return;
    ct_inited = 1;
    for (c = 0; c < 128; c++) {
        f = 0;
        if (c >= 65 && c <= 90) f = f + 3;
        if (c >= 97 && c <= 122) f = f + 9;
        if (c >= 48 && c <= 57) f = f + 27;
        if (c == 32 || (c >= 9 && c <= 13)) f = f + 81;
        if (c < 32 || c == 127) f = f + 243;
        /* punct：图形字符里扣掉字母/数字（musl：非字母数字非空白的可打印） */
        if (c > 32 && c < 127) {
            if (!(c >= 65 && c <= 90)) {
                if (!(c >= 97 && c <= 122)) {
                    if (!(c >= 48 && c <= 57)) f = f + 729;
                }
            }
        }
        if (c >= 48 && c <= 57) f = f + 2187;
        if (c >= 65 && c <= 70) f = f + 6561;
        if (c >= 97 && c <= 102) f = f + 19683;
        ct_tab[c] = f;
    }
}

/* 类测试：查表 + 单/多 trit 掩码（多 trit 掩码在此是「任一置位」测试：
 * TAND 保留这些 trit 的 min，非零 ⟺ 至少一类命中——与「取域」惯用法
 * 的区别见警示录） */
int isalpha(int c) {
    ct_init();
    if (c < 0 || c > 127) return 0;
    return (ct_tab[c] & 12) != 0;
}

int isalnum(int c) {
    ct_init();
    if (c < 0 || c > 127) return 0;
    return (ct_tab[c] & 39) != 0;
}

int isupper(int c) {
    ct_init();
    if (c < 0 || c > 127) return 0;
    return (ct_tab[c] & 3) != 0;
}

int islower(int c) {
    ct_init();
    if (c < 0 || c > 127) return 0;
    return (ct_tab[c] & 9) != 0;
}

int isdigit(int c) {
    ct_init();
    if (c < 0 || c > 127) return 0;
    return (ct_tab[c] & 27) != 0;
}

int isspace(int c) {
    ct_init();
    if (c < 0 || c > 127) return 0;
    return (ct_tab[c] & 81) != 0;
}

int iscntrl(int c) {
    ct_init();
    if (c < 0 || c > 127) return 0;
    return (ct_tab[c] & 243) != 0;
}

int ispunct(int c) {
    ct_init();
    if (c < 0 || c > 127) return 0;
    return (ct_tab[c] & 729) != 0;
}

int isxdigit(int c) {
    ct_init();
    if (c < 0 || c > 127) return 0;
    return (ct_tab[c] & 28458) != 0;
}

int isblank(int c) {
    if (c == 32) return 1;
    if (c == 9) return 1;
    return 0;
}

int isgraph(int c) {
    ct_init();
    if (c < 0 || c > 127) return 0;
    return c > 32 && c < 127;
}

int isprint(int c) {
    if (c == 32) return 1;
    return isgraph(c);
}

int isascii(int c) {
    return c >= 0 && c < 128;
}

int tolower(int c) {
    if (c >= 65 && c <= 90) return c + 32;
    return c;
}

int toupper(int c) {
    if (c >= 97 && c <= 122) return c - 32;
    return c;
}
