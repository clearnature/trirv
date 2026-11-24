/* time_tri —— 批次 8 time 三值本征实现（ADR-38 M4）
 * 算法参照 musl __secs_to_tm（era-based civil calendar，纯整数运算）。
 * 三值适配：long long → int（TriRV 全字 45 trit ≈ 2^71.3，86400 乘法安全）。
 * 时区：固定 UTC（微内核无 /etc/localtime）。
 * VM 时钟：SYS_CLOCK_GETTIME=113 写 sec+nsec 各 9 tryte。 */

int clock_gettime(int clk_id, int *timespec);

/* ---- struct tm（musl 布局）---- */
/* 展开定义（trirv_cc 不用 #define 的 typedef 复杂形态）：
 * struct tm = 9 int × 9 tryte = 81 tryte
 *   [0]tm_sec [1]tm_min [2]tm_hour [3]tm_mday [4]tm_mon
 *   [5]tm_year [6]tm_wday [7]tm_yday [8]tm_isdst
 */

/* ---- __secs_to_tm（musl 算法三值适配）---- */
int tri_secs_to_tm(int t, int *tm) {
    /* LEAPOCH = 946684800 + 86400*(31+29) = 946684800 + 5443200 = 952217600 */
    int LEAPOCH = 951868800;   /* 2000-03-01: 946684800+86400*60 */
    int DAYS_PER_400Y = 365*400 + 97;   /* 146097 */
    int DAYS_PER_100Y = 365*100 + 24;   /* 36524 */
    int DAYS_PER_4Y = 365*4 + 1;        /* 1461 */
    int days, secs, years;
    int remdays, remsecs, remyears;
    int qc_cycles, c_cycles, q_cycles;
    int months;
    int wday, yday, leap;
    /* days_in_month（3 月起）：31,30,31,30,31,31,30,31,30,31,31,29 */
    int dim0=31, dim1=30, dim2=31, dim3=30, dim4=31, dim5=31;
    int dim6=30, dim7=31, dim8=30, dim9=31, dim10=31, dim11=29;

    secs = t - LEAPOCH;
    days = secs / 86400;
    remsecs = secs % 86400;
    if (remsecs < 0) { remsecs += 86400; days--; }

    wday = (3 + days) % 7;
    if (wday < 0) wday += 7;

    qc_cycles = days / DAYS_PER_400Y;
    remdays = days % DAYS_PER_400Y;
    if (remdays < 0) { remdays += DAYS_PER_400Y; qc_cycles--; }

    c_cycles = remdays / DAYS_PER_100Y;
    if (c_cycles == 4) c_cycles--;
    remdays -= c_cycles * DAYS_PER_100Y;

    q_cycles = remdays / DAYS_PER_4Y;
    if (q_cycles == 25) q_cycles--;
    remdays -= q_cycles * DAYS_PER_4Y;

    remyears = remdays / 365;
    if (remyears == 4) remyears--;
    remdays -= remyears * 365;

    leap = (!remyears) && (qc_cycles || !c_cycles);
    yday = remdays + 31 + 28 + leap;
    if (yday >= 365 + leap) yday -= 365 + leap;

    years = remyears + 4*q_cycles + 100*c_cycles + 400*qc_cycles;

    /* 逐月消减（3 月起的月表） */
    months = 0;
    { int mvals[12]; mvals[0]=dim0; mvals[1]=dim1; mvals[2]=dim2; mvals[3]=dim3;
      mvals[4]=dim4; mvals[5]=dim5; mvals[6]=dim6; mvals[7]=dim7;
      mvals[8]=dim8; mvals[9]=dim9; mvals[10]=dim10; mvals[11]=dim11;
      while (mvals[months] <= remdays) {
          remdays -= mvals[months];
          months++;
      }
    }

    if (months >= 10) { months -= 12; years++; }

    tm[5] = years + 100;    /* tm_year */
    tm[4] = months + 2;     /* tm_mon */
    tm[3] = remdays + 1;    /* tm_mday */
    tm[6] = wday;           /* tm_wday */
    tm[7] = yday;           /* tm_yday */
    tm[8] = 0;              /* tm_isdst (UTC 固定) */
    tm[2] = remsecs / 3600;          /* tm_hour */
    tm[1] = remsecs / 60 % 60;       /* tm_min */
    tm[0] = remsecs % 60;            /* tm_sec */
    return 0;
}

/* ---- __tm_to_secs（反向：tm → seconds since epoch）----
 * Hinnant days_from_civil 精确式（与 tri_secs_to_tm 逐日互逆）。
 * Jan/Feb 归前一个 civil 年（era 年界在 3 月 1 日）——旧「月表累加法」
 * 在 Jan/Feb 会落到错误的 era 年块（差两个月），已废弃。 */
