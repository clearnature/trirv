# trirv-unified-memory-abi

# TriRV 统一内存与指针 ABI（已实现规格）

> 背景：软件运行在中国 UBIOS + 虚拟硬件层之上，该层对软件呈现原生三进制语义，与真实三进制硬件无区别。
> TriRV VM 是这层虚拟硬件的**规格参考实现**（黄金参考），编译器产出由其验证，语义等价于真实硬件。
> 本文记录 **2026-11-17 已实现并验证** 的数据模型、内存布局与指针 ABI。

---

## 1. 数据模型（已锁定）

| 类型 | 宽度 | 值域 | 存储指令 |
| --- | --- | --- | --- |
| `tryte` | 5 trit | 0\~242 | 内存最小编址单位 |
| `char` / `unsigned char` | 1 tryte | 0\~242 | `SB` 写 / `LBU` 读（零扩展） |
| `signed char` | 1 tryte | −121\~121 | `SB` 写 / `LB` 读（符号扩展） |
| `int` | 9 tryte (45 trit) | 3 补码 | `LW` / `SW` |
| 指针 (`int*` `char*` …) | 9 tryte | tryte 地址 | `LW` / `SW` |

- **地址单位 = 1 tryte**（不是 8-bit byte）。这是原生三进制的正确抽象，无 256 值域冲突。
- `char` = 5-trit tryte（你已确认）：glibc 按此 ABI 重编译，不做 8-bit 兼容。
- **`char` 无符号，`signed char` 有符号**：plain `char` 无符号与 RISC-V psABI 一致
  （GCC riscv 后端 `DEFAULT_SIGNED_CHAR = 0`），不是 TriRV 自己的偏离。
  底层对应 `LBU`/`LB` 两条不同指令——这也是 TriRV 的 `LB`/`LBU` 与 RISC-V 语义完全一致的原因。
- `SB` 写 1 tryte 时按 mod 243 截断，所以 `signed char c = -43` 实际存的是 200，
  `LB` 读回再解释为 −43；往返自洽。
- 45 trit = 9 tryte，int 与指针同宽。

## 2. 内存布局（统一单一地址空间）

| 区域 | 地址范围 | 用途 |
| --- | --- | --- |
| 保留 | 0 | 空指针/null |
| rodata | 100+ | 字符串常量（NUL 结尾） |
| 栈 | 4096+ ，向高地址增长 | 求值栈 + 函数栈帧 + 局部 char/int/指针 |
| **数据区上界** | **≤ 4095**（编译器静态强制，2026-11-22） | 全局 200.. + 字符串段接后；trirv_cc 链接期检查 STRBASE ≤ 4096，越界诚实拒绝（UNSUPPORTED: 数据区顶…越过栈基址）——防静态数据与栈帧静默互踩 |
| 堆 (brk) | 中段 (1\<\<19) | `brk` 系统调用，malloc 预留 |

- 所有数据（字符串、局部变量、指针目标、栈）在同一 tryte 地址空间，指针可指向任意区域。

## 3. 字存储格式（SW/LW）

一个 45-trit 字按 **5 trit 一组、小端** 拆成 9 个 tryte：

```
tryte[i] = (word // 3^(5i)) % 243     i = 0..8
word     = Σ tryte[i] * 3^(5i)          (mod 3^45)
```

每 tryte 存 0\~242（5 trit 上限），bytearray 容量 1\<\<20 tryte。

## 4. 栈帧布局（tryte 偏移）

```
调用前 sp = S
  [S+0]  = old fp   (9 tryte)
  [S+9]  = ra       (9 tryte)
  fp = S+18
  fp+off = 局部变量（按类型分配：char 占 1，其余占 9）
  sp = S+18+frame_size   ← 求值栈顶
```

- `push/pop` 固定步进 9 tryte（求值栈存整字）。
- 函数返回：从 fp-9 取 ra，sp 回 S，从 \[S\] 恢复 fp。

## 5. 指针 ABI（本轮实现）

| 操作 | 语义 | 缩放 |
| --- | --- | --- |
| `&x` | 取局部变量地址 = fp + off | — |
| `*p` | 按 pointee 类型 load | — |
| `p[i]` | 地址 = p + i × sizeof(\*p) | char\* ×1，int\* ×9 |
| `p + i` / `p - i` | 地址 ± i × sizeof(\*p) | char\* ×1，int\* ×9 |
| `p - q`（同类型指针） | (地址差) / sizeof(\*p) | 结果为 int（元素个数） |
| `*p = v` / `p[i] = v` | 按 pointee 类型 store | — |
| `p++` / `p--` | 地址 ± sizeof(\*p) | 按 pointee |

