// test31b_static_int_ptr —— 0.0.31 拒绝契约：static_cast int→指针（须 reinterpret）
class B31 {
    int v;
public:
    B31(int x) : v(x) {}
};
int main() {
    int a = 5;
    B31* p = static_cast<B31*>(a);   // ← int→指针：E0
    return 0;
}
