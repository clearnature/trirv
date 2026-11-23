# TriRV 阶段 4：LLVM 后端任务计划书
状态：进行中（ADR-32 批次 2.15 统一补标——该文档未逐份定稿，收官时更新）

> 状态：**计划（2026-11-21 落档）** —— 把 README §二 阶段表第 4 行"LLVM 寄存器式后端（正式优化路径）"
> 从一行占位展开为可执行的原子任务分解。既定决策不动摇：**原生三进制 ISA**（参考开源 RISC-V
> 基础/扩展指令集，只换进制层），工具链 **wasm/musl 先行、LLVM 收尾**，最终**使用开源 LLVM**。
> 本文是阶段 4 的执行合同：改动任何 D-L 决策须先更新本文并说明理由。

---

## 0. 战略支柱（先把最重要的架构事实说死）

**只需要给 llc 加 TriRV 目标（后端），clang 前端保持开源原样。**

C → LLVM IR 是目标无关的（clang -emit-llvm 出的是 IR，与目标机器编码无关）；
IR → TriRV 才需要三进制后端。这意味着：

| 组件 | 是否要改 | 说明 |
| --- | --- | --- |
| clang / 预处理器 / 词法解析 | ❌ 不改 | 开源原样，C→IR 白拿全部前端能力（含 C99/C11、优化 pass 前端部分） |
| 中端优化 pass（mem2reg/instcombine/…） | ❌ 不改 | 在 IR 上跑，目标无关——白拿 LLVM 数百个优化 pass |
| llc（指令选择/寄存器分配/汇编输出） | ✅ 加 TriRV target | 本阶段唯一的核心工程 |
| trirv_asm.py / mini linker / TRVO | ✅ 继续用 | 后端只发汇编文本，装配与链接复用既有工具 |
| trirv_vm / 双 VM 差分体系 | ✅ 继续用 | 验收基础，不动 |
| trirv_cc（自研编译器） | ✅ 保留为对照 | 不废弃——阶段 4 的验收方法就是双工具链差分 |

这条支柱把"给 GCC/LLVM 写完整三进制后端"的数人年工作量收敛为
"给 llc 写一个 out-of-tree 后端"的月级工作量，且天然继承 LLVM 的全部优化。

---

## 1. 现状盘点（四阶段进度，2026-11-21）

| 阶段 | 内容 | 进度 | 实测状态 |
| --- | --- | --- | --- |
| 1 | VM 软核（取指/译码/执行） | ✅ 100% | 96 条指令（TI 40 + TT 9 + M8/A9/Zicsr6/PRIV2 + F 20 + read8/write8 视图层）、Python/C 双 VM、fuzz 3300 case 字节级一致、tritref 成本模型 |
| 2 | 栈式 masm / wasm 先行 | ✅ 100%（路径 A 范围） | wasm 三路差分 112 例（含浮点 19）、操作数栈驻留、位运算拆位；范围边界：i16/i64/br_table/call_indirect 未做 |
| 3 | 自研工具链 | ✅ ~95% | trirv_cc 148/148、crt0/__libc_start_main、libc 层+busybox、mini linker、TRVO、musl 批量 65/66、TF 浮点 P1–P5、libc 浮点 P4（strtod/ecvt，7447/0）、39 套契约全 PASS |
| 4 | LLVM 后端 | ⬜ 0% | 本计划书 |

阶段 3 已登记缺口（LLVM 路线多数自然消解，见 §4）：
未声明标识符检查（2026-11-22 已转 GREEN：变量语境硬报错 + 原型/隐式声明/
函数名作值豁免）、trirv_cc 无 long/short 类型
（int=45-trit 单宽）、unsigned 部分语义、errno 完整传递。

---

## 2. 关键技术决策（D-L1..D-L5，进入 P-1 前逐个拍板）

