// test29_copy —— 0.0.29 拷贝构造/拷贝赋值
// 先算降级形态（本测试即验收规格）：
//   隐式拷贝构造（无用户定义）→ 两步结构体赋值 struct X q; q = p;
//   用户拷贝构造 X(const X&)  → ctor 照常发射，copy-init 调 X_ctor(&y, &x)
//   零拷贝 elision X v = X(..) → 直接构造 struct X v; X_ctor(&v, args);
//   用户 operator= → Itanium aS 编码 _ZN…aSERK…（void 形态子集——
//     X& 返回 + *this 链式 = 后续版本）
//   隐式拷贝赋值 a = b → 结构体赋值直通（已工作）
class Plain {
    int a;
    int b;
public:
    Plain(int x, int y) : a(x), b(y) {}
    int sum() { return a + b; }
    void seta(int v) { a = v; }
};
class Tagged {
    int t;
public:
    Tagged(int v) : t(v) {}
    Tagged(const Tagged& other) { t = other.t * 10; }   // 用户拷贝构造
    int get() { return t; }
    void operator=(const Tagged& r) { t = r.t + 1; }    // 用户拷贝赋值
};
int main() {
    Plain p(1, 2);
    Plain q = p;              // 隐式拷贝构造（成员级逐字复制）
    q.seta(100);              // 副本独立：不影响 p
    int r;
    r = p.sum() + q.sum();    // 3 + 102 = 105
    Tagged t1(5);
    Tagged t2 = t1;           // 用户拷贝构造 → t2.t = 50
    int s;
    s = t2.get();             // 50
    Tagged t3(7);
    t3 = t1;                  // 用户 operator= → t3.t = 5+1 = 6
    int u;
    u = t3.get();             // 6
    return r + s + u;         // 105 + 50 + 6 = 161
}
