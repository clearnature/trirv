// test35b_rethrow —— 0.0.35 拒绝契约：bare rethrow（=0.0.36）
class C2 {
public:
    void run() {
        throw;              // ← bare rethrow：E0（0.0.36）
    }
};
int main() { return 0; }
