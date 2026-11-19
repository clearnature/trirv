/* strcspn —— musl src/string/strcspn.c 的三进制原生改编。
 *
 * 与上游（musl 1.2.5）的差异：
 *   1. 删除 BITOP 字节位图查表（`int byteset[32/sizeof(int)]` +
 *      `|= 1<<(b%32)` 位集合）—— 依赖二进制位运算与 8-bit 字节索引，
 *      TriRV tryte 三值逻辑下 `|`/`&` 语义不同，位图不可用
 *   2. 删除 `__strchrnul` 单字符快路径（该函数在 musl 内部
 *      strchrnul.c，同样含位图实现）—— 朴素循环对单字符场景
 *      语义完全一致
 *   3. 保留上游的语义核心：返回 s 起始段中【不含】 c 中任何字符的
 *      前缀长度（不含结尾 NUL）
 * 算法：朴素双循环 O(len(s)*len(c))，正确性优先（musl 位图本质也是
 * 常数因子优化）。
 */
int strcspn(const char *s, const char *c)
{
	const char *a = s;

	for (; *s; s++) {
		const char *p;
		for (p = c; *p; p++)
			if (*s == *p) return s - a;
	}
	return s - a;
}
