# TriRV 决策记录（ADR）
状态：✅ ADR-01..33 已全部确认（2026-11-23 用户整批拍板）；新决策追加记录。

> 格式沿用开源通行的 Architecture Decision Record：一条记录 = 一个不轻易回退的决定
> + 理由 + 参照。**推翻旧决策不删旧记录**，新增一条并注明取代了谁。
> 本文件是「为什么这样定」的唯一权威；`docs/TASKS.md` 只管「还有什么活」。
>
> 状态：⏳ 待确认　✅ 已确认　❌ 已废案（保留供查阅）
>
> 2026-11-23 提出并于同日由用户整批确认 ADR-01..33（✅）。此前散落在各计划文档里的决策
> （ABI 的 D0–D3、ISA 的 D1–D5、TinyCC 的 D-T1..5、LLVM 的 D-L1..5）在此归并，
> 原文位置记在「出处」行。

## A. ISA 与 ABI

### ADR-01 ISA 变更纪律：基础冻结，只做加法扩展 ✅
- **决策**：D1–D5 与 102 条现有指令的编码和语义冻结。新能力只能以扩展加入，不改已有编码。
  每个扩展四件套齐全才算落地：规范条目（ternary-isa-design.md）、ISA 守卫用例、
  双 VM 实现、契约测试。
- **理由**：三套编译器、58 套回归、已发布的 TRV1/TRV2 容器都依赖现有编码；改编码是
  全链路返工，功能上零收益。
- **参照**：RISC-V 基础 ISA 冻结，新能力走扩展批准流程，已批准的规范不回改语义。
- **出处**：isa/trirv_isa.py 的 ISA_DECISIONS、ternary-isa-design.md §9（追认为纪律）。

### ADR-02 外部提案 RV-T 的数值规格不采纳 ✅
- **决策**：不采用 21-trit 字与指令、6-trit tryte、imm 8/14 trit、CSR 地址 4 trit。
- **理由**：21 trit 装不下 int64（需 41 trit）；21 与 6 不整除；CSR 4 trit 装不下 RISC-V
  标准编号。逐条对照与提案自身的错误见 TASKS.md §B4。
- **参照**：无（该提案没有参考实现，只有编码表与示意图）。

### ADR-03 压缩跳转 C-J / C-JAL 走 TC 子格式 sf5 ✅
- **决策**：不开 opcode 24。在 opcode 23 内新增子格式 sf5：t5 = 链接标志
  （0 → rd=zero 即 C-J；1 → rd=ra 即 C-JAL），t6–t14 = 9-trit 跳转量（±9841 tryte）。
  超窗回退全宽 JAL。
- **理由**：27 个 opcode 已用 24，只剩 24、25、26；子格式 sf5–sf8 都还空着，应先耗尽
  次级空间。语料里 JAL 占 7.5%，预计 TC 率从 28% 升到 35% 左右。
- **参照**：RISC-V C 扩展把全部压缩指令放在两个主 opcode 之下，靠 funct 字段区分；
  C.J 与 C.JAL。
- **取代**：TASKS.md B1② 的「C-JAL = TC 组 2（opcode 24）」与 B4a。
- **落点**：批次 1。

### ADR-04 3-trit 子格式重编：关闭 ✅
- **决策**：不做。今后的新压缩形态只走剩余子格式槽或新 opcode 组。
- **理由**：会破坏已提交的 TC v2 编码，且与 C-ADDI 双寄存器布局互斥；没有负载数据
  支持重编。
- **参照**：RISC-V 批准 RVC 之后只做加法子扩展（Zcb、Zcd、Zcf、Zcmp、Zcmt），没有重编。
- **取代**：TASKS.md B1③。

### ADR-05 加入 FENCE.I ✅
- **决策**：opcode 10、funct=1。统一内存的 VM 里是空操作；汇编器与两后端识别助记符。
- **理由**：占位成本极低；WASM/JIT 路线和自修改代码需要它作同步点，事后补会占用
  已发布的编码空间。
- **参照**：RISC-V Zifencei（FENCE.I 从基础 ISA 拆出的单指令扩展）。
- **落点**：批次 1。

### ADR-06 CSR 地址分区，保持 RISC-V 编号到 4095 ✅
- **决策**：低区 0–2186 沿用 opcode 16（imm7）。高区 2187–4373 用 opcode 9（SYSTEM）
  的 funct 2–7 编码同样六条 Zicsr 操作，地址 = imm7 + 2187。汇编器按地址自动选区。
  物理 CSR 新增只有 mhartid（AMP 核号）；身份类 CSR（mvendorid 等）读 0 即合规。
