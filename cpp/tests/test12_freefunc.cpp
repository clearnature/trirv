// test12_freefunc —— 0.0.12 自由函数透传验收
// 自由函数 mangling：_Z<len>name<params>（与成员 _ZN… 区分）
// ① 自由函数 ② const T& 物化 ③ 引用返回→引用局部延寿
// ④ 对象引用形参（Box&）⑤ 方法内裸调自由函数（隐式 this 判定不误绑）
int add(int a, int b) { return a + b; }
int sumc(const int& a, const int& b) { return a + b; }
const int& pick(const int& a, const int& b) {
    if (a < b) { return a; }
    return b;
}
class Box {
    int val;
public:
    Box(int v) : val(v) {}
    int get() { return val; }
    void set(int v) { val = v; }
};
int bump(Box& b, int d) {          // 对象引用形参 → struct Box *b
    b.set(b.get() + d);
    return b.get();
}
int main() {
    int r;
    r = add(1, 2);                 // ① → 3
    int s;
    s = sumc(10, 4);               // ② 物化 __tmp=10,4 → 14
    const int& lo = pick(3, 9);    // ③ 引用返回绑定局部（延寿）→ lo=3
    int t;
    t = sumc(lo, 1);               // lo 引用转发 + 物化 1 → 3+1=4
    Box b(5);
    int u;
    u = bump(b, 7);                // ④ b.val = 5+7 = 12
    return r + s + t + u;          // 3 + 14 + 4 + 12 = 33
}
