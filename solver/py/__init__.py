"""solver —— 见证-校验分离求解器包（py 搜索原型侧）。"""

from .graph import Graph, planted_3col, k4, triangle, c6
from .color3 import color3_solve
from .cnf import CNF, parse_dimacs, cnf_solve
from .witness import verify_coloring, verify_cnf
