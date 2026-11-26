/* bignum_tri —— 三值无限精度整数库（科学计算模块，ADR-38 M5 前置）。
 *
 * 肢基 B = 3^13 = 1594323（肢 < B，乘积累加 < 3^26 + B ≪ 3^45/2 单字
 * 安全），小端肢序，容量 = 调用方缓冲（256 肢 ≈ 3^3328 ≈ 2^5272）。
 * 非再入：模块全局工作区 BN_WORK/BN_TMP（单线程口径）。
 *
 * API：
 *   bn_from_int(a,&len,v)            小整数 → bignum
 *   bn_mul_small(a,&len,m)           ×小整数（m ≲ 9e14 安全）
 *   bn_div_small(a,&len,d)           ÷小整数，返回余数（d ≲ 9e14）
 *   bn_dec_one(a,&len)               −1
 *   bn_mul_big(a,la,b,lb,c)          教科书大乘（c 容量 ≥ la+lb）
 *   bn_cmp(a,la,b,lb)                −1/0/+1
 *   bn_dec_str(a,len,out,&outlen)    十进制串（10^12 分块取余，
 *                                    三值 bignum → 科学计算十进制输出）
 *
 * 上游参照：Python int 无限精度（oracle），本库 = TriRV C 侧对应物。
 * 精度验证 oracle 数据集见 docs/MERSENNE-PRIMES.md（梅森素数）。 */

int BN_WORK[256];
int BN_WLEN;
char BN_TMP[2000];

void bn_from_int(int *a, int *len, int v) {
    int i = 0;
    while (v > 0) {
        a[i] = v % 1594323;
        v = v / 1594323;
        i = i + 1;
    }
    *len = i;
}

int bn_is_zero(int *a, int len) {
    return len == 0;
}

void bn_mul_small(int *a, int *len, int m) {
    int i = 0;
    int carry = 0;
    int t;
    while (i < *len) {
        t = a[i] * m + carry;
        a[i] = t % 1594323;
        carry = t / 1594323;
        i = i + 1;
    }
    while (carry > 0) {
        a[*len] = carry % 1594323;
        carry = carry / 1594323;
        *len = *len + 1;
    }
}

int bn_div_small(int *a, int *len, int d) {
    int i = *len - 1;
    int r = 0;
    int t;
    while (i >= 0) {
        t = r * 1594323 + a[i];
        a[i] = t / d;
        r = t % d;
        i = i - 1;
    }
    while (*len > 0) {
        if (a[*len - 1] != 0) break;
        *len = *len - 1;
    }
    return r;
}

void bn_dec_one(int *a, int *len) {
    int i = 0;
    while (a[i] == 0) {
        a[i] = 1594322;
        i = i + 1;
    }
    a[i] = a[i] - 1;
    while (*len > 0) {
        if (a[*len - 1] != 0) break;
        *len = *len - 1;
    }
}

void bn_mul_big(int *a, int la, int *b, int lb, int *c) {
    int i = 0;
    int j;
    int t;
    int carry;
    while (i < la + lb) {
        c[i] = 0;
        i = i + 1;
    }
    i = 0;
    while (i < la) {
        carry = 0;
        j = 0;
        while (j < lb) {
            t = c[i + j] + a[i] * b[j] + carry;
            c[i + j] = t % 1594323;
            carry = t / 1594323;
            j = j + 1;
        }
        c[i + lb] = carry;
        i = i + 1;
    }
}

int bn_cmp(int *a, int la, int *b, int lb) {
    int i;
    if (la < lb) return -1;
    if (la > lb) return 1;
    i = la - 1;
    while (i >= 0) {
        if (a[i] < b[i]) return -1;
        if (a[i] > b[i]) return 1;
        i = i - 1;
    }
    return 0;
}

/* 规范化：返回去前导零后的有效肢长。 */
int bn_norm(int *a, int len) {
    while (len > 0) {
        if (a[len - 1] != 0) break;
        len = len - 1;
    }
    return len;
}

/* 十进制串：10^12 分块取余（t ≈ d·B ≈ 1.59e18 < 3^45/2 安全），
   每块 12 位按位写 tmp（位置 p = 10^p 系数），尾反向去前导零。 */
void bn_dec_str(int *a, int len, char *out, int *outlen) {
    int n = 0;
    int i;
    int r;
    int lead;
    for (i = 0; i < len; i++) BN_WORK[i] = a[i];
    BN_WLEN = len;
    if (BN_WLEN == 0) {
        out[0] = 48;
        *outlen = 1;
        return;
    }
    while (BN_WLEN > 0) {
        r = bn_div_small(BN_WORK, &BN_WLEN, 1000000000000);
        i = 0;
        while (i < 12) {
            BN_TMP[n] = 48 + r % 10;
            r = r / 10;
            n = n + 1;
            i = i + 1;
        }
    }
    lead = 0;
    while (lead < n - 1) {
        if (BN_TMP[n - 1 - lead] != 48) break;
        lead = lead + 1;
    }
    *outlen = 0;
    i = n - 1 - lead;
    while (i >= 0) {
        out[*outlen] = BN_TMP[i];
        *outlen = *outlen + 1;
        i = i - 1;
    }
}
