/* lock_tri —— stdio FILE 锁桩（批次 6.2，ADR-25/38 M2；依赖矩阵 libc-musl/stdio_matrix.md §2.1）
 *
 * TriRV VM 单核协作调度（AMP 单步轮转，无真并行）→ FILE 锁恒无争用：
 *   __lockfile 恒 1（首次持有语义——stdio_impl.h 的 FLOCK 宏据返回值决定
 *   FUNLOCK 是否解锁，恒 1 即「持有了、配对解锁」）、__unlockfile 空操作。
 * 用户面 flockfile/ftrylockfile/funlockfile 同口径（递归计数对单核无意义）。
 * 真并行（B3 SMP）立项时按 hub 步进锁回填，符号面不变（只增不改，ADR-09）。 */

/* FILE 只作指针用——前向声明避免依赖 vendor 头 */
struct _IO_FILE;
typedef struct _IO_FILE FILE;

int __lockfile(FILE *f) {
    return 1;
}

void __unlockfile(FILE *f) {
}

void flockfile(FILE *f) {
}

void funlockfile(FILE *f) {
}

int ftrylockfile(FILE *f) {
    return 1;
}
