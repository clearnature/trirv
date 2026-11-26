"""cnf —— CNF/3-SAT 求解与 DIMACS 解析（Python 搜索侧）。

文字编码：±1..±nv（DIMACS 惯例）；子句 = 文字列表；flat = 子句依次
串联。搜索序：变量升序、赋值 0→1 升序回溯——与 C 版逐位一致。"""


class CNF:
    def __init__(self, nv, clauses):
        self.nv = nv
        self.clauses = [list(cl) for cl in clauses]
        self.flat = []
        self.start = []
        for cl in self.clauses:
            self.start.append(len(self.flat))
            self.flat += cl + [0]

    def satisfied(self, assign):
        """全子句在赋值（0/1 列表，下标 i = 变量 i+1）下是否满足。"""
        for cl in self.clauses:
            ok = False
            for lit in cl:
                v = abs(lit) - 1
                val = assign[v]
                if (lit > 0 and val == 1) or (lit < 0 and val == 0):
                    ok = True
                    break
            if not ok:
                return False
        return True


def parse_dimacs(text):
    """DIMACS cnf 文本 → (nv, clauses)。"""
    nv = 0
    clauses = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] in ("c", "%"):
            continue
        if line[0] == "p":
            parts = line.split()
            nv = int(parts[2])
            continue
        lits = [int(t) for t in line.split()]
        if lits and lits[-1] == 0:
            lits = lits[:-1]
        if lits:
            clauses.append(lits)
    return CNF(nv, clauses)


def cnf_solve(cnf):
    """回溯求解（变量升序、赋值 0→1 升序——与 C 版同构）。
    返回赋值列表或 None（UNSAT）。"""
    assign = [0] * cnf.nv

    def bt(v):
        if v == cnf.nv:
            return cnf.satisfied(assign)
        val = 0
        while val <= 1:
            assign[v] = val
            if bt(v + 1):
                return True
            val = val + 1
        return False

    if bt(0):
        return list(assign)
    return None


def to_dimacs(cnf):
    """CNF → DIMACS 文本（对外交换格式）。"""
    lines = [f"p cnf {cnf.nv} {len(cnf.clauses)}"]
    for cl in cnf.clauses:
        lines.append(" ".join(str(x) for x in cl) + " 0")
    return "\n".join(lines) + "\n"
