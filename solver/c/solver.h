/* solver.h —— solver/ C 层原型头（链接期符号契约）。
 * trirv_cc 不处理 #include：本头由构建方前置到各 TU（musl SHIM 同款）。
 * 命名分层：g_* = graph 层；c3_* = 着色回溯；sat_* = CNF 回溯。 */

void g_build(int m, char *edges);
void g_reset_colors(int *colr, int n);
int g_feas(int v, int c, int *colr);

int color3_solve(int n, int m, char *edges, char *colors_out);

int sat_solve(int nv, int ncl, int *flat, int *clstart, int *assign_out);
