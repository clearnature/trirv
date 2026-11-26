// test19_dtor —— 0.0.19 ~X dtor + delete 对接 X_dtor + 全局可观测通道
// 语义序：delete p → X_dtor(p); free(p);（dtor 写全局计数，free 前落盘）
int g_freed = 0;
class Res {
    int handle;
public:
    Res(int h) : handle(h) {}
    ~Res() { g_freed = g_freed + 1; }   // 析构可观测：全局计数
    int get() { return handle; }
};
int main() {
    Res* p = new Res(3);
    int r;
    r = p->get();              // 3
    delete p;                  // Res_dtor(p) → g_freed=1; free(p)
    Res* q = new Res(5);       // 块复用
    int s;
    s = q->get();              // 5
    delete q;                  // g_freed=2
    int t;
    t = g_freed;               // 2（全局可观测）
    return r + s + t * 10;     // 3 + 5 + 20 = 28
}
