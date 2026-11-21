/* ============================================================
   trirv_crt.c —— TriRV 的 crt0/__libc_start_main 层（C 子集实现）

   定位：musl 对接点的最小可用实现（设计文档步骤 7 的落地物）。
   链路：VM 构造零页进程信息 -> 编译器内建 crt0（_start）->
        __libc_start_main(main, argc, argv) -> main(argc, argv) -> exit(ret)

   与 musl 的对应关系：
     musl real:  crt1.s (_start) -> __libc_start_main(main,argc,argv,...)
                 -> __init_tls -> __init_ssp -> init_array -> main -> exit
     TriRV 本层: 省略 TLS/SSP（单线程无栈保护），init_array 由编译器
                 将来提供（暂无全局构造函数机制）；本层只做
                 参数搬移 + 调 main + 退出码回收。

   为什么 argc/argv 从零页读而不是从 sp 读：
     本 VM 栈向上生长（ADDI sp,sp,+framesize），RISC-V Linux 的
     "sp 指向 argc" 惯例会立刻被第一帧覆盖。零页固定地址是栈方向
     决定的必要偏差（见 trirv_vm.c vm_init_ex 注释）。

   对外 API：
     void exit(int code)          SYS_exit 的包装（供用户代码调用）
     int __libc_start_main(int(*main)(int,char**), int argc, char**argv)
                                  _start 调用；返回值不返回（直接 exit）

   注意：本文件不能单独通过 gate —— exit 是递归自调用（编译器没有
   内联汇编机制，exit 的系统调用由 syscall_no 名表直连）。实际使用中
   __libc_start_main 由 e2e 拼接进源码，exit 用法见其中注释。
   ============================================================ */

/* exit：直连系统调用（编译器 syscall_no 名表：exit -> 93）。
 * C 子集的"递归"写法在这里是安全的：syscall_no(exit) 命中 93，
 * N_CALL 走 ECALL 路径，根本不会生成 JAL。 */
void exit(int code) {
    exit(code);
}

/* __libc_start_main：musl 启动链的 TriRV 形态。
 * 与 musl 签名的差异（必要偏差）：musl 是
 *   __libc_start_main(main, argc, argv, init, fini, rtld, stack_end)
 * 本实现是
 *   __libc_start_main(argc, argv)
 * —— main 与本函数在同一编译单元（crt 与程序一起编译），直接 JAL main，
 * 不需要函数指针参数；init/fini（构造/析构函数表）等编译器暂无对应机制，
 * 全部省略。_start（编译器内建）已把 argc/argv 装进 a0/a1。 */
int __libc_start_main(int argc, char **argv) {
    int ret;
    ret = main(argc, argv);
    exit(ret);
    return 0;             /* 不可达：exit 不返回 */
}
