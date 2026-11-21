/* strtol —— musl src/stdlib/strtol.c 的三进制原生改编。
 *
 * 与上游差异：
 *   1. 上游经 strtox → FILE/sh_fromstring/__intscan（musl stdio 内部）。
 *      TriRV 无 FILE 子系统 → 直接字符解析；字符类判断内联
 *      （isspace/digittoint 作为独立函数调用时触发 TriRV C 后端
 *      callee-saved 重分配的已知问题，后续修复后可换回函数形态）
 *   2. 溢出钳制：上游 LONG_MAX/LONG_MIN；TriRV 全标量等宽映射后为
 *      int 极值
 *   3. errno 省略
 * 支持：base 0 自动判定、base 2..36、空白、+/−、end 写回
 * （musl/glibc：无有效数字时 end=nptr）、溢出钳制。
 */
int strtol(const char *s, char **end, int base)
{
    int neg = 0;
    int acc = 0;
    int any = 0;
    int overflow = 0;
    const char *start = s;

    while (*s == 32 || *s == 9 || *s == 10 || *s == 11 || *s == 12 || *s == 13) s++;
    if (*s == 43) s++;
    else if (*s == 45) { neg = 1; s++; }

    if ((base == 0 || base == 16) && s[0] == 48 && (s[1] == 120 || s[1] == 88)) {
        s += 2;
        base = 16;
    } else if (base == 0) {
        if (s[0] == 48) base = 8;
        else base = 10;
    }

    for (;;) {
        int c = *s;
        int d = -1;
        if (c >= 48 && c <= 57) d = c - 48;
        else if (c >= 97 && c <= 122) d = c - 87;
        else if (c >= 65 && c <= 90) d = c - 55;
        if (d < 0 || d >= base) break;
        if (acc > (2147483647 - d) / base) overflow = 1;
        if (!overflow) acc = acc * base + d;
        any = 1;
        s++;
    }

    if (end) {
        if (!any) end[0] = (char *)start;
        else end[0] = (char *)s;
    }

    if (overflow) return neg ? (-2147483647 - 1) : 2147483647;
    if (neg) return -acc;
    return acc;
}
