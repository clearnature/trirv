/* stdio_bridge —— musl stdio 到 TriRV C 子集的桥（批次 6.3a，ADR-25/38 M2）
 *
 * 用法：preprocess_musl.py 产出的纯 C 文本，前面拼接本文件再交 trirv_cc。
 * preprocess 丢弃 #include，本桥补齐 stdio_impl.h/stdio.h 的最小闭包：
 * FILE 结构（musl 原样布局，volatile 剥离、locale 字段降级 void*——
 * T3 宽字符批次再恢复）、F_* 旗标、SEEK/EOF 常量、内部函数原型。
 * 改结构 = 违背「上游 vendor 只读」分工——本桥只补声明不改语义。 */

/* —— preprocess 规则外的前置别名（桥在预处理后拼接，typedef 不被改写）—— */
typedef int size_t;
typedef int off_t;
typedef int ssize_t;
typedef int off64_t;
typedef int intmax_t;
typedef int uintmax_t;
typedef int ptrdiff_t;
typedef int intptr_t;
typedef int uintptr_t;
typedef int wchar_t;
typedef int wint_t;
typedef int va_list_;    /* 占位：stdarg 面见下注 */

/* —— FILE 结构：musl stdio_impl.h 原样字段序（volatile 剥离）—— */
struct _IO_FILE {
	unsigned flags;
	unsigned char *rpos, *rend;
	int (*close)(FILE *);
	unsigned char *wend, *wpos;
	unsigned char *mustbezero_1;
	unsigned char *wbase;
	size_t (*read)(FILE *, unsigned char *, size_t);
	size_t (*write)(FILE *, const unsigned char *, size_t);
	off_t (*seek)(FILE *, off_t, int);
	unsigned char *buf;
	size_t buf_size;
	FILE *prev, *next;
	int fd;
	int pipe_pid;
	int lockcount;
	int mode;
	int lock;
	int lbf;
	void *cookie;
	off_t off;
	char *getln_buf;
	void *mustbezero_2;
	unsigned char *shend;
	off_t shlim, shcnt;
	FILE *prev_locked, *next_locked;
	void *locale;
};
typedef struct _IO_FILE FILE;

/* —— 常量（stdio_impl.h + stdio.h）—— */
#define UNGET 8
#define F_PERM 1
#define F_NORD 4
#define F_NOWR 8
#define F_EOF 16
#define F_ERR 32
#define F_SVB 64
#define F_APP 128
#define F_BADJ 256
#define F_EOF_WIDE 512
#define F_NAVAIL 1024
#define F_TMB 2048
#define BUFSIZ 1024
#define EOF (-1)
#define SEEK_SET 0
#define SEEK_CUR 1
#define SEEK_END 2
#define _IOFBF 0
#define _IOLBF 1
#define _IONBF 2

/* —— 内部函数原型（hidden 剥离后）—— */
int __lockfile(FILE *);
void __unlockfile(FILE *);
size_t __stdio_read(FILE *, unsigned char *, size_t);
size_t __stdio_write(FILE *, const unsigned char *, size_t);
size_t __stdout_write(FILE *, const unsigned char *, size_t);
off_t __stdio_seek(FILE *, off_t, int);
int __stdio_close(FILE *);
int __toread(FILE *);
int __towrite(FILE *);
void __stdio_exit(void);
size_t __fwritex(const unsigned char *, size_t, FILE *);
int __overflow(FILE *, int);
int __uflow(FILE *);
FILE **__ofl_lock(void);
void __ofl_unlock(void);
int __fseeko_unlocked(FILE *, off_t, int);
off_t __ftello_unlocked(FILE *);
int __putc_unlocked(int, FILE *);
int putc_unlocked(int, FILE *);
int getc_unlocked(FILE *);
void __stdio_exit_needed(void);

/* —— libc 面（链期由 musl_tri 其余层/宿主面供给）—— */
void *malloc(size_t);
void free(void *);
void *realloc(void *, size_t);
void *memcpy(void *, const void *, size_t);
void *memset(void *, int, size_t);
size_t strlen(const char *);
int strcmp(const char *, const char *);
long strtol(const char *, char **, int);
int open();
int fcntl();
int close(int);
ssize_t read(int, void *, size_t);
ssize_t write(int, const void *, size_t);
off_t lseek(int, off_t, int);
int unlink(const char *);
int rename(const char *, const char *);
char *getenv(const char *);
int dup2(int, int);
int pipe(int *);
int nanosleep(void *, void *);
int isatty(int);
unsigned strtoul(const char *, char **, int);

/* —— 三值本征面（方言口径：无 <stdarg>，变参由编译器内建路径处理；
 *    vfprintf 族按 6.8/6.9 探针另行落地，本桥不含 va_list）—— */
