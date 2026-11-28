// test39b_deduct_mismatch —— 0.0.39 模板实参类型不一致 → E0（test39 注释规格）
// 字符以数值书写（方言惯例：无字符字面量形态）
template<typename T>
T tmax(T a, T b) {
    if (a > b) { return a; }
    return b;
}
int main() {
    char c = 97;
    int r;
    r = tmax(3, c);      // int vs char → E0 实参类型不一致（多类型参数=0.0.40）
    return r;
}
