// test31c_dynamic —— 0.0.31 拒绝契约：dynamic_cast（RTTI=0.0.33 带）
class B31 {
    int v;
public:
    B31(int x) : v(x) {}
};
int main() {
    B31* p = 0;
    B31* q = dynamic_cast<B31*>(p);   // ← RTTI 未落地：E0
    return 0;
}
