// test23_ops_ptr —— 0.0.23 指针/算子面收尾
// ① operator-（mi）/ operator<（lt）算子表扩展 ② 一元 * 解引用读+写穿
class Acc {
    int v;
public:
    Acc(int x) : v(x) {}
    int operator-(const Acc& r) { return v - r.v; }   // _ZN3AccmiERK3Acc
    bool operator<(const Acc& r) { return v < r.v; }  // _ZN3ACcltERK3Acc → lt
    int get() { return v; }
};
class Box2 {
    int val;
public:
    Box2(int x) : val(x) {}
    int get() { return val; }
};
int main() {
    Acc a(10);
    Acc b(3);
    int r;
    r = a - b;                 // 7
    int s;
    s = (a < b);               // 0
    Box2* p = new Box2(30);
    Box2 tmp(55);
    *p = tmp;                  // 解引用写（对象左值→结构体赋值，p18c 已证）
    int t;
    t = p->get();              // 55
    int u;
    u = t + 1;                 // 56
    delete p;
    return r + s + t + u;      // 7 + 0 + 55 + 56 = 118
}
