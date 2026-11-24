/* tri_stdio —— TriRV 自包含 stdio 实现（批次 6.4-6.7，musl_tri 三值重写层）
 * 设计参照 picolibc/AVR-Libc 极简 stdio——自包含、无 musl 内部依赖。
 * 旗标编码：3 的幂（trit 掩码——& TAND 天然做单 trit 测试，审计/实现双证）。
 * openat 调用时翻译为 Linux 数值（vdisk 侧按 Linux 口径消费）。
 * 值域：全部字面值直写（无 #define——非预处理文件宏是死文本） */

int openat(int dirfd, char *pathname, int flags, int mode);
int close(int fd);
int read(int fd, const void *buf, int n);
int write(int fd, const void *buf, int n);
int lseek(int fd, int off, int w);

struct tri_FILE {
    int fd;
    int flags;         /* trit 旗标：1=WR, 3=RD, 9=ERR, 27=EOF, 81=APPEND */
    int pos;
    int wpos;
    char wbuf[128];
};
typedef struct tri_FILE TRI_FILE;

char tri_pool[704];
int tri_pool_pos = 0;

void *tri_alloc(int n) {
    if (tri_pool_pos + n > 704) return 0;
    { void *p = tri_pool + tri_pool_pos;
      tri_pool_pos += n;
      return p; }
}

int tri_flush(TRI_FILE *f) {
    int got;
    if (f->wpos == 0) return 0;
    got = write(f->fd, f->wbuf, f->wpos);
    f->wpos = 0;
    if (got < 0) { f->flags |= 9; return -1; }
    f->pos += got;
    return 0;
}

/* 模式解析 → trit 旗标（1=WR, 3=RD）+ Linux openat 旗标分开存 */
int tri_mode_lnx;      /* Linux 数值（给 openat） */
int tri_mode_wr;       /* 1=可写 */
int tri_mode_rd;       /* 1=可读 */
int tri_mode_append;   /* 1=追加 */

int tri_parse_mode(const char *mode) {
    tri_mode_lnx = 0;
    tri_mode_wr = 0;
    tri_mode_rd = 0;
    tri_mode_append = 0;
    if (mode[0] == 114) {                       /* 'r' */
        tri_mode_rd = 1;
        tri_mode_lnx = 0;                        /* O_RDONLY */
        if (mode[1] == 43) { tri_mode_wr = 1; tri_mode_lnx = 2; }  /* r+ */
    } else if (mode[0] == 119) {                 /* 'w' */
        tri_mode_wr = 1;
        tri_mode_lnx = 577;                      /* O_WRONLY|O_CREAT|O_TRUNC */
        if (mode[1] == 43) { tri_mode_rd = 1; tri_mode_lnx = 578; }  /* w+ */
    } else if (mode[0] == 97) {                  /* 'a' */
        tri_mode_wr = 1;
        tri_mode_append = 1;
        tri_mode_lnx = 1025;                     /* O_WRONLY|O_CREAT|O_APPEND */
        if (mode[1] == 43) { tri_mode_rd = 1; tri_mode_lnx = 1026; }  /* a+ */
    }
    return tri_mode_lnx;
}

TRI_FILE *fopen(const char *path, const char *mode) {
    int linux_flags = tri_parse_mode(mode);
    int fd;
    TRI_FILE *f;
    if (tri_mode_wr == 0 && tri_mode_rd == 0) return 0;
    fd = openat(0, path, linux_flags, 0644);
    if (fd < 0) return 0;
    f = tri_alloc(sizeof(TRI_FILE));
    if (!f) { close(fd); return 0; }
    f->fd = fd;
    f->flags = 0;
    if (tri_mode_wr) f->flags |= 1;   /* trit WR */
    if (tri_mode_rd) f->flags |= 3;   /* trit RD */
    if (tri_mode_append) f->flags |= 81;  /* trit APPEND */
    f->pos = 0;
    f->wpos = 0;
    if (tri_mode_append) lseek(fd, 0, 2);
    return f;
}

