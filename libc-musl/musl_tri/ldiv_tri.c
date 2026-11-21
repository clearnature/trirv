/* ldiv —— 与上游语义一致；TriRV 全标量 45-trit 等宽，long 与 int 同型
 * （预处理映射 long→int）。ldiv_t 定义内联（上游在 <stdlib.h>）。
 */
typedef struct { int quot; int rem; } ldiv_t;

ldiv_t ldiv(int num, int den)
{
	return (ldiv_t){ num/den, num%den };
}
