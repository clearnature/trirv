"""witness —— 见证格式与独立校验（py 侧镜像 witness_tri.c 语义）。"""
from .cnf import CNF


def verify_coloring(n, edges, colors):
    """着色见证独立复验：返回 True = 所有边两端异色。"""
    return all(colors[u] != colors[v] for (u, v) in edges)


def verify_cnf(cnf, assign):
    """CNF 见证独立复验：赋值满足全部子句。"""
    return cnf.satisfied(assign)


def color_str(colors):
    return "".join(chr(48 + c) for c in colors)
