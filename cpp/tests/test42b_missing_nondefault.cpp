// test42b_missing_nondefault —— 0.0.42 非默认位实参缺失 → E0
// 多类型参数无默认；B4<int> 只给 1 实参、缺省位 U 无默认 → E0
template<typename T, typename U>
class B4 {
    T a;
    U b;
public:
    B4(T x, U y) : a(x), b(y) {}
};
int main() {
    B4<int> b(1, 2);   // 缺省位 U 无默认 → E0 实参数不足
    return 0;
}
