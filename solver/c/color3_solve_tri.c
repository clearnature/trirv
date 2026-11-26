/* color3_solve_tri —— 图 3-着色回溯求解器（TriRV 原生，witness-sat 求解侧）。
 *
 * 与 solver/py/color3.py 严格同搜索序（顶点升序、色 0→1→2 升序回溯）
 * ——双端同输入必得同一见证（契约逐字符对拍钉死）。
 * 容量：n ≤ 16（ADJ[256] 邻接矩阵）；回溯深度 ≤ 16。
 * 求解-校验分离：本文件 = 求解侧（搜索重）；校验侧 = witness_tri.c
 * w_coloring_check（轻，可独立审计——lean4checker 同构类比）。 */

int ADJ[256];   /* 邻接矩阵 n ≤ 16：ADJ[u * 16 + v] */
int COLR[16];   /* 当前部分着色 */

int feas(int v, int c) {
    int u = 0;
    while (u < v) {
        if (ADJ[v * 16 + u] == 1) {
            if (COLR[u] == c) return 0;
        }
        u = u + 1;
    }
    return 1;
}

int csolve(int v, int n) {
    int c = 0;
    if (v == n) return 1;
    while (c < 3) {
        if (feas(v, c) == 1) {
            COLR[v] = c;
            if (csolve(v + 1, n) == 1) return 1;
        }
        c = c + 1;
    }
    COLR[v] = 0;
    return 0;
}

/* 返回 1 = SAT（colors_out 含 n 个色数字符 + NUL）；0 = UNSAT（空串）。 */
int color3_solve(int n, int m, char *edges, char *colors_out) {
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
    i = 0;
    while (i < n) {
        COLR[i] = 0;
        i = i + 1;
    }
    if (csolve(0, n) == 1) {
        i = 0;
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
