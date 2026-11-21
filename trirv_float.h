/* trirv_float.h —— TF 三进制浮点的 C 实现（P2）。
 * 与 trirv_float.py 逐位对齐；Python 版作差分 oracle。
 * freg 存 TF45 的 45-trit raw（分离表示 (sign,mant,exp3) 的无损打包）。
 * 穿透模型：mant 走宿主 FPU（恒在 [1,3)），exp3 走整数 ALU。
 *
 * 作为头文件被 trirv_vm.c #include（单翻译单元，无链接烦恼）。
 */
#ifndef TRIRV_FLOAT_H
#define TRIRV_FLOAT_H
#include <math.h>
#include <stdint.h>

/* ---- TF45 常量：1 符号 + 10 阶码 + 34 尾数 = 45 trit ---- */
#define TF45_EXP_TRITS 10
#define TF45_MANT_TRITS 34
#define TF45_BIAS 29524                 /* (3^10-1)/2 */
#define TF45_MAX_EXP 59048              /* 3^10 - 1 */
#define TF45_P_MANT 16677181699666569ULL /* 3^34 */
#define TF45_P_NORM 5559060566555523ULL  /* 3^33 */
#define TF45_P_SIGN 0                    /* 3^44 > 2^64，运行时用 POW3[44] */

/* 说明：3^34=16677181699666569 < 2^64(1.8e19)，可用 uint64。
 * 3^44≈2.8e20 > 2^64，符号权重用 __uint128_t POW3[44]（由 trirv_vm.c 传入）。
 * 为避免重复定义，本头文件的函数接收 POW3 表指针。 */

typedef struct {
    __uint128_t P_MANT, P_NORM, P_SIGN;  /* 3^mant, 3^(mant-1), 3^(exp+mant) */
    __uint128_t ZERO_POS, ZERO_NEG, INF_POS, INF_NEG, QNAN;
    long bias, max_exp;                   /* P3：档位参数化，替代硬编码 TF45_* 宏 */
    int mant_trits;
} TFFmt;

static inline void tf45_init(TFFmt *f, const __uint128_t *POW3) {
    f->P_MANT = POW3[34];
    f->P_NORM = POW3[33];
    f->P_SIGN = POW3[44];
    f->bias = TF45_BIAS;
    f->max_exp = TF45_MAX_EXP;
    f->mant_trits = TF45_MANT_TRITS;
    f->ZERO_POS = 0;
    f->ZERO_NEG = f->P_SIGN;
    f->INF_POS = (__uint128_t)TF45_MAX_EXP * f->P_MANT;
    f->INF_NEG = f->P_SIGN + f->INF_POS;
    f->QNAN = f->INF_POS + 1;
}

/* P3：TF20 档位 = 1 符号 + 5 阶码 + 14 尾数 = 20 trit（wasm f32 对应）。
 * 与 TF45 共用同一套 tf_* 函数，仅格式常量不同——这是档位参数化的意义。 */
#define TF20_EXP_TRITS 5
#define TF20_MANT_TRITS 14
#define TF20_BIAS 121                    /* (3^5-1)/2 */
#define TF20_MAX_EXP 242                 /* 3^5 - 1 */
static inline void tf20_init(TFFmt *f, const __uint128_t *POW3) {
    f->P_MANT = POW3[TF20_MANT_TRITS];
    f->P_NORM = POW3[TF20_MANT_TRITS - 1];
    f->P_SIGN = POW3[TF20_EXP_TRITS + TF20_MANT_TRITS];
    f->bias = TF20_BIAS;
    f->max_exp = TF20_MAX_EXP;
    f->mant_trits = TF20_MANT_TRITS;
    f->ZERO_POS = 0;
    f->ZERO_NEG = f->P_SIGN;
    f->INF_POS = (__uint128_t)TF20_MAX_EXP * f->P_MANT;
    f->INF_NEG = f->P_SIGN + f->INF_POS;
    f->QNAN = f->INF_POS + 1;
}

/* raw → (s, E, M)：s∈{0,1}, E 阶码, M 尾数整数 */
typedef struct { int s; long E; __uint128_t M; } TFParts;
static inline TFParts tf_parts(const TFFmt *f, __uint128_t raw) {
    TFParts p;
    p.s = (int)(raw / f->P_SIGN);
    __uint128_t rest = raw % f->P_SIGN;
    p.E = (long)(rest / f->P_MANT);
    p.M = rest % f->P_MANT;
    return p;
}

