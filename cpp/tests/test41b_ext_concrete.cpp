// test41b_ext_concrete —— 0.0.41 具体实参外联定义 → 拒绝（子集边界）
// 外联形态限模板参数恒等映射（Box2<T>）；Box2<int> 具体外联=ParseError
template<typename T>
class Box2 {
    T v;
public:
    T get();
};
template<typename T>
int Box2<int>::get() { return v; }   // 具体实参外联 → ParseError
int main() {
    return 0;
}
