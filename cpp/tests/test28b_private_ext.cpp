// test28b_private_ext —— 0.0.28 拒绝契约：private 成员外部访问（C++ 法）
class Vault {
    int gold;                            // private
public:
    Vault(int g) : gold(g) {}
};
int main() {
    Vault v(99);
    return v.gold;                       // ← 外部访问私有成员：E0
}
