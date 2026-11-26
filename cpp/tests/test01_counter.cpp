// test01_counter —— 最简 C++ class：构造/方法/this
// 降级策略：class → struct + 前缀函数 + this→指针参数
class Counter {
    int val;
public:
    Counter() : val(0) {}
    void increment() { val = val + 1; }
    int get() { return val; }
};

int main() {
    Counter c;
    c.increment();
    c.increment();
    return c.get();  // 应返回 2
}
