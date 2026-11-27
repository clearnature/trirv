// test36c_terminate —— 0.0.36 拒绝转行为：main 无 try 未捕获 = terminate 134
int thrower(int kind) {
    if (kind == 1) { throw 10; }
    return 0;
}
int forward2(int k) {
    thrower(k);
    return 0;
}
int main() {
    forward2(1);            // 无 try → 上浮穿越 → main 检查点 → terminate 134
    return 0;               // 不可达
}
