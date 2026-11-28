// test48 —— 0.1.1b 除零 trap（拍板落地）：exit 136 = POSIX SIGFPE 惯例
// （128+8，8 位 exit 通道内）——双编译器×双 VM 四路恒等定义故障
// 先算：7/0 运行时 DIV（双编译器均不常量折叠实证）→ VM trap 136
int main() {
    int z = 0;
    return 7 / z;      // → trap 136（非静默 0）
}