- **理由**：现有上限 2186，计数器类 CSR（mcycle 0xB00、cycle 0xC00）编不进；opcode 9 的
  funct 2–8 空闲，零 opcode 成本。
- **参照**：RISC-V CSR 地址 12 bit，编号一经分配不改；mvendorid/marchid/mimpid 允许为 0。
- **落点**：编码与守卫用例在批次 1；mhartid 在批次 4。

### ADR-07 JALR 对齐与移位量：定案，RTL 阶段不再重议 ✅
- **决策**：ISA 语义定为「跳转目标必须是合法指令起点，否则停机或陷入」。TRV2 流制引擎
  已如此；TRV1 槽制引擎的向下取整是历史兼容行为，保留但不扩展。移位量维持 7-trit
  无符号，≥45 时结果为 0 或符号位。
- **理由**：4-tryte 步长不是 3 的幂，三进制硬件无法用掩码对齐；向下取整会把错误目标
  变成「看起来合法」的跳转。
- **参照**：AArch64 的 BLR 遇到不对齐目标产生对齐故障；RISC-V 的 SLLI 移位量越界为
  非法指令。
- **取代**：TASKS.md B4d。
- **落点**：文档登记，批次 2。

### ADR-08 CSR 汇编操作数顺序：不改 ✅
- **决策**：维持现序 `CSRRW rd, rs1, csr`，登记进 RISC-V 差异表。
- **理由**：两种顺序的操作数都是寄存器或编号，无法无歧义地同时接受；改动只会破坏
  现有汇编用例。
- **落点**：文档登记，批次 2。

### ADR-09 syscall ABI 冻结 ✅
- **决策**：
  1. 寄存器约定：a7 = 调用号，a0–a5 = 参数，a0 = 返回值，负值表示 −errno（现行）。
  2. Linux asm-generic 编号照抄，语义照 Linux。
  3. TriRV 扩展区按族登记，只增、不改、不复用：
     5000–5009 8-bit 视图族（read8 5000、write8 5001）；
     5010–5019 AMP 族（coreid 5010、msend 5011、mrecv 5012；5013、5014 预留给 ADR-23）；
     5020–5099 虚拟设备族（未启用）。
  4. 未知调用号返回 −ENOSYS（38）（现行）。
  5. 已发布号的语义不改；要变语义就加新号。
- **理由**：syscall 表就是这个虚拟平台的设备接口，musl 与 AMP 程序都依赖它；事后改号
  等于破坏所有已发布程序。
- **参照**：Linux 系统调用号一经发布不复用；virtio 设备接口版本化并保留 legacy 支持；
  RISC-V psABI 的稳定流程。
- **取代**：TASKS.md B2 的「ABI 冻结政策」建议。
- **落点**：写入 ternary-abi-minimal.md §6，批次 2。

### ADR-10 目标文件格式：TRVO + TRV1/TRV2 为正式格式，不做 ELF ✅
- **决策**：不引入 ELF；ELF 随 LLVM 立项时再议。
- **理由**：ELF 的价值在 binutils 与 GDB 生态，当前没有消费者；TRVO 已支撑 61 单元
  增量链接。
- **出处**：ternary-abi-minimal.md §8 的「阶段 2 切 ELF」作废。

### ADR-11 二进制兼容层后置 ✅
- **决策**：不做二进制翻译层；不提供 int32/uint32 回绕语义（C 方言三值本征，72 位视图
  方案已于 2026-11-22 废案）。需要 32 位回绕的 libc 代码由移植层显式处理。进入硬件
  直通阶段时重议。
- **参照**：Rosetta 2、FEX-Emu、Box64 都是独立后置的翻译层。
- **出处**：TASKS.md B0、B2；TERNARY_COMPAT_PLAN.md §1。

## B. 工具链

### ADR-12 编译器格局：三套并存，角色固定 ✅
- **决策**：
  - tri-tcc = 真 C 前端（musl 多 TU、真实 C 语法）。只输出 TRV1，不做 TC 发射。
  - trirv_cc = 主力（TRVO 链接、TRV1/TRV2、TC 发射）。
  - trirv_cc.py = 参考栈，与 PyVM 一起充当教学与对照实现。不扩展 TC、TRV2、AMP、tint；
    特性只补作对照需要的。
- **理由**：tri-tcc 与 trirv_cc 是两个独立实现，互为差分对照，这比把三者做成一样更有
  价值；参考栈必须小才可信。
