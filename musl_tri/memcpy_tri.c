/* memcpy —— musl src/string/memcpy.c 的三进制原生改编。
 *
 * 与上游差异：
 *   1. 删除 #ifdef __GNUC__ 字长打包块（u32 别名读/写、LS/RS 移位拼接
 *      —— 依赖二进制位语义与 4-字节对齐）
 *   2. 删除对齐探测（(uintptr_t)s % 4 等循环与分支）
 *   3. 保留逐字节拷贝主体 —— 进制无关
 * TriRV 视图：tryte 8-bit 视图经 read8/write8 边界保证，内存侧按 tryte
 * 拷贝即语义正确。
 */
void *memcpy(void *dest, const void *src, int n)
{
	unsigned char *d = dest;
	const unsigned char *s = src;

	for (; n; n--) *d++ = *s++;
	return dest;
}
