// test46c —— 0.0.x 审计 F3：非模板函数显式模板实参 → E0
// （f<int>(1) 曾静默忽略 targs 按普通调用编译——子集泄漏）
int f(int x) { return x; }
int main() {
    return f<int>(1);
}
