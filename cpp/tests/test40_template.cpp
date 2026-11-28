// test40_template —— 0.0.40 显式模板实参 + 多类型参数
// 先算降级形态（本测试即验收规格）：
//   template<typename T, typename U> int tsum(T a, U b) {...}
//     → 多类型参数模板；tsum(3, c)（c=100:char）按参数位推导 T=int
//       U=char → int tsum__int__char(int a, char b) → 103
//   tmax<int>(3, 5) 显式实参 → tmax__int(3, 5)（显式覆盖推导）→ 5
//   tmax<char>(c1, c2)（65/66）→ char tmax__char → 66
//   <> 歧义消解（探针实证：tmax<int>(3,5) 曾报 意外的 'int'——Bin
//     路径误吃 <）：primary ID 后 <类型序列>( 形态=模板实参
//     （static_cast<int>(x) 同形态先例）；否则回退比较表达式
//   显式实参数 ≠ 类型参数数 = E0（无部分显式/空 <>）
//   推导：param i 类型 Tj ← arg i（去 ref）；同 Tj 多参数不一致=E0
//     （「实参类型不一致」保留）；无法推导=E0
//   单态化名 {name}__{t1}__{t2}…（类名 :: → _ 平化）
template<typename T>
T tmax(T a, T b) {
    if (a > b) { return a; }
    return b;
}
template<typename T, typename U>
int tsum(T a, U b) {
    return a + b;
}
int main() {
    int r;
    r = tmax<int>(3, 5);               // 显式 T=int → 5
    char c1 = 65;
    char c2 = 66;
    int t;
    t = tmax<char>(c1, c2);            // 显式 T=char → 66
    char c = 100;
    int s;
    s = tsum(3, c);                    // T=int U=char → 103
    return r + s - 100 + t;            // 5 + 103 - 100 + 66 = 74
}