| 编号 | 决策点 | 选项 | 建议 | 状态 |
| --- | --- | --- | --- | --- |
| **D-L1** | 接入形态 | out-of-tree 下游 target（fork 外挂）/ 主线 PR | **fork + out-of-tree**（llvm-project 有官方 out-of-tree 目标机制；迭代快，主线 PR 待稳定后另立任务） | 待 P-0 实测 |
| **D-L2** | 数据模型 | i45 原生字 + DataLayout"1 地址单位=1 tryte"（i8→5-trit tryte 视图，零模拟税）/ 8-bit 视图层模拟（read8/write8 路线） | **tryte 直通**——LLVM 支持任意宽度整数（i45 合法），45-trit 字天然是 i45；风险点：DataLayout 要求 8-bit 可寻址假设的边缘、`char`（5-trit）与 i8 的映射。P-0 必须实测 | 待 P-0 实测 |
| **D-L3** | 目标产物 | 先发 TriRV 汇编文本（喂 trirv_asm.py）/ 直接实现 TriRV MC 层出 TRVO/ELF | **汇编文本先行**——装配/链接复用既有工具，MC 层后置 | 建议，待拍板 |
| **D-L4** | 前端调用 | stock clang（`-emit-llvm`），DataLayout 由 TriRV target 在 llc 侧覆盖 | **stock clang**（P-0 首个验证点：clang 对 IR 的 target 依赖面到底多大） | 建议，待拍板 |
| **D-L5** | trirv_cc 去留 | 废弃 / 保留为差分对照 | **保留**——双工具链差分是阶段 4 的验收方法；阶段 3 投入不浪费 | 建议，待拍板 |

---

## 3. 原子任务分解

### P-0 可行性 spike（GO/NO-GO 门，先回答"行不行"再投入）

| ID | 任务 | 交付物 | 验收标准 | 依赖 |
| --- | --- | --- | --- | --- |
| **L-0.1** | 环境与构建 spike：获取 llvm-project（锁定版本），跑通 out-of-tree dummy target 的 llc 构建 | 构建脚本 + 版本号 + 构建时长入账 | dummy target 的 llc 能接受一个 trivial .ll 并输出 dummy 汇编 | — |
| **L-0.2** | DataLayout spike：定义 TriRV DataLayout（i45=9 tryte 字、tryte 可寻址实验），验证 i45 IR 能过寄存器分配（假指令级） | DataLayout 定义 + 边界坑清单（char/i8 映射、alloca、memcpy 内建） | i45 函数经 SelectionDAG 不崩溃；坑清单入账 | L-0.1 |
| **L-0.3** | 最小编码 spike：TriRV.td 定义 ADDI/LW/SW/JAL 四条，AsmPrinter 打出与 trirv_asm.py 同语法的汇编文本 | td 文件 + 打印样例 | 打印文本喂 trirv_asm.assemble 成功且双 VM 执行语义正确 | L-0.2 |
| **L-0.4** | 端到端 spike：hello.c → stock clang(-emit-llvm) → hello.ll → tri-llc → hello.s → trirv_asm → hello.trv → trirv_vm | 全链路脚本 + 步数/耗时入账 | stdout 字节正确、halted=1、退出码正确 | L-0.3 |
| **L-0.5** | GO/NO-GO 决策：spike 实测工作量、坑清单、D-L1..D-L4 逐个拍板 | 决策记录追加本文 §2 | 四项 D-L 全部落定 + 工作量评估 | L-0.4 |

### P-1 寄存器与 ABI 骨架

| ID | 任务 | 验收标准 | 依赖 |
| --- | --- | --- | --- |
| **L-1.1** | RegisterInfo：32 寄存器 + 寄存器类（GPR/a/t/s 分组）与别名表 | 寄存器名输出与 trirv_asm 别名一致 | L-0.5 |
| **L-1.2** | CallingConv：对齐 ternary-abi-minimal（a0–a7 参数/返回、s 被调保存、t 调用者保存） | 两函数调用参数寄存器正确 | L-1.1 |
| **L-1.3** | FrameLowering：prologue/epilogue（ra/fp 保存、9-tryte 栈对齐、大帧走 §11.1 的 _mem_addr 语义） | 递归 + 大局部数组程序正确 | L-1.2 |
| **L-1.4** | P-1 端到端：多函数 + 递归（fib）双工具链行为差分 | LLVM 版 vs trirv_cc 版 vs 期望值三方一致 | L-1.3 |

