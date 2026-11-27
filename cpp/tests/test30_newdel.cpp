// test30_newdel —— 0.0.30 类专属 operator new/delete + explicit
// 先算降级形态（本测试即验收规格）：
//   static void* operator new(size_t) → void* Pool_new(int)（隐式静态约定名）
//   static void operator delete(void*) → void Pool_delete(void*)
//   new Pool(5) → Pool_new(sizeof(struct Pool)) + Pool_ctor（分配器接管 malloc）
//   delete p → Pool_dtor(p); Pool_delete(p);（dtor 先于回收 = C++ 序）
//   Temperature t = 30 → 隐式转换（非 explicit 单参 ctor）
//   explicit Strict(int) → 直接构造合法 / 隐式转换 E0（test30b）
// extern "C" void* malloc(int)/void free(void*) —— 指针形态放宽（0.0.14 原限制）
extern "C" void* malloc(int sz);
extern "C" void free(void* p);
class Pool {
    int v;
public:
    Pool(int x) : v(x) {}
    static void* operator new(size_t sz) { return malloc(sz); }
    static void operator delete(void* p) { free(p); }
    int get() { return v; }
};
class Temperature {
    int c;
public:
    Temperature(int x) : c(x) {}         // 非 explicit：允许隐式转换
    int get() { return c; }
};
class Strict {
    int c;
public:
    explicit Strict(int x) : c(x) {}     // explicit：禁隐式转换
    int get() { return c; }
};
int main() {
    Pool* p = new Pool(5);               // 类专属分配器
    int a;
    a = p->get();                        // 5
    delete p;                            // Pool_delete
    Temperature t = 30;                  // 隐式转换 ✓
    int b;
    b = t.get();                         // 30
    Strict s(7);                         // 直接构造 ✓（explicit 不禁直接）
    int c;
    c = s.get();                         // 7
    return a + b + c;                    // 5 + 30 + 7 = 42
}
