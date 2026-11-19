/* memmem —— musl src/string/memmem.c 的三进制原生改编。
 *
 * 与上游差异：
 *   1. 删除 twobyte/threebyte/fourbyte 字节打包特化（uint16_t/uint32_t
 *      拼接 + 二进制移位——依赖 8-bit 字节语义）
 *   2. 删除 twoway_memmem（线性算法，byteset/shift 表依赖位图）
 *   3. 取朴素语义：在 h 的前 l 字节中查找 n 的前 nl 字节，返回首现指针；
 *      nl 为 0 或 l 小于 nl 返回 0（与上游 `!nl || l < nl → 0` 一致；
 *      上游对 nl==0 的返回是 h，属 GNU 扩展分歧点——本实现取 POSIX
 *      明确行为，注释标记）
 * 语义：memcmp 式字节比较，不含 NUL 终止假设。
 */
int memcmp(const void *a, const void *b, int n);

void *memmem(const void *h0, int l, const void *n0, int nl)
{
	const unsigned char *h = h0;
	const unsigned char *n = n0;

	if (!nl) return (void *)h;
	if (l < nl) return 0;

	for (; l >= nl; l--, h++) {
		if (memcmp(h, n, nl) == 0) return (void *)h;
	}
	return 0;
}
