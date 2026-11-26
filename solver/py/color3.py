"""color3 —— 图 3-着色回溯求解器（Python 搜索侧）。

搜索序与 solver/c/color3_tri.c 严格一致（顶点升序、色 0→1→2 升序）
——双端同输入必得同一见证（契约逐字符对拍钉死）。"""
from .graph import Graph


def color3_solve(n, edges):
    """n 顶点、edges 边对列表 → 色数字符串（"012…"）或 None（UNSAT）。"""
    g = Graph(n, edges)
    colr = [0] * n

    def feas(v, c):
        for u in range(v):
            if g.adj[v][u] == 1 and colr[u] == c:
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
