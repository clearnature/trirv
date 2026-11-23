/* fcvt_tri —— fcvt 薄单元（T9 拆分）：共享核心 ec_core_tri（TRVO 导入）。
 * musl src/stdlib/fcvt.c 的三进制原生改编；契约见 ec_core_tri.c。 */
extern void ec_big_init(void);
extern int *ec_core(int raw, int n, int *decpt, int *sign);
extern int ec_peek_decpt(int raw, int *sign);
extern int g_ec_buf[17];
extern int SD_E_P34;
extern int SD_E_P44;
extern int SD_E_P2M44;

char *fcvt(double value, int n, int *decpt, int *sign)
{
    int i;
    int s;
    int d;
    int lz;
    int raw;

    ec_big_init();
    raw = *(int *)&value;
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

    /* lz = -D'（舍入到 n 位小数后的 decpt）——契约锁定该定义 */
    d = ec_peek_decpt(raw, &s);
    *sign = s;
    lz = -d;

    if (n <= lz) {
        *decpt = 1;
        if (n > 14) n = 14;
        i = 0;
        while (i <= n) { g_ec_buf[i] = 48; i = i + 1; }
        while (i < 16) { g_ec_buf[i] = 0; i = i + 1; }
        return (char *)g_ec_buf;
    }
    return (char *)ec_core(raw, n + d, decpt, sign);
}
