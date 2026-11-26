// test20_static —— 0.0.20 static 成员：数据成员（类外定义）+ static 方法
// 全局名：Counter_count；mangle：_ZN7Counter5totalEv（static 与成员同编码）
// 双调用形态：Counter::total()（限定）/ a.total()（经实例，省 receiver）
class Counter {
    int inst;
public:
    static int count;
    Counter(int i) : inst(i) { count = count + 1; }
    static int total() { return count; }
    int get() { return inst; }
};
int Counter::count = 0;
int main() {
    Counter a(1);
    Counter b(2);
    int r;
    r = Counter::total();      // 2（两次构造各 +1）
    int s;
    s = a.get() + b.get();     // 3
    int t;
    t = a.total();             // 经实例调静态 → 2
    int u;
    u = Counter::count;        // 静态数据成员限定访问 → 2
    return r + s + t + u;      // 2 + 3 + 2 + 2 = 9
}
