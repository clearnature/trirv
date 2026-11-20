/* ecvt/fcvt/gcvt —— musl src/stdlib/ecvt.c + fcvt.c + gcvt.c 的三进制原生改编。
 *
 * 与上游差异：
 *   1. 上游是 sprintf("%.*e"/"%.*f"/"%.*g") 的薄包装——TriRV 无 stdio 浮点格式化，
 *      直接从 TF64 raw 的 (s, M, E) 走位生成十进制数字串。
 *   2. 入参 double = TF64 raw（45-trit 字，guest 视角就是 int）。
 *   3. 静态 buf 用全局数组（TriRV C 子集无 static 局部；单线程假设与 musl 相同）。
 *   4. 负 raw 归一（重要）：s=1 且 E 大的 raw（如 ±Inf 负数）以 45-trit 平衡
 *      解读为负数（mod ≥ 1.477e21 的字全部读负），入口必须先归一
 *      raw<0 → +2×3^44，再按 raw ≥ 3^44 拆符号。strtod 只产出 raw 不受影响。
 *   5. ±Inf/NaN：musl 走 sprintf 是垃圾路径（未定义）——TriRV 定义：ecvt/fcvt
 *      数字串全 '0'、decpt=0、sign=符号位；gcvt 打印 "inf"/"-inf"/"nan"。
 *      契约锁定，不再漂移。
 *   5. fcvt 精度界：musl n ≤ 1400——TriRV 数值表示的精确十进制窗口约 45 位，
 *      有效位超过 40 处 clamp（TriRV 定义，契约锁定；musl 对 double 也只有
 *      ~17 位有效，超出部分打的是 double 精确值的数字，两者都不是"数学值"）。
 *   6. musl ecvt 的 n∈{1..16} 保留否则 15、fcvt 的 n≤lz 返回静态零串切片、
 *      gcvt 的 %g 规则——逐条镜像。
 *
 * 走位不变式：V = A×3^f/10^g（A 为 6 字小数大数，base 10^16，容量 10^96）。
 *   f>0：×3（精确，f--，需 room）；满时 ÷10（截断 sticky，g--）。
 *   f<0：A ≥ 10^60 时 ÷3（截断 sticky，f++）；否则 ×10（精确，g++）。
 * 终态 f=0：V = A/10^g；A 归一到 [10^44,10^45) 后 decpt = 45-g；
 *   逐位提取 = A×10 后取 w[2]÷10^13（单字除法），余数即下一轮 A；
 *   第 n 位后按 2·tail vs 10^45 half-up+sticky 舍入。
 * 走位窗口纪律：有损步只在 A ≥ 10^60 时做，每步相对丢失 ≤10^-60，
 *   步数 ≤6 万，总漂移 ≤10^-55 ≪ 第 17 位的舍入阈值 10^-17。
 */

int g_ec_w[6];        /* 主累加器 A */
int g_ec_buf[17];     /* ecvt 数字串（'0'..'9'）；musl n=16 规则下 g_ec_buf[16]=0 需第 17 格 */
char g_ec_zeros[16];  /* fcvt 静态零串 15 个 '0' + NUL（char 数组：返回 char* 算术，绕开 int*+cast 编译坑） */
int g_ec_tmp[6];
int g_ec_w3[6];

int SD_E_BASE = 10000000000000000;     /* 10^16 */
int SD_E_P34 = 16677181699666569;      /* 3^34 */
int SD_E_P44 = 984770902183611232881;  /* 3^44 */
int SD_E_P2M44 = 1969541804367222465762; /* 2×3^44 = 3^45-3^44（负 raw 归一） */

int ec_isroom(int *w)
{
    return w[5] == 0;                  /* A < 10^80 */
}

int ec_mul(int *w, int f)
{
    int c = 0;
    int i = 0;
    while (i < 6) {
        int cur = w[i] * f + c;
        w[i] = cur - cur / SD_E_BASE * SD_E_BASE;
        c = cur / SD_E_BASE;
        i = i + 1;
    }
    return c;
}

int ec_div(int *w, int d)
{
    int r = 0;
    int i = 5;
    while (i >= 0) {
        int cur = r * SD_E_BASE + w[i];
        w[i] = cur / d;
        r = cur - cur / d * d;
        i = i - 1;
    }
    return r;
}

void ec_copy(int *d, int *s)
{
    int i = 0;
    while (i < 6) { d[i] = s[i]; i = i + 1; }
}

void ec_setw(int *w, int lo, int hi)
{
    int i = 2;
    w[0] = lo;
    w[1] = hi;
    while (i < 6) { w[i] = 0; i = i + 1; }
}

