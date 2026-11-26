// test08b_lifetime —— const T& 绑临时量：0.0.9 拒绝 → 0.0.10 物化延寿
// v0.0.9 曾以 N8 拒绝本用例；0.0.10 实现「调用者作用域具名物化」后转正。
// 仍被拒的：非 const T& 绑临时量（test08a——C++ 法：非常量左值引用不延寿）。
class Math {
    int base;
public:
    Math(int b) : base(b) {}
    int sum(const int& a, const int& b) { return a + b; }
};

int main() {
    Math m(10);
    return m.sum(3, 4);   // 双临时量物化（__tmp0=3, __tmp1=4）→ 7
}
