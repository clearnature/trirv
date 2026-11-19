/* lldiv —— 与上游语义一致；long long 经预处理映射为 int（TriRV 全标量
 * 45-trit 等宽，无 128 位扩展——溢出行为与上游在 64 位域的差异由
 * "45-trit 就是 TriRV 的字宽"这一 ISA 决策承担）。lldiv_t 定义内联。
 */
typedef struct { int quot; int rem; } lldiv_t;

lldiv_t lldiv(int num, int den)
{
	return (lldiv_t){ num/den, num%den };
}
