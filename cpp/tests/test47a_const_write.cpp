// test47a —— 0.1.1 G1：const 局部对象写 → K3
// 拍板第 2 条「const 交 C 编译器」承诺失效实证（trirv_cc 接受
// const int x=1; x=2;）——检查由前端承担
int main() {
    const int x = 1;
    x = 2;
    return x;
}
