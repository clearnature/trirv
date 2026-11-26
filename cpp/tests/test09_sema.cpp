// test09_sema —— sema 管线验收：REF→REF 转发 / VALUE 语境解引用 /
// const 方法跨类 cast 一致 / 隐式 this 调用 / 全 Itanium（_ZNK…）
class Base {
    int bval;
public:
    Base(int v) : bval(v) {}
    int get_b() const { return bval; }          // _ZNK4Base5get_bEv
};

class Box : public Base {
    int val;
public:
    Box(int v) : Base(v), val(v) {}
    int& ref() { return val; }                  // _ZN3Box3refEv
    void add1(int& r) { r = r + 1; }            // _ZN3Box4add1ERi
    int peek() const {                          // _ZNK3Box4peekEv
        return val + get_b();                   // 跨类调 const 方法：
    }                                           // (const struct Base *)this_
};

int main() {
    Box b(10);
    b.add1(b.ref());     // REF→REF 转发：r 直绑 val（无 &），val=11
    b.ref() = 30;        // 写穿引用（VALUE 语境自动解引用），val=30
    return b.peek() + b.ref();   // (30+10) + *(…)=30 → 70
}