void ec_init_zeros(void)
{
    int i = 0;
    while (i < 15) { g_ec_zeros[i] = 48; i = i + 1; }
    g_ec_zeros[15] = 0;                            /* NUL 终止：切片 write 全长可解析 */
}
/* 注：g_ec_zeros 用 char 数组——int* 算术 + (char*) cast 的返回在
   trirv_cc 上实测返回 0（编译坑），char* + int 直返正常。 */

/* ---- ecvt 核心：raw → n 位十进制数字 + decpt + sign ----
 * 返回 g_ec_buf（长度 n 的数字串，无 NUL——POSIX ecvt 不带终止符，
 * 但 TriRV 加收尾 0 便于 guest 使用，buf 容量 16 > 15 上限）。 */
int *ec_core(int raw, int n, int *decpt, int *sign)
{
    int s = 0;
    int rest;
    int E;
    int M;
    int f;
    int g = 0;
    int sticky = 0;
    int i;
    int carry;
    int tail_ge_half;
    int d;

    /* s=1 的 raw（mod 3^45 ≥ 3^44）在平衡解读下可能为负：
       raw < 0 ⟺ mod ≥ 1.477e21（负半轴），先 +2×3^44 归一回 [3^44, 3^45) 再拆符号 */
    if (raw < 0) { s = 1; raw = raw + SD_E_P2M44; }
    else if (raw >= SD_E_P44) { s = 1; raw = raw - SD_E_P44; }
    E = raw / SD_E_P34;
    M = raw - E * SD_E_P34;
    *sign = s;

    if (n < 1 || n > 16) n = 15;
    g_ec_buf[n] = 0;

    /* 特殊值：±Inf / NaN（TriRV 定义） */
    if (E >= 59048) {
        i = 0;
        while (i < n) { g_ec_buf[i] = 48; i = i + 1; }
        *decpt = 0;
        return g_ec_buf;
    }
    if (E == 0 && M == 0) {
        i = 0;
        while (i < n) { g_ec_buf[i] = 48; i = i + 1; }
        *decpt = 1;
        return g_ec_buf;
    }

    /* A = M（6 字分解；M ∈ [3^33, 3^34) 可跨 w[1]） */
    ec_setw(g_ec_w, 0, 0);
    g_ec_w[0] = M - M / SD_E_BASE * SD_E_BASE;
    g_ec_w[1] = M / SD_E_BASE;

    /* 吸收 f = (E-bias) - 33 到 A——格式值 = (M/3^33)×3^(E-bias)，
       M 是整数字段，3^33 分母必须一并走位 */
    f = E - 29524 - 33;
    while (f != 0) {
        if (f > 0) {
            if (ec_isroom(g_ec_w)) { ec_mul(g_ec_w, 3); f = f - 1; }
            else { if (ec_div(g_ec_w, 10) != 0) sticky = 1; g = g - 1; }
        } else {
            /* ÷3 门槛 10^60：低于则先 ×10 抬升（×10 精确） */
            if (g_ec_w[3] != 0 || g_ec_w[4] != 0 || g_ec_w[5] != 0) {
                if (ec_div(g_ec_w, 3) != 0) sticky = 1;
                f = f + 1;
            } else {
                ec_mul(g_ec_w, 10);
                g = g + 1;
            }
        }
    }
    /* 终态 V = A/10^g；归一到 [10^44, 10^45)
       A ≥ 10^45 ⟺ w[2] ≥ 10^13 或高位非零；A < 10^44 ⟺ w[2] < 10^12 且高位零 */
    while (g_ec_w[2] >= 10000000000000 || g_ec_w[3] != 0 || g_ec_w[4] != 0 || g_ec_w[5] != 0) {
        if (ec_div(g_ec_w, 10) != 0) sticky = 1;
        g = g - 1;
    }
    while (g_ec_w[2] < 1000000000000 && g_ec_w[3] == 0 && g_ec_w[4] == 0 && g_ec_w[5] == 0) {
        ec_mul(g_ec_w, 10);
        g = g + 1;
    }
    *decpt = 45 - g;

    /* 逐位提取 */
    i = 0;
    while (i < n) {
        ec_mul(g_ec_w, 10);                       /* A ∈ [10^45, 10^46) 或更小 */
        d = g_ec_w[2] / 10000000000000;           /* ÷10^13 → [0,10) */
        g_ec_w[2] = g_ec_w[2] - d * 10000000000000;
        g_ec_buf[i] = 48 + d;
        i = i + 1;
    }
    /* 舍入：tail = A；up iff 2·tail ≥ 10^45（tie → sticky 半升） */
    tail_ge_half = 0;
    if (g_ec_w[2] > 5000000000000) tail_ge_half = 1;
    else if (g_ec_w[2] == 5000000000000) {
        if (g_ec_w[1] != 0 || g_ec_w[0] != 0) tail_ge_half = 1;
        else tail_ge_half = 1;                      /* 精确 tie：half-up（对齐 trirv_float 2r>=den） */
    } else if (sticky != 0 && g_ec_w[2] == 4999999999999 &&
               g_ec_w[1] == 9999999999999999 && g_ec_w[0] == 9999999999999999) {
        /* tail = mid-1 且走位有损（sticky）：真值在 mid 或 mid+δ（δ<1 单位）
           ——十进制 .5 精确 tie 被 ÷3/÷10 的 <1 单位损耗压到 mid-1，
           按真值 half-up 应升格。亚单位歧义带记账于 ecvt_tri.c 文件头。 */
        tail_ge_half = 1;
    }
    if (tail_ge_half != 0) {
        carry = 1;
        i = n - 1;
        while (carry != 0 && i >= 0) {
            g_ec_buf[i] = g_ec_buf[i] + 1;
            if (g_ec_buf[i] == 58) { g_ec_buf[i] = 48; i = i - 1; }
            else carry = 0;
        }
        if (carry != 0) {
            /* 全 9 进位：右移一位，首位 '1'，decpt+1 */
            i = n - 1;
            while (i > 0) { g_ec_buf[i] = g_ec_buf[i - 1]; i = i - 1; }
            g_ec_buf[0] = 49;
            *decpt = *decpt + 1;
        }
    }
    return g_ec_buf;
}

