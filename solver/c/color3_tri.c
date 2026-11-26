/* color3_tri —— 着色回溯求解器（search 层；图层 = graph_tri.c）。
 * 搜索序：顶点升序、色 0→1→2 升序——与 py/color3.py 逐位一致。 */
void g_build(int m, char *edges);
void g_reset_colors(int *colr, int n);
int g_feas(int v, int c, int *colr);

int COLR[16];   /* search 层状态（本 TU 私有） */

int c3_bt(int v, int n) {
    int c = 0;
    if (v == n) return 1;
    while (c < 3) {
        if (g_feas(v, c, COLR) == 1) {
            COLR[v] = c;
            if (c3_bt(v + 1, n) == 1) return 1;
        }
        c = c + 1;
    }
    COLR[v] = 0;
    return 0;
}

int color3_solve(int n, int m, char *edges, char *colors_out) {
    int i = 0;
    g_build(m, edges);
    g_reset_colors(COLR, n);
    if (c3_bt(0, n) == 1) {
        while (i < n) {
            colors_out[i] = 48 + COLR[i];
            i = i + 1;
        }
        colors_out[n] = 0;
        return 1;
    }
    colors_out[0] = 0;
    return 0;
}
