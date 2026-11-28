// test46a —— 0.0.x 审计 F1：类继承自身 → E0（曾致 _root_of 死循环挂死）
class A : public A {
public:
};
int main() {
    return 0;
}
