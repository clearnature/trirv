// test30b_explicit —— 0.0.30 拒绝契约：explicit ctor 禁隐式转换（C++ 法）
class Strict {
    int c;
public:
    explicit Strict(int x) : c(x) {}
};
int main() {
    Strict s2 = 8;          // ← explicit ctor 隐式转换：E0
    return 0;
}
