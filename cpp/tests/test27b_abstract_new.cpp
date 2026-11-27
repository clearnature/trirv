// test27b_abstract_new —— 0.0.27 拒绝契约：抽象类不可实例化（C++ 法）
// new Shape(...) 与栈对象 Shape s 两条实例化路径均 sema E0
class Shape {
    int tag;
public:
    Shape(int t) : tag(t) {}
    virtual int area() = 0;
};
int main() {
    Shape* p = new Shape(1);     // ← 抽象类堆实例化：E0
    return 0;
}
