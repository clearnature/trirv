// test04_ref —— 引用返回（int& → int*，f()=v 经指针落盘；引用形参 v0.0.4）
class Box {
    int val;
public:
    Box(int v) : val(v) {}
    int& get_ref() { return val; }
    int get() { return val; }
};

int main() {
    Box b(42);
    b.get_ref() = 99;
    return b.get();  // 99
}
