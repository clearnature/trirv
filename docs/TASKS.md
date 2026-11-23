# TriRV 任务台账（动态管理）

> **口径**：本文件是任务队列的唯一权威视图。每批次收官时更新对应状态行
> 与「基线快照」；新任务先进「待办」，开工前在此登记拆解。历史决策与
> 坑实录以 ~/.zcode 记忆 + 各 PLAN 文档为准，本表只管「还有什么活」。

## 基线快照（2026-11-23 最新）

- 回归：python3 tests/run_all_tests.py → PASS 58 / FAIL 0（连续 30+ 次全绿）
- HEAD：trirv 66a8e6e（TRV2 正式化）/ tinycc 21410af4
- TC v2：窗口 {t0-t2,s0,s1,a0-a2} + 五类子格式（C-ADDI 双寄存器 /
  C-LWs/C-SWs 帧相对 / C-BEQ/C-BNE vs zero）——isa.C_REGS canonical
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
- [x] T10d TC 压缩发射（5412ae9 基础设施 → 9d5074a 文本层 peephole 原型 →
      bb19bcc 编码期选取收官：asm_line 五类选取点取代文本层改写，OUT 文本
      保持规范全宽；契约三票——语义等价/压缩确实发生/门控干净）
- [x] T10e 码长对比 + TC v2 数据拍板（musl string+stdlib 28 文件 7844 槽：
      v1 ABI 窗口命中 0.8% → v2 重映射窗口 {t0-t2,s0,s1,a0-a2} + ADDI2
      双寄存器 + 帧窗 C-LWs/C-SWs → **28.0% TC 率 / TRV2 打包节省 7.0%**；
      四类子格式全发射；15% 目标在 15-trit 预算下不可达——结构边界记账
      于 TC_COMPRESSION_DESIGN.md §8，C-JAL 等扩展留 B1 拍板）
- [x] T11 AMP 先行（mailbox syscall 5010-5012 + 共享页窗口 64 tryte +
      单步协作轮转调度——CVM `-a N` / PyVM run_amp 双 VM 同构；
      保留符号 `__amp_shared` 编译器绑定窗口地址；契约 10 项四票：
      mailbox 往返/共享窗加倍/窗口地址绑定/错误路径 -3 -2 自环）
- [ ] T11b AMP 扩展（记账：tri-tcc 侧 AMP 用例、>2 核契约、阻塞式 mrecv）
- [x] T7b py fn-ptr 收官（声明符 `int (*fp)(int)` + 函数名退化 `fp=f` +
      间接调用 `(*fp)(x)`/`(fp)(x)`；trirv_asm &sym 定点展开（ADDI/LUI+ADDI
      对齐 C 后端 sym_emit_seg）；契约 11 项双后端 STRICT 同构。
      记账：C 后端裸 `(fp)(x)` 形态与声明带初始化 declarator 不支持——回退已实证）
- [x] T9b TRVO 多单元直链自旋——破案修复（根因：global_add 的 g->ty 裸
      别名 TRVO 导入的栈上行缓冲，悬垂后类型塌成 int*（9 tryte）→ 数组
      全局互相重叠 → SD_E 常量被 g_ec_buf 初始化循环踩踏 → ec 边界永假
      自旋。修复 = global_add 内 strdup(ty)。契约：直链==拼接轨迹级一致
      （PyVM (1,True,20,19674) / CVM exit=1），trvo_e2e 25→30 项）

### 前置缺口记账（不阻断主线）

- py 参考后端 tint 正向全量（当前以语法错误拒绝——双后端拒绝纪律成立）
- trirv_cc.py switch / typedef / do-while / goto / 三目 / 强转 / const
  （py 参考后端教学子集差距——按需补；fn-ptr 已于 T7b 补齐）
- C 后端 fn-ptr 裸括号形态 (fp)(x) 与声明带初始化 int (*fp)(int)=f;（回退实证）

## B. 待办——需拍板才启动（附行业/开源参考意见）

### B0. 架构拍板（2026-11-23 用户定案）

虚拟化分层架构：TriRV = 隔离底层真实硬件的三进制虚拟硬件平台。
纯虚拟硬件+软件栈先行；硬件直通后置（行业默认不暴露底层硬件）。
syscall 表 = 虚拟设备接口（virtio 半虚拟化同构）。

### B1. TC 压缩扩展（TC v2 已收官，增益扩展三选待拍板）

TC v2（bb19bcc+881b702）：五类子格式 + 窗口 {t0-t2,s0,s1,a0-a2}，
musl 语料 28.0% TC 率 / TRV2 打包节省 7.0%（实测）。

