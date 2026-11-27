// test32b_operand —— 0.0.32 拒绝契约：typeid 基本型操作数（子集边界）
int main() {
    int x = 5;
    return typeid(x);   // ← typeid 操作数须为对象/类名：E0
}