/* M/P_NORM 的正确舍入 double（对齐 Python int.__truediv__）。
 * 直接 (double)M/(double)P 会把 M>2^53 先舍入一次；q+r/P 有二次舍入
 * （M=5×3^32 时给 1.6666666666666665，Python 给 ...67，差 1 ulp）。
 * 这里用整数移位 + round-to-nearest-even 一次到位：
 *   e = floor(M/P) ∈ {1,2}（正规化尾数 M∈[P,3P)）
 *   q = round(M / P × 2^(52-e))，53-bit，可精确转 double
 *   result = q × 2^-(52-e)，乘 2 的幂精确。 */
static inline double tf_ratio(const __uint128_t M, const __uint128_t P) {
    int e = (M < (P << 1)) ? 0 : 1;
    int sh = 52 - e;
    __uint128_t q = (M << sh) / P, r = (M << sh) % P;
    __uint128_t twice = r << 1;
    if (twice > P || (twice == P && (q & 1))) q += 1;
    double scale = 1.0;
    for (int i = 0; i < sh; i++) scale *= 0.5;   /* 2^-sh，精确 */
    return (double)q * scale;
}

/* 分离表示分类 */
enum { TF_NUM, TF_ZERO, TF_INF, TF_NAN };
typedef struct { int kind, sign; double mant; long exp3; } TFVal;

static inline TFVal tf_dec(const TFFmt *f, __uint128_t raw, long max_exp) {
    TFParts p = tf_parts(f, raw);
    TFVal v;
    v.sign = p.s ? -1 : 1;
    if (p.E == max_exp) {
        v.kind = (p.M != 0) ? TF_NAN : TF_INF;
        v.mant = 0; v.exp3 = 0;
        return v;
    }
    if (p.E == 0 && p.M == 0) {
        v.kind = TF_ZERO; v.mant = 1.0; v.exp3 = 0;
        return v;
    }
    v.kind = TF_NUM;
    /* 精确对齐 trirv_float.py 的 M/P_NORM（Python 大整数真除法一次舍入）。
     * P2 曾用 q + r/P，但 5.0 的 M=5×3^32 走这条路给 1.6666666666666665，
     * Python 给 ...67，差 1 ulp（5.0+3.0 在 C VM 里偏 -3，PyVM 偏 +2）。
     * P3 改用 tf_ratio() 的整数正确舍入，彻底消除跨 VM 精度漂移。 */
    v.mant = tf_ratio(p.M, f->P_NORM);                     /* [1,3) */
    v.exp3 = p.E - f->bias;                       /* 整数，无 double 约束（P3 档位参数化） */
    return v;
}

/* double a>0 → (mant_int, exp2) 使 a == mant_int × 2^exp2，精确（对齐 Python frexp 版） */
static inline void tf_double_pow2(double a, uint64_t *mi, int *e2) {
    int p;
    double m = frexp(a, &p);                       /* m∈[0.5,1) */
    *mi = (uint64_t)(m * 9007199254740992.0);      /* ×2^53 */
    *e2 = p - 53;
}

/* round(mant_int × 2^exp2 × 3^k)，整数精确。k = mant_trits-1 = 33。
 * 返回 __uint128_t（3^34 装得进，但再乘可能到 3^35≈5e16 也装得进）。 */
static inline __uint128_t tf_round_mant(uint64_t mi, int e2, int k) {
    __uint128_t num = mi, den = 1;
    __uint128_t p3 = 1;
    for (int i = 0; i < k; i++) p3 *= 3;           /* 3^k */
    num *= p3;
    if (e2 >= 0) num <<= e2;
    else den <<= (-e2);
    __uint128_t q = num / den, r = num % den;
    if (2 * r >= den) q += 1;
    return q;
}

/* (sign, mant∈[1,3), exp3) → raw（compose），含上/下溢。 */
static inline __uint128_t tf_compose(const TFFmt *f, int sign, double mant, long exp3,
                                     long max_exp) {
    if (exp3 == 999999) return sign < 0 ? f->INF_NEG : f->INF_POS;  /* +inf 标记 */
    if (exp3 == -999999) return sign < 0 ? f->ZERO_NEG : f->ZERO_POS; /* 0 标记 */
    uint64_t mi; int e2;
    tf_double_pow2(mant, &mi, &e2);
    __uint128_t M = tf_round_mant(mi, e2, f->mant_trits - 1);
    long E = exp3 + f->bias;
    if (M >= f->P_MANT) { M /= 3; E += 1; }
    if (E >= max_exp) return sign < 0 ? f->INF_NEG : f->INF_POS;
    if (E <= 0) return sign < 0 ? f->ZERO_NEG : f->ZERO_POS;
    if (M < f->P_NORM) M = f->P_NORM;
    __uint128_t raw = (__uint128_t)(sign < 0 ? 1 : 0) * f->P_SIGN;
    raw += (__uint128_t)E * f->P_MANT + M;
    return raw;
}

