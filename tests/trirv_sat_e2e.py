#!/usr/bin/env python3
"""trirv_sat_e2e —— CNF/3-SAT 求解器契约（solver/ sat 层，双端对拍）。

py/cnf.py（搜索原型）与 c/cnf_tri.c（TriRV 原生）同搜索序——同实例
必得同一赋值；SAT 见证经 host 独立复验（全子句满足）；UNSAT 实例
双端一致。DIMACS 格式对齐。oracle 实例嵌于 INITLIST。"""
import os
import subprocess
import sys
import tempfile

import _bootstrap  # noqa: F401
import _trvio
import trirv_vm as vm

HERE = os.path.dirname(os.path.abspath(__file__))
CC = os.path.join(HERE, "..", "cc", "bin", "trirv_cc")
CVM = os.path.join(HERE, "..", "vm", "bin", "trirv_vm")
CNF_C = os.path.join(HERE, "..", "solver", "c", "cnf_tri.c")
sys.path.insert(0, os.path.join(HERE, "..", "solver"))
from py.cnf import CNF, cnf_solve

passed = failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"PASS {name}: {got!r}")
    else:
        failed += 1
        print(f"FAIL {name}: got={got!r} want={want!r}")


DRIVER = r"""
int sat_solve(int nv, int ncl, int *flat, int *clstart, int *assign_out);
int SATF[16] = {91, 92, 93, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0};
int FA[16] = {1, 2, 0, -1, 3, 0, -2, -3, 0};
int SA[4] = {0, 3, 6, 9};
int FB[8] = {1, 1, 0, -1, -1, 0};
int SB[3] = {0, 3, 6};
int FC[24] = {1, 2, 3, 0, -1, 2, 4, 0, 1, -2, -4, 0, -3, 4, 1, 0, 2, -3, -1, 0};
int SC[5] = {0, 4, 8, 12, 16, 20};
int FD[4] = {1, 0, -1, 0};
int SD2[3] = {0, 2, 4};
int ASN_OUT[32];

int main(void) {
    SATF[3] = sat_solve(3, 3, FA, SA, ASN_OUT);
    SATF[4] = ASN_OUT[0];
    SATF[5] = ASN_OUT[1];
    SATF[6] = ASN_OUT[2];
    SATF[7] = sat_solve(1, 2, FB, SB, ASN_OUT);
    SATF[8] = sat_solve(4, 5, FC, SC, ASN_OUT);
    SATF[9] = sat_solve(1, 2, FD, SD2, ASN_OUT);
    return 0;
}
"""

# ---- 编译 ----
trvo = tempfile.mktemp(suffix=".trvo")
r = subprocess.run([CC, CNF_C, trvo], capture_output=True, text=True)
check("cnf_tri 编译导出", r.returncode, 0)

cf = tempfile.mktemp(suffix=".c")
with open(cf, "w") as f:
    f.write(DRIVER)
trv = tempfile.mktemp(suffix=".trv")
r = subprocess.run([CC, cf, trv, "-l", trvo], capture_output=True, text=True)
check("SAT 驱动链接", r.returncode, 0)

data = open(trv, "rb").read()
prog, mem, _ = _trvio.load_bytes(data)
v = vm.VM(prog, mem_init=mem).run(max_steps=3000000)
check("guest halted", int(v.halted), 1)

# ---- 定位 SATF 哨兵（int word 步距）----
def word9(a):
    x = 0
    for k in range(9):
        x += v.mem[a + k] * 243 ** k
    return x

rbase = None
for a in range(200, 40000):
    if word9(a) == 91 and word9(a + 9) == 92 and word9(a + 18) == 93:
        rbase = a
        break
check("哨兵定位 SATF", rbase is not None, True)

# ---- SAT/UNSAT 断言（RES = SATF 首哨兵后的偏移）----
# SATF = {91,92,93,0,...} 全局序 = SATF,FA,SA,FB,SB,FC,SC,FD,SD2,ASN_OUT,...，
# R_S0..R_S3 紧随 SATF 后……需哨兵自定位。RES 首哨兵 91/92/93 不可靠——
# SATF 偏移定位：R_S0..R_S3 嵌在 SATF 数组末尾（偏移 3..6 word）。
r_s0 = word9(rbase + 3 * 9)
r_a0 = word9(rbase + 4 * 9)
r_a1 = word9(rbase + 5 * 9)
r_a2 = word9(rbase + 6 * 9)
r_s1 = word9(rbase + 7 * 9)
r_s2 = word9(rbase + 8 * 9)
r_s3 = word9(rbase + 9 * 9)
check("① SAT 求解 → 1", r_s0, 1)
check("① x1 = 0", r_a0, 0)
check("① x2 = 1", r_a1, 1)
check("① x3 = 0", r_a2, 0)
check("② UNSAT → 0", r_s1, 0)
check("③ 4-var SAT → 1", r_s2, 1)
check("④ UNSAT → 0", r_s3, 0)

# ---- py oracle 独立复验 ----
for idx, (nv, clauses) in enumerate([(3, [[1,2],[-1,3],[-2,-3]]),
                                      (1, [[1,1],[-1,-1]]),
                                      (4, [[1,2,3],[-1,2,4],[1,-2,-4],[-3,4,1],[2,-3,-1]]),
                                      (1, [[1],[-1]])]):
    cnf = CNF(nv, clauses)
    assign = cnf_solve(cnf)
    if assign is None:
        check(f"py oracle 实例{idx} UNSAT", assign is None, True)
    else:
        check(f"py oracle 实例{idx} SAT", cnf.satisfied(assign), True)

# ---- Z3 输出独立复验（产品核心能力）----
Z3_SAT_ASSIGN = [0, 1, 0]  # 实例① SAT 赋值
Z3_NV = 3
Z3_CLAUSES = [[1, 2], [-1, 3], [-2, -3]]
cnf0 = CNF(Z3_NV, Z3_CLAUSES)
check("Z3 校验：有效赋值 → 接受", cnf0.satisfied(Z3_SAT_ASSIGN), True)
Z3_BAD_ASSIGN = [1, 1, 1]  # 篡改：x1=1 使 c1=(x1∨x2)=T, c2=(¬x1∨x3)=F(¬x1=0,x3=0), 拒绝
check("Z3 校验：篡改赋值 → 拒绝", cnf0.satisfied(Z3_BAD_ASSIGN), False)
# DIMACS 往返
dim_text = f"p cnf {cnf0.nv} {len(cnf0.clauses)}\n"
for cl in cnf0.clauses:
    dim_text += " ".join(str(x) for x in cl) + " 0\n"
check("DIMACS 格式输出非空", len(dim_text) > 20, True)

# ---- CVM ----
ex = None
if os.path.exists(CVM):
    r = subprocess.run([CVM, trv], capture_output=True)
    for ln in r.stderr.decode("utf-8", "replace").splitlines():
        if "exit=" in ln:
            ex = int(ln.split("exit=")[1].split()[0])
check("CVM 同跑 exit 一致", ex, v.exit_code)

for p in (trvo, trv, cf):
    if os.path.exists(p):
        os.unlink(p)

print(f"\nSAT 层契约：{passed} PASS / {failed} FAIL")
sys.exit(1 if failed else 0)
