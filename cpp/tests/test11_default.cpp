// test11_default —— 默认参数：调用点补全 + 全默认 ctor 兼任无参构造
// 语义：默认表达式在调用者语境求值（降级=调用点文本补全）
// mangling 不含默认值（Itanium 口径）；歧义/常量限制=sema 拒绝
class Math {
    int bias;
public:
    Math(int b = 7) : bias(b) {}                  // 全默认 ctor
    int add(int a, int b = 10) { return a + b; }  // 单默认
    int add3(int a, int b = 5, int c = 2) { return a + b + c; }
};

int main() {
    Math m;                 // 全默认 ctor → bias=7
    int r;
    r = m.add(1);           // b=10 → 11
    int s;
    s = m.add(1, 2);        // 显式 → 3
    int t;
    t = m.add3(1);          // 5+2 → 8
    int u;
    u = m.add3(1, 4);       // c=2 → 7
    return r + s * 2 + t * 3 + u * 4;   // 11 + 6 + 24 + 28 = 69
}
