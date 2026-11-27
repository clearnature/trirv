// test36b_rethrow_scope —— 0.0.36 拒绝契约：bare rethrow 须在 catch 内
int main() {
    throw;              // ← catch 外 bare rethrow：E0
    return 0;
}