- **参照**：GCC 与 Clang 并存互验；CPython（参考实现，求简求对）与 PyPy（性能实现）的分工。
- **取代**：TASKS.md「前置缺口」里 py 后端 tint 正向全量一项（关闭）。

### ADR-13 TinyCC 路线 D-T1..D-T5 追认，tri-tcc 的 ABI 口径定案 ✅
- **决策**：D-T1..D-T5（fork riscv64-gen.c 为 tri-gen.c；PTR_SIZE=9；TRVO 与 TRV1 产物；
  trirv_isa.py 为真相源；trirv_cc 保留）追认为已确认。tri-tcc 的 ABI 口径以「与 trirv_cc
  实测一致」为准：常量折叠在 45-trit 域、char 取模 243、short 为 2 tryte、
  `& | ^ << >>` 为三值语义、struct 成员 9-tryte 对齐。
- **证据**：2026-11-23 八条探针双工具链结果一致——`100000*100000%1000` = 0；
  `(char)300` = 57；`1<<4` = 81；`12&10` = 9；`struct{char; int*}` 的指针成员偏移 9。
- **理由**：行为笔记 TCC_BEHAVIOR_NOTES.md 把 §1.3、§1.5、§5.1 标为待拍板，实际代码
  已按此实现，只剩文档没关账。
- **落点**：探针固化为契约，批次 2。

### ADR-14 TC 寄存器窗口冻结 ✅
- **决策**：窗口 {t0, t1, t2, s0, s1, a0, a1, a2} 是 ISA 规范的一部分，不随代码生成器
  重拍。TC 命中率列为编译器质量指标，每批次汇报。代码生成器想受益，靠寄存器分配向
  窗口倾斜。
- **理由**：窗口一旦进了编码就是 ABI；LLVM 路线下用分配提示就能适配，不必改 ISA。
- **参照**：LLVM 的 RISC-V 后端为可压缩寄存器提供分配提示（getRegisterAllocHints），
  让更多指令能压成 16 位。
- **出处**：PROJECT_AUDIT_BRIEF.md §9 问题 1。

### ADR-15 撤销「码长省 15%」硬指标 ✅
- **决策**：不设固定压缩率目标。每版本公布 musl 语料实测的 TC 率与字节数。当前
  28.0% / 6.7%。
- **理由**：15-trit 预算下结构性不可达；压缩率是负载相关的统计量，不适合作验收线。
- **参照**：RISC-V 文档给 RVC 的 25–30% 是负载相关的统计值，不作保证。
- **出处**：TC_COMPRESSION_DESIGN.md §8。

### ADR-16 长分支弛豫 ✅
- **决策**：装配层自动把超窗的条件分支改写为「反条件分支 + JAL」，两后端与 trirv_asm
  共用同一实现；TRV2 流制下迭代到布局稳定。只有连弛豫都放不下才报错。
- **理由**：流制 B 型窗口是 ±1093 tryte，语料最大跳距 219 槽折合最多 876 tryte；
  大函数必然超窗。
- **参照**：RISC-V 链接器的分支弛豫；Thumb 的长跳转 veneer；x86 的 2 字节跳转扩成
  5 字节跳转。
- **落点**：批次 3。

### ADR-17 TRV2 默认化与 TRV1 退役政策 ✅
- **决策**：四条准入全部满足后，trirv_cc 默认输出 TRV2（TRV_FORMAT=1 回退）：
  ① 长分支弛豫落地；② 调试器支持流制 PC；③ 61 单元 musl 链、busybox、crt 链在 TRV2
  下全过；④ 全部 C 端到端语料的 TRV1 与 TRV2 退出码一致。
  之后 TRV1 容器永久可读，不再为它加功能；tri-tcc 继续产出 TRV1。
- **理由**：双引擎的维护成本只能靠「一个默认、一个只读」收敛；准入条件把引擎漂移
  风险变成可测项。
- **参照**：RISC-V 在应用级 profile 里把 C 扩展列为必选，旧的非压缩代码仍然合法；
  Linux ABI 不破坏旧程序。
- **出处**：PROJECT_AUDIT_BRIEF.md §9 问题 2、8。