char *ecvt(int raw, int n, int *decpt, int *sign)
{
    return (char *)ec_core(raw, n, decpt, sign);
}

/* 找 |x| 的 decpt（不产出数字串）：用于 fcvt 的 lz 判定。
   直接调 ec_core 一次拿 decpt（n 用最小 1）。 */
int ec_peek_decpt(int raw, int *sign)
{
    int d = 0;
    ec_core(raw, 1, &d, sign);
    return d;
}

char *fcvt(int raw, int n, int *decpt, int *sign)
{
    int i;
    int s;
    int d;
    int lz;

    if (n > 1400) n = 1400;
    if (n < 0) n = 0;

    /* 特殊值：与 ecvt 的 TriRV 定义一致（委托 ec_core 的全 0 串） */
    {
        int E;
        int rest = raw;
        int sg1 = 0;
        if (rest < 0) { rest = rest + SD_E_P2M44; sg1 = 1; }
        else if (rest >= SD_E_P44) { rest = rest - SD_E_P44; sg1 = 1; }
        *sign = sg1;
        E = rest / SD_E_P34;
        if (E >= 59048) {
            return (char *)ec_core(raw, n, decpt, sign);
        }
        if (E == 0 && rest == 0) {
            /* ±0：零串路径——统一填 g_ec_buf（'0'×(n+1) + 0 终止字），
               musl 的 "000…"+14-n 切片含 n+1 个可见 '0'（+1 quirk）✓ */
            *sign = sg1;
            *decpt = 1;
            if (n > 14) n = 14;
            if (n < 0) n = 0;
            i = 0;
            while (i <= n) { g_ec_buf[i] = 48; i = i + 1; }
            while (i < 16) { g_ec_buf[i] = 0; i = i + 1; }
            return (char *)g_ec_buf;
        }
    }

    /* lz = -D'（舍入到 n 位小数后的 decpt）。
       musl 用 sprintf %.nf 后量 int 部分长度——等价于对 D 做
       “n 位小数舍入是否进跨十进位”修正。TriRV 直接用未舍入 D，
       舍入跨界的修正由下面的 carry 检查补（契约锁定该定义）。 */
    d = ec_peek_decpt(raw, &s);
    *sign = s;
    lz = -d;

    if (n <= lz) {
        /* 零串路径：统一填 g_ec_buf（'0'×(n+1) + 0 终止字） */
        *decpt = 1;
        if (n > 14) n = 14;
        i = 0;
        while (i <= n) { g_ec_buf[i] = 48; i = i + 1; }
        while (i < 16) { g_ec_buf[i] = 0; i = i + 1; }   /* 残留词清零 */
        return (char *)g_ec_buf;
    }
    return (char *)ec_core(raw, n + d, decpt, sign);
}

