# TriRV 项目审核简报（外部模型审核/诊断用）

> **读者**：对本项目零上下文的外部审核模型。
> **原则**：每条声明附验证锚点（文件/命令/提交号）；第 9 节是维护者自评
> 弱点清单，请优先拷问。撰写日 2026-11-23，HEAD=66a8e6e。

## 0. 快速验证入口

```bash
cd /Users/zesheng/ai-lab/trirv
python3 tests/run_all_tests.py          # 全量回归（~115s，期望 PASS 58 / FAIL 0）
git log --oneline | wc -l               # 161 次提交
wc -l cc/trirv_cc.c vm/trirv_vm.c isa/trirv_isa.py   # 核心代码
TRV_COMPACT=1 cc/bin/trirv_cc demo.c demo.trv2       # TRV2 压缩发射
vm/bin/trirv_vm -a 2 amp_demo.trv -                  # AMP 双核
```

双仓布局：`trirv`（本仓，161 commits）+ `trirv/tinycc`（TinyCC fork，
分支 trirv，累计 3870 commits（上游主体+本项目增量）；HEAD 21410af4）。

## 1. 项目定位与已拍板方向

**TriRV = 45-trit（三进制 trit）CPU 虚拟硬件平台 + 全软件栈**：
ISA → 双 VM → 三编译器 → 链接器 → musl 移植 libc → 调试器 → 固件面。

已拍板（2026-11-23，用户定案，docs/TASKS.md §B0）：
- **虚拟化分层**：TriRV 隔离底层真实硬件；纯虚拟硬件+软件先行，
  硬件直通后置；syscall 表 = 虚拟设备接口（virtio 半虚拟化同构）。
- **战略主线**（ISA 决策 D3，isa/trirv_isa.py ISA_DECISIONS）：
  「以开源 RISC-V 为规范标准、只换进制、不另建生态」——32 寄存器
  对齐 x0-x31、syscall 编号照抄 Linux、psABI 概念对齐。
- **C 方言三值本征**（2026-11-22）：三值类型（__tint/__tint2/__tbool）
  以本征 C 类型落地，拒绝「VM 二元语义」表述纪律（见 ~/.zcode 记忆
  trirv-pure-ternary-phrasing）。

## 2. 架构总览

```
┌─ C 方言（限制子集，见 §4.1）── 三编译器同构 ──────────────┐
│ tri-tcc(TinyCC fork)   trirv_cc.c(自研)   trirv_cc.py(参考) │
└──────────────┬──────────────────────────────────────────┘
               ▼
     TRVO 文本目标格式 ── 增量链接（61+ 单元实测）──►
               ▼
  容器：TRV1（槽制 4-tryte/指令）│ TRV2（tryte 流，TC 3/全宽 4）
               ▼
  ┌──────────────────────────────────────────┐
  │ PyVM（参考语义）      CVM（性能，OpenMP）  │ ← 双 VM 差分纪律
  │ TRV1 槽制引擎 + TRV2 流制引擎（双格式双引擎）│
  └──────────────────────────────────────────┘
               ▼
  syscall 面：Linux 编号照抄 + 5000 扩展区（8-bit 视图/AMP）
  musl_tri libc（20 三进制原生单元 + 65/66 上游编译通过）
```

关键文件锚点：
- ISA 真相源：`isa/trirv_isa.py`（编码/解码/TC/ISA_DECISIONS D1-D5）
- C 编译器（自研）：`cc/trirv_cc.c`（~4200 行，单文件）
- 参考后端：`cc/trirv_cc.py`（~1500 行）
- VM×2：`vm/trirv_vm.py`（~1000 行）/ `vm/trirv_vm.c`（~1450 行）
- 独立汇编器：`isa/trirv_asm.py`
- tri-tcc 增量：`trirv/tinycc/tri-gen.c`+`tri-link.c`+`tri-isa.h`（~1.4K 行）
- libc：`libc-musl/musl_tri/`（20 单元）；批量管线 `libc-musl/batch_musl_compile.py`

## 3. ISA 与核心语义决策

**基底**：指令 20 trit = 4 tryte；tryte = 5 trit（243 值）；寄存器字
45 trit = 9 tryte；32 寄存器（4-trit 字段）；3 补码符号体系（模 3^45，
无 INT_MIN 角例）。**全幅差分 fuzz 已验证**（ALU/访存/原子/CSR 四类，
tests/trirv_fuzz*.py 共 3300 随机指令/次）。