### ADR-18 LLVM 后端：现在不启动，并修正原计划的两处前提 ✅
- **决策**：前置条件是批次 1–3 完成。P-0 spike 必须回答下面的三问后才判 GO/NO-GO。
  原计划 D-L1..D-L5 逐条处置：
  - D-L1（fork + out-of-tree）：确认。
  - D-L2（数据模型）**修正**：用 i128 容器加目标自定义降低。原计划写「i45 原生字」
    不成立——LLVM 的 i45 是 45 bit，而 45 trit ≈ 2^71.3。
  - D-L3（产物）**修正**：先出 TriRV 汇编文本，交给既有装配器产出 TRV2 与 TRVO。
  - D-L4（前端）**修正**：不能用 stock clang。C 方言是三值本征（`1<<4` = 81、
    `12&10` = 9），而 clang 的常量折叠和 InstCombine 按二进制代数化简（`x^x` → 0、
    `x+x` → `x<<1`）。需要给 clang 打小补丁，把三值运算发成内建并跳过折叠。
  - D-L5（trirv_cc 保留为差分对照）：确认。
  - 三问：i128 容器的合法化路径是否可行；clang 补丁面有多大；相对 trirv_cc 的码长收益。
  - GO 条件：三方退出码一致不少于 50 例；musl string 与 stdlib 语料可编译；码长不超过
    trirv_cc 的 1.3 倍。
- **理由**：原计划把数据模型和前端想得过于现成；三值语义与 LLVM 的二进制优化管线
  有结构性冲突，必须先量化。
- **参照**：LLVM 官方的 out-of-tree 目标机制；M68k 后端回归主干（小团队维护的范例）。
- **落点**：批次 10。

### ADR-19 trirv_cc 结构：不整体重构 ✅
- **决策**：三条规则代替重构：进 AST 或元数据的字符串一律 strdup；新写的可分离的遍
  可以拆文件（如 TRV2 发射）；提交前过 ASan/UBSan 门禁（ADR-29）。
- **理由**：两次悬垂事故都源于字符串生命周期，门禁比重构对症；若 LLVM 路线成立，
  重构的投入会作废。
- **参照**：tcc、chibicc 这类小型编译器都是少文件加严格测试；sanitizer 是 LLVM 等项目
  的常规门禁。
- **出处**：PROJECT_AUDIT_BRIEF.md §9 问题 5。

### ADR-20 关闭 C 后端 fn-ptr 的两个缺口 ✅
- **决策**：支持裸 `(fp)(x)` 调用形态；支持声明带初始化 `int (*fp)(int) = f;`。
- **理由**：两种都是 musl 里常见的真实 C 写法，目前靠回退到 py 后端掩盖。
- **落点**：批次 2。

### ADR-21 wasm 路线：继续路径 A，验收改用官方测试集 ✅
- **决策**：继续自研翻译器。验收改用 WebAssembly 官方 spec testsuite 的通过数跟踪。
  补全顺序：call_indirect、br_table、table → 浮点剩余运算与 convert/trunc → 多参与
  多返回 → i64 → i16。i32 回绕用 Barrett 约减。
- **理由**：前两项决定真实 wasm 模块能不能跑；官方测试集给出与实现无关的完成度刻度。
- **参照**：WebAssembly spec testsuite（Apache-2.0）是 wabt 与 wasmtime 的一致性基线。
- **落点**：批次 9。

## C. 运行时与平台

### ADR-22 AMP 内存模型与执行模式 ✅
- **决策**：VM 级对共享窗和邮箱保证顺序一致。两种执行模式并存：确定性协作轮转是契约的
  规范模式；宿主多线程是可选性能模式，用一把全局粗粒度锁保持顺序一致。两种模式跑
  同一组终态契约。
- **理由**：顺序一致是 RVWMO 允许的实现，向后兼容；粗粒度锁最简单，不会引入竞态类。
- **参照**：QEMU 的确定性执行与多线程 TCG 并存；Zephyr 的 timetravel 测试模式。
- **出处**：PROJECT_AUDIT_BRIEF.md §9 问题 7；TASKS.md B3。

### ADR-23 AMP 推进顺序：先消除轮询，再上真并行 ✅
- **决策**：顺序改为三步：
  ① 新增 5013 mrecvw（阻塞收，mrecv 语义不变）与 5014 mipi（核间中断，按 RISC-V CLINT
  模型：置目标核的中断位，mie 使能，走既有 trap 路径）；
  ② 4 核契约、tri-tcc 的 AMP 用例、mhartid；
  ③ 宿主多线程。
- **理由**：B0 把 TriRV 定位为虚拟硬件平台，中断驱动才像硬件；轮询在真并行下只是空转。
- **参照**：OpenAMP 的 rpmsg 是中断驱动；RISC-V CLINT 的 MSIP；FreeRTOS 用任务通知
  替代轮询。