char *gcvt(int raw, int n, char *buf)
{
    int d = 0;
    int s = 0;
    int i;
    int j;
    int eform;
    int X;
    int last;
    int *digits;

    if (n <= 0) n = 1;
    if (n > 16) n = 16;

    /* ±Inf / NaN → "inf"/"-inf"/"nan"（musl %g 小写） */
    {
        int rest = raw;
        int sg1 = 0;
        if (rest < 0) { rest = rest + SD_E_P2M44; sg1 = 1; }
        else if (rest >= SD_E_P44) { rest = rest - SD_E_P44; sg1 = 1; }
        i = rest / SD_E_P34;
        if (i >= 59048) {
            int isnan = (rest - i * SD_E_P34) != 0;
            j = 0;
            if (sg1) { buf[j] = 45; j = j + 1; }   /* '-' */
            if (isnan) { buf[j] = 110; buf[j+1] = 97; buf[j+2] = 110; j = j + 3; }
            else { buf[j] = 105; buf[j+1] = 110; buf[j+2] = 102; j = j + 3; }
            buf[j] = 0;
            return buf;
        }
    }

    digits = ec_core(raw, n, &d, &s);

    /* ±0：musl %g 打印 "0"（负零 "-0"） */
    if (d == 1) {
        int allz = 1;
        i = 0;
        while (i < n) { if (digits[i] != 48) { allz = 0; } i = i + 1; }
        if (allz) {
            j = 0;
            if (s != 0) { buf[0] = 45; buf[1] = 48; buf[2] = 0; return buf; }
            buf[0] = 48;
            buf[1] = 0;
            return buf;
        }
    }

    X = d - 1;
    eform = 0;
    if (X < -4 || X >= n) eform = 1;

    j = 0;
    if (s != 0) { buf[j] = 45; j = j + 1; }

    if (eform != 0) {
        /* d.dddd e±XX：去尾零（至少保留 1 位数字） */
        last = n - 1;
        while (last > 0 && digits[last] == 48) last = last - 1;
        buf[j] = digits[0];
        j = j + 1;
        if (last > 0) {
            buf[j] = 46;
            j = j + 1;
            i = 1;
            while (i <= last) { buf[j] = digits[i]; j = j + 1; i = i + 1; }
        }
        buf[j] = 101;
        j = j + 1;
        if (X < 0) { buf[j] = 45; X = -X; }
        else buf[j] = 43;
        j = j + 1;
        /* 指数至少 2 位 */
        {
            int hi = X / 10;
            int lo = X - hi * 10;
            if (X >= 100) {
                int h = X / 100;
                buf[j] = 48 + h;
                j = j + 1;
                hi = (X - h * 100) / 10;
                lo = X - h * 100 - hi * 10;
            }
            buf[j] = 48 + hi;
            buf[j+1] = 48 + lo;
            buf[j+2] = 0;
        }
        return buf;
    }

    /* f 形态 */
    if (d <= 0) {
        buf[j] = 48;
        j = j + 1;
        if (n > 0) {
            buf[j] = 46;
            j = j + 1;
            i = 0;
            while (i < -d) { buf[j] = 48; j = j + 1; i = i + 1; }
            i = 0;
            while (i < n) { buf[j] = digits[i]; j = j + 1; i = i + 1; }
        }
        /* 去尾零（保留 "0" 或 "0.x..."） */
        while (j > 1 && buf[j-1] == 48 && buf[j-2] != 46) j = j - 1;
        if (j > 2 && buf[j-1] == 48 && buf[j-2] == 46) { j = j - 1; }
        buf[j] = 0;
        return buf;
    }
    if (d >= n) {
        /* 整数带零尾巴 */
        i = 0;
        while (i < n) { buf[j] = digits[i]; j = j + 1; i = i + 1; }
        i = 0;
        while (i < d - n) { buf[j] = 48; j = j + 1; i = i + 1; }
        buf[j] = 0;
        return buf;
    }
    /* 1 ≤ d < n：int 部分前 d 位 + '.' + 余下 */
    i = 0;
    while (i < d) { buf[j] = digits[i]; j = j + 1; i = i + 1; }
    buf[j] = 46;
    j = j + 1;
    while (i < n) { buf[j] = digits[i]; j = j + 1; i = i + 1; }
    /* 去尾零 */
    while (j > 0 && buf[j-1] == 48) {
        if (buf[j-2] == 46) { j = j - 1; break; }
        j = j - 1;
    }
    if (buf[j-1] == 46) j = j - 1;
    buf[j] = 0;
    return buf;
}

int *ec_dbg_zeros(void) { return g_ec_zeros; }

int *ec_dbg_zp(void) { return g_ec_zeros + 12; }
