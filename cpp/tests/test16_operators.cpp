// test16_operators —— 0.0.16 操作符重载（+/== 限算术数值结果）
// 成员形态：_ZN3NumplERK3Num / _ZN3NumeqeqERK3Num（算子表 pl/eq 无长度前缀）
// 自由形态：_ZplRK5MeterRK5Meter / _ZeqeqRK5MeterRK5Meter
class Num {
    int v;
public:
    Num(int x) : v(x) {}
    int operator+(const Num& r) { return v + r.v; }
    bool operator==(const Num& r) { return v == r.v; }
    int get() { return v; }
};
class Meter {
    int m;
public:
    Meter(int x) : m(x) {}
    int get() { return m; }
};
int operator+(const Meter& a, const Meter& b) {   // 自由形态
    return a.get() + b.get();
}
bool operator==(const Meter& a, const Meter& b) {
    return a.get() == b.get();
}
int main() {
    Num a(3);
    Num b(4);
    int r;
    r = a + b;                 // 成员 operator+ → 7
    int s;
    s = (a == b);              // 成员 == → 0
    Meter x(10);
    Meter y(5);
    int t;
    t = x + y;                 // 自由 operator+ → 15
    int u;
    u = (x == y);              // 自由 == → 0
    int w;
    w = (a == a);              // == 真值 → 1
    return r + s + t + u * 10 + w * 100;   // 7 + 0 + 15 + 0 + 100 = 122
}
