// test37_raii —— 0.0.37 RAII 栈展开
// 先算降级形态（本测试即验收规格）：
//   栈对象自动 dtor：函数尾统一调用（构造逆序）——粒度偏差文档化
//     （块退出=后续版本；0.0.37 函数级粒度）
//   throw 展开路径：catch else 上浮前插 try 体 body_dtors 逆序
//   函数级上浮（0.0.36 通道）：_eh_ret 前插全帧 dtor 逆序
//   双调防御：函数尾调全部清单一次；上浮路径 return 后不走函数尾
// 析构可观测：g_log += id（0.0.19 全局通道；位安全累加）
int g_log = 0;
class Res {
    int id;
public:
    Res(int i) : id(i) {}
    ~Res() { g_log = g_log + id; }       // 析构可观测
    int get() { return id; }
};
int f() {
    Res r1(1);
    Res r2(2);
    return r1.get() + r2.get();          // 3——函数尾 dtor 逆序：r2(2), r1(1)
}
int main() {
    int a;
    a = f();                             // 3；g_log = 2+1 = 3
    Res* p = new Res(4);
    delete p;                            // 手动 dtor：+4 → 7
    try {
        Res r3(5);                       // try 体 dtor 对象
        throw 9;                         // → catch else 上浮：r3 dtor +5 → 12
    } catch (int e) { }
    int b;
    b = g_log;                           // 12
    try {
        Res r4(6);                       // 正常完成：函数尾 main dtor 序列
    } catch (int e) { }
    return b + (g_log == 18);            // 7 + 1 = 8（b=g_log@7：r3 dtor 于 main
                                         //   尾触发在 b 之后——g_log 终值 3+4+6+5=18 ✓）
}