### P-2 ISA 覆盖（按组原子化，每组独立可验收）

| ID | 任务 | 条数 | 验收标准 | 依赖 |
| --- | --- | --- | --- | --- |
| **L-2.1** | TI ALU（R 型 + I 型） | 21 | 每条指令双 VM 差分（复用 trirv_fuzz 生成器思路） | L-1.4 |
| **L-2.2** | Load/Store | 8 | 访存语义对齐 §统一内存 ABI（LB/LBU 符号/零扩展口径） | L-1.4 |
| **L-2.3** | 分支/跳转（B/J/I） | 8 | PC 编址 §11.3（槽偏移、align_down4）与汇编器重定位口径一致 | L-1.4 |
| **L-2.4** | LUI/AUIPC 大常量 | 2 | load_imm 3×15-trit 平衡分段与 trirv_cc 同语义，全幅 fuzz | L-2.3 |
| **L-2.5** | M 扩展 | 8 | mul90 低半/MULH floor 语义对齐 trirv_vm，mdiff 复跑 | L-1.4 |
| **L-2.6** | CSR/trap | 8 | Zicsr6 + MRET/WFI，csr_diff 复跑 | L-1.4 |
| **L-2.7** | TA 原子 | 9 | LR/SC/AMO，fuzz_mem 原子段复跑 | L-2.2 |
| **L-2.8** | F 扩展 | 20 | TF20/TF45 档位，语义对齐 trirv_float.h（tf_ratio 舍入），float_isa/wasm 契约复跑 | L-1.4 |
| **L-2.9** | 全覆盖守卫：trirv_isa.py INST（96 条，单一真相源）自动生成/校验 TriRV.td | — | ISA 表与 td 逐条对齐的自动一致性测试（防漂移） | L-2.1..2.8 |

### P-3 优化与代码质量（寄存器式收益兑现）

| ID | 任务 | 验收标准 | 依赖 |
| --- | --- | --- | --- |
| **L-3.1** | 后端基础：寄存器分配压力（musl 大文件编译不溢出）、窥孔（冗余 LUI 段合并） | bench_cc_backend 的中/大程序负载，静态指令数 ≤ trirv_cc | P-2 |
| **L-3.2** | 动态指令对照：LLVM 版 vs trirv_cc 版在同一负载的动态步数/墙钟 | **≥ trirv_cc 的 6.2× 基线**（阶段 4 存在的意义） | L-3.1 |
| **L-3.3** | 优化 pass 白名单实测：mem2reg/instcombine 等中端 pass 在 i45 IR 上的收益 | 三负载对照表入账 | L-3.1 |

### P-4 工具链整合（musl 生态接到 LLVM 工具链）

| ID | 任务 | 验收标准 | 依赖 |
| --- | --- | --- | --- |
| **L-4.1** | tri-cc 驱动脚本：clang→llc→trirv_asm→linker 一条命令 | 对用户呈现为单个编译命令（对标 trirv_cc 用法） | P-2 |
| **L-4.2** | musl 打通：65 文件经 LLVM 工具链编译 | 编译成功率 ≥ trirv_cc 版（65/66）；TRVO 增量链接兼容 | L-4.1 |
| **L-4.3** | libc 浮点对照：strtod/ecvt LLVM 版行为差分 | Fraction oracle 契约对 LLVM 产物复跑全绿 | L-4.2 |
| **L-4.4** | crt0/启动链：_start→__libc_start_main 用 LLVM 产物 | crt 契约（29 项）LLVM 版复跑 | L-4.2 |