D1-D5 拍板（isa/trirv_isa.py ISA_DECISIONS，有完整 rejected/reason）：
- D1 指令字长 20 trit（恰填 4 tryte）；D2 3 补码（拒平衡三进制——
  模 3^45 无溢出角例）；D3 32 寄存器（撤销 27 方案，对齐 RISC-V）；
  D4 立即数 imm7/imm13（大常数走 LUI+ADDI 分段）；D5 字长 45 trit
  （拒 40，long=pointer 同宽）。

**指令面**（ISA 一致性守卫 103 项，tests/trirv_tri_isa_guard.py）：
102 条全宽指令 = RISC-V 基本集（算术/访存/分支/跳转）+ M 扩展 + A 扩展
（LR/SC/AMO）+ Zicsr/PRIV（M 态 trap/mret/mtimer）+ F 扩展（TF45/TF20
三进制浮点，三进制编码 (sign,mant,exp3)）+ T-LOGIC/T-PACK（三值本征
TAND/TOR/TNOT/TMIN/TMAX/TSEXT/TPACK）+ T-LIT（Post 文字运算 x^a）+
LW4/SW4（4-tryte 数据档）。

**TC 压缩 ISA v2**（2026-11-23 数据拍板，docs/TC_COMPRESSION_DESIGN.md）：
15-trit / 3-tryte 短格式，首 tryte %27≥23 判宽（全宽 opcode 仅 0-22，
判别无歧义）。五类子格式：C-ADDI（窗内双寄存器 ±40）/ C-LWs/C-SWs
（基址隐含 s0 帧指针）/ C-BEQ/C-BNE（vs zero）。寄存器窗口
C_REGS={t0,t1,t2,s0,s1,a0,a1,a2}——**由语料实测拍板**（v1 ABI 窗口
命中率 0.8%，v2 28.0%）。opcode 24-26 预留 TC 组 2/3。

**TRV2 容器**（B1① 拍板落地）：tryte 流变长指令（TC 3/全宽 4），
跳转 imm=tryte delta。双格式双引擎：TRV1 槽制引擎保留（生态零改动），
TRV2 流制引擎并行（PyVM `VM.from_stream` / CVM packed 取指）；
编译器按 `.trv2` 后缀 / `TRV_FORMAT=2` 发射。
TRV1 二进制**不做自动迁移**（无符号表，烘焙槽制地址常量不可靠识别）
——测试用 `isa.migrate_trv1_stream`（跳转重写）做等价验证。

## 4. 软件栈现状

### 4.1 三编译器同构
同一 C 方言三个实现，契约=「同源码→同退出码/同行为」：
- **trirv_cc.c**（主力）：寄存器式代码生成（局部驻留寄存器分配、
  叶子函数 a 系免保存）、TC 编码期压缩选取（TRV_COMPACT=1）、
  .trv/.trv2/TRVO 三输出、弱符号/闭包导出/多单元链接。
- **trirv_cc.py**（参考实现）：教学子集+回退路径；T7b 起支持 fn-ptr
  声明符/间接调用；`TRV_CC=py` 强制，`TRV_CC_STRICT=1` 杜绝静默回退
  （回退伪装成「同构 PASS」是历史踩过的假绿坑）。
- **tri-tcc**（TinyCC fork 分支 trirv）：真 C 前端（88 例双 VM 契约 +
  musl 多 TU 30 断言 + 双工具链差分 22 组四路一致）。

**C 方言限制**（诚实清单，CC_寄存器式编译器说明.md）：
无 static/inline 链接语义、形参单 token 类型、无 sizeof、无 ULL 后缀、
trirv_cc 无 (type) 强转与 ?: 三元（py 后端有）、变参单层、switch 已有
（T6）。double 形参/返回 = TF45 raw（45-trit 字，guest 视角即 int）；
浮点字面量调用路径 trirv_cc 不支持（float libc 管线用 raw int 传参绕行）。

### 4.2 链接器
TRVO 文本目标格式（FUNC/PARAM/LINE/GLOB/STR/ALIAS/END）：
- 数据地址符号化（§5.7）：跨单元全局重排后仍正确；
- 同名合并 tentative/common 语义、extern 纯引用、弱符号（#pragma weak
  同款）、闭包导出（.trvo 链可传递）、61+ 单元增量链接实测
  （tests/trirv_trvo_e2e.py，25→30 项）；
- T9b 案：TRVO 导入全局类型悬垂（global_add 裸别名栈缓冲）已修
  （strdup），多单元直链==拼接轨迹级一致。

### 4.3 libc 与 syscall 面
- musl 上游 66 文件 65 编译通过（explicit_bzero 因内联汇编跳过——
  诚实 SKIP）+ 20 个三进制原生重写单元（musl_tri/：位图系、qsort、
  strtol、strtod、ecvt 系、arch 层 read8/write8+原子纯 C）。
