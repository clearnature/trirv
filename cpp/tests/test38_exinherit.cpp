// test38_exinherit —— 0.0.38 异常×继承（Phase C 收官）
// 先算降级形态（本测试即验收规格）：
//   throw obj（类左值）→ __throw_obj(&obj, sizeof, &X_ti)——拷入 __ex_buf
//     全局缓冲（检查点展开=thrower 帧即返→须拷贝）
//   __ex_ti 语义扩展：基本抛=1/2；类抛=(int)&X_ti（0.0.32 _ti 全局地址）
//   catch(ExBase& e) 匹配派生 throw → __cm_N(__ex_ti)——可接受集=
//     target ∪ 编译期枚举 descendants（0.0.33 __dc_N 同模式）
//   catch 形参 → struct X *e = (struct X*)__ex_obj（引用→指针，共享槽零调整）
//   基本型 ti（1/2）与类 ti（地址）不相交
//   catch 按值 X e / throw 类右值 → E0 子集（文档化）
class ExBase {
    int code;
public:
    ExBase(int c) : code(c) {}
    int code_get() { return code; }
};
class ExDerived : public ExBase {
    int extra;
public:
    ExDerived(int c, int x) : ExBase(c), extra(x) {}
    int extra_get() { return extra; }
};
int thrower(int kind) {
    ExDerived d(7, 3);
    if (kind == 1) { throw d; }          // 派生对象 → 基类 catch 匹配
    return 0;
}
int main() {
    int total = 0;
    try { thrower(1); } catch (ExBase& e) { total = total + e.code_get(); }        // +7（基类引用捕获派生）
    try { thrower(1); } catch (ExDerived& e) { total = total + e.extra_get(); }    // +3（精确捕获）
    try { thrower(1); } catch (int e) { total = total + 100; } catch (...) { total = total + 1; }   // 类抛≠int → +1
    return total;                                // 7 + 3 + 1 = 11
}