- 左值支持：`变量`、`*p`、`p[i]` 均可赋值与自增。
- 类型驱动：`gen_expr` 返回表达式类型，决定 load/store 宽度与指针算术缩放。

## 6. 编译器能力现状

- 类型：`int` / `char` / 多级指针 `int*` `char*` `int**` … / **多维数组** `int[N]` `int[R][C]` / **结构体** `struct S` `struct S*`
- 数组：维度按 C 顺序 `[行][列]`（ty\_elem 取最左维）；局部变量连续分配（总大小×元素）；数组名退化为首元素指针；多维下标链逐层退化，内层返回数组类型时保留地址不 load；形参仅首维退化为指针、其余维度保留（`int a[][3]` → `int[3]*`）
- 初始化列表：`T x={...}` / `int a[2][3]={{1,2,3},{4,5,6}}` / `struct S s={...}`；支持嵌套与扁平、部分初始化补零；先整块清零（不越界踩相邻变量）再按内存顺序填值
- 结构体：`struct S{...};` 顶层定义；成员按声明顺序、起始对齐到 9 tryte 排布，记录 `(类型,偏移)`；支持栈变量、`struct S*`、结构体数组、`.`/`->` 成员读写、`={...}` 初始化
- 结构体按值传参/返回：**struct 表达式求值 = 结构体首地址**；按值传参=调用方压源地址、被调方 prologue 逐字拷入本地副本（修改隔离）；返回用 **sret**（调用方栈帧预留缓冲、隐藏 a0 传缓冲地址、被调方 return 逐字拷入）；struct 间赋值/初始化按 `type_size` 逐字拷贝
- **变参函数**：形参表末 `...` 标记；`va_list` 游标（9 tryte）+ `va_start(ap)` / `va_arg(ap,T)` / `va_end(ap)` 内建；本 ABI 所有参数等宽 9 tryte（char 实参整数提升），故按字寻址成立
- **栈参数传递 ABI**：参数总数 \>8（sret 时 \>7）或函数为变参时，调用方把**全部实参**从低到高连续压栈成"参数块"，块基址存 `t6`，前 8（或 7）个同时搬到寄存器；被调方 prologue 把第 9+ 形参从 `t6 + i*9` 读入局部槽，变参函数把 `t6` 存入隐藏槽 `__argbase`。`va_start` 游标 = `__argbase + 固定参数数*9`，`va_arg` 在连续参数块上顺序递增——**跨寄存器/栈边界无需特殊处理**。调用方 JAL 返回后按 `sparams*9` 回收参数块。参数总数 ≤8 的非变参调用仍走纯寄存器路径（零额外开销）
- 字符字面量：`'A'` `'\n'` 等，求值为 tryte 整数
- 表达式：完整算术/比较/逻辑/位运算 + 指针全套
- 语句：if/else、while、for、break/continue、return、块、函数递归
- stdio 库（`trirv_stdio.c`，C 子集）：`strlen` `putchar` `putstr` `print_int` `print_base` `printf`（`%d/%u/%o/%x/%X/%s/%c/%%`）`puts`，均基于真实 va\_list 机制，非编译器内建黑盒
- 内置 syscall（asm-generic / RISC-V Linux 编号，a7=号、a0..a5=参数、a0=返回）：
  - 进程/线程：`getpid`(172) `gettid`(178) `set_tid_address`(96) `exit`(93) `exit_group`(94) `sched_yield`(124)
  - 内存：`brk`(214) `mmap`(222) `munmap`(215) `mprotect`(226)；mmap 区从高位向下分配，与 brk 反向，9 tryte 对齐
  - 时钟/随机：`clock_gettime`(113，timespec 两字=sec+nsec，每次 +1ms 单调) `getrandom`(278，xorshift32 确定性 PRNG)
  - 文件（stub）：`openat`(56) `close`(57) `lseek`(62) `fstat`(80) `newfstatat`(79) `readv`(65) `writev`(66)
  - 信号（单线程 stub）：`rt_sigaction`(134) `rt_sigprocmask`(135) `rt_sigreturn`(139) `futex`(98)（不阻塞）
  - 其他：`uname`(160，写 "TriRV") `getcwd`(17)；未实现号返回 `-ENOSYS`
  - 错误码走约定：合法 fd/地址返回 ≥0，失败返回负 errno（-EBADF/-ENOMEM/-EINVAL/-ENOSYS）
