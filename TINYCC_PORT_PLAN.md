# TriRV 工具链路线转折：TinyCC 移植任务计划书

> 状态：**计划（2026-11-21 落档）** —— 用户决策：**tinycc 取代 llvm-project**，LLVM 不是当前任务。
> 上游仓库 `clarenature/tinycc`（TinyCC/tinycc 的 C23 开发 fork），已从 `dev-c23` 建立
> 分支 `trirv`（本地 ~/ai-lab/tinycc）。本文取代 `LLVM_BACKEND_PLAN.md`（保留作历史决策记录）。

---

## 0. 为什么 TinyCC 是对的路（立项理由，对齐既有决策）

1. **项目文档早已预留**：`trirv-unified-memory-abi.md` §10"编译器选型：TinyCC/tcc 式，
   不移植 GCC 后端……TinyCC 单遍、代码量小、ABI 假设少"——当时自研 trirv_cc 即此路线的
   先行验证（148/148），现在把"tcc 式"升级为"直接移植 tcc 本体"。
2. **自带 riscv64 后端**：`riscv64-gen.c`(1496行)/`riscv64-asm.c`/`riscv64-link.c`——
   与"参考开源 RISC-V、只换进制层"完全同构：fork riscv64 后端改编码为 TriRV，
   前端/优化（tccgen 10032 行目标无关）/预处理器（tccpp 4522 行）全部白拿。
3. **20-trit 恰好装进 32-bit 管线（关键事实）**：3²⁰ ≈ 2³¹·⁷ < 2³²——TriRV 指令编码后
   是一个 uint32，tinycc 的 `o(u32)` 直发缓冲、gen_function、标签回填机制**零容器改动**。
   TRV1 格式本来就按 u32 存指令。
4. **CHAR_IS_UNSIGNED 已是 tinycc riscv64 默认**（gen 文件头部 `#define CHAR_IS_UNSIGNED`）
   ——与 TriRV psABI（char=0..242 无符号）天然吻合。
5. LLVM 的中端优化优势以"编译慢、工程量月级"为代价；TinyCC 单遍直出与 TriRV 软核
   当前的迭代速度匹配（阶段 1–3 全部两天级完成）。中端优化若未来需要，tinycc 之上
   仍可叠 LLVM（tinycc→IR 桥），不在本阶段。

---

## 1. 现状与交付物

| 项 | 状态 |
| --- | --- |
| `gh repo clone clearnature/tinycc` → ~/ai-lab/tinycc | ✅ 2026-11-21 |
| 从 `dev-c23` 建分支 `trirv` | ✅（dev-c23 = origin 默认分支，HEAD aaaebc5f） |
| 上游摸底 | ✅ tccgen 10032（目标无关）/tccpp 4522/tccelf 4215；riscv64 后端 5597；目标选择 = `-DTCC_TARGET_RISCV64`（tcc.h/Makefile/tcc.c 三处） |
| tri-gen.c / tri-link.c / tri-tok.h | ⬜ 本计划 |

---

## 2. 关键决策（D-T1..D-T5）

| 编号 | 决策点 | 建议 | 状态 |
| --- | --- | --- | --- |
| **D-T1** | 后端基线 | fork `riscv64-gen.c` → `tri-gen.c`（不改上游文件，新目标并排） | 建议 |
| **D-T2** | 数据模型 | PTR_SIZE=9（tryte）、long=pointer=45-trit、CHAR_IS_UNSIGNED、i8=5-trit tryte（复用 8-bit 视图层 read8/write8 处理 CHAR_BIT 依赖） | 建议 |
| **D-T3** | 产物 | 不走 tccelf/ELF——`tri-link.c` 直接产 TRVO（文本中间格式）+ TRV1（u32 指令流），链接复用既有 mini linker 语义 | 建议 |
| **D-T4** | 语义真相源 | 指令编码/调用约定/PC 编址一律以 `trirv_isa.py`（96 条）+ `ternary-isa-design.md` 为准，TriRV.td 式漂移问题以"自动一致性测试"防（对齐 LLVM 计划 L-2.9 思路） | 建议 |
| **D-T5** | trirv_cc 去留 | 保留为差分对照（双工具链差分仍是验收方法），XFAIL 缺口不补 | 建议 |

---

## 3. 原子任务分解（T 系列）

### T-0 可行性 spike（GO/NO-GO 门）

