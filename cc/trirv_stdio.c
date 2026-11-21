/* ============================================================
   trirv_stdio.c —— TriRV 基于 va_list 的格式化输出（C 子集实现）

   依赖编译器的变参机制：
     - 变参函数形参表末尾 `...`
     - va_list / va_start(ap) / va_arg(ap, type) / va_end(ap)
     - 本 ABI 所有参数等宽 9 tryte，char 实参按整数提升，故 va_arg 按字读取

   对外 API：
     int strlen(char *s)
     int putchar(int c)
     int putstr(char *s)
     int print_int(int v)
     int print_base(unsigned v, unsigned base, int upper)
     int printf(char *fmt, ...)   支持 %d %u %o %x %X %s %c %%
     int puts(char *s)
   ============================================================ */

/* 字符串长度（NUL 结尾，tryte 编址） */
int strlen(char *s) {
    int n = 0;
    while (s[n] != 0) { n = n + 1; }
    return n;
}

/* 输出单个字符：栈上 1-tryte 缓冲 + write(1,buf,1) */
int putchar(int c) {
    char b[1];
    b[0] = c;
    write(1, b, 1);
    return 1;
}

/* 输出字符串 */
int putstr(char *s) {
    int n;
    n = strlen(s);
    if (n > 0) write(1, s, n);
    return n;
}

/* 整数转十进制并逐字符输出，返回字符数（含负号） */
int print_int(int v) {
    char tmp[24];
    int i = 0;
    int j = 0;
    int neg = 0;
    if (v < 0) { neg = 1; v = 0 - v; }
    if (v == 0) {
        putchar('0');
        return 1;
    }
    while (v > 0) {
        tmp[i] = v % 10 + '0';
        i = i + 1;
        v = v / 10;
    }
    if (neg == 1) { putchar('-'); j = j + 1; }
    while (i > 0) {
        i = i - 1;
        putchar(tmp[i]);
        j = j + 1;
    }
    return j;
}

/* 无符号整数按指定基数输出（base=10/8/16，upper=1 用大写 A-F），返回字符数。
   v 声明为 unsigned：% 与 / 走 REMU/DIVU，使 3 补码 word 按无符号解释。 */
int print_base(unsigned v, unsigned base, int upper) {
    char tmp[48];
    int i = 0;
    int j = 0;
    if (v == 0) { putchar('0'); return 1; }
    while (v > 0) {
        unsigned d = v % base;
        char ch;
        if (d < 10) ch = d + '0';
        else if (upper) ch = d - 10 + 'A';
        else ch = d - 10 + 'a';
        tmp[i] = ch;
        i = i + 1;
        v = v / base;
    }
    while (i > 0) {
        i = i - 1;
        putchar(tmp[i]);
        j = j + 1;
    }
    return j;
}

/* 格式化输出：%d/%u/%o/%x/%X 整数 / %s 字符串 / %c 字符 / %% 百分号 */
int printf(char *fmt, ...) {
    va_list ap;
    va_start(ap);
    int n = 0;
    int i = 0;
    char c;
    while (1) {
        c = fmt[i];
        if (c == 0) break;
        if (c != '%') {
            putchar(c);
            i = i + 1;
            n = n + 1;
        } else {
            i = i + 1;
            c = fmt[i];
            if (c == 'd') {
                n = n + print_int(va_arg(ap, int));
                i = i + 1;
            } else if (c == 'u') {
                n = n + print_base(va_arg(ap, unsigned), 10, 0);
                i = i + 1;
            } else if (c == 'o') {
                n = n + print_base(va_arg(ap, unsigned), 8, 0);
                i = i + 1;
            } else if (c == 'x') {
                n = n + print_base(va_arg(ap, unsigned), 16, 0);
                i = i + 1;
            } else if (c == 'X') {
                n = n + print_base(va_arg(ap, unsigned), 16, 1);
                i = i + 1;
            } else if (c == 's') {
                char *s;
                s = va_arg(ap, char*);
                n = n + putstr(s);
                i = i + 1;
            } else if (c == 'c') {
                putchar(va_arg(ap, int));
                i = i + 1;
                n = n + 1;
            } else if (c == '%') {
                putchar('%');
                i = i + 1;
                n = n + 1;
            } else {
                putchar(c);
                i = i + 1;
                n = n + 1;
            }
        }
    }
    va_end(ap);
    return n;
}

/* 输出字符串并换行 */
int puts(char *s) {
    int n;
    n = putstr(s);
    putchar('\n');
    return n + 1;
}