- 启动链：`_start → main → exit`

## 7. brk 语义（精确堆）

- `brk(0)`：查询当前堆顶，返回 `brk_ptr`（初始 = `1<<19` = 524288 tryte）。
- `brk(addr)`：`heap_base <= addr < mem_size` 时精确增长/收缩，返回新堆顶；否则返回旧堆顶（失败）。
- 地址单位为 tryte；`heap_base` 是收缩下限，防止覆盖 rodata/栈。

## 8. 最小 malloc（`trirv_libc.c`，C 子集实现）

- **状态存放**：编译器无全局变量，链表头存固定地址 `[65536]`（空闲块字地址，0=空链表）。
- **块布局**：块头 2 字（9 tryte/字），`blk[0]`=整块字数（含头），`blk[1]`=next；数据区 = `blk+2`。
- **分配**：首次适配。空闲块够大则切分（剩余 ≥3 字才切）；否则 `brk` 扩展新块；失败返回 0。
- **释放**：按地址升序插入空闲链表，与前后相邻空闲块主动合并。
- **API**：`malloc(n)` / `free(p)` / `calloc(n)` / `realloc(p,n)`，n 单位为 tryte，返回 `int*` 字指针。
- 以 C 子集写成，可直接编译进 TriRV 程序，未来可整体替换为 glibc malloc 层。

## 9. 验证结果

- syscall 接口 `trirv_syscall_e2e.py`：**18/18 PASS**（进程/线程、时钟、随机、mmap、文件/信号 stub、uname、大常量加载）
- 栈参数传递 `trirv_stackargs_e2e.py`：**11/11 PASS**（9/10/12 参数普通函数、8 参数纯寄存器路径、跨寄存器/栈边界变参、10 变参、printf 9/混合/负数超 8 参数、sret+多参数、struct 按值传参+多参数）
- 变参函数 + printf `trirv_vararg_e2e.py`：**12/12 PASS**（多类型变参 int/char\*/char、字符字面量、printf 的 %d/%s/%c/%%/负数/零/纯串、puts、多参数、变参与 malloc 混用）
- 结构体按值传参/返回 `trirv_byvalue_e2e.py`：**17/17 PASS**（修改隔离、sret 返回、参数+返回组合、struct 数组元素按值传、含 char 整块拷贝、返回再传参、赋值拷贝、嵌套字段、多实例隔离、`f().x` / `f().in.v` 链式读、两次 `f()` 缓冲互不覆盖）；**两套后端都实现**：C 后端（`trirv_cc.c`）在 2026-11-19 补齐，`TRV_CC_STRICT=1` 下同样 17/17，不再回退 Python
- 多维数组+初始化列表 `trirv_multiarray_e2e.py`：**15/15 PASS**（二维/三维读写遍历、二维形参首维退化、char 二维、嵌套/扁平初始化、struct 及其数组初始化、部分补零、与 malloc 混用）
- 结构体 `trirv_struct_e2e.py`：**10/10 PASS**（成员写读、`->` 指针参数、混合 char 成员、结构体数组、数组指针算术、malloc 链表节点、值拷贝、嵌套 struct 指针域、多实例隔离）
- 一维数组 `trirv_array_e2e.py`：**11/11 PASS**（下标写读、循环填充、数组名退化、形参退化、传址修改、多数组共存、char 数组、与 malloc 混用）
- malloc 端到端 `trirv_malloc_e2e.py`：**16/16 PASS**（含写读、隔离、复用、相邻合并、calloc/realloc、动态链表、字符串、分配失败语义 + brk 查询/增长/收缩/越界专项）
- 指针/char 专项 `trirv_ptr_e2e.py`：**12/12 PASS**
- char 符号性/LB-LBU 语义 `trirv_charsign_e2e.py`：**21/21 PASS**（char/unsigned char/signed char 三态、
  5-trit 与 10-trit 符号边界、形参归一化、struct 成员、`char*` 步长，并用 C VM 交叉验证 21/21）
- C 编译器 `trirv_cc_e2e.py`：**14/14 PASS**
- 汇编 e2e `trirv_e2e.py`、VM `trirv_vm.py`：全绿

## 10. 编译器路线与下一步（不走 GCC 后端）

**编译器选型决策：采用轻量 C 编译器路线（TinyCC/tcc 式），不移植 GCC 后端。** 理由：