- **取代**：TASKS.md B3 的三步顺序与 T11b。
- **落点**：批次 4。

### ADR-24 固件：在现有框架上接真实定时器，特权态止于 M/U ✅
- **决策**：在已有框架（RMS 调度、优先级继承、看门狗、静态内存池）上推进：定时器按
  CLINT 模型接入 VM 的 mtimer 中断作 tick，建 ISR 向量表。特权态止于 M 态与 U 态，
  不做 S 态与 PMP；RVV 向量不立项。固件文档里「浮点、压缩未实现」两条已过时，更正。
- **理由**：S 态和 PMP 只有要跑通用 OS 才需要；嵌入式 RTOS 都在 M 态运行。
- **参照**：Zephyr 与 FreeRTOS 在 RISC-V 上默认跑在 M 态；OpenSBI 只在引导 Linux 这类
  S 态负载时才需要。
- **出处**：trirv-firmware-architecture.md §10、§13.5、§14.5；TASKS.md C3。

### ADR-25 musl 覆盖顺序 ✅
- **决策**：stdio → multibyte → time → legacy 与 locale。linux、network、process、signal
  目录不移植（需要 POSIX 进程模型，属 ADR-11 的后置范围）。
- **理由**：按 musl 目录间的依赖排序；stdio 是 busybox 从「能跑」到「好用」的关键。
- **参照**：musl 各目录的依赖顺序；picolibc 与 newlib 的最小 profile 划分。
- **落点**：批次 6–8。

### ADR-26 新立项「TF45 libm」 ✅
- **决策**：按 musl math 的函数清单，把算法改写到三值浮点（exp、log、sin、cos、pow 等；
  sqrt 已有 FSQRT）。排在 musl stdio、multibyte、time 之后。
- **理由**：musl 的 math 源自 FreeBSD msun，针对 IEEE 二进制格式，直接移植会算错。
- **落点**：批次 8 之后。

### ADR-27 Post 代数应用层：暂不立项 ✅
- **决策**：不立项。T-LIT 指令与内建已就绪，出现具体消费方再开。
- **理由**：没有可验证的需求，也没有参照实现。
- **出处**：TASKS.md C4。

## D. 验证与工程

### ADR-28 差分同源盲区的防线 ✅
- **决策**：两条制度化要求。
  ① 多轴差分：每个新引擎、格式、编译器都要与既有轴做随机程序差分。新增「槽制 ↔ 流制」
  双引擎 fuzz 与「TRV1 ↔ TRV2」全语料等价两项。
  ② 手算黄金：每条指令和扩展都要有不靠运行 VM 得出的期望值用例。PyVM 用 Python 大整数
  写成的算术是规范定义。形式化模型列为 LLVM 立项的前置。
- **理由**：PyVM 与 CVM 同源于一份理解，互相比对查不出共同错误。
- **参照**：RISC-V 的三层——riscv-isa-sim 黄金模型、ACT 合规测试（手写汇编加签名）、
  Sail 形式化模型。
- **出处**：PROJECT_AUDIT_BRIEF.md §9 问题 4。
- **落点**：①的双引擎 fuzz 在批次 2，全语料等价在批次 3；②每批次执行。

### ADR-29 提交门禁 ✅
- **决策**：每批次提交前跑三样：全量回归；用 ASan/UBSan 构建的编译器跑编译器相关套件；
  随机种子 fuzz 抽 3 个种子。
- **理由**：两次悬垂事故只有 ASan 能稳定抓到；固定种子的 fuzz 只覆盖一条路径。
- **参照**：sanitizer 与 fuzzing（libFuzzer、AFL++、OSS-Fuzz）是 LLVM 等项目的常规门禁。
- **落点**：批次 2。

### ADR-30 CI：配置先进仓，托管即生效 ✅
- **决策**：仓库目前没有远端。现在就落 `.github/workflows/ci.yml`（push 跑 `--fast`，
  每晚跑全量加随机种子）和等价的本地脚本。macOS 为主平台，Linux 由 CI 覆盖。
- **参照**：GitHub Actions 是开源项目的默认 CI，配置随仓库走。
- **落点**：批次 2。

### ADR-31 性能主张的口径 ✅
- **决策**：沿用 PERF_REPORT.md 的方法（同机、内部计时、每档 5 次取中位数）。新增固定
  微基准集（fib、qsort、串操作、memcpy），每个版本重跑并更新报告。所有性能主张必须
  出自这套 harness。
