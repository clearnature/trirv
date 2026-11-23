# TriRV 任务台账（动态管理）

> **口径**：本文件是任务队列的唯一权威视图。每批次收官时更新对应状态行
> 与「基线快照」；新任务先进「待办」，开工前在此登记拆解。历史决策与
> 坑实录以 `~/.zcode` 记忆 + 各 PLAN 文档为准，本表只管「还有什么活」。

## 基线快照（2026-11-23）

- 回归：`python3 tests/run_all_tests.py` → **PASS 54 / FAIL 0**（连续 21 次全绿）
- HEAD：trirv `e110531` / tinycc `21410af4`
- 三工具链同构：tri-tcc / trirv_cc(C) / trirv_cc.py(py) 全支持
  `__tint` / `__tint2` / `__tbool` / `__builtin_tri_lit` / LW4-SW4

## A. 待办——可直接开工

### A1. py 参考后端语法差距 ×7（各 0.5~1 批，独立可验）
C 后端已全有可对照；TRV_CC=py 下 cc_e2e 全绿是验收口径。
- [ ] A1.1 `switch/case/default/break`
- [ ] A1.2 `typedef`（含 typedef fn-ptr 别名——C 后端已销案可对照）
- [ ] A1.3 `do-while`
- [ ] A1.4 `goto`/标签语句
- [ ] A1.5 三目 `?:`
- [ ] A1.6 `(type)` 强转（含 ptr↔int 同宽无操作口径）
- [ ] A1.7 `const` 修饰（全局/局部；const 全局入 rodata 或 bss 等价）

### A2. ecvt_tri 符号改名/拆共享核心（1 批）
全量链接测试「按符号感知去重跳过」的终极解法：atof/fcvt/gcvt.trvo 与
strtod/ecvt.trvo 同源（TRI_REWRITE 映射表），同名强符号不可同链——
内部符号按变体改名或拆分共享核心，去除测试侧去重逻辑。

### A3. 文档陈旧修正（半批）
README「模块完善度」节过时：调试器"🔴空白"（MVP 已入册 18 项）、
链接器"~60%"（符号化/61 单元/弱符号/闭包导出已落地）、tri-tcc
"for+指针写 bug 待修"（已销案）、TINT C1"进行中"（已收官）。
顺带同步指令计数（97→102）。

## B. 待办——需拍板才启动（附行业/开源参考意见，2026-11-23 检索落档）

### B1. TC 压缩扩展（opcode 23–26 预留区）

- **行业参照**：RISC-V RVC（16/32-bit 变长，核心教训四条——①只压缩高频
  子集，映射表法不重新设计编码；②长度由指令前缀位决定，流式解码无需
  前瞻；③压缩与原指令一一对应，反汇编/调试零成本；④分支目标对齐约束）
  / ARM Thumb-2（16/32 混合流） / MIPS16e、microMIPS / PowerPC VLE。
- **开源三进制参照**：Termite（16-trit 定长 + 8-trit tryte——与 TriRV
  tryte 概念同构，可对照其码密度取舍）；alopex.li TernaryComputer wiki
  （ISA 编码/寻址设计讨论）；REBEL-6（32-trit ISA，IEEE ISMVL 2025）。
- **建议拍板方案**：13-trit 短格式 + 20-trit 长格式变长流（RVC 式），
  长度由 t0 前缀 trit 决定；首批只压缩高频五类（ADDI/LW/SW/BEQ/BNE+JAL
  片段），映射表一一对应；opcode 23 = C-LOAD/STORE、24 = C-OPIMM、
  25 = C-BRANCH/JAL，26 机动。验收：musl_tri 热点函数码长对比 + 双 VM
  差分。**待拍板点：变长流 vs 双定长模式切换。**

### B2. 二进制兼容白名单

- **行业参照**：二进制兼容的行业主流是**翻译层**而非双模式硬件——
  Rosetta 2 / FEX-Emu / Box64（x86→ARM 用户态翻译）、QEMU user-mode；
  同家族双模式先例 = AArch64 的 AArch32 兼容模式。
- **开源三进制参照**：REBEL-6 的 RV32I 兼容对比管线（同 ISA 家族双模式
  学术实现）；Douglas Jones 的 binary-coded ternary（trit 装箱到二进制
  宿主——反向问题，可借鉴其 trit↔bit 表示学）；xTern 的 5-trits/byte
  装箱。
