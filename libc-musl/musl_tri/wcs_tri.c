/* wcs_tri —— 批次 7 wcs/wmem 全字步进（与 str、mem 同构，按 9 tryte 步进）
 * wchar_t = int = 9 tryte。全部函数无 locale 依赖。 */

/* wcslen */
int wcslen(const int *s) {
    int n = 0;
    while (s[n]) n++;
    return n;
}

/* wcscpy */
int *wcscpy(int *dst, const int *src) {
    int i = 0;
    while (src[i]) { dst[i] = src[i]; i++; }
    dst[i] = 0;
    return dst;
}

/* wcsncpy */
int *wcsncpy(int *dst, const int *src, int n) {
    int i = 0;
    while (src[i] && i < n) { dst[i] = src[i]; i++; }
    while (i < n) { dst[i] = 0; i++; }
    return dst;
}

/* wcscat */
int *wcscat(int *dst, const int *src) {
    int d = 0;
    while (dst[d]) d++;
    { int i = 0;
      while (src[i]) { dst[d + i] = src[i]; i++; }
      dst[d + i] = 0; }
    return dst;
}

/* wcscmp */
int wcscmp(const int *a, const int *b) {
    int i = 0;
    while (a[i] && b[i] && a[i] == b[i]) i++;
    return a[i] - b[i];
}

/* wcsncmp */
int wcsncmp(const int *a, const int *b, int n) {
    int i = 0;
    while (i < n && a[i] && b[i] && a[i] == b[i]) i++;
    if (i >= n) return 0;
    return a[i] - b[i];
}

/* wcschr */
int *wcschr(const int *s, int c) {
    int i = 0;
    while (s[i]) { if (s[i] == c) return (int *)&s[i]; i++; }
    return c == 0 ? (int *)&s[i] : 0;
}

/* wcsrchr */
int *wcsrchr(const int *s, int c) {
    int last = -1;
    int i = 0;
    while (s[i]) { if (s[i] == c) last = i; i++; }
    if (c == 0) return (int *)&s[i];
    return last >= 0 ? (int *)&s[last] : 0;
}

/* wmemchr */
int *wmemchr(const int *s, int c, int n) {
    int i;
    for (i = 0; i < n; i++) if (s[i] == c) return (int *)&s[i];
    return 0;
}

/* wmemcmp */
int wmemcmp(const int *a, const int *b, int n) {
    int i;
    for (i = 0; i < n; i++) if (a[i] != b[i]) return a[i] - b[i];
    return 0;
}

/* wmemset */
int *wmemset(int *s, int c, int n) {
    int i;
    for (i = 0; i < n; i++) s[i] = c;
    return s;
}

/* wmemcpy */
int *wmemcpy(int *dst, const int *src, int n) {
    int i;
    for (i = 0; i < n; i++) dst[i] = src[i];
    return dst;
}

/* wmemmove（重叠安全：反向拷贝处理） */
int *wmemmove(int *dst, const int *src, int n) {
    if (dst <= src || dst >= src + n) {
        int i; for (i = 0; i < n; i++) dst[i] = src[i];
    } else {
        int i; for (i = n - 1; i >= 0; i--) dst[i] = src[i];
    }
    return dst;
}
