/* mersenne_ll_tri —— 三值高精度验证（梅森素数 + base-3^13 多肢 bignum）。
 *
 * 一、整数无限精度：base B = 3^13 = 1594323 多肢 bignum（肢 < B，
 *     乘积累加 < 3^26 + B ≪ 3^44 单字安全），64 肢容量 = 3^832 ≈ 2^1324。
 *     验证载荷：100!（乘小整数链）、2^521−1（梅森素数 M_521！）、
 *     3^500、大×大教科书乘法（(3^339+5)(3^339+7)）。
 * 二、梅森素数 Lucas-Lehmer（原生单字）：M_31 = 2147483647 素（s29=0）、
 *     M_11 = 2047 合（s9≠0）双向验证。s² ≤ 4.6e18 < 3^45/2 单字安全，
 *     mod 用 DIV/REM（三值原生除）。
 * 三、校验和（CVM 退出通道）：全肢 Σ mod 1000003。
 * 结果经全局肢数组导出，host 对 Python 无限精度 oracle 逐肢对拍。 */

int OUT[560];/* 导出：OUT[0]=魔数 OUT[1..5]=肢长 OUT[6..]=各数肢序列 + S31/S11 */
int F[64];   /* 100! */
int G[64];   /* 2^521 - 1 */
int H[64];   /* 3^500 */
int A[64];   /* 3^339 + 5 */
int Bb[64];  /* 3^339 + 7 */
int P[132];  /* A * Bb（双倍长） */
int LEN[6];  /* 各数组有效肢长（F/G/H/A/Bb/P） */
int S31;     /* LL(M_31) 结果（0 = 素） */
int S11;     /* LL(M_11) 结果（非 0 = 合） */

void mul_small(int *a, int *len, int m) {
    int i;
    int carry;
    int t;
    carry = 0;
    for (i = 0; i < *len; i++) {
        t = a[i] * m + carry;
        a[i] = t % 1594323;
        carry = t / 1594323;
    }
    while (carry > 0) {
        a[*len] = carry % 1594323;
        carry = carry / 1594323;
        *len = *len + 1;
    }
}

void dec_one(int *a, int *len) {
    int i;
    i = 0;
    while (a[i] == 0) {
        a[i] = 1594322;
        i = i + 1;
    }
    a[i] = a[i] - 1;
    if (a[*len - 1] == 0) *len = *len - 1;
}

void mul_big(int *a, int la, int *b, int lb, int *c) {
    int i;
    int j;
    int t;
    int carry;
    for (i = 0; i < la + lb; i++) c[i] = 0;
    for (i = 0; i < la; i++) {
        carry = 0;
        for (j = 0; j < lb; j++) {
            t = c[i + j] + a[i] * b[j] + carry;
            c[i + j] = t % 1594323;
            carry = t / 1594323;
        }
        c[i + lb] = carry;
    }
}

int main(void) {
    int i;
    int m;
    int s;
    int M;
    int chk;
    /* ---- 100! ---- */
    F[0] = 1;
    LEN[0] = 1;
    for (m = 2; m <= 100; m++) mul_small(F, &LEN[0], m);
    /* ---- 2^521 - 1 ---- */
    G[0] = 1;
    LEN[1] = 1;
    for (i = 0; i < 521; i++) mul_small(G, &LEN[1], 2);
    dec_one(G, &LEN[1]);
    /* ---- 3^500 ---- */
    H[0] = 1;
    LEN[2] = 1;
    for (i = 0; i < 500; i++) mul_small(H, &LEN[2], 3);
    /* ---- (3^339 + 5)(3^339 + 7) ---- */
    A[0] = 1;
    LEN[3] = 1;
    for (i = 0; i < 339; i++) mul_small(A, &LEN[3], 3);
    A[0] = A[0] + 5;
    Bb[0] = 1;
    LEN[4] = 1;
    for (i = 0; i < 339; i++) mul_small(Bb, &LEN[4], 3);
    Bb[0] = Bb[0] + 7;
    mul_big(A, LEN[3], Bb, LEN[4], P);
    LEN[5] = LEN[3] + LEN[4];
    /* ---- Lucas-Lehmer M_31（素）：s=4，迭代 29 次，末项 ≡ 0 ---- */
    M = 2147483647;
    s = 4;
    for (i = 0; i < 29; i++) {
        s = s * s - 2;
        s = s - (s / M) * M;
    }
    S31 = s;
    /* ---- Lucas-Lehmer M_11（合）：迭代 9 次，末项 ≠ 0 ---- */
    M = 2047;
    s = 4;
    for (i = 0; i < 9; i++) {
        s = s * s - 2;
        s = s - (s / M) * M;
    }
    S11 = s;
    /* ---- 校验和（全肢折叠，CVM 退出通道） ---- */
    chk = 0;
    for (i = 0; i < 64; i++) chk = (chk + F[i] + G[i] + H[i] + A[i] + Bb[i]) % 1000003;
    for (i = 0; i < 132; i++) chk = (chk + P[i]) % 1000003;
    chk = (chk + S31 % 1000003 + S11 % 1000003) % 1000003;
    /* ---- 导出（哨兵自定位，host 扫 OUT[0] 魔数）---- */
    OUT[0] = 475769;
    for (i = 0; i < 6; i++) OUT[1 + i] = LEN[i];
    for (i = 0; i < 64; i++) OUT[7 + i] = F[i];
    for (i = 0; i < 64; i++) OUT[71 + i] = G[i];
    for (i = 0; i < 64; i++) OUT[135 + i] = H[i];
    for (i = 0; i < 64; i++) OUT[199 + i] = A[i];
    for (i = 0; i < 64; i++) OUT[263 + i] = Bb[i];
    for (i = 0; i < 132; i++) OUT[327 + i] = P[i];
    OUT[459] = S31;
    OUT[460] = S11;
    return chk;
}
