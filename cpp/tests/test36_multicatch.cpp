// test36_multicatch —— 0.0.36 多 catch / bare rethrow / catch(...) / 函数级上浮
// 先算降级形态（本测试即验收规格）：
//   多 catch → __ex_ti 链（int=1 / char=2）if/else-if
//   未匹配 else → fall-through（无 return）——外层 try catch 接续
//   catch arm 清位后置（body 完成后 __ex_active=0——bare rethrow/new throw 支持）
//   bare rethrow throw; → 调用者方向传播（active 保持；同帧外层=结构化嵌套
//     try + 全局累计——设计偏差文档化：无 goto 的同帧跳转不支持）
//   catch(...) → if(1) 化无条件匹配 arm（bare 块不可接 else——trirv_cc 解析）
//   函数级上浮 → 非 try 调用点后 if (__ex_active) { ret }（无 try 帧穿透）；
//     main 未捕获 = terminate 134（test36c）
int g_total = 0;
int thrower(int kind) {
    if (kind == 1) { throw 10; }         // int（ti=1）
    char c = 65;
    if (kind == 2) { throw c; }          // char（ti=2）
    return 0;                            // kind=9：无抛出
}
int forward2(int k) {
    thrower(k);                          // 无 try 帧：异常经本帧上浮（函数级通道）
    return 0;
}
int inner(int k) {
    try { thrower(k); } catch (int e) { g_total = g_total + 1; throw; }   // +1 → bare rethrow 穿帧
    return 0;
}
int main() {
    try { inner(1); } catch (...) { g_total = g_total + 2; }              // +2（重抛穿越 inner 帧）
    try { thrower(1); } catch (int e) { g_total = g_total + 10; }         // +10（int arm）
    try { thrower(2); } catch (int e) { g_total = g_total + 100; } catch (char c) { g_total = g_total + c; }   // +65（char arm）
    try { thrower(9); } catch (...) { g_total = g_total + 7; }            // 无抛出：+0
    try {
        try { thrower(2); } catch (int e) { g_total = g_total + 100; }    // char≠int → fall-through
        g_total = g_total + 99;                                           // 守卫跳过
    } catch (char c) { g_total = g_total + 65; }                          // +65（外层跨层接住）
    try { forward2(1); } catch (...) { g_total = g_total + 3; }           // +3（函数级上浮穿透）
    return g_total;                                // 2+10+65+0+1+65+3 = 146（thrower(9) 无抛出 +0）
}