### P-5 验证体系（自 P-1 起同步建设，不后补）

| ID | 任务 | 验收标准 | 依赖 |
| --- | --- | --- | --- |
| **L-5.1** | 双工具链差分 harness：同一 C 源 → trirv_cc 版 vs LLVM 版 → 双 VM 行为对比 | harness 落地 + 每组 ISA 任务即用 | L-1.4 |
| **L-5.2** | 39 套契约的 LLVM 版抽样跑绿（红/绿名单） | 名单入账；绿名单全过 | L-4.1 |
| **L-5.3** | fuzz：LLVM 产物 × PyVM/CVM 字节差分 | ≥1000 case MATCH | L-2.1 |
| **L-5.4** | CI 脚本：llvm 重编译成本、回归一键化 | 全量 LLVM 回归 ≤30 分钟 | L-4.1 |

---

## 4. 依赖顺序与阶段 3 缺口的处置

```
L-0.1 → L-0.2 → L-0.3 → L-0.4 → L-0.5（GO 门）
                                  ↓
                              L-1.1 → L-1.2 → L-1.3 → L-1.4
                                                        ↓
                  L-2.1..2.4（可并行）→ L-2.5..2.8（可并行）→ L-2.9
                                                        ↓
                                       L-3.x → L-4.x → 完成定义
                  （L-5.1 自 L-1.4 起同步；L-5.3 自 L-2.1 起同步）
```

阶段 3 缺口在 LLVM 路线下的处置：

| 缺口 | 处置 |
| --- | --- |
| 未声明标识符 XFAIL（fn-as-value 语义） | 仅 trirv_cc 受影响——LLVM 路线用 stock clang 天然免疫；trirv_cc 保留为对照时缺口随 XFAIL 记账即可 |
| trirv_cc 无 long/short（int=45-trit 单宽） | LLVM 路线下 clang 前端原生支持全部 C 类型；TriRV 侧 long=pointer=45-trit 由 D-L2 数据模型定义。trirv_cc 不再补 |
| errno 传递 | 已注入 30 项常量；LLVM 版由 musl 源码原生处理 |
| wasm i16/i64/br_table | 与后端无关，维持路径 A 范围边界 |

---

## 5. 阶段 4 完成定义（DoD）

1. musl busybox 全链路：C 源 → stock clang → tri-llc → trirv_asm → mini link → **双 VM 执行正确**
2. 双工具链差分：39 套契约绿名单在 LLVM 产物上全绿
3. 性能入账：LLVM 产物动态步数/墙钟 ≥ trirv_cc 基线（6.2×/8.4×），数字可复现
4. 96 条 ISA 全覆盖守卫（L-2.9）常绿
5. 文档：README 阶段表第 4 行改 ✅，本文全部任务关闭

---

## 6. 与既有文档的关系

- README §二 阶段表：本计划书是其第 4 行的展开；完成后回填
- ternary-isa-design.md §9-D3：D3 撤销（32 寄存器/4-trit 字段）正是为 LLVM 复用铺路——本计划书的 D-L2 数据模型继续沿用其口径
- ternary-abi-minimal.md：调用约定（L-1.2）的规范来源
- TERNARY_FLOAT_PROPOSAL.md §5.5：F 扩展语义（L-2.8）的真相源（tf_ratio 舍入、双档）
- trirv-unified-memory-abi.md §10：明确"编译器选型 TinyCC 路线……最终寄存器式交给 LLVM"——本文是其执行计划

---

## 7. 立即下一步

**L-0.1（环境与构建 spike）**——唯一能同时验证 D-L1/D-L2/D-L4 假设的任务：
本机获取 llvm-project 源码、锁定版本、按官方 out-of-tree target 模板跑通 dummy llc 构建。
完成即可写 L-0.2 的 DataLayout 实验，L-0.5 出 GO/NO-GO。
