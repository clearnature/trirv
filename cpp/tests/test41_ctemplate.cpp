// test41_ctemplate —— 0.0.41 类模板（单参数）+ 成员外联定义
// 先算降级形态（本测试即验收规格）：
//   template<typename T> class Box { T v; Box(T x); T get(); }（声明）
//   外联定义：
//     template<typename T> Box<T>::Box(T x) : v(x) {}
//     template<typename T> T Box<T>::get() { return v; }
//   Box<int> b(7) 首次引用 → 惰性实例化 class Box__int { int v; }
//     + Box__int ctor/get（deepcopy+T→int 替换+重 check）
//   Box<char> c(65) → class Box__char { char v; }（每类型一实例）
//   Box<int> b2 复用 → 无第二 Box__int
//   类模板实参语法：ID<类型>（声明位 parse_type 内 tspec 平化——
//     Box<int>→Box__int）；嵌套 Box<Box<int>> = 子集边界（>> 单 token
//     词法实证 + 平化名拆分歧义——文档化拒绝）
//   外联形态限定 Box<T>（tvars 恒等映射）→ 归模板定义；
//     Box<int> 具体实参外联定义=E0 子集
//   惰性实例化挂 resolve_class_name（平化名前缀匹配类模板名）；
//     基本型实参反解（int/char/bool）
template<typename T>
class Box {
    T v;
public:
    Box(T x);
    T get();
};
template<typename T>
Box<T>::Box(T x) : v(x) {}
template<typename T>
T Box<T>::get() { return v; }
int main() {
    Box<int> b(7);
    int r = b.get();            // 7
    Box<char> c(65);
    char cr = c.get();          // 65
    Box<int> b2(20);
    int s = b2.get();           // 20（Box__int 复用）
    return r + s + (cr == 65);  // 7 + 20 + 1 = 28
}
