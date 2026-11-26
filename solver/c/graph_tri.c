/* graph_tri —— 图层：邻接构建 + 可行性查询（数据与搜索解耦）。
 * ADJ[256] = n≤16 邻接矩阵（ADJ[u*16+v]）。 */
int ADJ[256];

void g_build(int m, char *edges) {
    int i = 0;
    int u;
    int v;
    while (i < 256) {
        ADJ[i] = 0;
        i = i + 1;
    }
    i = 0;
    while (i < m) {
        u = edges[i * 2];
        v = edges[i * 2 + 1];
        ADJ[u * 16 + v] = 1;
        ADJ[v * 16 + u] = 1;
        i = i + 1;
    }
}

void g_reset_colors(int *colr, int n) {
    int i = 0;
    while (i < n) {
        colr[i] = 0;
        i = i + 1;
    }
}

int g_feas(int v, int c, int *colr) {
    int u = 0;
    while (u < v) {
        if (ADJ[v * 16 + u] == 1) {
            if (colr[u] == c) return 0;
        }
        u = u + 1;
    }
    return 1;
}
