// test47e —— 0.1.1 G2 边界确认：8 int 成员=72 tryte 恰好通过
// 先算：throw 8-int 对象 → __ex_buf 8 槽恰满 → catch 读回 → exit
//   a1..a8 = 8,7,6,5,4,3,2,1 → 校验和 36 <256 ✓
class Eight {
public:
    int a; int b; int c; int d;
    int e; int f; int g; int h;
    Eight() {}
};
int main() {
    try {
        Eight o;
        o.a = 8; o.b = 7; o.c = 6; o.d = 5;
        o.e = 4; o.f = 3; o.g = 2; o.h = 1;
        throw o;
    } catch (Eight& e) {
        return (e.a + e.b + e.c + e.d + e.e + e.f + e.g + e.h) - 0;
    }
    return 0;
}
