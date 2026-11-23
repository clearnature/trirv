/* ecvt_tri —— ecvt 薄单元（T9 拆分）：共享核心 ec_core_tri（TRVO 导入）。
 * musl src/stdlib/ecvt.c 的三进制原生改编；语义契约见 ec_core_tri.c 与
 * float_libc 契约套件。 */
extern void ec_big_init(void);
extern int *ec_core(int raw, int n, int *decpt, int *sign);
extern int g_ec_buf[17];

char *ecvt(double value, int n, int *decpt, int *sign)
{
    int raw;
    ec_big_init();
    raw = *(int *)&value;
    return (char *)ec_core(raw, n, decpt, sign);
}
