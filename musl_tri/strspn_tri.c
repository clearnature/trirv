/* strspn —— musl src/string/strspn.c 的三进制原生改编。
 *
 * 与上游差异：
 *   1. 删除 BITOP 字节位图查表（同 strcspn，依赖二进制位运算）
 *   2. 删除单字符快路径特化（朴素循环语义一致）
 *   3. 保留上游语义核心：返回 s 起始段中【只含】 c 中字符的前缀长度
 * 算法：朴素双循环 O(len(s)*len(c))。
 */
int strspn(const char *s, const char *c)
{
	const char *a = s;

	for (; *s; s++) {
		const char *p;
		for (p = c; *p && *p != *s; p++);
		if (!*p) break;
	}
	return s - a;
}
