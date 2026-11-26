// test21_fwd —— 0.0.21 前向声明 + 指针成员/形参解锁
// class B;（不完整类型）→ B* 成员/形参合法（C++ 法：指针即不完整 OK）
class B;
class A {
    B* peer;
public:
    A() : peer(0) {}
    void link(B* q) { peer = q; }      // 指针形参（0.0.15 守卫解除）
    int has() { return peer != 0; }    // 指针判零
    int val() { return peer->get(); }  // 指针穿透调用
};
class B {
    int v;
public:
    B(int x) : v(x) {}
    int get() { return v; }
};
int main() {
    A a;
    B b(7);
    int has0;
    has0 = a.has();            // 0（未链接）
    a.link(&b);                // &b 实参（指针形参）
    int has1;
    has1 = a.has();            // 1
    int v;
    v = a.val();               // peer->get() = 7
    return has0 + has1 * 10 + v;   // 0 + 10 + 7 = 17
}
