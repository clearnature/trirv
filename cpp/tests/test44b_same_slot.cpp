// test44b_same_slot —— 0.0.44 多基类同名虚槽 → E0（二义分派子集拒绝）
class W1 {
public:
    W1() {}
    virtual int f() { return 1; }
};
class W2 {
public:
    W2() {}
    virtual int f() { return 2; }
};
class DD : public W1, public W2 {
public:
    DD() {}
};
int main() {
    DD obj;
    return 0;      // vslots 并集时同名虚槽 f → E0
}
