// test17_ctor_expr —— 0.0.17 显式构造表达式 + 对象临时量物化
// ① Vec(x,y) 表达式 → 栈临时 + ctor + 取址（无结构体赋值依赖）
// ② operator+ 对象返回：return Vec(...) 物化后按值返回
// ③ 边界保留：调用结果对象绑定 E0（trirv_cc 结构体赋值缺失实证 250≠511）
class Vec {
    int x;
    int y;
public:
    Vec(int px, int py) : x(px), y(py) {}
    int getx() { return x; }
    int gety() { return y; }
    Vec operator+(const Vec& r) {
        return Vec(x + r.x, y + r.y);   // 显式构造表达式（对象返回）
    }
};
int area(const Vec& v) { return v.getx() * v.gety(); }
int main() {
    int r;
    r = area(Vec(6, 7));       // ① 对象临时量物化（0.0.16 曾 E0）→ 42
    int t;
    t = area(Vec(1, 2));       // 2
    Vec a(1, 1);
    Vec b(2, 2);
    a + b;                     // ③ operator 对象返回调用（值弃，编译路径）
    return r + t;              // 42 + 2 = 44
}
