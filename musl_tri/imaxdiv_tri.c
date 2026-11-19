/* imaxdiv —— 与上游语义一致；intmax_t 映射为 int（TriRV 全标量等宽）。
 * imaxdiv_t 定义内联。
 */
typedef struct { int quot; int rem; } imaxdiv_t;

imaxdiv_t imaxdiv(int num, int den)
{
	return (imaxdiv_t){ num/den, num%den };
}
