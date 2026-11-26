// test08b_n8_constref —— N8 拒绝：const T& 绑临时量（cfront 路线最易低估的坑）
// 真实语义：调用者作用域物化临时对象 + 生命周期延长——降级到 C 必须
// 显式具名。0.0.8 明确拒绝（要么对、要么报错），0.0.9 生命周期扩展。
class Math {
    int base;
public:
    Math(int b) : base(b) {}
    int sum(const int& a, const int& b) { return a + b; }
};

int main() {
    Math m(10);
    return m.sum(3, 4);   // ← 字面量绑 const int&：拒绝（N8）
}
