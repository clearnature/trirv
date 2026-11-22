/* atomic_tri.h —— TriRV arch 层：原子操作（musl arch/atomic_arch.h 的 TriRV
 * 重写）。上游 riscv64 版本用 lr/sc/amoswap 内联汇编；TriRV 单核软核 +
 * 调度非抢占（协作式单线程），原子序退化为普通读写 + 编译器屏障。
 * A 扩展指令（LR/SC/AMO）在 ISA 层存在（双 VM 已实现），若将来上 SMP
 * 模拟再切换为内建；当前口径（与 musl 单核最小集一致）：
 *   a_cas/a_swap/a_fetch_add/a_and/a_or/a_barrier —— 纯 C 实现。
 */
#ifndef _TRIRV_ARCH_ATOMIC_H
#define _TRIRV_ARCH_ATOMIC_H

/* 编译器屏障：trirv_cc/tri-tcc 无 asm barrier，全局 sink 读写近似
 * （单核 + 非抢占调度下无 CPU 重排问题；trirv_cc 无 volatile/激进优化，
 * 跨步合并风险本就不存在——管线预处理面见 preprocess_musl.py） */
static inline void a_barrier(void)
{
	static int a_barrier_sink;
	a_barrier_sink = a_barrier_sink;
}

static inline int a_cas(int *p, int t, int s)
{
	int old = *p;
	if (old == t)
		*p = s;
	return old;
}

static inline int a_swap(int *p, int v)
{
	int old = *p;
	*p = v;
	return old;
}

static inline int a_fetch_add(int *p, int v)
{
	int old = *p;
	*p = old + v;
	return old;
}

static inline void a_and(int *p, int v)
{
	*p &= v;
}

static inline void a_or(int *p, int v)
{
	*p |= v;
}

static inline void a_inc(int *p)
{
	(*p)++;
}

static inline void a_dec(int *p)
{
	(*p)--;
}

static inline int a_ctz_64(unsigned x)  /* TriRV unsigned = 全宽 45-trit（long long 同宽），musl 调用点兼容 */
{
	/* 三进制 ctz = 尾部零 trit 计数。坑实录：二进制惯用法 x&1 在本征
	 * 方言下是 TAND（逐 trit min），奇偶判定失效——用 x%3 判末 trit。 */
	int n = 0;
	while (x != 0 && x % 3 == 0) {
		x /= 3;
		n++;
	}
	return n;
}

#define a_crash() ((void)0) /* 单核：上游 self-destruct 内联汇编无对象 */

#endif
