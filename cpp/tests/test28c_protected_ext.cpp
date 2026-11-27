// test28c_protected_ext —— 0.0.28 拒绝契约：protected 成员外部访问
class Base {
protected:
    int prot;
public:
    Base(int p) : prot(p) {}
};
int main() {
    Base b(1);
    return b.prot;                       // ← 类外访问受保护成员：E0
}
