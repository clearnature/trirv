/* cnf_tri —— CNF/3-SAT 回溯求解器（sat 层）。
 * flat = ±1..±nv 文字流（DIMACS 惯例），clstart = 各子句起点。
 * 搜索序：变量升序、赋值 0→1 升序——与 py/cnf.py 逐位一致。
 * 容量：nv ≤ 32、ncl ≤ 128、每子句 ≤ 16 文字、flat ≤ 511 文字。 */

/* ---- Z3 输出独立复验（产品核心能力，对外 ABI）---- */
int cnf_verify_assign(int ncl, int *flat, int *clstart, int *assign) {
    int ci = 0;
    while (ci < ncl) {
        int sat = 0;
        int j = clstart[ci];
        while (flat[j] != 0) {
            int lit = flat[j];
            int var = lit > 0 ? lit : 0 - lit;
            int val = assign[var - 1];
            if ((lit > 0 && val == 1) || (lit < 0 && val == 0)) {
                sat = 1;
                break;
            }
            j = j + 1;
        }
        if (sat == 0) return 1;
        ci = ci + 1;
    }
    return 0;
}

int CLFLAT[512];
int CLSTART[130];
int ASN[32];

int cl_ok(int *flat, int *st, int ci, int *asn) {
    int j = st[ci];
    while (flat[j] != 0) {
        int lit = flat[j];
        int var = lit > 0 ? lit : 0 - lit;
        int val = asn[var - 1];
        if ((lit > 0 && val == 1) || (lit < 0 && val == 0)) return 1;
        j = j + 1;
    }
    return 0;
}

int sat_bt(int nv, int ncl, int v) {
    int c;
    int ok;
    if (v == nv) {
        c = 0;
        ok = 1;
        while (c < ncl) {
            if (cl_ok(CLFLAT, CLSTART, c, ASN) == 0) {
                ok = 0;
                c = ncl;
            }
            c = c + 1;
        }
        return ok;
    }
    ASN[v] = 0;
    if (sat_bt(nv, ncl, v + 1) == 1) return 1;
    ASN[v] = 1;
    if (sat_bt(nv, ncl, v + 1) == 1) return 1;
    return 0;
}

int sat_solve(int nv, int ncl, int *flat, int *clstart, int *assign_out) {
    int i = 0;
    int c = 0;
    while (c < ncl) {
        int j = clstart[c];
        while (flat[j] != 0) {
            CLFLAT[i] = flat[j];
            i = i + 1;
            j = j + 1;
        }
        CLFLAT[i] = 0;
        i = i + 1;
        c = c + 1;
    }
    CLSTART[0] = 0;
    c = 0;
    while (c < ncl) {
        int j = CLSTART[c];
        while (CLFLAT[j] != 0) j = j + 1;
        CLSTART[c + 1] = j + 1;
        c = c + 1;
    }
    i = 0;
    while (i < nv) {
        ASN[i] = 0;
        i = i + 1;
    }
    if (sat_bt(nv, ncl, 0) == 1) {
        i = 0;
        while (i < nv) {
            assign_out[i] = ASN[i];
            i = i + 1;
        }
        return 1;
    }
    return 0;
}