- float libc 五函数（ecvt/fcvt/gcvt/strtod/atof）TF45 口径
  4928 项契约。
- syscall：Linux 编号照抄（musl 依赖）+ 5000 扩展区（read8/write8
  8-bit 视图；5010-5012 AMP）。musl 的 CHAR_BIT==8 由边界编解码满足，
  内存保持纯三进制（tryte 0..242）。

### 4.4 AMP 多核（T11）
mailbox syscall（coreid/msend/mrecv）+ 共享页窗口（地址空间顶部
64 tryte，保留符号 `__amp_shared`）+ 单步协作轮转调度（顺序一致性，
双 VM 同构：CVM `-a N` / PyVM `run_amp`）。错误口径：无此核 -3 /
邮箱满 -105 / 空 -2 / 畸形 -22。

### 4.5 其他
调试器 MVP（debugger/，18 项）；M 态 CSR/trap/mtimer（固件面基建）；
8-bit 视图层（octet↔双 tryte，13 项）。

## 5. 验证体系与实测数据

- **全量回归 58 套 / 连续 36 次全绿**（tests/run_all_tests.py；
  退出码+输出双判定，防「PASS 但 exit 0」假绿）。
- **双 VM 差分纪律**：PyVM vs CVM（trirv_difftest.py）+ CSR/trap 差分
  + M 扩展 700 项差分 + wasm 三路差分 112 例 + tryte 参考实现 46164 项
  + 随机 fuzz 3300 指令。
- **实测数据**（本周批次）：
  - TC v2 密度：musl string+stdlib 28 文件 7844 槽 → 28.0% TC 率；
  - TRV2 文件体积：同语料 32608B → 30414B = **6.7% 节省**
    （TC 理论 7.0% 吻合；strtod 系最高 8.7%）；
  - 目标差距：台账曾写「码长省 ~15%」——**在 15-trit 预算下不可达**
    （非窗内双寄存器 ADDI 占 33.6% + JAL 7.5% 装不下），已如实记账
    并给出 C-JAL 扩展路径（+7.5% → ~35%/8.8%）。
- **ASAN 纪律**（本周引入）：两次悬垂案（T9b global_add、parse_decl
  dtypes）均由 ASAN 一发命中；间歇红=未初始化读是本项目已被实证的
  失效形态。

## 6. 本周批次时间线（快速演进证据链）

| 批次 | 提交 | 内容 |
|---|---|---|
| T1-T8 | 18bb708..132233f | const/do-while/goto/三目/强转/switch/typedef（py+CC 后端） |
| T9 | 83b4013 | ecvt_tri 拆分共享核心（拼接式） |
| T10a-e | 8c531a0..881b702 | TC 压缩全链路：设计→ISA→双 VM→发射→**v2 数据拍板** |
| T11 | 7214135 | AMP mailbox+共享页+双 VM 多实例 |
| T9b | 590b585 | TRVO 直链自旋破案（悬垂 ty，一行 strdup） |
| T7b | f31b2af | py 后端 fn-ptr + &sym 定点展开 |
| B1① | 5446131+66a8e6e | TRV2 打包正式化 + **附带破案**（parse_decl 悬垂 base） |

更早里程碑（本征化/链接器三刀/弱符号/TF 浮点/TINT C1 等）：
~/.zcode 记忆 trirv-ternary-project.md（跨会话台账）。

## 7. 已知缺口与债务（诚实清单）

1. **TC 密度未达 15% 目标**：28%/6.7% 兑现即止；增益需 B1② C-JAL
   （+7.5%）或 B1③ 3-trit 子格式重编（与 sf0 双寄存器布局互斥，
   破坏性）。窗口 {t0-t2,s0,s1,a0-a2} 与本代码生成的寄存器分配器
   **耦合**——换代码生成器（如 LLVM）窗口命中率失效，需重测重拍。
2. **流制 B 型窗口窄**：delta7=±1093 tryte≈273 槽（槽制语义窗的 1/4）；
   语料最大步距 219 槽安全，但大函数（ec_core 1047 行）内回跳可能
   超窗——超界诚实报错，无 LUI+JALR 长链重写。
3. **C 后端 fn-ptr 缺口**：裸 (fp)(x) 形态、声明带初始化
   `int (*fp)(int)=f;`（py 后端支持前者、两后端均缺后者）。
4. **py 参考后端 tint 正向全量**未做（当前以语法错误拒绝——双后端
   拒绝纪律成立，但正向覆盖缺）。
5. **TRV1→TRV2 无自动迁移**：无符号表所致；长期双引擎维护成本
   （TRV1 何时退役未定）。
