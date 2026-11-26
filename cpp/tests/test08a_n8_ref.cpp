// test08a_n8_ref —— N8 拒绝：非 const 引用形参绑字面量（右值）
// 期望：transpiler 退出码 !=0，stderr 含 N8（要么对、要么报错）
class Box {
    int val;
public:
    Box(int v) : val(v) {}
    void add1(int& r) { r = r + 1; }
};

int main() {
    Box b(10);
    b.add1(42);      // ← 42 是右值：真实语义需物化具名临时量，拒绝
    return 0;
}
