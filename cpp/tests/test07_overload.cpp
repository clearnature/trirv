// test07_overload —— 方法重载（同名异参）：Itanium mangling _ZN…E…
// add(int,int)→_ZN4Math3addEii / add(int,int,int)→_ZN4Math3addEiii
// 注1：局部声明不带调用初始化器（trirv_cc INITLIST 限制），拆两步
// 注2：退出值须 <256——VM exit 通道是 POSIX 8 位截断（vm/trirv_vm.py:515
//      a0 & 0xFF，C VM 同构；全值断言须读 regs[10]，如 syscall 套件）
class Math {
    int base;
public:
    Math(int b) : base(b) {}
    int add(int a, int b) { return a + b; }
    int add(int a, int b, int c) { return a + b + c; }
    int get() { return base; }
};

int main() {
    Math m(10);
    int r;
    r = m.add(1, 2);      // 3   → _ZN4Math3addEii
    int s;
    s = m.add(1, 2, 3);   // 6   → _ZN4Math3addEiii
    return r * 30 + s * 10 + m.get();   // 90 + 60 + 10 = 160
}
