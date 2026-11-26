"""color3 —— 图 3-着色回溯求解器（Python 搜索侧）。

witness-sat 求解-校验分离架构的求解侧：
  求解器（本文件，搜索重）产出着色见证；
  校验器（witness_tri.c w_coloring_check，轻）独立复验。
搜索序与 solver/c/color3_solve_tri.c 严格一致（顶点 0..n-1、色 0,1,2
升序回溯）——双端同输入必得同一见证，可逐字符对拍。

用例：research/p_trit_lab.py 的 planted 3-col（解保证存在）与
K4（4-团，3-着色不可解 = 完备枚举证明 UNSAT）。"""
MAXN = 16


def color3_solve(n, edges):
    """n 顶点、edges 边对列表 → 着色串（"012…"，色数字符）或 None（UNSAT）。

    回溯序：顶点升序、色值 0→1→2 升序——与 C 版逐位一致。"""
    adj = [[0] * MAXN for _ in range(MAXN)]
    for (u, v) in edges:
        adj[u][v] = 1
        adj[v][u] = 1
    colr = [0] * MAXN

    def feas(v, c):
        for u in range(v):
            if adj[v][u] == 1 and colr[u] == c:
                return False
        return True

    def solve(v):
        if v == n:
            return True
        c = 0
        while c < 3:
            if feas(v, c):
                colr[v] = c
                if solve(v + 1):
                    return True
            c = c + 1
        colr[v] = 0
        return False

    if solve(0):
        return "".join(chr(48 + colr[i]) for i in range(n))
    return None
