// test45_integration —— 0.1.0 完整子集里程碑：五带混合集成回归
// 先算降级形态（本测试即验收规格）——五带交互面（版本单独测试未覆盖组合）：
//   D 模板×A 类：函数模板显式/推导 + 类模板外联方法
//   C 异常×B RTTI×虚分派：throw 穿过基指针虚调用（检查点在调用点展开）
//   B RTTI：typeid 动态穿越基指针 + dynamic_cast 下转回派生
//   E 多继承×A RAII：双基虚分派 + 多继承对象函数尾 dtor（g_log 可观测）
//   全部惯用法=各带已验证形态；期望值 8 位安全（<256 强制自检）
template<typename T>
T tmax(T a, T b) {
    if (a > b) { return a; }
    return b;
}
template<typename T>
class Box {
    T v;
public:
    Box(T x);
    T get();
};
template<typename T>
Box<T>::Box(T x) : v(x) {}
template<typename T>
T Box<T>::get() { return v; }
class Base {
public:
    Base() {}
    virtual int run(int k) { return k; }
};
class Thr : public Base {
public:
    Thr() {}
    int run(int k) {
        if (k == 1) { throw 55; }
        return k + 3;
    }
};
class VA {
public:
    VA() {}
    virtual int fa() { return 1; }
};
class VB {
public:
    VB() {}
    virtual int fb() { return 2; }
};
class D : public VA, public VB {
public:
    D() {}
    int fa() { return 10; }
    int fb() { return 20; }
    ~D() { g_log = g_log + 1; }        // 多继承对象 dtor 可观测
};
int g_log = 0;
int raii() {
    D obj;                             // 函数尾 dtor → g_log=1
    return obj.fa() + obj.fb();        // 30（双基虚分派直达）
}
int main() {
    int r = tmax<int>(3, 5);           // 5（D: 显式实参）
    char c1 = 65;
    char c2 = 66;
    r = r + tmax(c1, c2);              // +66 = 71（D: 推导）
    Box<int> b(7);
    r = r + b.get();                   // +7 = 78（D: 类模板+外联）
    Thr t;
    Base* bp = &t;
    int got = 0;
    int r2 = 0;
    try { r2 = bp->run(0); } catch (int e) { got = 1; }        // 3 无抛
    try { r2 = r2 + bp->run(1); } catch (int e) { r2 = r2 + e; }   // +55=58（C: throw 穿虚分派）
    r = r + r2 + got;                  // 78 + 58 + 0 = 136
    int ti_ok = typeid(*bp) == typeid(Thr);                    // 1（B: 动态 ti 穿基指针）
    Thr* back = dynamic_cast<Thr*>(bp);                        // B: 下转回派生
    int dc_ok = 0;
    if (back != 0) { dc_ok = back->run(2); }                   // 5
    r = r + ti_ok + dc_ok;             // 136 + 1 + 5 = 142
    r = r + raii();                    // +30 = 172（E: 双基虚分派）
    return r + (g_log == 1);           // 172 + 1 = 173（E: 多继承 RAII dtor）
}
