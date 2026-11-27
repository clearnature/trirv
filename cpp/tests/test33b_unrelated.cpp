// test33b_unrelated —— 0.0.33 拒绝契约：dynamic_cast 无关类型（编译期 E0）
class X1 {
    int v;
public:
    X1(int x) : v(x) {}
    virtual int f() { return 0; }
};
class Y1 {
    int w;
public:
    Y1(int x) : w(x) {}
    virtual int g() { return 0; }
};
int main() {
    X1* x = new X1(1);
    Y1* y = dynamic_cast<Y1*>(x);   // ← 无关类型：E0
    return 0;
}
