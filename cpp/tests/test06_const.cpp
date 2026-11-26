// test06_const —— const 方法 `f() const` + const 形参（值/引用）
class Box {
    int val;
public:
    Box(int v) : val(v) {}
    int get() const { return val; }                      // const 方法
    void setc(const int v) { val = v; }                  // const 值形参
    int addc(const int& r) const { return val + r; }     // const 引用形参 + const 方法
};

int main() {
    Box b(10);
    b.setc(7);                  // val = 7
    int x = 5;
    return b.addc(x) + b.get(); // (7+5) + 7 = 19
}
