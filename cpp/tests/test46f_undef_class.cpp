// test46f —— 回审 F7：自由函数签名未定义类 → E0
// （裸 T 曾静默注册、体内算子解析 KeyError crash——诚实于声明处）
T tmax(T a, T b) {
    if (a > b) { return a; }
    return b;
}
int main() {
    return 0;
}
