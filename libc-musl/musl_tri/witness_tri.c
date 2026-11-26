/* witness_tri —— 见证运行时 v1（可见证性 = 产品要求，对外能力 ABI）。
 *
 * witness-sat 形态：昂贵求解（搜索/因数分解/任何计算）产出
 * 「结果 + 见证」，独立廉价校验器只验见证不重做搜索。
 * 本库 = TriRV C 侧标准校验器集；对外 ABI 见 docs/WITNESS.md。
 *
 * 返回码（全局约定）：
 *   0 = W_OK（见证成立）
 *   1 = W_REJECT_COLORING（着色见证被拒：存在同色相邻边）
 *   2 = W_REJECT_LL（梅森 LL 证书末项不符）
 *   4 = W_REJECT_HASH（数据完整性见证不符）
 *
 * 纪律：纯函数（无 I/O）、确定性、双 VM 同构（py VM + CVM 必须一致）。
 * 全库不使用二进制位惯用法（见 docs/C-TERNARY-PITFALLS.md）。 */

/* W1 图 3-着色见证：O(m) 边扫描——所有边两端异色则见证成立。
   edges = 2m 个 tryte（u,v 交替），colors = n 个 tryte，顶点号 < 128。 */
int w_coloring_check(int n, char *edges, int m, char *colors) {
    int i;
    int u;
    int v;
    i = 0;
    while (i < m) {
        u = edges[i * 2];
        v = edges[i * 2 + 1];
        if (colors[u] == colors[v]) return 1;
        i = i + 1;
    }
    return 0;
}

/* W2 梅森素数 Lucas-Lehmer 证书：claim = LL 序列末项（M_p 素 ⟺ 0）。
   p ≤ 31 原生单字安全（s² ≤ 4.6e18 < 3^45/2；M = 2^p−1 乘链构造）。 */
int w_ll_cert(int p, int claim) {
    int M;
    int s;
    int i;
    M = 1;
    i = 0;
    while (i < p) {
        M = M * 2;
        i = i + 1;
    }
    M = M - 1;
    s = 4;
    i = 0;
    while (i < p - 2) {
        s = s * s - 2;
        s = s - (s / M) * M;
        i = i + 1;
    }
    if (s == claim) return 0;
    return 2;
}

/* W3 大乘积证书（对接 bignum_tri）：recompute a·b 并逐肢比对 claim。
   work = 调用方缓冲（≥ la+lb 肢）。返回 0 = 见证成立 / 3 = 拒绝。
   bn_mul_big/bn_cmp 链接期绑 bignum_tri.c（多 TU）。 */
void bn_mul_big(int *a, int la, int *b, int lb, int *c);
int bn_cmp(int *a, int la, int *b, int lb);

int w_bn_product_check(int *a, int la, int *b, int lb,
                       int *claim, int lcl, int *work) {
    int wl;
    bn_mul_big(a, la, b, lb, work);
    wl = la + lb;
    while (wl > 0) {
        if (work[wl - 1] != 0) break;
        wl = wl - 1;
    }
    if (wl == 0) return 3;
    if (bn_cmp(work, wl, claim, lcl) != 0) return 3;
    return 0;
}

/* W4 数据完整性见证：三值 FNV（h = (h XOR t) × 16777619 mod 3^45，
   XOR = 逐 trit (ta+tb)%3）。claim = 完整 45-trit 字值。 */
int w_hash_check(char *data, int len, int claim) {
    int h;
    int i;
    h = 2166136261;
    i = 0;
    while (i < len) {
        h = h ^ data[i];
        h = h * 16777619;
        i = i + 1;
    }
    if (h == claim) return 0;
    return 4;
}