6. **调试器/固件未适配 TRV2**（默认 TRV1 不受影响，流制 PC 需适配）。
7. **tri-tcc 未接 TC v2/TRV2/AMP**：三编译器同构在压缩/AMP 面不完整
   （tri-tcc 的 __tri_syscall 是通用的，AMP 契约用例缺）。
8. **musl 覆盖**：string+stdlib 目录级覆盖；stdio/math 等未动。
9. **无 CI**：全量回归手动触发；macOS (clang/arm64) 单平台构建。
10. **AMP 是协作轮转非真并行**：顺序一致性由调度器保证；真并行
    （OpenMP+锁/核间中断/硬件共享窗）在 B3 菜单未开工。
11. **单维护者**：所有决策记录在 ~/.zcode 记忆 + docs/TASKS.md，
    无外部贡献者。

## 8. 未来工作（拍板菜单，docs/TASKS.md §B/C）

**B 节（进行中方向）**：
- B1② C-JAL=TC 组 2（opcode 24，加法零破坏，+7.5% 槽覆盖；
  参照 RVC C.JAL/Thumb BL）——**建议下一个批次**
- B1③ 3-trit 子格式重编（远期，负载取舍）
- B2 syscall ABI 冻结政策（零代码，定案即收官；RISC-V psABI 参照）
- B3 真并行三步：①OpenMP AMP（FreeRTOS SMP/Zephyr 参照）→
  ②核间中断（RISC-V CLINT/MSIP、OpenAMP 参照）→ ③共享窗硬件并行
  （RVWMO/MESI 参照）

**C 节（立项未开工）**：
- C1 LLVM 后端（参照 RISC-V/M68k 后端骨架；核心难题=LLVM 整数强制
  2 幂，45-trit≈2^71.3 → i128 容器+掩码或自定义 EVT）
- C2 WASM 深化（wasm3 元编译同构 → wasm→TriRV 直译）
- C3 固件（OpenSBI 同构，与 B3② 联动）
- C4 Post 代数应用层（无强开源参照，等消费方）
- C5 musl 覆盖扩展（填充批次）

## 9. 维护者自评：审核者应重点拷问的问题

1. **窗口-代码生成耦合**：TC 窗口 {t0-t2,s0,s1,a0-a2} 是按本编译器
   寄存器分配器实测拍的——这是「ISA 语义被单一实现绑架」还是合理
   的协同设计？RVC 的窗口是 ABI 忠实的。若未来换分配器/LLVM，
   窗口是否该重拍？ISA 层是否该预留窗口无关的编码？
2. **双引擎永久成本**：TRV1 引擎因「生态兼容」保留，但没有退役计划；
   双引擎的语义漂移风险（已有先例：PyVM/CVM 的 LUI 语义不一致，
   靠各自汇编器自洽掩盖——见 ~/.zcode 记忆坑实录）如何制度化防范？
3. **「RISC-V 对齐」的成色**：寄存器数/syscall 编号对齐了，但 B 型
   分支窗、指令字长、PC 语义（tryte vs 字节）、压缩窗选择都与
   RISC-V 实质不同——「只换进制不另建生态」的实际达成度如何？
4. **测试体系的假绿防御**：退出码+输出双判定、STRICT 回退门、
   NEG 闸门已建；但 58 套里差分类依赖 PyVM/CVM 互为参照——
   **两 VM 同源于同一理解时，共享错误不可检**。是否存在 ISA 级
   第三参照（黄金模型/形式化）？46164 项 tryte 参考实现算到什么层？
5. **单文件编译器（trirv_cc.c 4200 行）的可维护性**：无模块边界、
   全局状态多（NOUT/LABS/LOC…）、两次悬垂案都源于字符串生命周期
   管理——是否值得结构化重构，还是「能跑+ASAN+全绿」就好？
6. **性能主张**：CVM 宣称 MIPS 吞吐但无基准套件；寄存器式代码生成
   「消除栈搬运」无量化对比。性能声明是否可验证？
7. **AMP 顺序一致性 vs 真并行语义差**：协作轮转下测过的契约在
   OpenMP 真并行下是否仍成立（内存模型弱化的暴露面）？
8. **TRV2 迁移断层**：编译器默认仍发 TRV1；TRV2 需显式触发。
   何时切默认？切默认时 61 单元 musl 链、busybox、crt 链的回归
   面是否已盘点？
9. **文档 vs 代码漂移**：docs/ 15 份计划文档，部分（LLVM/WASM/
   firmware）是早期草案——声明「在库未开工」的东西实际可行度如何？
10. **依赖与可复现**：构建依赖宿主 clang/OpenMP（macOS）；fuzz 用
    固定 seed——种子泛化性是否验证过？
