// test43_multiinherit —— 0.0.43 多继承·布局：base 偏移表 + this 调整
// 先算降级形态（本测试即验收规格）：
//   class C : public A, public B → struct C { struct A __base;
//     struct B __base1; int c; }（位 0 基类保 "__base" 名=0.0.26
//     共享槽兼容；附加基类 __base{i} 顺次嵌入——嵌套子对象即偏移表）
//   ctor 多基类 init：A(x)→A_ctor((struct A*)this_);（位 0 零调整）
//     B(y)→B_ctor(&this_->__base1, y);（偏移调整=子对象地址）
//   上转型偏移：A* pa = &obj 位 0 → &obj 零调整（现状不变）；
//     B* pb = &obj → &obj.__base1（sema 标注 Un&.up_base=1 → lower
//     发射子对象取址）
//   经基指针访问：pb->gb() this=struct B*（子对象完整 B 布局，
//     成员/方法零二次调整）；pb->b 直读同址
//   成员查找链：own → base0（DFS）→ base1；两基类同名成员=E0 二义
//   虚分派/dynamic_cast 多基面=0.0.44（本版非虚基类布局面）
class A {
    int a;
public:
    A(int x) : a(x) {}
    int ga() { return a; }
};
class B {
    int b;
public:
    B(int x) : b(x) {}
    int gb() { return b; }
};
class C : public A, public B {
    int c;
public:
    C(int x, int y) : A(x), B(y), c(x + y) {}
    int gc() { return c; }
};
int main() {
    C obj(3, 4);
    int r = obj.ga() + obj.gb() + obj.gc();    // 3+4+7=14
    A* pa = &obj;                              // 位 0：零调整
    B* pb = &obj;                              // __base1：偏移调整
    int s = pa->ga() + pb->gb();               // 3+4=7
    return r + s;                              // 21
}