- **理由**：现有报告有数据，但没有随版本重跑的固定集，主张无法复核。
- **参照**：CoreMark、Embench 这类固定微基准加公开方法论。
- **落点**：批次 2。

### ADR-32 文档纪律 ✅
- **决策**：每份 docs 文档首部一行状态（草案、进行中、已收官、已过时）。决策一律记入
  本文件。本批校准：ternary-abi-minimal.md §6–§8（syscall 约定写的是 r15 与 r3–r7、
  short 写的是 2 byte、「阶段 2 切 ELF」，均与实现不符）；TCC_BEHAVIOR_NOTES.md 的
  ⚠ 三条关闭；固件文档 §14.5；TASKS.md 的陈旧缺口行（已于 2026-11-23 更正）。
- **参照**：Rust RFC 的状态标注；ADR 惯例。
- **落点**：批次 2。

## E. 路线图

### ADR-33 执行序 ✅
原则：先把门禁立起来，再做影响面最大的动作（切默认格式）；平台化先于前端扩展；
LLVM 放最后并设 GO/NO-GO。每个批次仍按 TDD → 全量回归 → 提交 → 台账与记忆更新。

| 批次 | 内容 | 依据 |
|---|---|---|
| 1 | ISA 小项：C-J/C-JAL sf5、FENCE.I、CSR 高区编码与守卫用例、手算黄金用例审计；重测 TC 密度 | ADR-03、05、06、28② |
| 2 | 质量门禁：ASan/UBSan 与随机种子门禁、CI 文件、双引擎 fuzz 轴、fn-ptr 两缺口、ABI 探针固化、微基准、文档校准 | ADR-07、08、09、13、20、28①、29、30、31、32 |
| 3 | TRV2 默认化：长分支弛豫、全语料 TRV1 ≡ TRV2、调试器流制 PC → 切默认 | ADR-16、17、28① |
| 4 | AMP：mrecvw、mipi、mhartid、4 核契约、tri-tcc 用例 | ADR-06、23 |
| 5 | 固件：真实定时器 tick 与 ISR 向量表 | ADR-24 |
| 6–8 | musl：stdio → multibyte → time | ADR-25 |
| 9 | wasm 补全，官方 spec testsuite 计数 | ADR-21 |
| 10 | LLVM P-0 spike，GO/NO-GO | ADR-18 |
| 之后 | TF45 libm、RTL（按 ADR-07）按需推进 | ADR-26 |

## F. 评审处置追加（2026-11-24，ARCH_PLAN_REVIEW 25 条）

### ADR-36 仓库远端 ✅
- tinycc trirv 分支：已推送 github.com/clearnature/tinycc（3fdd47a8..c77e0661，
  21 提交含三值本征全量）；本地代理 127.0.0.1:10808 已写入两仓 git config。
- trirv 主仓：✅ 已推送 github.com/clearnature/trirv（2026-11-24，190 提交
  rebase 到远端 LICENSE 初始提交之上；pre-commit 钩子与 ci.yml 随仓生效；
  本地代理 127.0.0.1:10808 已持久化）。注意：另存在 trirv-（带连字符）
  仓库属其他会话，勿混用。
- .gitmodules 已登记 tinycc 子模块（此前为无 .gitmodules 的 gitlink）。

### ADR-17 修订：性能准入 ✅（批准 2）
TRV2 默认化准入追加：TRV2 流制引擎相对 TRV1 慢 ≤1.15×（bench/fib 分支
密集口径，run_perf.py 测量）。现值 2.26×——**未达前不切默认**（3b.4
hint 缓存强化为对应任务）。
**2026-11-24 达标**：slot_hint 预测修正（bfb1eff）+ pc→si 直接映射缓存
（bisect 回退）后 fib 1.105×（5 次取最小）——准入四条 + 性能修订全部满足，
3b.2 切默认解锁。

### ADR-28 修订：规范模型提前 ✅（批准 4）
纯 Python 规范函数 + 编码空间全枚举提前至批次 3b.5，与 ADR-35（未定义
角落定死：非法指令统一陷入、TC reg_s≥8 统一回绕或陷入——择一后双 VM
对齐 + 守卫用例）合并执行。

### ADR-37 内存模型声明 ✅（批准 3）
「统一内存」规范化为统一**地址空间**（代码/数据物理分离是 VM 实现细节，
须入 trirv-unified-memory-abi.md——链接布局全局 200 起/栈 4096 依赖
分离前提，批次 3b 落文档）。
