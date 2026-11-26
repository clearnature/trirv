// test24_virtual —— 0.0.24 虚函数单继承切片（vtable 间接调用）
// 布局：__vtbl 最前（单继承零 this 调整）；槽序=base 先序+override 保位；
// 赋槽=ctor 运行时幂等（全局 fn 地址初始化=trirv_cc 拒，探针已证）
class Shape {
    int tag;
public:
    Shape(int t) : tag(t) {}
    virtual int area() { return 0; }        // 默认槽
    int describe() { return area(); }       // 经 this 二级虚分派
};
class Square : public Shape {
    int side;
public:
    Square(int t, int s) : Shape(t), side(s) {}
    int area() { return side * side; }      // override（同槽）
};
int main() {
    Square* s = new Square(1, 5);
    int r;
    r = s->area();             // 经指针虚分派 → 25
    int d;
    d = s->describe();         // describe 非虚 → area 虚 → 25
    delete s;
    Shape* up = new Square(2, 3);   // 基指针 upcast（首成员兼容）
    int w;
    w = up->area();            // 经基指针虚分派 → 9
    delete up;
    return r + d + w;          // 25 + 25 + 9 = 59
}
