/* ============================================================
   trirv_busybox.c —— mini-busybox：Alpine 用户空间模型（TriRV 原生）

   Alpine 的 RISC-V 发行版 = musl libc + busybox。本文件是
   busybox 多调用模型在 TriRV 上的对应物：
     一个二进制，按 argv[0] 分派 applet。
     `-- applet 参数...` 经 VM 的 guest argv 进入 main。

   已实现 applet（各自体现一条 libc/启动链能力）：
     echo     write + argv 遍历 + 空格拼接
     cat      read 循环 -> write（stdin passthrough）
     true     exit 0          false    exit 1
     wc -c    stdin 字节计数（read 循环）
     head -n  按 \n 截断前 N 行
     cmp      两个输入比较（简化：单个 stdin 与字符串比）
     dirname/basename 字符串处理（strchr/strrchr 语义）

   返回值遵循 Unix 惯例：0 成功，1 失败（可作 shell && 判断）。
   ============================================================ */

/* stdout 写包装 */
int bb_write(char *s, int n) { return write(1, s, n); }

int bb_printstr(char *s) {
    int n = 0;
    while (s[n] != 0) n = n + 1;
    if (n > 0) write(1, s, n);
    return n;
}

int bb_printint(int v) {
    char tmp[12];
    int i = 0;
    int j = 0;
    int neg = 0;
    if (v < 0) { neg = 1; v = 0 - v; }
    if (v == 0) { write(1, "0", 1); return 1; }
    while (v > 0) { tmp[i] = '0' + (v % 10); v = v / 10; i = i + 1; }
    if (neg) { write(1, "-", 1); j = j + 1; }
    j = j + i;
    while (i > 0) { i = i - 1; write(1, tmp + i, 1); }
    return j;
}

/* ---------------- echo ---------------- */
int bb_echo(int argc, char **argv) {
    int i = 1;
    while (i < argc) {
        bb_printstr(argv[i]);
        if (i + 1 < argc) write(1, " ", 1);
        i = i + 1;
    }
    write(1, "\n", 1);
    return 0;
}

/* ---------------- cat ---------------- */
int bb_cat(void) {
    char buf[128];
    int n;
    n = read(0, buf, 128);
    while (n > 0) {
        write(1, buf, n);
        n = read(0, buf, 128);
    }
    return 0;
}

/* ---------------- wc -c ---------------- */
int bb_wc(void) {
    char buf[128];
    int total = 0;
    int n;
    n = read(0, buf, 128);
    while (n > 0) {
        total = total + n;
        n = read(0, buf, 128);
    }
    bb_printint(total);
    write(1, "\n", 1);
    return 0;
}

/* ---------------- head -n N ---------------- */
/* 无 libc sscanf，N 用 atoi（musl 层已实现）；行截断按 \n */
int bb_head(int argc, char **argv) {
    int want = 10;
    int i;
    char buf[256];
    int n;
    int lines = 0;
    int k = 0;
    for (i = 1; i < argc; i = i + 1) {
        if (argv[i][0] == '-' && argv[i][1] == 'n' && i + 1 < argc) {
            want = atoi(argv[i + 1]);
            i = i + 1;
        }
    }
    n = read(0, buf, 256);
    i = 0;
    while (i < n && lines < want) {
        write(1, buf + i, 1);
        if (buf[i] == '\n') lines = lines + 1;
        i = i + 1;
    }
    return 0;
}

/* ---------------- dirname / basename ---------------- */
/* 无 strrchr：从尾向前扫 */
int bb_dirname(int argc, char **argv) {
    int last = -1;
    int i = 0;
    if (argc < 2) { write(1, ".\n", 2); return 0; }
    while (argv[1][i] != 0) {
        if (argv[1][i] == '/') last = i;
        i = i + 1;
    }
    if (last <= 0) { write(1, ".\n", 2); return 0; }
    write(1, argv[1], last);
    write(1, "\n", 1);
    return 0;
}

int bb_basename(int argc, char **argv) {
    int last = -1;
    int i = 0;
    if (argc < 2) { write(1, "\n", 1); return 0; }
    while (argv[1][i] != 0) {
        if (argv[1][i] == '/') last = i;
        i = i + 1;
    }
    if (last < 0) { bb_printstr(argv[1]); write(1, "\n", 1); return 0; }
    write(1, argv[1] + last + 1, i - last - 1);
    write(1, "\n", 1);
    return 0;
}

/* ---------------- 分派器（busybox main） ---------------- */
int main(int argc, char **argv) {
    const char *app;
    if (argc < 2) {
        bb_printstr("mini-busybox (TriRV)\napps: echo cat wc head dirname basename true false\n");
        return 1;
    }
    app = argv[1];
    /* applet 分派：strcmp 链（无函数指针表也能表达同样的多调用模型） */
    if (strcmp(app, "echo") == 0)  return bb_echo(argc - 1, argv + 1);
    if (strcmp(app, "cat") == 0)   return bb_cat();
    if (strcmp(app, "wc") == 0)    return bb_wc();
    if (strcmp(app, "head") == 0)  return bb_head(argc - 1, argv + 1);
    if (strcmp(app, "dirname") == 0) return bb_dirname(argc - 1, argv + 1);
    if (strcmp(app, "basename") == 0) return bb_basename(argc - 1, argv + 1);
    if (strcmp(app, "true") == 0)  return 0;
    if (strcmp(app, "false") == 0) return 1;
    bb_printstr("applet not found: ");
    bb_printstr(app);
    write(1, "\n", 1);
    return 127;
}