- GCC 后端依赖机器描述 DSL、多遍优化、数百万行代码；把 20-trit TriRV ISA 与 tryte 编址塞进 GCC 的成本远高于收益。
- TinyCC 式编译器单遍、代码量小、ABI 假设少，后端就是「语句 → 指令」的直接映射，与 TriRV 这种规整、无乱序、无复杂地址模式的 ISA 天然契合。
- 现有 `trirv_cc.py` 已是这条路线：直接产出 TriRV 汇编、已解决 CHAR\_BIT/字节编址假设（char=1 tryte、指针=9 tryte、tryte 编址）。后续在此基础补齐 C 语法，而非从零移植 TinyCC 源码——TinyCC 内核同样硬编码 8-bit 字节编址，迁到 tryte 编址的改动量与自研相当。

**GCC 的定位**：仅作宿主机测试程序的编译器（如 `ternary_pack_test.c`），不进入 TriRV 目标工具链；TriRV 目标代码全程由自研 `trirv_asm.py` + `trirv_cc.py` + `trirv_vm.py` 产出与执行。

**下一步（距 glibc API/ABI 兼容层）**：

1. **glibc sysdeps 层**：按本 ABI 用 `trirv_asm.py` 编写 `crt0`/`start.S`/`syscall.S`（不依赖 GCC/binutils）。
2. **sbrk/mmap 与 brk 协同**、完善 malloc 的 arena 语义（当前为最小实现）。
3. ~~补齐 C 语法：printf `%x/%o/%u`、`unsigned` 宽度语义、`f().x` 链式成员访问~~ ✅ **已实施**（见下）；剩余 printf 宽度/精度、`long` 宽度语义仍为后续目标。

**本轮已落地：unsigned 类型 + printf 无符号格式 + f().x 链式成员访问（2026-11-18）**
- `trirv_cc.py`：`unsigned`/`unsigned int` 关键字，与 `int` 同为 9 tryte 整字；二元 `/` `%` 在任一操作数为 unsigned 时生成 `DIVU`/`REMU`，关系比较生成 `SLTU`（C 通常算术转换）。三补码 word 天然是 mod 3⁴⁵ 的非负表示，故与 signed 同一位型可给出两种解释。
- `trirv_stdio.c`：新增 `print_base(unsigned,base,upper)`；`printf` 支持 `%u`/`%o`/`%x`/`%X`（`%d/%s/%c/%%` 不变）。
- `trirv_cc.py`：`gen_addr(member)` 对结构体右值 base（如 `f()` 的 sret 缓冲）改用 `gen_expr(base)` 取首址，`f().x` / `mk(a).y` / `f().in.v` 链式访问成立（`f().x*10+f().y` 两次调用顺序求值、sret 缓冲无冲突）。
- 验证：`trirv_fmt_e2e.py` **15/15 PASS**（无符号除/取模/比较、signed 对照、四种格式混合、大 word 按 %x）；`trirv_byvalue_e2e.py` **17/17 PASS**（新增 5 项 `f().x` 链式访问）；全量回归无退化。

### 本轮修复的确定性缺陷

- `load_imm`** 大立即数溢出**：`divmod(n,3^7)` 低半 `lo` 可达 2186，但 ADDI 立即数是 7-trit 有符号（±1093）；`lo>1093` 时被 sign\_extend 解成负数（如 10000→7813）。修复为向高位借位（`lo-=3^7; hi+=1`）。
- `getrandom`** 参数错位**：count 误读 a2（flags），修正为 a1。

（已落地链路：统一 tryte 内存与指针 → brk/malloc → 一维数组 → 结构体与成员访问 → 多维数组与初始化列表 → 结构体按值传参与 sret 返回 → 变参函数与 printf → 超过 8 参数的栈传递 ABI → glibc 启动 syscall 最小集）

## 11. 三进制无预测分支优化（C VM）

**目标**：消除分支执行对 CPU 动态分支预测器的依赖——用平衡三值逻辑 + 数据选择代替 `if` 控制流。适用于无分支预测器的真实三进制硬件模型。

**三项改动**（`trirv_vm.c`）：

