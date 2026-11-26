"""graph —— 图模型与实例工厂（结构化层：数据与生成器解耦求解器）。"""
import random


class Graph:
    """无向图：n 顶点 + 边对列表；邻接查询 O(1)。"""

    def __init__(self, n, edges):
        self.n = n
        self.edges = list(edges)
        self.m = len(self.edges)
        self.adj = [[0] * n for _ in range(n)]
        for (u, v) in self.edges:
            self.adj[u][v] = 1
            self.adj[v][u] = 1

    def conflicts(self, colors):
        """给定着色的同色相邻边数（见证独立复验）。"""
        return sum(1 for (u, v) in self.edges if colors[u] == colors[v])


def planted_3col(n, p=0.5, seed=2026):
    """planted 3-coloring：随机 proper 着色 + 异色顶点间 p 概率加边。"""
    rng = random.Random(seed)
    col = [rng.randrange(3) for _ in range(n)]
    edges = [(i, j) for i in range(n) for j in range(i + 1, n)
             if col[i] != col[j] and rng.random() < p]
    return n, edges


def k4():
    """K4 四团：3-着色 UNSAT 的最小完备枚举负样本。"""
    return 4, [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)]


def triangle():
    return 3, [(0, 1), (1, 2), (0, 2)]


def c6():
    return 6, [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (0, 5)]
