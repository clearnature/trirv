// test18_struct_assign —— 0.0.18 调用结果对象绑定（E0 解除）
// 0.0.17 的「结构体赋值缺失」是探针误判（250=506 mod 256，POSIX 8 位
// exit 通道——v0.0.7 已入册的 ABI，排查时遗忘）；p18c-e 复验全绿。
// ① 算子结果直接进自由函数 ② 嵌套：临时量再进 operator+（接收者物化）
class Vec {
    int x;
    int y;
public:
    Vec(int px, int py) : x(px), y(py) {}
    int getx() { return x; }
    int gety() { return y; }
    Vec operator+(const Vec& r) {
        return Vec(x + r.x, y + r.y);
    }
};
int area(const Vec& v) { return v.getx() * v.gety(); }
int main() {
    Vec a(3, 4);
    Vec b(5, 6);
    int r;
    r = area(a + b);           // (8,10) → 80
    Vec c(1, 1);
    int s;
    s = area((c + b) + c);     // (c+b)=(6,7) → +(1,1)=(7,8) → 56
    return r + s;              // 80 + 56 = 136
}
