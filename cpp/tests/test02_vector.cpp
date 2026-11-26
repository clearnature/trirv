// test02_vector —— 多参数方法 + 构造参数 + 成员互操作
class Vector {
    int x;
    int y;
public:
    Vector(int px, int py) : x(px), y(py) {}
    int dot(Vector other) { return x * other.x + y * other.y; }
    int get_x() { return x; }
    int get_y() { return y; }
};

int main() {
    Vector a(3, 4);
    Vector b(1, 2);
    return a.dot(b);  // 3*1 + 4*2 = 11
}
