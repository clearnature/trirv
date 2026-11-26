// test14_externc —— 0.0.14 extern "C"：裸名链接面（不 mangle）
// 块形态 + 单声明形态；无体声明=原型直出（未调用不依赖链接）
extern "C" {
    int tri_add(int a, int b) { return a + b; }
    int tri_mul(int a, int b) { return a * b; }
}
extern "C" int tri_max(int a, int b);        // 原型直出（跨语言符号声明）
extern "C" int tri_max2(int a, int b) {
    if (a > b) { return a; }
    return b;
}
int main() {
    int r;
    r = tri_add(1, 2);             // 3
    int s;
    s = tri_mul(3, 4);             // 12
    int t;
    t = tri_max2(10, 7);           // 10
    return r + s + t;              // 25
}
