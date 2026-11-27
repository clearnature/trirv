// test28d_private_derived —— 0.0.28 拒绝契约：派生类访问基类 private（不可见）
class B2 {
    int hidden;                          // private
public:
    B2(int h) : hidden(h) {}
};
class D2 : public B2 {
public:
    D2(int h) : B2(h) {}
    int steal() { return hidden; }       // ← 基类 private 派生不可见：E0
};
int main() { return 0; }
