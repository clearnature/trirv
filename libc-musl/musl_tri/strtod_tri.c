/* strtod/atof —— musl src/stdlib/strtod.c + atof.c 的三进制原生改编。
 *
 * 与上游差异：
 *   1. 上游经 stdio 内部 __floatscan（shgetc + 精确大数）——TriRV 无 FILE 子系统，
 *      直接字符解析 + 6 字小数大数走位（base 10^16，容量 10^96 ≈ 3^201）。
 *   2. 返回值是 TF45 raw（45-trit 字，guest 视角就是 int）；
 *      musl 的 double 形参/返回值在 TriRV ABI 里就是 TF45 raw，无需编译器浮点支持。
 *   3. 不支持：十六进制浮点 0x1p3（IEEE 位语义的表示层）、errno/ERANGE。
 *      支持：空白、±、inf/infinity/nan（不区分大小写，nan 可带 (...)——括号内容
 *      不校验，与 musl 的 n-char-sequence 校验不同，契约锁定）、十进制、小数点、
 *      e/E 指数。
 *
 * 精度声明（诚实记账，详见 TERNARY_FLOAT_PROPOSAL.md §5.6）：
 *   - ≤20 位有效数字输入正确舍入（契约用 Fraction 精确 oracle 锁定）；
 *   - 更长输入 sticky 舍入——十进制串不可能精确落在两个 TF45 值正中（tie 需
 *     分母含 3^33 且 3 不整除分子），tie 分支只由 sticky 驱动；
 *   - 走位窗口纪律：有损步只在 A ≥ 10^80 时做，每步相对丢失 ≤10/10^80，
 *     总步数 ≤6 万，总漂移 ≤10^-75，比 tie 间隙 10^-36.6 低 38 个数量级；
 *   - 值域捷径：exp10+sig ≥ 14100 → ±Inf、≤ -14130 → ±0（TF45 值域
 *     10^±14086 的数学必然，非估算；阈值取保守，灰区全走精确走位）。
 *
 * 走位不变式：R = A×10^P / 3^t 恒等于真值 V×(1-δ)，δ ≥ 0 仅由有损步产生；
 *   P>0：×10（精确，P--，需 room）；满时 ÷3（有损 sticky，t--）。
 *   P<0：A≥10^80 时 ÷10（截断 sticky，P++）；否则 ×3（精确，t++）——
 *        先 ×3 到高水位再 ÷10 的交错率 ≈2.1:1 由窗口自动形成，无死锁。
 * 终态 P=0：M = round(A/3^u)，u = trimag(A)-33（可负：负则 A×3^|u| 精确乘）；
 *   u>0 时 ÷3 共 u 步，disc 与 S=3^u−disc 同步累加（免存 3^u），
 *   比较 disc vs S：> 升格，= 看 sticky（half-even 由公理兜底）。
 */

int g_sd_w[6];        /* 主累加器 A（base 10^16 小端，值 = Σ w[i]·10^(16i)） */
int g_sd_disc[6];     /* 终除余量 disc = A mod 3^u（×3+r 逐步累加） */
int g_sd_sbar[6];     /* S = 3^u − disc（×3+(3-r) 同步累加） */
int g_sd_t[6];        /* trimag 的现场 3^j 扫描（不用巨型全局表——见下） */
int g_sd_w3[6];       /* 终除权重 3^i（LSB-first 累加用） */
int g_sd_tmp[6];      /* 终除 scratch */

int SD_BASE = 10000000000000000;       /* 10^16 */
int SD_P33  = 5559060566555523;        /* 3^33 */
int SD_P34  = 16677181699666569;       /* 3^34 */
/* 3^44 级常量运行时化（2026-11-22 坑实录）：字面量 9.85e20/1.97e21 > uint64
   （CValue.i 容器 2^64=1.8e19），tri-tcc 前端截断 + 只给 warning——截断值作
   "==比较" 两处同截断巧合自洽，作数学界（rest>=SD_P44 判负域）则界错位。
   解法：小因子（3^34/59048/59048 均在 uint64 内）编译期给定，45-trit 运行时
   MUL/ADD 精确合成（积 < 3^45）。契约：消费前必调 sd_big_init()。 */
