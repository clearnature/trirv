// test05_refparam —— 引用形参：int& r → int *r，body 自动解引用，实参自动取址
class Box {
    int val;
public:
    Box(int v) : val(v) {}
    void bump(int& r) { r = r + val; }   // 引用写入落盘到调用方变量
    int get() { return val; }
};

int main() {
    Box b(10);
    int x = 5;
    b.bump(x);    // x = 5 + 10 = 15
    b.bump(x);    // x = 15 + 10 = 25
    return x;     // 25
}