1. **预计算绝对目标**：`Inst` 增加 `target` 字段，`load_trv` 时算好 `oldpc+imm`（6 种条件分支 + JAL），运行时省掉一次加法。JALR 目标是运行时寄存器，排除。
2. **三值条件码**：`lt=(a<b); gt=(a>b); t=gt-lt ∈ {-1,0,+1}`（平衡三值 trit），统一 BEQ/BNE/BLT/BGE/BLTU/BGEU 六种比较；顺带消除 BEQ/BNE 原本对 `s()` 的重复调用（直接复用已算好的 `a/b`）。无符号比较用原始 `__uint128_t`。
3. **branchless PC 选择**：`mask=-cond`（0 或 -1），`pc_ += mask & (target - pc_)`，纯数据选择，无 data-dependent 控制流。`-DTRIRV_PREDICTED` 可切回传统 `if` 做对照。

**正确性**：无预测版与 `if` 对照版均 **11/11 差分测试通过**（与 Python VM 逐项一致，含算术/递归/循环/printf/write/read/struct/数组/malloc）。

**反汇编结构证据**：`step()` 中 data-dependent 条件跳转从 31 条降到 22 条（消掉 9 条），`setcc/cmov` 从 1 增到 8——9 个分支决策由控制流改为数据选择，结构上实现"无预测"。

**性能基准（诚实结论）**：sum(20000) 与不可预测分支 randbr 两种负载下，无预测版 / `if` 版 / 原始基线吞吐均约 40–42 MIPS，**差异在测量噪声内**。原因：gcc -O3 已将简单 `if` 编译为 `cmov`（8 个），且解释器瓶颈在 switch 分发与逐 trit 内存访问，分支预测失败非热点。此优化的价值在于**面向无分支预测器的目标硬件**，而非加速当前 x86 上的解释执行。

**交付物**：`trirv_vm.c`（无预测实现）、`trirv_vm`（无预测二进制）、`trirv_vm_predopt`（if 对照）、`trirv_vm_predforce`（禁用 if-conversion 的强制预测版）、`trirv_vm_portable`（可移植无预测版）、`trirv_perf_branch_bench.sh`（基准脚本）。

## 12. 三值运算的三层性能优化（逐元素 / 向量化 / 并行）

**背景**：纠正此前"AVX2 对三值运算无效"的粗结论——SIMD 其实把 min/max 阶段加速了 19.6×（117→6 cyc），但该阶段仅占基线 22%，所以整体看不出。真正的热点是 **unpack（9×128 位除法，\~****180 cyc）和 pack（45×乘加，\~****185 cyc），合计占 74%**。微基准 `trirv_trit_bench.c` 精确拆分了各阶段。

**① 逐元素优化（已落地，有效）**：

- 预计算 tryte 级 `LUT_MIN/LUT_MAX/LUT_NOT[243][243]`，把逐 trit 的 45 次运算降到 9 次 tryte 查表。
- 用**常数 243 反复取模/除**（编译器替换为乘法逆元）+ 正向累加替代 `POW3[5*i]` 变除数除法与 45 乘加。
- 微基准：基线 491 cyc → OPT 140 cyc = **3.52×**（标量版 4.35×）。
- 等价性：50 万组随机输入 × 3 模式与原版逐位比对，不一致 = 0 → PASS。
- 端到端三值密集负载：4.89 → 6.45 MIPS（1.32×，循环里还有分支/ADDI 摊薄）。

**② 向量化（三种尝试均无正收益，诚实结论）**：

- min/max SIMD：19.6× 但仅占 22%，整体无感。
- ILP 2-way 交错：74→136 cyc（0.55×），gcc 已自动调度，手动交错反增寄存器压力。
- AVX2 gather 查表：6→51 cyc（0.12×），小表 L1 命中，gather 索引扩展开销反超。
- 根因：TriRV ISA 逐 word 标量语义 + 小查表 L1 命中，数据级并行度不足。真正的并行机会在多实例（见 ③）。

**③ 并行（已落地，近线性）**：OpenMP 多实例批处理，三值密集负载 1→2→4 线程 = 7.00→13.00→26.23 MIPS（4 线程 3.75×）。

**交付物**：`trirv_trit_bench.c`（阶段拆分微基准）、`trirv_perf_element_bench.sh`（三层整合基准）、`trirv_vm.c`（已集成 LUT 优化）、`trirv_verify_trit.c`（等价性验证）。

## 13. 无除法器优化（位移/乘法倒数替代 128 位 div）

**目标**：消除 tritwise 取 tryte 时的 128 位硬件除法（此前 `a%243; a/=243` 编译成 9 次 `__udivmodti4` 库调用）。对无除法器的真实三进制硬件，这是必需项。

**算法**（`trirv_vm.c`）：

