// test33_dyncast —— 0.0.33 dynamic_cast（Phase B 收官）
// 先算降级形态（本测试即验收规格）：
//   上行 dynamic_cast<Animal*>(d) → (struct Animal *)(src) 直发恒真
//     （单继承共享槽零调整）
//   下行 dynamic_cast<Dog*>(a) → ((struct Dog *)__dc_N(src, dyn_ti))
//     helper：if (obj==0) return 0; if (dyn==&X_ti) return obj; …
//     可接受集 = target ∪ 编译期枚举 descendants（运行期 ti 同一性）
//   下行失败 → 0；零对象 → 0（helper 首行守卫）
//   无关类型 → 编译期 E0（sema 静态可判，test33b 拒绝契约）
class Animal {
    int a;
public:
    Animal(int x) : a(x) {}
    virtual int legs() { return 0; }
};
class Dog : public Animal {
    int t;
public:
    Dog(int x) : Animal(x), t(x * 2) {}
    int legs() { return 4; }
    int tag() { return t; }
};
class Cat : public Animal {
public:
    Cat(int x) : Animal(x) {}
    int legs() { return 4; }
};
int main() {
    Dog* d = new Dog(3);
    Animal* a = d;                          // 上行（共享槽）
    Dog* back = dynamic_cast<Dog*>(a);      // 下行成功（动态=Dog）
    int r1;
    r1 = back->tag();                       // 6
    Cat* wrong = dynamic_cast<Cat*>(a);     // 动态=Dog ∉ Cat 族 → 0
    int r2;
    r2 = wrong == 0;                        // 1
    Animal* np = 0;
    Animal* ok = dynamic_cast<Animal*>(np); // 零对象 → 0
    int r3;
    r3 = ok == 0;                           // 1
    Animal* up2 = dynamic_cast<Animal*>(d); // 上行恒真直达
    int r4;
    r4 = up2->legs();                       // 4
    delete d;
    return r1 + r2 * 2 + r3 * 4 + r4 * 8;   // 6+2+4+32 = 44
}