int SD_P10  = 59049;                   /* 3^10（P44 合成因子） */
int SD_P44  = 0;                       /* 3^44 = SD_P34*SD_P10（运行时合成） */
int SD_INF  = 0;                       /* +Inf = 59048*SD_P34（运行时合成） */
int SD_NINF = 0;                       /* −Inf = SD_P44+SD_INF（运行时合成） */
int SD_NAN  = 0;                       /* NaN = SD_INF+1（运行时合成） */
int SD_BIG_INIT = 0;

void sd_big_init(void)
{
    if (SD_BIG_INIT) return;
    SD_P44  = SD_P34 * SD_P10;
    SD_INF  = 59048 * SD_P34;
    SD_NINF = SD_P44 + SD_INF;
    SD_NAN  = SD_INF + 1;
    SD_BIG_INIT = 1;
}

/* ---- 6 字原语（只做 字×小因子 / ÷小因子，单字积 < 3^45 不溢出）---- */

int sd_isroom(int *w)                     /* A < 10^80 → ×3/×10 后仍 < 10^96 */
{
    return w[5] == 0;
}

int sd_mul(int *w, int f)                 /* ×f（f=3 或 10）；返回最高进位（恒 0） */
{
    int c = 0;
    int i = 0;
    while (i < 6) {
        int cur = w[i] * f + c;
        w[i] = cur - cur / SD_BASE * SD_BASE;
        c = cur / SD_BASE;
        i = i + 1;
    }
    return c;
}

int sd_div(int *w, int d)                 /* ÷d 就地；返回余数 */
{
    int r = 0;
    int i = 5;
    while (i >= 0) {
        int cur = r * SD_BASE + w[i];
        w[i] = cur / d;
        r = cur - cur / d * d;
        i = i - 1;
    }
    return r;
}

int sd_addc(int *w, int v)                /* w[0]+=v 并进位（v ≤ 9） */
{
    int c;
    int i;
    int cur = w[0] + v;
    w[0] = cur - cur / SD_BASE * SD_BASE;
    c = cur / SD_BASE;
    i = 1;
    while (c != 0 && i < 6) {
        cur = w[i] + c;
        w[i] = cur - cur / SD_BASE * SD_BASE;
        c = cur / SD_BASE;
        i = i + 1;
    }
    return c;
}

int sd_cmp(int *a, int *b)
{
    int i = 5;
    while (i >= 0) {
        if (a[i] != b[i]) {
            if (a[i] < b[i]) return -1;
            return 1;
        }
        i = i - 1;
    }
    return 0;
}

void sd_copy(int *d, int *s)
{
    int i = 0;
    while (i < 6) { d[i] = s[i]; i = i + 1; }
}

void sd_addw(int *a, int *b)              /* a += b（6 字带进位） */
{
    int c = 0;
    int i = 0;
    while (i < 6) {
        int cur = a[i] + b[i] + c;
        a[i] = cur - cur / SD_BASE * SD_BASE;
        c = cur / SD_BASE;
        i = i + 1;
    }
}

void sd_setw(int *w, int lo, int hi)      /* 装标量（lo + hi·10^16） */
{
    int i = 2;
    w[0] = lo;
    w[1] = hi;
    while (i < 6) { w[i] = 0; i = i + 1; }
}

/* 最大 j 使 A ≥ 3^j。无表：现场构造 3^168（≈3.67e79，6 字内）后 ÷3 下扫。
 * 不用巨型全局表的理由：未初始化全局量从零页 207 号字起线性铺开，
 * 1212 词的表会一路踩进初始化全局量/字符串区（实测 stomped "0.5" 串）。
 * 走位窗口保证 A ∈ (10^79, 10^81) ⊂ (3^166, 3^170)，上界 169 足够。 */
int sd_trimag(int *w)
{
    int j = 169;
    int i = 0;
    sd_setw(g_sd_t, 1, 0);
    while (i < 169) { sd_mul(g_sd_t, 3); i = i + 1; }
    while (j > 0 && sd_cmp(g_sd_t, w) > 0) { sd_div(g_sd_t, 3); j = j - 1; }
    return j;
}

/* ---- 解析辅助（字符码点风格，同 strtol_tri）---- */

int sd_isspace(int c)
{
    return c == 32 || c == 9 || c == 10 || c == 11 || c == 12 || c == 13;
}