- **建议拍板方案**：与已拍板红线（VM 拦截二进制 = 安全边界）一致——
  **不做硬件双模式**；兼容层立项为「二进制→三进制用户态翻译器」
  （QEMU user-mode 式）：白名单 = guest syscall 面（对齐 TriRV syscall
  表）+ 允许指令子集翻译表；先做 ELF 子集（静态链接、无 TLS）。
  **待拍板点：翻译粒度（整函数 AOT vs 逐块 JIT）。**

### B3. SMP 模拟

- **行业参照**：RISC-V 生态惯例——multi-hart + RVWMO 弱内存模型 +
  LR/SC 的 aq/rl 序约束 + MESI 一致性；模拟器先例 = Spike（multi-hart）、
  QEMU TCG 多核、gem5。嵌入式行业惯例 = AMP（非对称多处理：核间中断 +
  共享内存标志），一致性 SMP 后置。
- **开源三进制参照**：检索确认**无现成多核三进制模拟器**（niche）——
  TriRV 若做即是首个；CVM 已有雏形（v->shared/sh_lock 共享内存 +
  __atomic 版本计数）。
- **建议拍板方案**：分两步——①**AMP 先行**（多实例 PyVM/CVM + 核间
  mailbox 系统调用 + 共享页），复用现有 A 扩展指令做核间同步，零内存
  模型改动；②**SMP 后置**（RVWMO 式弱模型 + hart 概念 + aq/rl），需要
  时另行立项。**待拍板点：AMP 起步是否满足当前需求（固件/并发实验）。**

## C. 大方向立项（计划文档在库，未开工）

- [ ] C1. **LLVM 后端**——docs/LLVM_BACKEND_PLAN.md
- [ ] C2. **WASM 路线深化**——docs/WASM_ROUTE_A.md
- [ ] C3. **固件架构**——docs/trirv-firmware-architecture.md
- [ ] C4. **三值逻辑综合器 / Post 代数**——T-LIT（LIT0/1/2）的应用层，
      代数综合是原始动机
- [ ] C5. **musl 覆盖面扩展**（开放式）——musl_tri 16 文件 /
      trirv_cc 管线 65/66，可继续扩函数面

## D. 归档——已收官（2026-11-23 会话，16 批）

| 批次 | 提交 | 内容 |
|---|---|---|
| C2-a LW4/SW4 ISA | tinycc cdb11a68 + trirv 960c7df | 4-tryte 档 ISA+双 VM+mem4 契约 |
| C2-b tri-tcc __tint2 | tinycc 1d9d5e76 + trirv 59ca906 | VT_TINT2 全链路 |
| C2-c tri-tcc __tbool | tinycc aecfa0ec + trirv 0fa2417 | Kleene 布尔收官 TINT C2 |
| musl arch 层 | trirv 9832f42 | read8/write8 对接面 + 原子纯 C |
| T-LIT 预留登记 | trirv 59619c3 | opcode 22 + §6.1（未实现） |
| tri-link __tri_syscall | tinycc a65ccd1e + trirv 6f8aae2 | tri-tcc syscall 面 |
| T-LIT 实现 | tinycc 7d79f968 + trirv 8d9a6e8 | LIT0/1/2 双 VM |
| T-LIT 内建 | tinycc 21410af4 + trirv 2a301c0 | __builtin_tri_lit（tri-tcc） |
| typedef fn-ptr 销案 | trirv ab74848 | 四形态契约入册 |
| trirv_cc 内建 | trirv 0ed086e | 双后端 __builtin_tri_lit |
| trirv_cc __tint2 批A | trirv 43ccd3d | 存取即窄化 |
| trirv_cc __tint/__tbool 批B | trirv bd71a27 | Kleene 门控 |
| py tint 局部 | trirv 8a7215f | 参考后端局部形态 |
| py 全局变量 | trirv e110531 | TRV_CC=py 下 cc_e2e 45/45 全绿 |

更早里程碑：见 `~/.zcode` 记忆（C 方言本征化、未声明标识符、qsort 转正、
链接器三刀、弱符号/闭包、调试器 MVP、TF 浮点、TINT C1 等）。
