// test47b —— 0.1.1 G2：throw 对象超 __ex_buf 容量 → E0
// 9 int 成员=81 tryte > 72（8 int 槽）——溢出实证：覆盖序幸存=静默破坏
class Big {
public:
    int a; int b; int c; int d; int e;
    int f; int g; int h; int i;
    Big() {}
};
int main() {
    try {
        Big b2;
        throw b2;
    } catch (Big& e) {
        return 1;
    }
    return 0;
}
