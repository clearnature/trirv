/* syscall_tri.h —— TriRV arch 层：系统调用对接面（musl arch/syscall_arch.h 的
 * TriRV 重写）。上游 riscv64 版本用内联汇编（ecall 指令 + a7 传号），
 * TriRV 无内联汇编（tri-tcc spike），全部换纯 C：
 *   - read/write 路由到 read8/write8（8-bit 视图系统调用，号 5000/5001，
 *     octet <-> 双 tryte [lo=o%243, hi=o/243]）；
 *   - exit 直接调内建 exit（号 93）；
 *   - 其余 Linux 号位（brk/getcwd/openat/close/lseek/...）经 syscall_no 映射，
 *     trirv_cc 内建表见 cc/trirv_cc.c syscall_no()。
 * 本头文件是 musl 源层（musl_src/*.c）的唯一 syscall 对接面：
 * 上游 `__asm__ ecall` 一律替换为本层包装。
 *
 * tri-tcc 路径（2026-11-23 已通）：tri-link 前置链接器内建 `__tri_syscall`
 * 代码块（输出槽 5 = 绝对地址 20；n→a7(x17)、参数整体下移一档、ECALL、
 * JALR ret）。tri-tcc 侧直接声明：
 *   long __tri_syscall(long n, long a0, long a1, long a2, long a3, long a4, long a5);
 * （契约 trirv_tcc_syscall_e2e 7 项；(long)buf 指针实参依赖 gen_cast 的
 * ptr→long 同宽无操作守卫——见 tccgen.c TRIRV 分支坑实录。）
 */
#ifndef _TRIRV_ARCH_SYSCALL_H
#define _TRIRV_ARCH_SYSCALL_H

/* 8-bit 视图系统调用（trirv_cc 内建名，号 5000/5001） */
long read8(int fd, void *buf, unsigned n_octets);
long write8(int fd, const void *buf, unsigned n_octets);

/* TriRV syscall 号：Linux 基线 + 5000 区 TriRV 扩展（8-bit 视图层） */
#define TRI_SYS_write         64
#define TRI_SYS_read          63
#define TRI_SYS_read8         5000
#define TRI_SYS_write8        5001
#define TRI_SYS_exit          93
#define TRI_SYS_brk           214

/* musl read/write 的 TriRV 路由：字节视图经 read8/write8 编解码，
 * 语义与上游 write(2)/read(2) 一致（fd、计数、返回值三对齐）。 */
static inline long __tri_read(int fd, void *buf, unsigned n)
{
	return read8(fd, buf, (unsigned)n);
}

static inline long __tri_write(int fd, const void *buf, unsigned n)
{
	return write8(fd, buf, (unsigned)n);
}

#endif
