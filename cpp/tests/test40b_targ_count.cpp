// test40b_targ_count —— 0.0.40 显式实参数 ≠ 类型参数数 → E0（无部分显式）
template<typename T>
T tmax(T a, T b) {
    if (a > b) { return a; }
    return b;
}
int main() {
    int r;
    r = tmax<int, char>(3, 5);   // 显式实参数 2 ≠ 类型参数数 1 → E0
    return r;
}
