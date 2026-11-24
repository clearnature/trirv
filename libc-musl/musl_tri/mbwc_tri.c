/* mbwc_tri —— 批次 7 multibyte+wctype+wcs 三值本征实现（ADR-38 M3/M4）
 *
 * 三值本征特性：char=1 tryte(0..242)，wchar_t=int(9 tryte)。
 * tryte 序列已是"平坦"编码——无 UTF-8 多字节状态机。
 * mbrtowc/wcrtomb = 恒等映射（1 tryte ↔ 1 wchar）。
 * wctype 分类：tryte 0..127 映射 ASCII 标准分类，128..242 归扩展。
 */

/* ---- 7.2 multibyte 核心（恒等映射）---- */

/* mbrtowc：读 1 tryte → 返回 wchar 值；*pstate 不需要（无多字节序列） */
int mbrtowc(int *pwchar, const char *s, int n, int *pstate) {
    if (!s) return 0;               /* 重置状态（无状态可重置） */
    if (n < 1) return -2;           /* incomplete */
    { int t = s[0] % 243;           /* tryte 值域归一 */
      if (t == 0) { if (pwchar) *pwchar = 0; return 0; }  /* NUL */
      if (pwchar) *pwchar = t;
      return 1; }                    /* 消费 1 tryte */
}

/* wcrtomb：wchar → 写 1 tryte */
int wcrtomb(char *s, int wc, int *pstate) {
    if (!s) return 1;                /* 重置状态返回 1 */
    s[0] = wc % 243;                 /* tryte 截断 */
    return 1;
}

/* mbrlen：恒返回 1（每 tryte 一个完整"字符"）或 0（NUL） */
int mbrlen(const char *s, int n, int *pstate) {
    if (!s) return 0;
    if (n < 1) return -2;
    return (s[0] % 243 == 0) ? 0 : 1;
}

/* mbsinit：无状态 → 恒 1（始终在初始状态） */
int mbsinit(int *pstate) { return 1; }

/* mbstowcs：逐 tryte → 逐 wchar */
int mbstowcs(int *dst, const char *src, int n) {
    int i = 0;
    while (src[i] && i < n - 1) { dst[i] = src[i] % 243; i++; }
    if (i < n) { dst[i] = 0; return i; }
    return n;
}

/* wcstombs：逐 wchar → 逐 tryte */
int wcstombs(char *dst, const int *src, int n) {
    int i = 0;
    while (src[i] && i < n - 1) { dst[i] = src[i] % 243; i++; }
    if (i < n) { dst[i] = 0; return i; }
    return n;
}

/* ---- 7.3 wctype 分类（tryte 0..242，0..127 映射 ASCII）---- */

/* isdigit: 48-57 = '0'-'9' */
int isdigit(int c) { return c >= 48 && c <= 57; }
/* isalpha: 65-90 或 97-122 */
int isalpha(int c) { return (c >= 65 && c <= 90) || (c >= 97 && c <= 122); }
/* isalnum */
int isalnum(int c) { return isalpha(c) || isdigit(c); }
/* isspace: 32, 9-13 */
int isspace(int c) { return c == 32 || (c >= 9 && c <= 13); }
/* isupper: 65-90 */
int isupper(int c) { return c >= 65 && c <= 90; }
/* islower: 97-122 */
int islower(int c) { return c >= 97 && c <= 122; }
/* isprint: 32-126 */
int isprint(int c) { return c >= 32 && c <= 126; }
/* iscntrl: 0-31 或 127 */
int iscntrl(int c) { return (c >= 0 && c <= 31) || c == 127; }
/* ispunct: isprint 但非 isalnum */
int ispunct(int c) { return isprint(c) && !isalnum(c); }
/* isxdigit: 48-57, 65-70, 97-102 */
int isxdigit(int c) { return isdigit(c) || (c >= 65 && c <= 70) || (c >= 97 && c <= 102); }
/* toupper */
int toupper(int c) { return (c >= 97 && c <= 122) ? c - 32 : c; }
/* tolower */
int tolower(int c) { return (c >= 65 && c <= 90) ? c + 32 : c; }
