// test32_typeid —— 0.0.32 RTTI·typeinfo + typeid
// 先算降级形态（本测试即验收规格）：
//   vtbl 扩展 __ti 首槽 + 每类全局 char X_ti[] = "X";（ctor 期 X_vt.__ti = X_ti）
//   typeid(对象)：有虚类 → ((struct Root *)&o)->__vtbl->__ti（动态类型，
//     基指针穿越取派生 ti——多态可观测）；非虚类 → X_ti 直发（静态=动态）
//   typeid(Class) → X_ti 直发；比较 = 指针同一性（同类=同全局地址）
class Shape {
    int tag;
public:
    Shape(int t) : tag(t) {}
    virtual int area() { return 0; }
};
class Square : public Shape {
    int side;
public:
    Square(int t, int s) : Shape(t), side(s) {}
    int area() { return side * side; }
};
class Plain2 {
    int v;
public:
    Plain2(int x) : v(x) {}
};
int main() {
    Square* sq = new Square(1, 5);
    int r1;
    r1 = typeid(*sq) == typeid(Square);      // 1（动态 ti = 派生）
    int r2;
    r2 = typeid(*sq) == typeid(Shape);       // 0（动态 ≠ 静态基类）
    Shape* up = sq;                          // 上行（共享槽零调整）
    int r3;
    r3 = typeid(*up) == typeid(Square);      // 1（动态穿越基指针——多态实证）
    Plain2 p2(3);
    int r4;
    r4 = typeid(p2) == typeid(Plain2);       // 1（非虚类静态 ti）
    int r5;
    r5 = typeid(p2) == typeid(Shape);        // 0
    delete sq;
    return r1 + r2 * 2 + r3 * 4 + r4 * 8 + r5 * 16;   // 1+0+4+8+0 = 13
    // 乘数归约 exit 通道安全（<256——POSIX 截断 ABI a0 & 0xFF，1101→77
    // 教训三犯；乘数 1/2/4/8/16 为进制无关算术打包，非位运算）
}
