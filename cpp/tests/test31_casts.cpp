// test31_casts —— 0.0.31 命名 cast（Phase A 收官）
// 先算降级形态（本测试即验收规格）：
//   static_cast/reinterpret_cast/const_cast → C cast 直发 (T)(expr)
//   sema 边界：static=数值↔数值+ptr↔ptr（上行 upcast 零调整）；
//     reinterpret=ptr↔num/ptr↔ptr（数值↔数值 E0 指向 static）；
//     const=仅 const 性变更；dynamic_cast=E0（0.0.33 RTTI 带）
extern "C" void* malloc(int sz);
extern "C" void free(void* p);
class Box31 {
    int v;
public:
    Box31(int x) : v(x) {}
    int get() { return v; }
};
class Base31 {
    int b;
public:
    Base31(int v) : b(v) {}
    int getb() { return b; }
};
class Der31 : public Base31 {
public:
    Der31(int v) : Base31(v) {}
};
int main() {
    int r;
    r = static_cast<int>(3) + 1;                // 数值 static → 4
    int src = 66;
    char c2 = static_cast<char>(src);           // int→char ✓
    int a;
    a = c2;                                     // 66
    Box31* b = new Box31(9);
    void* raw = static_cast<void*>(b);          // T*→void* ✓
    Box31* b2 = static_cast<Box31*>(raw);       // void*→T* ✓
    int c;
    c = b2->get();                              // 9
    Base31* up = static_cast<Base31*>(b2);      // 上行 upcast（零调整）✓
    int d;
    d = up->getb();                             // 9
    int addr = reinterpret_cast<int>(b2);       // ptr→int ✓
    Box31* b3 = reinterpret_cast<Box31*>(addr); // int→ptr ✓
    int e;
    e = b3->get();                              // 9
    int val = 80;
    const int* cp = const_cast<const int*>(&val);   // 加 const ✓
    int f;
    f = *cp;                                    // 80
    return r + a + c + d + e + f;               // 4+66+9+9+9+80 = 177
}
