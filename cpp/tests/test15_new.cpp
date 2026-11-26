// test15_new —— 0.0.15 new/delete：new → malloc+ctor，delete → free
// （子集无 dtor 语法：delete 仅回收存储，ARCHITECTURE §四 注记）
class Box {
    int val;
public:
    Box(int v) : val(v) {}
    int get() { return val; }
    void set(int v) { val = v; }
};
class Widget {
    int a;
    int b;
public:
    Widget(int x, int y) : a(x), b(y) {}
    int sum() { return a + b; }
};
int main() {
    Box* p = new Box(30);          // malloc + ctor
    int r;
    r = p->get();                  // 指针穿透 ->get() = 30
    p->set(12);
    int s;
    s = p->get();                  // 12
    delete p;                      // 回收
    Widget* q = new Widget(3, 4);  // 双参 ctor
    int t;
    t = q->sum();                  // 7
    delete q;
    Box* u = new Box(8);           // 再分配（块复用验证）
    int w;
    w = u->get();                  // 8
    delete u;
    return r + s + t + w;          // 30 + 12 + 7 + 8 = 57
}
