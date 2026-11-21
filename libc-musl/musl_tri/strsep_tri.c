/* strsep —— 与上游 musl 语义一致（无二进制依赖，属 TriRV 直接可编译；
 * 留此文件作为"musl_tri = 三进制原生改编层"的样例结构参考）。
 * 上游 src/string/strsep.c 无位运算，本文件与上游逐语义等价。
 */
char *strsep(char **str, const char *sep)
{
	char *s = *str, *end;
	if (!s) return 0;
	end = s + strcspn(s, sep);
	if (*end) *end++ = 0;
	else end = 0;
	*str = end;
	return s;
}