int tri_tm_to_secs(int *tm) {
    int Y = tm[5] + 1900;      /* civil year */
    int m0 = tm[4];            /* 0=Jan .. 11=Dec */
    int d = tm[3];             /* 1..31 */
    int y = Y;
    if (m0 <= 1) y--;          /* Jan/Feb → 前 civil 年（era 对齐） */
    int era = y / 400;         /* 本项目日期域 y>0，trunc=floor */
    int yoe = y - era * 400;   /* [0,399] */
    int mm = (m0 >= 2) ? (m0 - 2) : (m0 + 10);   /* 3 月起月序 [0,11] */
    int doy = (153 * mm + 2) / 5 + (d - 1);      /* 年内日序（March-based） */
    int doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    int days = era * 146097 + doe - 719468;      /* days since 1970-01-01 */
    return days * 86400 + tm[2] * 3600 + tm[1] * 60 + tm[0];
}

/* ---- time（调用 VM SYS_CLOCK_GETTIME；原型见文件头）---- */
int time(int *t) {
    int ts[2];               /* [0]=sec [1]=nsec */
    clock_gettime(0, ts);    /* CLOCK_REALTIME=0 */
    if (t) *t = ts[0];
    return ts[0];
}

/* ---- gmtime_r ---- */
int *gmtime_r(const int *t, int *tm) {
    tri_secs_to_tm(*t, tm);
    return tm;
}

/* ---- gmtime（静态缓冲版）---- */
int tri_gmtime_buf[9];
int *gmtime(const int *t) {
    tri_secs_to_tm(*t, tri_gmtime_buf);
    return tri_gmtime_buf;
}

/* ---- mktime / timegm（固定 UTC → 二者同义；符号面按 POSIX 补齐）---- */
int mktime(int *tm) {
    return tri_tm_to_secs(tm);
}

int timegm(int *tm) {
    return tri_tm_to_secs(tm);
}

/* ---- difftime（TriRV 全字等宽 → 简单减法）---- */
int difftime(int a, int b) {
    return a - b;
}

/* ---- strftime（基础格式符：%Y %m %d %H %M %S——picolibc 极简模式）---- */
int tri_d2s_t(int val, char *buf) {
    int pos = 0; int neg = 0; int i, j;
    char tmp[20];
    if (val < 0) { neg = 1; val = -val; }
    if (val == 0) { buf[0] = 48; buf[1] = 0; return 1; }
    while (val > 0) { tmp[pos] = 48 + val % 10; pos++; val = val / 10; }
    if (neg) { tmp[pos] = 45; pos++; }
    for (i = 0, j = pos - 1; i < pos; i++, j--) buf[i] = tmp[j];
    buf[pos] = 0;
    return pos;
}

/* tri_pad2：两位补零 */
int tri_pad2(int val, char *buf) {
    int n = tri_d2s_t(val, buf);
    if (n == 1) { buf[1] = buf[0]; buf[0] = 48; n = 2; }
    buf[n] = 0;
    return n;
}

int strftime(char *buf, int max, const char *fmt, int *tm) {
    int total = 0;
    const char *p = fmt;
    char nb[8];
    while (*p && total < max - 1) {
        if (*p == 37) {                     /* '%' */
            p++;
            if (*p == 89) {                  /* %Y → year+1900 */
                { int n = tri_d2s_t(tm[5] + 1900, nb);
                  int i; for (i = 0; i < n && total < max - 1; i++) buf[total++] = nb[i]; }
            } else if (*p == 109) {          /* %m → month+1 (2位) */
                { int n = tri_pad2(tm[4] + 1, nb);
                  int i; for (i = 0; i < n && total < max - 1; i++) buf[total++] = nb[i]; }
            } else if (*p == 100) {          /* %d → mday (2位) */
                { int n = tri_pad2(tm[3], nb);
                  int i; for (i = 0; i < n && total < max - 1; i++) buf[total++] = nb[i]; }
            } else if (*p == 72) {           /* %H → hour (2位) */
                { int n = tri_pad2(tm[2], nb);
                  int i; for (i = 0; i < n && total < max - 1; i++) buf[total++] = nb[i]; }
            } else if (*p == 77) {           /* %M → min (2位) */
                { int n = tri_pad2(tm[1], nb);
                  int i; for (i = 0; i < n && total < max - 1; i++) buf[total++] = nb[i]; }
            } else if (*p == 83) {           /* %S → sec (2位) */
                { int n = tri_pad2(tm[0], nb);
                  int i; for (i = 0; i < n && total < max - 1; i++) buf[total++] = nb[i]; }
            } else {
                buf[total++] = *p;
            }
            p++;
        } else {
            buf[total++] = *p; p++;
        }
    }
    buf[total] = 0;
    return total;
}