int sd_low(int c)
{
    if (c >= 65 && c <= 90) return c + 32;
    return c;
}

/* 匹配 "inf"/"infinity"（返回 3 或 8 或 0）与 "nan"（返回 3 或 0） */
int sd_match(const char *s, int kind)
{
    if (kind == 2) {
        if (sd_low(s[0]) == 110 && sd_low(s[1]) == 97 && sd_low(s[2]) == 110) return 3;
        return 0;
    }
    if (sd_low(s[0]) == 105 && sd_low(s[1]) == 110 && sd_low(s[2]) == 102) {
        if (kind == 0) return 3;
        if (sd_low(s[3]) == 105 && sd_low(s[4]) == 110 &&
            sd_low(s[5]) == 105 && sd_low(s[6]) == 116 &&
            sd_low(s[7]) == 121) return 8;
        return 3;
    }
    return 0;
}

int strtod(const char *s, char **end)
{
    const char *start = s;
    sd_big_init();
    int neg = 0;
    int seen = 0;
    int sticky = 0;
    int sig = 0;
    int dot = 0;
    int exp10 = 0;
    int anydigit = 0;
    int i;

    sd_setw(g_sd_w, 0, 0);                    /* 累加器清零——全局跨调用残留 */
    while (sd_isspace(*s)) s++;
    if (*s == 43) s++;
    else if (*s == 45) { neg = 1; s++; }

    /* inf / infinity / nan */
    i = sd_match(s, 1);
    if (i == 0) i = sd_match(s, 0);
    if (i > 0) {
        s = s + i;
        if (end != 0) *end = (char *)s;
        return neg ? SD_NINF : SD_INF;
    }
    if (sd_match(s, 2) == 3) {
        s = s + 3;
        if (*s == 40) {
            const char *q = s + 1;
            while (*q != 41 && *q != 0) q++;
            if (*q == 41) s = q + 1;
        }
        if (end != 0) *end = (char *)s;
        return SD_NAN;
    }

    /* 数字主体 */
    for (;;) {
        int c = *s;
        int d = -1;
        if (c >= 48 && c <= 57) d = c - 48;
        else if (c == 46 && dot == 0) { dot = 1; s++; continue; }
        if (d < 0) break;
        s++;
        anydigit = 1;
        if (dot != 0) exp10 = exp10 - 1;
        if (seen == 0 && d == 0) continue;    /* 前导零不显著 */
        seen = 1;
        if (sig < 20) {
            sig = sig + 1;
            sd_mul(g_sd_w, 10);
            if (sd_addc(g_sd_w, d) != 0) sticky = 1;
        } else {
            if (d != 0) sticky = 1;
        }
    }

    if (seen == 0) {
        /* 全零串（如 "-0"、"0.000e99"）→ 合法转换：值 = ±0（TriRV 抽象零
           无符号 → +0），end = 消费结束处 */
        if (anydigit != 0) {
            if (end != 0) *end = (char *)s;
            return 0;
        }
        if (end != 0) *end = (char *)start;
        return 0;
    }

    /* e/E 指数 */
    if (*s == 101 || *s == 69) {
        const char *q = s + 1;
        int eneg = 0;
        int ev = 0;
        int any = 0;
        if (*q == 43) q++;
        else if (*q == 45) { eneg = 1; q++; }
        while (*q >= 48 && *q <= 57) {
            ev = ev * 10 + (*q - 48);
            if (ev > 20000) ev = 20000;       /* 钳制；捷径界外仍正确 */
            any = 1;
            q++;
        }
        if (any != 0) {
            if (eneg != 0) ev = -ev;
            exp10 = exp10 + ev;
            s = q;
        }
    }

    if (end != 0) *end = (char *)s;

    /* 值域捷径：V ∈ [10^(mag-1), 10^mag]，mag = exp10+sig */
    {
        int mag = exp10 + sig;
        if (mag >= 14100) return neg ? SD_NINF : SD_INF;
        if (mag <= -14130) return neg ? SD_P44 : 0;
    }

    /* ---- 走位：吸收 exp10 ---- */
    {
        int t = 0;
        int P = exp10;
        while (P != 0) {
            if (P > 0) {
                if (sd_isroom(g_sd_w) != 0) { sd_mul(g_sd_w, 10); P = P - 1; }
                else { if (sd_div(g_sd_w, 3) != 0) sticky = 1; t = t - 1; }
            } else {
                if (g_sd_w[5] != 0) {          /* A ≥ 10^80：÷10 有损步（≤10^-79 相对） */
                    if (sd_div(g_sd_w, 10) != 0) sticky = 1;
                    P = P + 1;
                } else {                       /* A < 10^80：×3 攀升防下溢 */
                    sd_mul(g_sd_w, 3);
                    t = t + 1;
                }
            }
        }

        /* 终态：R = A/3^t。u = trimag(A)-33；M = round(A/3^u)；exp3 x = u-t */
        {
            int u = sd_trimag(g_sd_w) - 33;
            int uo = u;
            int m0;
            int x;
            if (u > 0) {
                /* disc = A mod 3^u：÷3 逐步取余是 LSB-first，
                   必须按权重累加 disc += r·3^i（旧版 disc×3+r 把位序反了）。
                   S' = Σ(2-r)·3^i = 3^u-1-disc；中点判定 disc > S'+1
                   （2disc > 3^u ⟺ disc-S' > 1；3^u 奇 → 精确 tie 不存在，
                   贴中点的真值由 sticky 半升语义救——对齐 trirv_float 的
                   2r>=den 半升规则）。 */
                sd_setw(g_sd_disc, 0, 0);
                sd_setw(g_sd_sbar, 0, 0);
                sd_setw(g_sd_w3, 1, 0);
                while (u > 0) {
                    int r = sd_div(g_sd_w, 3);
                    if (r != 0) {
                        sd_copy(g_sd_tmp, g_sd_w3);
                        sd_mul(g_sd_tmp, r);
                        sd_addw(g_sd_disc, g_sd_tmp);
                        /* 注意：不设 sticky——r 已加权进 disc，无丢失 */
                    }
                    if (r != 2) {
                        sd_copy(g_sd_tmp, g_sd_w3);
                        sd_mul(g_sd_tmp, 2 - r);
                        sd_addw(g_sd_sbar, g_sd_tmp);
                    }
                    sd_mul(g_sd_w3, 3);
                    u = u - 1;
                }
                /* mid 判定 */
                sd_copy(g_sd_tmp, g_sd_sbar);
                sd_addc(g_sd_tmp, 1);          /* tmp = S'+1 = 3^u-disc */
                i = sd_cmp(g_sd_disc, g_sd_tmp);
                if (i > 0) {
                    if (sd_addc(g_sd_w, 1) != 0) sticky = 1;
                } else if (sticky != 0) {
                    /* R = V-δ（δ>0，sticky 已证）：真值可能触/过中点。
                       rescue 阈值 gap ≤ 2：真 tie（/2 型小数）时 gap==1，
                       非贴中点值 gap ≫ 2 不会误救（1e6 例 corpus 校准）。 */
                    sd_copy(g_sd_tmp, g_sd_sbar);
                    sd_addc(g_sd_tmp, 1);
                    sd_copy(g_sd_w3, g_sd_disc);
                    sd_addc(g_sd_w3, 2);
                    if (sd_cmp(g_sd_w3, g_sd_tmp) >= 0) {
                        if (sd_addc(g_sd_w, 1) != 0) sticky = 1;
                    }
                }
            } else {
                while (u < 0) { sd_mul(g_sd_w, 3); u = u + 1; }
            }
            m0 = g_sd_w[0] + g_sd_w[1] * SD_BASE;
            x = uo - t;
            if (m0 >= SD_P34) {                /* 升格进位到 3^34 */
                m0 = m0 - SD_P34 + SD_P33;
                x = x + 1;
            }
            {
                /* x 相对整型 M0（V = M0×3^x）；格式值 = M0×3^(E-bias-33)
                   → E-bias = x+33，bias=29524 */
                int e = x + 29524 + 33;
                if (e >= 59048) return neg ? SD_NINF : SD_INF;
                if (e <= 0) return neg ? SD_P44 : 0;
                {
                    int raw = m0 + e * SD_P34;
                    if (neg != 0) raw = raw + SD_P44;
                    return raw;
                }
            }
        }
    }
}

int atof(const char *s)
{
    return strtod(s, 0);
}
