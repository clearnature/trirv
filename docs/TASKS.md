# TriRV 任务台账（动态管理）

> **口径**：本文件是任务队列的唯一权威视图。每批次收官时更新对应状态行
> 与「基线快照」；新任务先进「待办」，开工前在此登记拆解。历史决策与
> 坑实录以 ~/.zcode 记忆 + 各 PLAN 文档为准，本表只管「还有什么活」。

## 基线快照（2026-11-23 最新）

- 回归：python3 tests/run_all_tests.py → PASS 54 / FAIL 0（连续 30+ 次全绿）
- HEAD：trirv 9e177d6 / tinycc 21410af4
- 三工具链同构：tri-tcc / trirv_cc(C) / trirv_cc.py(py) 全支持
  __tint / __tint2 / __tbool / __builtin_tri_lit / LW4-SW4 / LIT0-2
- ISA 总量：102 条（含 LW4/SW4 4-tryte 档 + LIT0/1/2 文字运算）

## A. 执行队列（2026-11-23 授权连续执行）

执行序：T1→T2→T3→T4→T5→T6→T7→T8→T9→T10→T11（按序连续执行）

### 已收官（本会话 T1~T10c-1）

- [x] T1 const（a7661ad）
- [x] T2 do-while（3180791）
- [x] T3 goto/标签（同上）
- [x] T4 三目 ?:（429d888）
- [x] T5 (type) 强转（429d888）
- [x] T6 switch（429d888）
- [x] T7 typedef 基础别名 + C 后端数组别名（8677768 + bdac71d）
- [x] T8 文档陈旧修正（132233f）
- [x] T9 ecvt_tri 拆分共享核心（83b4013；拼接式口径）
- [x] T10a TC 设计文档（8c531a0）
- [x] T10b TC isa 编码层（TC:23 + is_c_format + encode_c/decode_c）
- [x] T10c-1 PyVM TC 执行（ff8f50a；C-ADDI/BNE/SW/LW 四形态）

### 待办——按序连续执行

- [x] T10c-2 CVM TC 执行验证 PASS（9e177d6 + 正确 exit 序列；早期"失败"
      系测试 syscall 号设错——ADDI x17,x11,93 设 a7=100 非 93=exit）
- [ ] T10d trirv_cc -mcompact 发射（TC 编码选取：高频五类静态子集，
      超界回退全宽——零正确性风险）
- [ ] T10e 码长对比（musl_tri 热点函数 C 占比 >= 50% → 码长省 ~15%）
- [ ] T11 AMP 先行（mailbox syscall + 共享页，多实例双 VM）
- [ ] T7b py fn-ptr 声明符与间接调用（py 无 fn-ptr 解析基础）
- [ ] T9b TRVO 多单元直链自旋 bug（取证：符号解析/布局全对，
      热点=未调用单元体 → 导入补丁层 bug——独立深水排查）

### 前置缺口记账（不阻断主线）

- py 参考后端 tint 正向全量（当前以语法错误拒绝——双后端拒绝纪律成立）
- trirv_cc.py fn-ptr / switch / typedef / do-while / goto / 三目 / 强转 / const
  （py 参考后端教学子集差距——按需补）

## B. 待办——需拍板才启动（附行业/开源参考意见）

### B0. 架构拍板（2026-11-23 用户定案）

虚拟化分层架构：TriRV = 隔离底层真实硬件的三进制虚拟硬件平台。
纯虚拟硬件+软件栈先行；硬件直通后置（行业默认不暴露底层硬件）。
syscall 表 = 虚拟设备接口（virtio 半虚拟化同构）。

### B1. TC 压缩扩展（进行中，见执行队列 T10）

参照：RISC-V RVC / Thumb-2 / MIPS16e；Termite / REBEL-6 / alopx.li wiki

### B2. 二进制兼容白名单——直通期后置

参照：Rosetta 2 / FEX-Emu / Box64（翻译层惯例）；REBEL-6 双模式学术参照

### B3. SMP 模拟——纯虚拟期，AMP 先行

参照：RISC-V multi-hart + RVWMO + MESI；AMP 惯例（核间中断+共享内存）

## C. 大方向立项（计划文档在库，未开工）

- [ ] C1. LLVM 后端（docs/LLVM_BACKEND_PLAN.md）
- [ ] C2. WASM 路线深化（docs/WASM_ROUTE_A.md）
- [ ] C3. 固件架构（docs/trirv-firmware-architecture.md）
- [ ] C4. 三值逻辑综合器 / Post 代数（T-LIT 应用层）
- [ ] C5. musl 覆盖面扩展（65/66 → 开放式扩展）

## D. 归档——已收官批次汇总

| 批次 | 提交 | 内容 |
|---|---|---|
| C2-a LW4/SW4 ISA | tinycc cdb11a68 + trirv 960c7df | 4-tryte 档 ISA+双 VM |
| C2-b tri-tcc __tint2 | tinycc 1d9d5e76 + trirv 59ca906 | VT_TINT2 全链路 |
| C2-c tri-tcc __tbool | tinycc aecfa0ec + trirv 0fa2417 | Kleene 布尔收官 |
| musl arch 层 | trirv 9832f42 | read8/write8 + 原子纯 C |
| T-LIT 预留 | trirv 59619c3 | opcode 22 + §6.1 |
| T-LIT 实现 | tinycc 7d79f968 + trirv 8d9a6e8 | LIT0/1/2 双 VM |
| T-LIT 内建 | tinycc 21410af4 + trirv 2a301c0 | __builtin_tri_lit |
| tri-link __tri_syscall | tinycc a65ccd1e + trirv 6f8aae2 | tri-tcc syscall 面 |
| typedef fn-ptr 销案 | trirv ab74848 | 四形态契约 |
| trirv_cc 内建 | trirv 0ed086e | 双后端 __builtin_tri_lit |
| trirv_cc __tint2 | trirv 43ccd3d | 批A 存取即窄化 |
| trirv_cc __tint/__tbool | trirv bd71a27 | 批B Kleene 门控 |
| py tint 局部 | trirv 8a7215f | 参考后端局部 |
| py 全局变量 | trirv e110531 | TRV_CC=py 45/45 |
| trirv_cc 内建 | trirv 0fa2417 | 双后端 __builtin_tri_lit |
| T1~T7 py 语法 ×7 | trirv 18bb708..132233f | 详见 A 节 |
| T9 ecvt_tri 拆分 | trirv 83b4013 | 拼接式 |
| T10a+b+c-1 TC | trirv 8c531a0..9e177d6 | 设计+编码+PyVM 执行 |
| TRV2 原型 | trirv ff8f50a | tryte 流+密度测算 |

更早里程碑：见 ~/.zcode 记忆（C 方言本征化、未声明标识符、qsort 转正、
链接器三刀、弱符号/闭包、调试器 MVP、TF 浮点、TINT C1 等）。