**拍板项 ①TRV2 打包正式化——已拍板已收官（2026-11-23，5446131+66a8e6e）**
落地方案=**双格式双引擎**（RISC-V「C 是可选编码」同款）：TRV1 槽制引擎
原样保留（历史生态/57 套零改动），TRV2 原生 tryte 流引擎并行
（PyVM VM.from_stream / CVM load_trv TRV2 分支 + packed 取指）；编译器
.trv2 后缀 / TRV_FORMAT=2 发射。TRV1 二进制因无符号表（烘焙槽制地址
常量不可靠识别）不做自动迁移——测试用 isa.migrate_trv1_stream 做
跳转重写等价验证。
实测：musl 语料 TRV1 32608B → TRV2 30414B = **6.7% 节省兑现**
（TC v2 理论 7.0% 吻合）。契约 50 项（roundtrip/迁移等价/流制 TC 分支/
非对齐 JALR 诚实停机/.trv2 直发 sym 重写/双 VM/密度）。
附带破案：parse_decl 多声明符 dtypes 悬垂栈缓冲（ASAN
stack-use-after-scope）——batch_musl 间歇红根因（1cfd909→5446131）。
遗留记账：流制 B 型 delta7（±1093 tryte）比槽制窗口窄 4 倍，大函数
（>273 槽）内部回跳需诚实报错或 LUI+JALR 长链（语料 max 219 槽安全）；
调试器/固件默认 TRV1 不受影响。

**拍板项 ②C-JAL = TC 组 2（opcode 24）——建议次之（+7.5% 槽覆盖）**
设计：opcode 24 首 subformat = C-JAL delta7（ra 隐含，10-trit payload
3+7 恰好放下）——**加法扩展，零破坏已提交的 TC v2 编码**；判宽推广为
`首 tryte %27 >= 23`（全宽 opcode 仅用 0-22，23-26 恒空闲，无歧义）。
覆盖 +592 槽（7.5%）→ 合计 ~35%，节省 ~8.8%。
参照：RVC C.JAL（RV32；RV64 因 rtireturn 惯例裁撤——我们 ra 隐含同款
取舍）/ Thumb BL 长跳两段式（超 delta7 的调用回退全宽 JAL 即可）。

**拍板项 ③3-trit 子格式重编——建议远期（破坏性，负载取舍）**
9 子格式槽，imm 降 6 trit；与 sf0 双寄存器布局互斥（payload 预算）——
「调用密集 vs 指针运算」负载二选一。TC v2 刚提交，重编即全链返工；
等 TRV2 正式化后的真实负载分布数据再定。

参照：RISC-V RVC / Thumb-2 / MIPS16e；Termite / REBEL-6 / alopx.li wiki

### B2. 二进制兼容白名单——翻译层直通期后置；ABI 冻结政策建议现在定

翻译层本体（Rosetta 2 / FEX-Emu / Box64 惯例；REBEL-6 双模式学术参照）
按 B0 拍板后置，无需现在动工。
**建议现在拍板的可执行动作 = syscall ABI 冻结政策**（虚拟平台的
「设备接口契约」，零代码成本）：
- Linux 编号空间照抄（17/56/62/63/64/93/94/...——现状即如此，musl 依赖）；
- 5000+ TriRV 扩展区登记制：5000/5001（8-bit 视图）、5010-5012（AMP）
  已开张，新号只增不挪（Linux syscall 稳定先例：号码永不变语义）；
- .trv 容器 magic+version 字段纪律（TRV1 现状 → TRV2 升级通道已留）。
参照：RISC-V psABI 的 stability process（riscv-abi SIG 冻结流程）。

### B3. SMP——AMP 已先行（T11），真并行路径待拍板

已落地：mailbox + 共享窗 + 单步协作轮转（顺序一致性，契约可复现）。
**建议路径（三步递进）**：
① 真并行 AMP：CVM OpenMP 多线程 + hub 锁保护邮箱（CVM 已有
   SharedSync/-s 共享基建可复用）——参照 FreeRTOS SMP（2021 起官方
   SMP 内核）、Zephyr SMP 调度；
② 核间中断替代轮询：VM 已有 irq_timer + mstatus.MIE/MPIE + mtimer
   陷入基建，加 MSIP 型核间中断位——参照 RISC-V CLINT/MSIP 惯例、
   OpenAMP（rpmsg/remoteproc——AMP 的工业标准开源实现，T11 mailbox
   即其玩具版）；
