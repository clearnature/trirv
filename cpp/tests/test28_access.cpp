// test28_access —— 0.0.28 访问控制强制（合法面）
// 先算可见性矩阵（验收规格）：private 仅类自身；protected 类自身+派生链；
// public 任意。class 默认 private / struct 默认 public。
// 访问控制不入 C（纯 sema 期拦截——C 侧无此概念）。
class Widget {
    int secret;                          // private（class 默认）
public:
    Widget(int s) : secret(s) {}
    int read() { return secret; }        // 类自身访问 private ✓
};
class Gadget : public Widget {
    int gear;                            // private
protected:
    int prot;                            // protected
public:
    Gadget(int s, int g) : Widget(s), gear(g), prot(9) {}
    int via_base() { return read(); }    // 基类 public 方法 ✓
    int own() { return gear + prot; }    // 自身 private + protected ✓
};
class SubGadget : public Gadget {
public:
    SubGadget(int s, int g) : Gadget(s, g) {}
    int deep() { return prot; }          // 跨层 protected（Gadget 的）✓
};
int main() {
    Widget w(3);
    int a;
    a = w.read();                        // 3
    Gadget g(4, 5);
    int b;
    b = g.via_base() + g.own();          // 4 + (5+9) = 18
    SubGadget sg(6, 7);
    int c;
    c = sg.deep() + sg.via_base();       // 9 + 6 = 15
    return a + b + c;                    // 3 + 18 + 15 = 36
}
