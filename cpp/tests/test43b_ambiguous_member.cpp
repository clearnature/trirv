// test43b_ambiguous_member —— 0.0.43 多基类同名字面 → E0 二义（子集拒绝）
// 显式默认 ctor（方言无隐式默认构造——诚实 E0 兜底）
class M1 {
public:
    int x;
    M1() : x(1) {}
};
class M2 {
public:
    int x;
    M2() : x(2) {}
};
class D : public M1, public M2 {
public:
    D() {}
};
int main() {
    D d;
    return d.x;    // x 在 M1/M2 两分支 → E0 成员二义
}
