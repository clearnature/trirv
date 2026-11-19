/* div —— musl src/stdlib/div.c 的三进制原生改编。
 *
 * 与上游差异：
 *   1. div_t 类型定义内联（上游在 <stdlib.h>，TriRV 无头文件系统）
 *   2. 上游的复合字面量 `return (div_t){ num/den, num%den };` 语法
 *      已由 TriRV 编译器原生支持（N_COMPOUND，复用 sret 帧内缓冲）
 * 语义：quot=num/den（向零截断）、rem=num%den（余数符号随被除数），
 * 满足 quot*den+rem==num。TriRV 的 DIV/REM 指令语义与之一致。
 */
typedef struct { int quot; int rem; } div_t;

div_t div(int num, int den)
{
	return (div_t){ num/den, num%den };
}
