// test42_ctemplate2 —— 0.0.42 类模板多参数/默认模板参数/全特化（Phase D 收官）
// 先算降级形态（本测试即验收规格）：
//   ① 多参数类模板 Pair<K,V>：0.0.41 机制泛化已覆盖（探针实证
//     Pair<int,char>→Pair__int__char 一次通过）——本测试为回归覆盖
//   ② 默认模板参数：template<typename T, typename U = char> class
//     Box2 → Box2<int> 缺省 U 补 char → Box2__int__char（尾部缺省位
//     须全有默认；非默认位缺失=E0）
//   ③ 全特化：template<> class Desc<int> {...} 覆盖主模板实例化
//     （Desc<int>→特化体 100；Desc<char>→主模板 1；特化须在主模板
//     之后；特化体内联方法/ctor；函数模板特化=子集边界文档化）
template<typename K, typename V>
class Pair {
    K first;
    V second;
public:
    Pair(K a, V b) : first(a), second(b) {}
    K get1() { return first; }
    V get2() { return second; }
};
template<typename T, typename U = char>
class Box2 {
    T a;
    U b;
public:
    Box2(T x, U y) : a(x), b(y) {}
    int sum() { return a + b; }
};
template<typename T>
class Desc {
public:
    Desc() {}
    int tag() { return 1; }
};
template<>
class Desc<int> {
public:
    Desc() {}
    int tag() { return 100; }
};
int main() {
    Pair<int, char> p(3, 65);      // Pair__int__char
    int r = p.get1() + p.get2();   // 3 + 65 = 68
    Box2<int> bx(1, 66);           // U 缺省补 char → Box2__int__char
    int s = bx.sum();              // 1 + 66 = 67
    Desc<int> d;                   // 特化体 → 100
    Desc<char> dc;                 // 主模板 → 1
    int t = d.tag() + dc.tag();    // 101
    return r + s - 100 + t;        // 68 + 67 - 100 + 101 = 136
}
