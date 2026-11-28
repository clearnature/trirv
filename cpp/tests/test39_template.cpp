// test39_template —— 0.0.39 函数模板：按需实例化/单态化
// 先算降级形态（本测试即验收规格）：
//   template<typename T> T tmax(T a, T b) {...} → 模板体不发射
//   tmax(3, 5) 首次 int 调用 → 实例化 int tmax__int(int a, int b)（T=int
//     深拷贝替换）+ 调用点解析到实例
//   tmax(c1, c2) char 实参 → 第二实例 char tmax__char(...)（每类型一实例）
//   同类型二次调用 → 复用既有实例（单态化缓存）
//   实参推导：首实参类型决定 T；其余实参同 T 检查（不一致=E0）
//   显式模板实参 tmax<int>(...) = 0.0.40（<> 解析）
template<typename T>
T tmax(T a, T b) {
    if (a > b) { return a; }
    return b;
}
int main() {
    int r;
    r = tmax(3, 5);                    // T=int 实例 → 5
    char c1 = 65;
    char c2 = 66;
    char cr;
    cr = tmax(c1, c2);                 // T=char 实例 → 66
    int s;
    s = tmax(10, 2);                   // T=int 复用 → 10
    return r + cr + s;                 // 5 + 66 + 10 = 81
}