/* 对阶：mant × 3^(-k)，k≥0；k≥40 返回 0（<1ulp） */
#define TF_EXP_GAP 40
static inline double tf_scale_down(double mant, long k) {
    static const double p3neg[TF_EXP_GAP + 1] = {
        1.0, 3.333333333333333e-1, 1.1111111111111111e-1,
        3.7037037037037037e-2, 1.2345679012345679e-2,
        4.11522633744856e-3, 1.3717421124828531e-3,
        4.572473708276177e-4, 1.5241579027583924e-4,
        5.080526342529301e-5, 1.6935087808430336e-5,
        5.645028269476778e-6, 1.881676423158926e-6,
        6.272254743863087e-7, 2.090751581287696e-7,
        6.969171937625654e-8, 2.323057312541885e-8,
        7.743524375139616e-9, 2.5811747917132053e-9,
        8.603915972377351e-10, 2.8679720e-10, 9.5599e-11,
        3.18663e-11, 1.06221e-11, 3.5407e-12, 1.18023e-12,
        3.9341e-13, 1.31137e-13, 4.3712e-14, 1.4571e-14,
        4.8569e-15, 1.6190e-15, 5.3966e-16, 1.7989e-16,
        5.9962e-17, 1.9987e-17, 6.6624e-18, 2.2208e-18,
        7.4026e-19, 2.4675e-19, 8.2250e-20
    };
    if (k >= TF_EXP_GAP) return 0.0;
    return mant * p3neg[k];
}

/* 正规化 mant 到 [1,3) 后 compose */
static inline __uint128_t tf_norm(const TFFmt *f, int sign, double mant, long exp3,
                                  long max_exp) {
    if (mant == 0.0) return sign < 0 ? f->ZERO_NEG : f->ZERO_POS;
    while (mant < 1.0) { mant *= 3.0; exp3 -= 1; }
    while (mant >= 3.0) { mant /= 3.0; exp3 += 1; }
    return tf_compose(f, sign, mant, exp3, max_exp);
}

static inline __uint128_t tf_add(const TFFmt *f, __uint128_t a, __uint128_t b,
                                 int subtract, long max_exp, const __uint128_t *P) {
    (void)P;
    TFVal va = tf_dec(f, a, max_exp), vb = tf_dec(f, b, max_exp);
    if (subtract) vb.sign = -vb.sign;
    if (va.kind == TF_NAN || vb.kind == TF_NAN) return f->QNAN;
    if (va.kind == TF_INF) {
        if (vb.kind == TF_INF && va.sign != vb.sign) return f->QNAN;
        return va.sign < 0 ? f->INF_NEG : f->INF_POS;
    }
    if (vb.kind == TF_INF) return vb.sign < 0 ? f->INF_NEG : f->INF_POS;
    if (va.kind == TF_ZERO && vb.kind == TF_ZERO)
        return (va.sign < 0 && vb.sign < 0) ? f->ZERO_NEG : f->ZERO_POS;
    if (va.kind == TF_ZERO) return tf_compose(f, vb.sign, 1.0, vb.exp3, max_exp);
    if (vb.kind == TF_ZERO) return tf_compose(f, va.sign, 1.0, va.exp3, max_exp);
    long E = va.exp3 > vb.exp3 ? va.exp3 : vb.exp3;
    double x = va.sign * va.mant * tf_scale_down(1.0, E - va.exp3);
    double y = vb.sign * vb.mant * tf_scale_down(1.0, E - vb.exp3);
    double t = x + y;
    if (t == 0.0) return f->ZERO_POS;
    return tf_norm(f, t > 0 ? 1 : -1, fabs(t), E, max_exp);
}

