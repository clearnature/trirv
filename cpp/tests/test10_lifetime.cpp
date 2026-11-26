// test10_lifetime —— 0.0.10 生命周期扩展验收
// ① 字面量物化 ② VALUE 返回值物化 ③ 嵌套物化（声明序=依赖序）
// ④ 引用局部声明延寿（const int& half = 5; 生命周期=作用域）
// ⑤ 引用转发实参（half 左值经 ref 通道直传）
class Math {
    int bias;
public:
    Math(int b) : bias(b) {}
    int clamp(const int& v) {
        if (v < bias) { return v; }
        return bias;
    }
    int add2(const int& a, const int& b) { return a + b; }
};

int main() {
    Math m(10);
    int r;
    r = m.add2(3, 4);             // ① → 7
    int s;
    s = m.clamp(50);              // ② 字面量：50>10 → 10
    int u;
    u = m.add2(m.clamp(50), 7);   // ③ clamp(50)=10 VALUE 返回→物化 → 17
    const int& half = 5;          // ④ 物化 + 延寿
    int w;
    w = m.add2(half, u);          // ⑤ half=5, u=17 → 22
    return r + s + u + w;         // 7 + 10 + 17 + 22 = 56
}
