# trirv_cc.c —— 寄存器式 C 编译器（C 语言实现）

## 目标与动机

`trirv_cc.py`（原版）是**栈式求值**编译器：每个表达式都要 `SW` 压栈、`LW` 弹栈。
实测 `while(i<N){s=s+i;i=i+1;}` 循环体 **70 条指令，其中 58 条（83%）是纯栈搬运**，
只有 6 条在做真正的计算。这是端到端 17× 指令膨胀的根源。

`trirv_cc.c` 用 C 重写编译器，核心改进：**局部变量映射到物理寄存器，表达式在寄存器间传递**。

## 核心设计

| 机制 | trirv_cc.py（栈式） | trirv_cc.c（寄存器式） |
|------|--------------------|-----------------------|
| 局部变量 | 栈帧槽 `LW/SW off(s0)` | callee-saved 寄存器 s1,s2-s6（6 个） |
| 表达式求值 | 压栈/弹栈 | `gen_expr` 返回寄存器号，`OP rd,ra,rb` |
| 二元运算 | `LW t0;LW t1;ADD;SW` | `ADDI t1,t0,0; ADD t0,t1,t0` |
| 纯寄存器函数 | 仍有帧 | 零 prologue/epilogue |
| 常量装载 | LUI+ADDI 分段 | 相同（40-trit 平衡分段） |
| 窥孔 copy-prop | 无 | 原型存在但默认关闭（正确性未达交付标准） |

### ABI 兼容（与现有 VM / printf 对齐）
- 参数 a0-a7，返回 a0，ra=x1，sp=x2，fp=s0=x8
- syscall：a7=编号，ECALL（write/read/exit/... 全套）
- 调用其他函数时：prologue 保存 ra + 用到的 s 寄存器到栈帧，epilogue 恢复
- 表达式跨函数调用的临时值：保存到帧内专用槽（XCALL_SLOT），避免 caller-saved 破坏
- 递归、嵌套调用正确（fib 验证通过）

## 实测收益（Apple M2，同一程序两编译器对比）

| 负载 | trirv_cc.py 指令数 | trirv_cc.c 指令数 | 缩减 | MIPS(py) | MIPS(c) | 吞吐加速 |
|------|---:|---:|---:|---:|---:|---:|
| sum 累加 | 107 | 42 | **2.55×** | 135 | 306 | **2.27×** |
| mul 乘 | 107 | 42 | **2.55×** | 117 | 130 | 1.11× |

乘加加速较小，因为 MUL 的瓶颈在 128 位软件算术本身（调度开销占比低），与瓶颈重定位结论一致。

## 正确性验证（10/10 与 Python 编译器 exit code 完全一致）

sum / forloop / ifelse / arith / rel / func（普通调用）/
nested（嵌套循环）/ recursive（阶乘递归）/ fib（双递归）/ manylocals（7 局部变量）/
字符串 + write syscall 输出正确。

## 已知限制（诚实记录）

1. **数组元素缩放**：`a[i]` 索引未按元素大小（int=9 tryte）缩放，仅支持 char 数组或标量索引；`int a[100]` 声明可解析但索引访存不正确。数组密集基准（mem）暂不能用本编译器。
2. **窥孔 copy-propagation**：`peephole()` 原型正确性未达标，默认关闭（`TRV_PEEP=1` 才启用）。寄存器式生成本身已消除绝大部分栈搬运，是主要收益来源。
3. **struct / vararg / va_list**：未实现（Python 版支持）。printf 的复杂变参调用走 Python 编译器。
4. **long / unsigned long**：未支持，用 int 替代。

## 用法

```bash
clang -O2 -o trirv_cc trirv_cc.c
./trirv_cc <src.c> <out.trv>          # 编译
./trirv_vm -n 1 -t 1 <out.trv>        # 运行（exit code 在 stderr）
TRV_DUMP_ASM=1 ./trirv_cc ...         # 导出汇编文本调试
```

## 定位

本编译器验证了「指令膨胀 17× 主要是编译器水平问题」的判断：
**同样的 ISA、同样的 VM，仅改进代码生成方式就把指令数砍掉 2.55×、吞吐提升 2.27×**。
这说明 1230× 端到端差距中，指令膨胀那一块是可以通过编译器工程显著压缩的，
不需要动 ISA 或硬件。剩余限制（数组/struct/vararg）是工作量问题，不是架构障碍。
