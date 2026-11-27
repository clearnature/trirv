// test29b_mismatch —— 0.0.29 拒绝契约：拷贝初始化类型不匹配（C++ 法）
class A2 {
    int x;
public:
    A2(int v) : x(v) {}
};
class B2 {
    int y;
public:
    B2(int v) : y(v) {}
};
int main() {
    A2 a(1);
    B2 b = a;               // ← A → B 拷贝初始化：E0
    return 0;
}
