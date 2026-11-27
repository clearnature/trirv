// test27_purevirtual —— 0.0.27 纯虚函数/抽象类
// 先算降级形态（本测试即验收规格）：
//   纯虚槽照常占 vtbl 位（Shape_v 含 area/edges 双槽）；Shape 侧无 impl
//   → ctor 不赋该槽（NULL）；_ZN5Shape4areaEv 不发射；
//   Square override 后 ctor 全槽赋派生 impl → 具体化。
// 合法程序纯虚槽不可达（抽象类不可实例化 = sema E0，见 test27b）。
class Shape {
    int tag;
public:
    Shape(int t) : tag(t) {}
    virtual int area() = 0;             // 纯虚：槽位保留，无 impl
    virtual int edges() { return 0; }   // 虚 + 默认实现
    int get_tag() { return tag; }
};
class Square : public Shape {
    int side;
public:
    Square(int t, int s) : Shape(t), side(s) {}
    int area() { return side * side; }  // override 纯虚 → 具体化
    int edges() { return 4; }           // override 默认实现
};
int main() {
    Square* s = new Square(1, 5);
    int r;
    r = s->area();            // 指针虚分派 → 25
    int e;
    e = s->edges();           // 4
    int t;
    t = s->get_tag();         // 非虚 → 1
    delete s;
    return r + e + t;         // 25 + 4 + 1 = 30
}
