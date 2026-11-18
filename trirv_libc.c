/* ============================================================
   trirv_libc.c —— TriRV 最小动态内存分配器（C 子集实现）

   设计约束：
     - tryte 编址；int/指针 = 9 tryte（1 字）；char = 1 tryte
     - 编译器无全局变量：跨调用状态存固定 tryte 地址 META=65536
       [META] = 空闲链表头（块字地址，0=空链表）
     - 块头 2 字：blk[0]=size（整块字数，含头），blk[1]=next（空闲块地址）
       数据区从 blk+2 起（字地址）
     - 空闲链表按地址升序；free 时主动与前后相邻空闲块合并

   对外 API：
     int* malloc(int n)        n = 请求 tryte 数，返回数据区字指针
     void  free(int *p)
     int*  calloc(int n)       零初始化
     int*  realloc(int *p,int n)
   ============================================================ */

/* 按地址升序把 blk 插入空闲链表 */
void insert_free(int *blk) {
    int *fh; int *prev; int *cur;
    fh = 65536;
    prev = 0;
    cur = *fh;
    while (cur != 0 && cur < blk) {
        prev = cur;
        cur = *(cur + 1);
    }
    *(blk + 1) = cur;
    if (prev == 0) *fh = blk;
    else *(prev + 1) = blk;
}

/* 首次适配分配：优先复用空闲块，否则向 brk 扩展 */
int* malloc(int n) {
    int need; int *fh; int *prev; int *cur; int sz; int *nxt; int *rest;
    int baddr; int total;
    need = 2 + (n + 8) / 9;          /* 2 字头 + ceil(n/9) 数据字 */
    fh = 65536;
    prev = 0;
    cur = *fh;
    while (cur != 0) {
        sz = *cur;                   /* cur[0] = 块大小（字） */
        if (sz >= need) {
            nxt = *(cur + 1);        /* cur[1] = next */
            if (prev == 0) *fh = nxt;
            else *(prev + 1) = nxt;
            if (sz - need >= 3) {    /* 剩余够放一个最小块则切分 */
                rest = cur + need;
                *rest = sz - need;
                *(rest + 1) = 0;
                insert_free(rest);
            }
            return cur + 2;          /* 数据区 = 块头 + 2 字 */
        }
        prev = cur;
        cur = *(cur + 1);
    }
    baddr = brk(0);                  /* 查询当前堆顶 */
    total = baddr + need * 9;
    if (brk(total) != total) return 0;  /* 扩展失败 */
    cur = baddr;
    *cur = need;
    *(cur + 1) = 0;
    return cur + 2;
}

/* 释放并与相邻空闲块合并 */
void free(int *p) {
    int *blk; int *fh; int *prev; int *cur; int sz; int *nxt;
    blk = p - 2;
    fh = 65536;
    prev = 0;
    cur = *fh;
    while (cur != 0 && cur < blk) {  /* 定位插入点：cur 是首个 > blk 的空闲块 */
        prev = cur;
        cur = *(cur + 1);
    }
    sz = *blk;
    /* 与后继合并：cur 恰好紧邻 blk 末尾 */
    if (cur != 0 && cur == blk + sz) {
        nxt = *(cur + 1);
        sz = sz + *cur;
        cur = nxt;
    }
    /* 与前驱合并：prev 末尾恰好紧邻 blk */
    if (prev != 0 && prev + *prev == blk) {
        *prev = *prev + sz;
        *(prev + 1) = cur;
    } else {
        *blk = sz;
        *(blk + 1) = cur;
        if (prev == 0) *fh = blk;
        else *(prev + 1) = blk;
    }
}

/* 零初始化分配 */
int* calloc(int n) {
    int *p; int words; int i;
    p = malloc(n);
    if (p == 0) return 0;
    words = (n + 8) / 9;
    i = 0;
    while (i < words) { *(p + i) = 0; i = i + 1; }
    return p;
}

/* 调整大小：新分配 + 复制 min(old,new) + 释放旧块 */
int* realloc(int *p, int n) {
    int *np; int oldw; int words; int i;
    if (p == 0) return malloc(n);
    np = malloc(n);
    if (np == 0) return 0;
    oldw = *(p - 2) - 2;             /* 旧数据字数 */
    words = (n + 8) / 9;
    if (oldw > words) oldw = words;  /* 复制较小者，避免越界 */
    i = 0;
    while (i < oldw) { *(np + i) = *(p + i); i = i + 1; }
    free(p);
    return np;
}