③ 共享窗真硬件并行：访问点路由（load/store 分流 hub 窗口）替代步间
   同步——参照 RISC-V multi-hart + RVWMO（弱内存模型契约）+ MESI。

### B4. 外部建议「RV-T」对照复核（2026-11-23）——结论：维持 D1–D5，不迁移

对照对象：标准三进制 {0,1,2} + 3 补码 + Kleene 的 RISC-V 式提案（21-trit 字与
指令、6-trit tryte、I/S/B imm 8 trit、U/J imm 14 trit、CSR 地址 4 trit）。
- **一致**：数值制式、负数判定、取负、Kleene AND/OR（min/max）、opcode/rd/funct
  字段宽、LUI 结构（imm × 3^7）。
- **分歧且不采纳**：21-trit 字覆盖不了 int64（需 41 trit；5-trit tryte 对齐下 45 最小）；
  21 与 6-trit tryte 不整除（字与指令均 3.5 tryte）；PC 按 trit 计则分支仅 ±156 条
  （TriRV ±1093、RV32I ±1024）；CSR 4 trit 装不下 RISC-V 标准编号（mstatus=768>80）；
  funct 子码数值为纯外观差异（TriRV 连续重编），改动无收益。
- **提案自身错误**：字段宽三处不一致（架构图 5 trit vs 编码表 4 trit）；PC 口径
  自相矛盾（按指令计 vs 按 trit 计）；「极限压缩版」witness 在其声明的延迟槽前提下
  偶数 n 返回 (t2,t2)；「乘法无减捷径」不成立（2·X·3^i = X·3^(i+1) − X·3^i）；
  标题 41 条实为 47 条。
- **实证**：witness(n) 的 TriRV C 等价形式（`2 << k`）双 VM 通过，n=0..43 恒等式
  0 错（exit=44 = 0×100 + 22 偶 + 22 奇）。
- **由对照引出、待拍板**：
  - [ ] B4a C-JAL 改走 TC 子格式 sf5（opcode 23 内 t5–t14 共 10 trit 放跳转量），
        不占 opcode 24——27 个 opcode 已用 24，仅余 24/25/26（修正 B1② 原方案）
  - [ ] B4b FENCE.I 占位（Zifencei；统一内存下为空操作，WASM/JIT 路线前置）
  - [ ] B4c CSR 地址空间扩展方案（现 imm7 上限 2186：mcycle 0xB00=2816、
        cycle 0xC00=3072 编不进；当前实现 8 个）——C3 固件前置
  - [ ] B4d RTL 期备忘（现不动）：指令步长 4 tryte 非 3 的幂，JALR 对齐只能舍入
        不能掩码；移位量取 7 trit（0–2186）需比较器而非截断

## C. 大方向立项（计划文档在库，未开工；附开源参照）

- [ ] C1. LLVM 后端（docs/LLVM_BACKEND_PLAN.md）
  参照：llvm/lib/Target/RISC-V（规范标准骨架）、M68k（2022 回归的现代
  简洁后端，非主流架构融入主干的成功先例）、VE/BPF（小后端范例）。
  核心难题：LLVM 整数类型强制 2 幂（MVT）——45-trit≈2^71.3，可行路径
  = i128 容器+掩码（务实，浪费）或自定义 MVT/EVT（重写 legalization）。
  建议：WASM 前端垫稳后再启动，首步 = out-of-tree 目标骨架 + 三条
  冒烟指令（ADDI/JAL/ECALL）。
- [ ] C2. WASM 路线深化（docs/WASM_ROUTE_A.md；现状 112 例差分已通）
  参照：wabt、Binaryen（优化器）、wasm3（wasm→C 元编译先例——
  与「wasm→TriRV native 直译」同构）、wasmtime/Cranelift。
  深化方向：wasm→TriRV 直译器（借 TC 压缩输出）。
- [ ] C3. 固件架构（docs/trirv-firmware-architecture.md）
  参照：OpenSBI（RISC-V M 态固件——我们的 VM 已有 M 态 CSR/trap/
  mret/mtimer 同构基建）；coreboot/U-Boot 分阶段惯例。
  建议：与 B3② 核间中断联动（固件是中断分发者）。
- [ ] C4. 三值逻辑综合器 / Post 代数应用层
  开源无强参照（学术领域）；建议以 T-LIT 用户态库形态推进，等具体
  消费方（如三值电路仿真/EDA 需求）立项。
- [ ] C5. musl 覆盖面扩展（65/66 → 开放式）
  参照：musl 自身 src/* 目录构建序（string/stdlib 已完成，stdio/math/
  multibyte 待扩）；建议作为批次间隙的填充项。

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
