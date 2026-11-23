/* gcvt_tri —— gcvt 薄单元（T9 拆分）：共享核心 ec_core_tri（TRVO 导入）。
 * musl src/stdlib/gcvt.c 的三进制原生改编（%g 规则逐条镜像）；契约见
 * ec_core_tri.c。 */
extern void ec_big_init(void);
extern int *ec_core(int raw, int n, int *decpt, int *sign);
extern int SD_E_P34;
extern int SD_E_P44;
extern int SD_E_P2M44;

char *gcvt(double value, int n, char *buf)
{
    int d = 0;
    int s = 0;
    int i;
    int j;
    int eform;
    int X;
    int last;
    int *digits;
    int raw;

    ec_big_init();
    raw = *(int *)&value;
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