| ID | 任务 | 验收标准 | 依赖 |
| --- | --- | --- | --- |
| **T-0.1** | 本机构建 tinycc（riscv64 目标交叉模式：`make tcc CROSS_TARGET=riscv64` 或原生），跑通 `tcc -version` | 构建成功 + 用 riscv64 tcc 编译 hello 输出 ELF（证明管线可用） | — |
| **T-0.2** | 目标注册 spike：加 `TCC_TARGET_TRIRV` 选择点（tcc.h/Makefile/tcc.c/libtcc.c 四处 + tcctok.h），空 tri-gen.c 挂进构建 | `make tcc DEF-trirv` 构建通过（哪怕只报"目标未实现"） | T-0.1 |
| **T-0.3** | 最小编码 spike：tri-gen.c 实现 `_start 装载常量+返回`（ADDI/LUI/JAL + exit 系统调用序），直接 o() 出 TRV1 | hello-ish 程序（return 42）经 tri tcc → TRV1 → trirv_vm exit=42 | T-0.2 |
| **T-0.4** | tccgen 适配 spike：PTR_SIZE=9、类型尺寸表（char=1 tryte/int=9/指针=9）让 tccgen 走通 `int main` 全语义 | return 42 与 i=i+1 循环两例过双 VM | T-0.3 |
| **T-0.5** | GO/NO-GO：坑清单 + 工作量实测入账 | D-T1..D-T5 拍板 | T-0.4 |

### T-1 核心 ISA 覆盖（tri-gen.c 指令发射器按组实现）

| ID | 任务 | 验收 |
| --- | --- | --- |
| **T-1.1** | load/store（LB/LBU/LH/LHU/LW/SB/SH/SW）| 访存差分 vs trirv_vm |
| **T-1.2** | TI ALU R/I 全套（21 条） | 每条双 VM 差分 |
| **T-1.3** | 分支/跳转（B/J/I 型 + PC 槽偏移） | 分支程序三方一致 |
| **T-1.4** | 函数调用（JAL/JALR + 调用约定 a0-a7/s/t 对齐 ternary-abi-minimal） | 递归 fib 三方一致 |
| **T-1.5** | M 扩展（8 条，mul90 语义） | mdiff 思路复跑 |
| **T-1.6** | F 扩展（20 条双档，对齐 trirv_float.h） | float_isa 契约复跑 |
| **T-1.7** | CSR/trap（8 条） | csr 契约复跑 |
| **T-1.8** | ISA 一致性守卫：tri-gen 编码表 ↔ trirv_isa.py 自动校验 | 96 条逐条对齐常绿 |

### T-2 运行时与 libc 接入

| ID | 任务 | 验收 |
| --- | --- | --- |
| **T-2.1** | tri-link.c：TRVO 目标文件产出（FUNC/PARAM/LINE/GLOB/STR 行格式）+ undefined ref 检测 | 与既有 mini linker 互通 |
| **T-2.2** | crt0/_start（编译器内建路径对齐 trirv_cc 现状）| crt 契约复跑 |
| **T-2.3** | musl 复用：65 文件经 tri-tcc 编译（preprocess_musl 同管线） | 成功率 ≥ trirv_cc 版（65/66） |
| **T-2.4** | libc 浮点（strtod/ecvt）tri-tcc 版行为对照 | Fraction oracle 契约复跑 |

### T-3 验证体系（自 T-0.4 起同步建）

| ID | 任务 | 验收 |
| --- | --- | --- |
| **T-3.1** | 双工具链差分 harness：同一 C 源 → trirv_cc 版 vs tri-tcc 版 → 双 VM 对比 | harness 落地，T-1 各任务即用 |
| **T-3.2** | 39 套契约 tri-tcc 版抽样（红/绿名单） | 绿名单全过 |
| **T-3.3** | fuzz：tri-tcc 产物 × 双 VM 字节差分 ≥1000 case | MATCH |

---

## 4. 依赖顺序

```
T-0.1 → T-0.2 → T-0.3 → T-0.4 → T-0.5（GO 门）
                                  ↓
          T-1.1..1.4（主体串行，1.5-1.7 可并行）→ T-1.8
                                  ↓
          T-2.x → 完成定义；T-3.1 自 T-0.4 同步，T-3.3 自 T-1.1 同步
```

## 5. 完成定义（DoD）

1. musl busybox：C 源 → tri-tcc → TRVO/TRV1 → 双 VM 执行正确
2. 双工具链差分：39 套契约绿名单在 tri-tcc 产物上全绿
3. 96 条 ISA 一致性守卫（T-1.8）常绿
4. README 工具链表更新：tri-tcc 成为正式编译器（trirv_cc 转对照）
5. 本文任务全部关闭

## 6. 与既有文档的关系

- 取代 `LLVM_BACKEND_PLAN.md` 的执行地位（保留作决策历史：LLVM 降级为未来可选）
- `trirv-unified-memory-abi.md` §10"TinyCC 路线"的正式执行计划
- `ternary-isa-design.md`/`trirv_isa.py`：编码真相源（D-T4）
- `TERNARY_FLOAT_PROPOSAL.md`：F 扩展语义真相源

## 7. 立即下一步

**T-0.1**：本机构建 tinycc（含 riscv64 交叉目标），验证构建管线可用——这是全部
后续任务的前提，也是对 dev-c23 分支健康度的第一手检查。
