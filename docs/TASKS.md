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

## B. 待办——需拍板才启动

- [ ] B1. **TC 压缩扩展**（opcode 23–26 预留区）——压缩格式设计未规划，
      需先拍板目标（码长/性能取舍、16-trit 槽形态？）
- [ ] B2. **二进制兼容白名单**——已拍板后期另立项
- [ ] B3. **SMP 模拟**——arch_tri 原子层当前单核口径；SMP 时切 ISA
      A 扩展（LR/SC/AMO 双 VM 已实现），需拍板多核语义与缓存模型

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
