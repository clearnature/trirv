/* memset —— musl src/string/memset.c 的三进制原生改编。
 *
 * 与上游（musl 1.2.5）的差异：
 *   1. 删除 #ifdef __GNUC__ 字长打包块（uint32_t/uint64_t 别名写，
 *      依赖 CHAR_BIT==8 与二进制位运算 —— TriRV tryte=5-trit 不适用）
 *   2. 删除对齐预处理段（k = -(uintptr_t)s & 3; s += k; n -= k; n &= -4;
 *      k = 24 + ((uintptr_t)s & 4);）—— & 掩码是二进制位语义，
 *      tryte 上三值逻辑结果不同且无对齐概念（TriRV 内存按 9-tryte 字
 *      天然对齐）
 *   3. 保留 musl 的头尾填充技巧（s[0]/s[n-1]/s[1]/s[2]...）—— 纯赋值，
 *      进制无关；n<=2/6/8 的提前返回同样成立
 * 语义：memset(dest, c, n)：dest[0..n) 填充 (unsigned char)c —— TriRV
 * 视图层语义下按 tryte 填充 c%243（8-bit 视图经 read8/write8 边界保证）。
 */
void *memset(void *dest, int c, int n)
{
	unsigned char *s = dest;

	if (!n) return dest;
	s[0] = c;
	s[n-1] = c;
	if (n <= 2) return dest;
	s[1] = c;
	s[2] = c;
	s[n-2] = c;
	s[n-3] = c;
	if (n <= 6) return dest;
	s[3] = c;
	s[n-4] = c;
	if (n <= 8) return dest;

	for (; n; n--, s++) *s = c;

	return dest;
}