int fwrite(const void *ptr, int size, int nmemb, TRI_FILE *f) {
    int total;
    const char *p;
    if (!f) return 0;
    if (!(f->flags & 1)) return 0;    /* trit WR 测试（TAND 单 trit min） */
    if (f->flags & 9) return 0;       /* trit ERR 测试 */
    total = size * nmemb;
    p = ptr;
    if (tri_mode_append) lseek(f->fd, 0, 2);
    while (total > 0) {
        int avail = 128 - f->wpos;
        int cp;
        if (avail > total) cp = total; else cp = avail;
        { int i;
          for (i = 0; i < cp; i++) f->wbuf[f->wpos + i] = p[i]; }
        f->wpos += cp;
        p += cp;
        total -= cp;
        if (total > 0) {
            if (tri_flush(f) != 0) return (size * nmemb - total) / size;
            while (total >= 128) {
                int got = write(f->fd, p, total);
                if (got <= 0) { f->flags |= 9; return (size * nmemb - total) / size; }
                f->pos += got;
                p += got;
                total -= got;
            }
        }
    }
    return nmemb;
}

int fread(void *ptr, int size, int nmemb, TRI_FILE *f) {
    int total;
    int got;
    if (!f) return 0;
    if (!(f->flags & 3)) return 0;    /* trit RD 测试 */
    if (f->flags & 27) return 0;      /* trit EOF 测试 */
    if (f->flags & 9) return 0;       /* trit ERR 测试 */
    tri_flush(f);
    total = size * nmemb;
    got = read(f->fd, ptr, total);
    if (got <= 0) { f->flags |= 27; return 0; }
    f->pos += got;
    return got / size;
}

int fclose(TRI_FILE *f) {
    int r = 0;
    if (!f) return -1;
    tri_flush(f);
    if (close(f->fd) != 0) r = -1;
    return r;
}

int fflush(TRI_FILE *f) {
    if (!f) return 0;
    return tri_flush(f);
}

int fseek(TRI_FILE *f, int off, int whence) {
    int r;
    if (!f) return -1;
    tri_flush(f);
    r = lseek(f->fd, off, whence);
    if (r < 0) return -1;
    f->pos = r;
    return 0;
}

int ftell(TRI_FILE *f) {
    if (!f) return -1;
    if (f->flags & 1) return f->pos + f->wpos;
    return f->pos;
}

void rewind(TRI_FILE *f) {
    if (!f) return;
    tri_flush(f);
    lseek(f->fd, 0, 0);
    f->pos = 0;
    f->flags &= ~27;
}

int fgetc(TRI_FILE *f) {
    unsigned char ch;
    int got;
    if (!f) return -1;
    if (f->flags & 27) return -1;
    got = read(f->fd, &ch, 1);
    if (got <= 0) { f->flags |= 27; return -1; }
    f->pos += 1;
    return ch;
}

int fputc(int c, TRI_FILE *f) {
    unsigned char ch;
    int got;
    if (!f) return -1;
    if (tri_mode_append) lseek(f->fd, 0, 2);
    ch = c;
    got = write(f->fd, &ch, 1);
    if (got != 1) { f->flags |= 9; return -1; }
    f->pos += 1;
    return c;
}

int feof(TRI_FILE *f) { return f ? ((f->flags & 27) ? 1 : 0) : 0; }
int ferror(TRI_FILE *f) { return f ? ((f->flags & 9) ? 1 : 0) : 0; }
void clearerr(TRI_FILE *f) { if (f) f->flags &= ~(27 | 9); }
int fileno(TRI_FILE *f) { return f ? f->fd : -1; }

int puts(const char *s) {
    int len = 0;
    const char *p = s;
    while (*p) { p++; len++; }
    write(1, s, len);
    write(1, "\n", 1);
    return len + 1;
}

int fputs(const char *s, TRI_FILE *f) {
    int len = 0;
    const char *p = s;
    if (!f) return -1;
    while (*p) { p++; len++; }
    { int got = write(f->fd, s, len);
      if (got != len) return -1; }
    return len;
}
