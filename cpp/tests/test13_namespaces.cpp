// test13_namespaces —— 0.0.13 命名空间（前缀扁平化 + 全 Itanium 嵌套名）
// 函数名：_ZN3geo4areaEii（N…E 嵌套组件）；C 结构体名：geo_Box（扁平）
// ns 内裸名解析（twice 调 area 不限定）；外部必须限定（geo::area）
// 同名类异 ns 不冲突（geo::Box / other::Box → geo_Box / other_Box）
namespace geo {
    int area(int w, int h) { return w * h; }
    class Box {
        int val;
    public:
        Box(int v) : val(v) {}
        int get() { return val; }
        int twice() { return area(val, val); }   // ns 内裸调
    };
}
namespace other {
    class Box {
        int v;
    public:
        Box(int x) : v(x) {}
        int get() { return v; }
    };
}
int main() {
    int r;
    r = geo::area(3, 4);           // 12
    geo::Box b(5);
    int s;
    s = b.twice();                 // area(5,5) = 25
    other::Box c(7);
    int t;
    t = c.get();                   // 7
    return r + s + t;              // 12 + 25 + 7 = 44
}