- 恒等式 `2^64 = 75912527052302681×243 + 133`。
- `div243(y)`（y\<2^64）：`q = mulhi(y, 75912527052302682)`；因 `MULT=ceil(2^64/243)` 的 mulhi 商误差至多 +1，用无分支 `q -= (q*243>y)`（编译成 `sbb`）修正。纯 `mul+imul+cmp+sbb`，零 div 指令。
- `divmod243(x)`（word\<3^45\<2^72）：拆 `hi=x>>64 + lo`，`mid=hi*133+lo`，借恒等式处理进位，得商与余数。
- tritwise 的 9 次取 tryte 全部改用 divmod243。

**证据与数据**：

- 反汇编：`__udivmodti4` 调用从 **91 个降到 0 个**；`div243` 汇编仅 `mul/imul/cmp/sbb`，零除法指令。剩余 `div` 指令均为 OP\_DIV/OP\_REM 等运行时除法（不可消除）。
- 微基准 tritwise：原始基线 506 cyc → 常数243查表版 155 cyc（3.26×）→ 无除法器版 **68.4 cyc（相对基线 7.40×，比查表版再快 2.27×）**。
- 等价性：50 万组随机 × 3 模式，无除法器版与原版逐位比对不一致 = 0 → PASS；11 项差分全过；TMAX/TMIN/TNOT 专项全过。
- 端到端吞吐持平（tri\_heavy 7.01→6.93 MIPS，噪声内）：TriRV 常数 243 除法已被 gcc 部分优化，且三值指令在总指令中占比低；但**架构上彻底消除除法器**，对目标硬件意义重大。并行仍近线性（1→2→4 线程 6.34→13.15→25.76 MIPS）。

**交付物**：`trirv_vm.c`（已集成无除法器 divmod）、`trirv_trit_bench.c`（含 NO-DIV 基准）、`trirv_verify_trit.c`（含 nodev 等价性）、`trirv_perf_nodev_bench.sh`（整合基准）。

## 14. u() 负数语义修复 + computed goto 分发引擎

**背景**：本轮目标是把 C VM 的指令分发从 `switch` 改为 computed goto（直接线程化），继续优化解释执行。

**过程中发现并修复了一个真实正确性 bug**：C VM 的 `u()`（有符号→无符号）原实现为 `(__uint128_t)x % MOD_X`，对负数 `x` 得到的是 `(2^128+x) mod M`，而 Python VM 的 `from_signed_word(x)=x % MOD_X` 是数学模。两者对负数不一致（如 `return -1`：Python 退出码 255，C VM 错误得 18）。此前 11 项差分测试全用正数结果，未暴露该缺陷。

**修复**：`u(x)` 改为 `x<0 ? x+MOD_X : x`（数学模，对齐 Python），输入域 `[-MOD_X, MOD_X)` 内精确，且**零除法**。修复后：

- 负数专项 4/4（`-1 / 1-3 / -100 / -5*7`）全部 Python=C；
- 11 项差分 + 11 个 e2e 套件全绿，无回归。

**computed goto 执行引擎**：新增 `vm_run_cg()`，用 `&&label` 分发表 + `goto *dispatch[op]` 替代 `switch(in->op)`，消除跳转表索引与范围检查；6 个分支改用独立标签，去掉原共享标签内的 `switch` 与 `lt/gt` 双重比较。`-DTRIRV_SWITCH` 宏可切回原 switch 版对照。语义逐条对齐 `step()`。

**性能数据（诚实）**：

- computed goto vs switch 单线程几乎持平：sum 41.2 vs 41.2 MIPS（噪声内）——gcc -O3 已把密集 case 的 switch 优化成高效跳转表，分发不是当前瓶颈。
- `u()` 零除法修复带来小幅提升：sum 40.6 → 42.7 MIPS（约 5%）；剩余软件除法来自 OP\_DIV/OP\_REM 与 store/load 里 `POW3` 变除数除法。
- OpenMP 多实例仍近线性：1→2→4 线程 = 41.76→78.14→103.27 MIPS（4 线程 2.47×）。
- 结论：本轮**最高价值产出是修复负数正确性 bug**；computed goto 为架构层面的分发优化（对无分支预测的目标硬件有意义），在当前 x86 解释器上无显著收益。

**交付物**：`trirv_vm.c`（已含 `vm_run_cg` + 修复的 `u()`）、`trirv_vm`（computed goto 二进制）、`trirv_vm_switch`（switch 对照）、`trirv_vm_portable`（可移植版）、`trirv_perf_dispatch_bench.sh`（整合基准，含负数回归）。
