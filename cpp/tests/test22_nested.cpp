// test22_nested —— 0.0.22 嵌套类（Outer::Inner）
// 键=Outer::Inner（ns 机制同构）；结构体扁平 Outer_Inner；
// mangle 嵌套组件 _ZN5Outer5Inner3getEv；外层体内裸名可见（resolve 回退）
// friend 解析为语义 no-op（子集无访问控制强制）
class Outer {
    int seed;
public:
    class Inner {
        int w;
public:
        Inner(int x) : w(x) {}
        int get() { return w; }
    };
    Outer(int s) : seed(s) {}
    int make() { Inner i(seed * 2); return i.get(); }   // 外层体内裸名
};
int main() {
    Outer::Inner x(5);         // 限定构造
    int r;
    r = x.get();               // 5
    Outer o(6);
    int s;
    s = o.make();              // Inner(12) → 12
    return r + s;              // 5 + 12 = 17
}
