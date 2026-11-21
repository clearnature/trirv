/* ============================================================
   trirv_musl.c —— musl 风格 libc 层（C 子集实现，TriRV 原生）

   定位：Alpine/musl API 面的 TriRV 最小实现。musl 的架构层
   （src/internal、arch/riscv64）由 trirv_crt.c 的启动链对接；
   本文件提供应用程序真正调用的 libc 函数。

   与 musl 的差异（都源自 TriRV 的定义层，不是功能缺失）：
   - char = 5-trit tryte（0..242 无符号），无 8-bit octet 视图
   - 文件系统是 VM 内建的伪设备（openat 只分配 fd，无真实路径），
     真实文件 IO 待 VM 落地 host passthrough
   - 无 locale/宽字符/多线程（VM 单线程）

   分层（对齐 musl 源码树习惯）：
     [string]  strlen/strcpy/strcat/strcmp/strncmp/memcpy/memset/memcmp
     [stdlib]  abs/atoi 依 malloc 层（trirv_libc.c）
     [unistd]  read/write/exit/brk（syscall 直连，编译器名表）
     [stdio]   printf/putchar/puts（trirv_stdio.c 已有）
     [errno]   __errno_location 返回全局 errno（单线程：直接给地址）
   ============================================================ */

/* ---- errno（musl: __errno_location() 返回 TLS 地址；TriRV 单线程，全局） ---- */
int __triv_errno = 0;
int *__errno_location(void) { return &__triv_errno; }

/* ==================== [string] ==================== */
/* strlen 在 trirv_stdio.c；此处不重复定义（链接期文本拼接去重由用户保证） */

void *memcpy(void *dst, const void *src, unsigned n) {
    unsigned char *d = dst;
    const unsigned char *s = src;
    unsigned i;
    for (i = 0; i < n; i++) d[i] = s[i];
    return dst;
}

void *memset(void *s, int c, unsigned n) {
    unsigned char *p = s;
    unsigned i;
    for (i = 0; i < n; i++) p[i] = c;
    return s;
}

int memcmp(const void *a, const void *b, unsigned n) {
    const unsigned char *x = a;
    const unsigned char *y = b;
    unsigned i;
    for (i = 0; i < n; i++) {
        if (x[i] != y[i]) return x[i] < y[i] ? -1 : 1;
    }
    return 0;
}

char *strcpy(char *d, const char *s) {
    int i = 0;
    while (s[i] != 0) { d[i] = s[i]; i = i + 1; }
    d[i] = 0;
    return d;
}

/* strlcpy 语义（musl 风格的安全拷贝）：最多拷 size-1 字节 + NUL，返回 src 长度 */
unsigned strlcpy(char *d, const char *s, unsigned size) {
    int slen = 0;
    int i = 0;
    while (s[slen] != 0) slen = slen + 1;
    if (size > 0) {
        while (i < slen && i < size - 1) { d[i] = s[i]; i = i + 1; }
        d[i] = 0;
    }
    return slen;
}

char *strcat(char *d, const char *s) {
    int i = 0;
    int j = 0;
    while (d[i] != 0) i = i + 1;
    while (s[j] != 0) { d[i] = s[j]; i = i + 1; j = j + 1; }
    d[i] = 0;
    return d;
}

int strcmp(const char *a, const char *b) {
    int i = 0;
    while (a[i] != 0 && a[i] == b[i]) i = i + 1;
    if (a[i] == b[i]) return 0;
    return a[i] < b[i] ? -1 : 1;
}

int strncmp(const char *a, const char *b, unsigned n) {
    unsigned i = 0;
    while (i < n && a[i] != 0 && a[i] == b[i]) i = i + 1;
    if (i == n) return 0;
    if (a[i] == b[i]) return 0;
    return a[i] < b[i] ? -1 : 1;
}

/* strchr：返回首字符位置指针，无则 0 */
char *strchr(const char *s, int c) {
    int i = 0;
    while (s[i] != 0) {
        if (s[i] == c) return (char *)(s + i);
        i = i + 1;
    }
    if (c == 0) return (char *)(s + i);
    return 0;
}

/* ==================== [stdlib] ==================== */
int abs(int x) { return x < 0 ? 0 - x : x; }

/* atoi：十进制（musl 走 strtol 全家桶；这里只做最常用形态） */
int atoi(const char *s) {
    int sign = 1;
    int v = 0;
    int i = 0;
    while (s[i] == ' ') i = i + 1;
    if (s[i] == '-') { sign = -1; i = i + 1; }
    else if (s[i] == '+') i = i + 1;
    while (s[i] >= '0' && s[i] <= '9') {
        v = v * 10 + (s[i] - '0');
        i = i + 1;
    }
    return sign * v;
}

/* ==================== [unistd] ==================== */
/* read/write/exit/brk 由编译器 syscall 名表直连 ECALL，无须包装。
 * getpagesize 与 TriRV 的"页"对齐（mmap 区按 9 tryte 对齐分配）——
 * TriRV 无硬件页概念，返回 9（字宽）只作占位语义。 */
int getpagesize(void) { return 9; }
