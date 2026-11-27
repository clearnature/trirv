// test35_eh —— 0.0.35 throw/catch 基础（方案 C 检查点展开落地）
// 先算降级形态（docs/EXCEPTION-DESIGN.md 决策）：
//   throw v → __throw_int(v);（全局 active/obj/ti=1）
//   try 体语句 → if (!__ex_active) { stmt }（语句级守卫）
//   try 体循环 → while (cond && !__ex_active)（条件增补）
//   catch(int e) → if (__ex_active) { if (__ex_ti == 1) { …e=__ex_obj… }
//                  else { return 0; } }（就地执行/未捕获上浮）
class Calc {
    int base;
public:
    Calc(int b) : base(b) {}
    int add(int x) {
        if (x < 0) { throw x * 10 + base; }   // 负数：抛 x*10+base
        return base + x;
    }
};
int main() {
    Calc c(100);
    int a;
    a = c.add(5);                // 无抛出 → 105
    try {
        int r;
        r = c.add(3);            // 无抛出 → 103（守卫跳过下句不影响）
        a = a + r;               // 208
    } catch (int e) {
        a = 999;                 // 不进
    }
    try {
        int r;
        r = c.add(-2);           // throw (-20+100)=80 → 上浮
        a = a + r;               // 守卫跳过
    } catch (int e) {
        a = a + e;               // 208 + 80 = 288
    }
    try {
        int r;
        r = c.add(1);            // 无抛出 → 101
        a = a + r;               // 389
    } catch (int e) {
        a = 0;
    }
    return a - 300;              // 389 - 300 = 89（8 位 exit 通道自检：
}                                //   a 本身 389 会截断为 133——强制自检项第 4 次生效）
