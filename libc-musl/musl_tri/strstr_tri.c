/* strstr —— musl src/string/strstr.c 的三进制原生改编。
 *
 * 与上游差异：
 *   1. 删除 twobyte/threebyte/fourbyte_strser 字节打包特化
 *      （`n[0]<<8 | n[1]` 等——依赖 8-bit 字节拼接与二进制移位语义）
 *   2. 删除 twoway_strstr（线性算法，含 `bitop` 与移位）
 *   3. 取上游的回退语义：strchr 定位首字符 + 逐位置 strncmp 比对
 *      —— 上游 `h = strchr(h, *n); if (!n[1]) return h;` 之后的
 *      慢路径与朴素法语义一致
 * 语义：返回 haystack 中 needle 首次出现的指针；needle 为空返回 h；
 * 未找到返回 0。
 */
int strchr(const char *s, int c);
int strncmp(const char *l, const char *r, int n);
int strlen(const char *s);

char *strstr(const char *h, const char *n)
{
	int l = strlen(n);

	if (!l) return (char *)h;

	for (; *h; h++) {
		if (*h == *n && strncmp(h, n, l) == 0) return (char *)h;
	}
	return 0;
}