static inline __uint128_t tf_mul(const TFFmt *f, __uint128_t a, __uint128_t b,
                                 long max_exp) {
    TFVal va = tf_dec(f, a, max_exp), vb = tf_dec(f, b, max_exp);
    int sign = va.sign * vb.sign;
    if (va.kind == TF_NAN || vb.kind == TF_NAN) return f->QNAN;
    if ((va.kind == TF_INF && vb.kind == TF_ZERO) ||
        (vb.kind == TF_INF && va.kind == TF_ZERO)) return f->QNAN;
    if (va.kind == TF_INF || vb.kind == TF_INF)
        return sign < 0 ? f->INF_NEG : f->INF_POS;
    if (va.kind == TF_ZERO || vb.kind == TF_ZERO)
        return sign < 0 ? f->ZERO_NEG : f->ZERO_POS;
    return tf_norm(f, sign, va.mant * vb.mant, va.exp3 + vb.exp3, max_exp);
}

static inline __uint128_t tf_divf(const TFFmt *f, __uint128_t a, __uint128_t b,
                                  long max_exp) {
    TFVal va = tf_dec(f, a, max_exp), vb = tf_dec(f, b, max_exp);
    int sign = va.sign * vb.sign;
    if (va.kind == TF_NAN || vb.kind == TF_NAN) return f->QNAN;
    if ((va.kind == TF_INF && vb.kind == TF_INF) ||
        (va.kind == TF_ZERO && vb.kind == TF_ZERO)) return f->QNAN;
    if (va.kind == TF_INF) return sign < 0 ? f->INF_NEG : f->INF_POS;
    if (vb.kind == TF_INF) return sign < 0 ? f->ZERO_NEG : f->ZERO_POS;
    if (vb.kind == TF_ZERO) return sign < 0 ? f->INF_NEG : f->INF_POS;
    if (va.kind == TF_ZERO) return sign < 0 ? f->ZERO_NEG : f->ZERO_POS;
    return tf_norm(f, sign, va.mant / vb.mant, va.exp3 - vb.exp3, max_exp);
}

static inline __uint128_t tf_sqrt(const TFFmt *f, __uint128_t a, long max_exp) {
    TFVal va = tf_dec(f, a, max_exp);
    if (va.kind == TF_NAN) return f->QNAN;
    if (va.kind == TF_INF) return va.sign > 0 ? f->INF_POS : f->QNAN;
    if (va.kind == TF_ZERO) return a;
    if (va.sign < 0) return f->QNAN;
    if ((va.exp3 & 1) == 0)
        return tf_norm(f, 1, sqrt(va.mant), va.exp3 / 2, max_exp);
    return tf_norm(f, 1, sqrt(3.0 * va.mant), (va.exp3 - 1) / 2, max_exp);
}

/* 比较：op 0=eq 1=lt 2=le，返回 1/0 */
static inline int tf_cmp(const TFFmt *f, __uint128_t a, __uint128_t b, int op,
                         long max_exp) {
    TFVal va = tf_dec(f, a, max_exp), vb = tf_dec(f, b, max_exp);
    if (va.kind == TF_NAN || vb.kind == TF_NAN) return 0;
    /* 全序：负inf < 负数 < ±0 < 正数 < 正inf */
    int c;
    if (va.kind != vb.kind || va.kind != TF_NUM) {
        int ka = va.kind == TF_ZERO ? 0 : (va.kind == TF_INF ? (va.sign > 0 ? 2 : -2) : 0);
        int kb = vb.kind == TF_ZERO ? 0 : (vb.kind == TF_INF ? (vb.sign > 0 ? 2 : -2) : 0);
        if (ka != kb) c = (ka > kb) ? 1 : -1;
        else if (va.kind == TF_INF && vb.kind == TF_INF) c = (va.sign > vb.sign) - (va.sign < vb.sign);
        else c = 0;
    } else {
        if (va.sign != vb.sign) c = va.sign > vb.sign ? 1 : -1;
        else {
            double la = log(va.mant) + va.exp3 * log(3.0);
            double lb = log(vb.mant) + vb.exp3 * log(3.0);
            if (fabs(la - lb) > 1e-9) c = la > lb ? 1 : -1;
            else if (va.exp3 == vb.exp3) c = (va.mant > vb.mant) - (va.mant < vb.mant);
            else {
                long E = va.exp3 > vb.exp3 ? va.exp3 : vb.exp3;
                double xa = va.mant * tf_scale_down(1.0, E - va.exp3);
                double xb = vb.mant * tf_scale_down(1.0, E - vb.exp3);
                c = (xa > xb) - (xa < xb);
                if (va.sign < 0) c = -c;
            }
        }
    }
    if (op == 0) return c == 0;
    if (op == 1) return c < 0;
    return c <= 0;
}

#endif /* TRIRV_FLOAT_H */
