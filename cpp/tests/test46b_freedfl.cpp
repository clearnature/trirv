// test46b —— 0.0.x 审计 F2：自由函数默认实参 → ParseError
// （条件表达式 raise 陷阱曾致 TypeError crash——0.0.11 起未工作）
int f(int a, int b = 1) { return a + b; }
int main() {
    return f(5);
}
