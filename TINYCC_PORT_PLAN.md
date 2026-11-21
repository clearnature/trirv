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

## 7. T-0 实施记录（2026-11-21，GO 判定）

**T-0.1 ✅**：原生 tcc（AArch64 Darwin）+ riscv64 交叉 tcc 双构建成功；riscv64-tcc
产出合法 RISC-V ELF（`file` 验证）。dev-c23 分支健康。

**T-0.2 ✅**：`TCC_TARGET_TRIRV` 注册（tcc.h 两遍 include 链 / tcctok.h / tcc.c
版本串 / Makefile DEF-trirv+TRIPLET+FILES / libtcc.c 第二遍 / config.h 守卫）。
**坑**：config.h 的目标 fallback 守卫链无 TRIRV → 交叉构建 fallback 到 ARM64
（arm64_pcs 混进二进制 + NB_REGS/PTR_SIZE 全错）——configure 脚本补链后解决。

**T-0.3 ✅ + T-0.4 ✅**：tri-gen.c（寄存器映射/算术/比较/访存/调用/分支）+
tri-link.c（TRV1 直写，绕过 ELF）+ tccelf 输出 hook。端到端 11 例双 VM 全绿：

| 用例 | 期望 | C VM | PyVM |
|---|---|---|---|
| return 42 | 42 | 42 ✓ | 42 ✓ |
| Σ square(0..10)（385&0xFF=129） | 129 | 129 ✓ | 129 ✓ |
| square(7)（跨函数调用） | 49 | 49 ✓ | 49 ✓ |
| while 循环求和 | 3 | 3 ✓ | 3 ✓ |
| 全局+if+char 数组+fib(10)+add3+int 数组 | 78 | 78 ✓ | 78 ✓ |
| 全局 int 初始化 | 8 | 8 ✓ | 8 ✓ |
| fib(10) 递归 | 55 | 55 ✓ | 55 ✓ |
| 三实参调用 | 6 | 6 ✓ | 6 ✓ |
| if(1) | 5 | 5 ✓ | 5 ✓ |
| char 数组声明 | 0 | 0 ✓ | 0 ✓ |
| char 数组读写 | 65 | 65 ✓ | 65 ✓ |

**T-0/T-1 五坑（全部修复并记档，防复发）**：
1. **config.h fallback**（见 T-0.2）——configure 守卫链补 TRIRV。
2. **C 的 `(unsigned)负数 % 2187` ≠ 三进制补码取模**：2^32 mod 2187 = 1102 ≠ 0，
   (unsigned)(-9)%2187 = 1093 ≠ 正确值 2178——imm 全部改符号安全取模
   `((x%N)+N)%N`（TRI_ENC_I/S/U 三宏 + TRI_JAL/gsym_addr/tri-link 三处调用点）。
   **方法论**：宏修好后 stale .o 不自动重编（头文件无依赖跟踪），必须 rm .o 强制重建；
   Makefile 已补依赖规则（tcc.o 依赖 LIBTCC_INC）。
3. **悬挂 if**：`#ifndef` 掏空 `tccelf_add_crtbegin` 的 if 体后，`return` 被吞成
   条件体——set_output_type 在 nostdlib/MEMORY 路径返回 TCCState 指针（ASLR 低字节
   = 变动退出码 32/96/208/224）。补 `{}` 包住 if 体。
4. **bss fwrite**：TRV1 writer 对 bss/common 段 fwrite(sec->data=NULL) → SEGV——
   VM 内存零初始化，bss/common 不写文件。
5. **CValue.i 是 uint64_t（T-1.1 审计修复，用户指正）**：load() 常量范围检查
   `sv->c.i >= -(int64_t)2147483647` 因 usual arithmetic conversions（负 int64
   转巨大无符号）恒 FALSE——12345 误入错误分支。当时错误地撤掉了检查（作弊），
   经用户指正后恢复检查并加显式 `(int64_t)` 转换（所有 c.i 裸用点全部符号化审计）。

**T-1.1/1.2 修复记档**：
- **R 型编码错位**：gen_opil 曾误用 RISC-V funct3 语义直填 funct——SUB 发成
  SLL（0,0,1 vs 0,1,0）、SHL 发成 XOR、SRL/SAR/&/|/^ 全部无效编码。全部改走
  tri-isa.h 生成宏 TRI_<mn>_OP/FUNCT/FUNCT2（单一真相源）。
- **VT_CMP 物化**：SLT/SLTU 编码同错修正；SNEZ=SLTU rd,zero,rd。
- **二进制移位语义**：TriRV SLL/SRL/SRA 是三进制 ×3^k/÷3^k，C 的 << >> 是
  ×2^k/÷2^k——直译是语义错误。const 移位量改走 MUL/DIVU/DIV by 2^k 常量
  （方案 A 口径；DIV 向零截断与 gcc floor 的负数差异记档）；变量移位量
  UNSUPPORTED。实现：vtop[0].c.i = 2^k 后走标准 gv2 路径（避免手写栈操作泄漏）。
- **数据重定位**：char* 字符串全局初始化需要 .rela.data 处理——tri-link writer
  增加 tri_apply_data_relocs（R_DATA_PTR → 45-trit 字 base-243 回填，文本符号
  +20B 前移）；tccgen init_putv 的 PTR_SIZE==8 reloc 分支对 TRIRV 开放；
  tcc.h ELF 类宽对 TRIRV 强制 64-bit（Elf32_Rel 无 addend 字段会丢 addend）。

**T-0.5 GO/NO-GO：GO。** 工作量实测：T-0 全程约半天（含 5 个坑的定位），
tri-gen.c ~470 行 + tri-link.c ~215 行 + tri-isa.h 自动生成（96/96 与 isa.encode
逐位对齐验证）。D-T1（fork riscv64 后端）✓、D-T2（PTR_SIZE=9 数据模型）✓、
D-T3（TRV1 直写绕 ELF）✓、D-T4（trirv_isa.py 单一真相源）✓、D-T5（trirv_cc 保留
对照）✓ 全部落定。下一阶段 T-1（核心 ISA 覆盖）按计划推进。

## 8. T-1 收尾轮记档（2026-11-21，主智能体亲自执行，TDD 门控）

> 方法：`TCC_BEHAVIOR_NOTES.md`（四视角精读 tcc 机制 + 独立核实）→ `T1_TDD_PLAN.md`
> → 分批 RED（e2e 新用例实证缺陷）→ 修复 → GREEN（双 VM 门控）。
> 全程门控：`python3 trirv_tri_tcc_e2e.py`，最终 **163 PASS / 0 FAIL / 84 用例（含 5 NEG）**；
> 方案 A 守卫：产物中 SLL/SRL/SRA/ANDI/ORI/XORI 逐 trit 指令 **0 出现**。

**B1 错码速修（全部一行级，均带探针）**：
1. **D1 实参倒装**：`vrotb(ii+1)`→`vrotb(i+1)`（弹出 arg_{n-1-i} 装入它自己的寄存器）。
   **新坑**：逆旋转必须配对 `vrott(i+1)`——首修只改 vrotb 漏改 vrott，栈逐轮搅乱
   （add3 106=100+3+3 暴露）。上游配对：riscv64-gen.c vrotb(i+1)+info[n-1-i]。
2. **D2 物化比较表**：GT(1,0)/GE(0,1)/LE(1,1)（旧表 GT 物化成 a>=b 等 3 类全错）。
3. **D3 SNEZ 反序**：TRI_ENC_R 第 3 参是 rs2——曾写 (0,rd) 编成 SLTU rd,rd,zero ≡ 恒 0。
4. **D4 NE/EQ 物化 SUB**：裸写 (TRI_OP_OP,0,b,a,1) 的 funct=1 是 SLL 非 SUB，改走宏。
5. **D5 load() fc**：VT_JMP 链头在 c.i——补 `fc=(int)(int64_t)sv->c.i`（对齐上游）。
6. **D8 JALR rd==rs1**：JALR 先写 rd 后读 rs1——目标经 ra 装载时改道 t0。

**B2 常量装载（D6）**：新通用 `tri_load_imm_hw(hw, unsigned __int128)`——3×20-trit
平衡分段（借位双向归一），负数 = 3^45+v 转正（raw 即位模式）；旧 LUI+ADDI 两槽路径
（负 hi 回绕/lo 借位单向/窗口误报）全删。顺带修 gen_opil fuse 的 `(int)fc` 截断
（VT_LLONG 常量 2^32+1 会截成 1 误融合）。

**B3 链接器/数据编码**：
1. **静态初值 base-243**（笔记 §1.6 critical）：init_putv TRIRV 分支 VT_INT/VT_LLONG/
   VT_PTR(非符号) → `tri_write_word45`（9 tryte，负数 3^45+v，__int128）；VT_SHORT →
   mod-59049；VT_BYTE → mod-243。旧 write32le/write64le 是 base-256——只有初值 <243
   时两种基数巧合一致（旧 e2e g=7/{10,20,30} 测不出的根因）。
2. **text 基址**：tri_get_section_base 设 `text_section->sh_addr = 20`，删两处散落 +20。
   **新坑（本轮最隐蔽）**：writer 的 _start JAL 公式 `main_addr/4 + 3` 假设 main_addr
   是 text 相对地址——tri_sym_addr 返回绝对地址后 +3 被重复补偿，JAL 跳过 main 序言、
   ra 未保存、ret 到 0、程序重启。修正：_start JAL 一律用 `es->st_value`（text 相对）。
3. **符号校验**：tri_sym_final 统一 UNDEF（weak→0/否则 unresolved）+ COMMON 干净报错 +
   shndx 界检查（曾对 UNDEF 取 sections[0]=NULL SIGSEGV）。代码引用（sym->c=0）报
   unresolved——**新坑：writer 期 get_tok_str(sym->v) 崩溃（EXC_BAD_ACCESS），该函数
   仅解析期安全，writer 不取名**。
4. **未覆盖 ALLOC 节诚实报错**；**新坑**：tcc 自生成 .eh_frame 历来被静默丢弃
   （VM 无展开器，无害）——检查放行 `.eh_` 前缀，其余用户节报错。
5. tccgen.c:8787（符号初值允许 VT_LLONG）/8830（addend 保留）补 TRIRV 豁免。

**B4 方案 A 适配层（C 位运算/移位二进制语义，对齐 ternary-isa-design.md §5.1）**：
1. **位游标**（tri_emit_bitop3，移植 trirv_cc emit_bitop3）：raw 值即位模式，p 从 2^71
   走 72 位（DIVU/2 递推），& = TMIN、| = TMAX、^ = |ta−tb|；工作寄存器用空闲硬件槽
   x3/x4/x6/x7/x9 + x5 scratch——位游标块是直线代码（无访存/调用），不需 callee-saved
   预扫描（tcc 单遍架构无法预扫，与 trirv_cc gate_check 方案的本质差异）。
2. **~a**：tccgen 把 ~a 折叠成 a^(-1)——gen_opil 拦截 → TILDE−a 一条 SUB（~0≠-1 是
   回绕必然结果，探针须用变量：`~0` 双常量被前端折叠测不到后端）。
3. **移位**：SHL = k≤10 ADD 链 / 否则 MUL by 2^k（k>62 用 128 位 pow2_mod）；SHR 无符号
   = DIVU（k≥72→0）；SAR = DIV+REM 负余数 floor 修正（-65>>3→-9）；变量移位量 = 单步
   循环（负计数按 0 次）；k<0/k>4096 编译期 tcc_error（NEG 门控）。产物守卫：SLL/SRL/
   SRA/ANDI/ORI/XORI 零出现（disassemble 扫描验证）。
   **新坑**：`SLT ac,ac,zero` 两处误写成 TRI_ENC_R(AC,AC)（第 3 参是 rs2）——编成
   (ac<ac)≡0，floor 修正永不触发。与 D3 同款宏参数序坑，TRI_ENC_R 调用点已全部复核。
4. e2e 翻转 3 个锁定值（与/或/异或的逐 trit 期望 → C 语义）+ 新增代数律锁定
   （幂等/零元/对合/交换 ✓、~0≠-1 回绕后果）。

**NEG 机制**：e2e 增加编译必须失败且 stderr 含子串的负用例（unresolved×2/自定义节/
移位量超限/移位量负），锁定诚实报错路径。

**修复过程被 TDD 实证拦截的 3 次自伤**（均为门控当场拦截，未漏网）：
vrott 配对遗漏（实参用例回归）、_start JAL 双重补偿（位游标探针程序重启）、
SLT 宏参序（floor 修正失效）。

**T-1 状态**：1.1/1.2/1.3/1.4 ✓、1.5 ✓（MUL/DIV/DIVU/REM/REMU 在用）、方案 A ✓、
1.8 ✓（gen_tri_isa_h.py --check 守卫 + trirv_tri_isa_guard.py 常绿套件，篡改检测已验证）；
1.6（F 扩展）/1.7（CSR）未做；MULH 3 条、A 扩展 9 条未做。

## 9. T-2 第一记档（2026-11-21，多 TU + musl→tri-tcc）

**T-2.1 多 TU（同进程）✅**：tcc 原生多文件单进程编译白拿——`trirv-tcc main.c lib.c
... -o out.trv` 符号经进程内 symtab 解析。**根治 §7.12 悬垂**：TRI_PATCHES 曾存 Sym*
到 writer 期——extern 声明与定义是不同 Sym（声明 c 恒 0）、static 符号的 Sym 在其 TU
后被回收（垃圾 c 实证 `symbol #1719946136`）。改为补丁创建时快照
`{name: strdup(get_tok_str(sym->v))（仅解析期安全）, elf_idx: sym->c}`，
writer 优先 elf_idx、未定义过的按名查 symtab（跨 TU 声明-定义分离也能解析）。
tri-isa.h 补丁表结构变更（生成器 FIXED 区同步 + 再生 + 守卫绿）。
e2e 新增多 TU 组 4 例（跨文件 extern/后续 TU static/共享全局/三文件链）。

**T-2.3 musl→tri-tcC 端到端 ✅（首里程碑）**：真 musl 上游 string 源码
（preprocess_musl.py 同管线）+ musl_tri 重写版 16 文件 → tri-tcc 同进程多 TU
→ TRV1 → 双 VM。新套件 `trirv_tri_musl_e2e.py`（3 组 30 断言全绿）：
memset/memcpy/strlen/strcmp/strncmp/memcmp/memchr/strcspn/strspn/strstr/memmem/
strsep/div/ldiv/lldiv/imaxdiv/qsort(函数指针)/strtol(2/8/10/16 进制) 全部
编译、链接、运行、断言通过。strtod/ecvt（TF64 整数编码）单独编译通过
（调用面待 F 扩展/浮点 ABI 决策）。

**TRVO 文本互通（T-2.1 的 trirv_cc 桥）评估后延后**：trirv_cc 导出的 TRVO
LINE 里全局引用是导出期数字地址（`LUI t1, 9`），导入器按 GLOB 顺序重排地址——
一致性依赖"导入单元先于主 TU 分配"的分配顺序巧合（atof.trvo 实证）；tri-tcc
若逐字模仿该约定即承接其脆弱性。多 TU 端到端（本节）已覆盖 musl 主线，
文本互通待 trirv_cc 导入器支持符号化引用后再接。

**新坑（防复发）**：e2e 框架编辑时 `for` 行被并进注释 → 循环体落进上一个
循环（多 TU 用例名出现在 NEG 检查、空 c 文件编译"意外成功"）——编辑测试
框架后先跑一遍确认循环结构。

## 10. T-1.6 F 扩展记档（2026-11-21，tri-tcc 浮点代码生成全绿）

**能力**：double/float/long double 统一 TF64 档（type_size 均 9 tryte）——
FADD/FSUB/FMUL/FDIV（funct2=1）、FEQ/FLT/FLE 比较物化（GT/GE 交换操作数，
结果 int 0/1 非 VT_CMP）、FLW/FSW（搬 raw 与档位无关）、FCVT.F/FCVT.W
双向转换、TOK_NEG = 0-x（FCVT.F 读整数 0 产 +0.0，再 FSUB；TriRV 无 FNEG）、
浮点实参 fa0-fa7（prolog FSW / call gv(RC_F)）、浮点常量经 tccgen 既有常量池
（gv 时转 rodata 左值）+ init_putv TF64 base-243 编码。freg 独立堆映射
tcc r8-15 → fa0-fa7，与整数 x0-x31 分离。
e2e 13 例全绿（197 PASS / 0 FAIL / 101 用例）；tri-musl 30 PASS 无回归。

**三处新坑（防复发）**：
1. **tri_cmp3 负指数**：`for(i<e) rhs*=3` 对负 e 得 rhs=1——修正环永真
   → 编译器死循环（float域 0.5 暴露）。修：负指数走 ×3^|e| 后
   tri_cmp_pow2（商+余数精确比较），修正环加 ±8 保险。
2. **tcc token 顺序**：TOK_GE=0x9d 在 TOK_LE=0x9e/TOK_GT=0x9f 之前——
   `op<=TOK_GE` 区间检查排除 LE/GT（"bad float op 159" 暴露）。比较族
   分派一律显式 switch（与 D2 物化表同源教训）。
3. **浮点常量池归 tccgen 管**：gv 时浮点常量已被前端转成 rodata 左值
   （tccgen.c:1910），常量字节由 init_putv 写入——TF64 编码必须接进
   init_putv 的 VT_FLOAT/VT_DOUBLE/VT_LDOUBLE（曾只接 gen 侧导致 rodata
   里是 IEEE base-256）。tri_tf64_raw 提为 ST_FUNC 共用。

**TF64 编码器**（tri_tf64_raw，ST_FUNC）：整数精确 round-half-up，
__int128 域 |exp3| ≤ 47（|v| ∈ [2.1e-7, 4.3e7]）；域外诚实报错
（bignum 编码器留待后续，覆盖 1e-9/1e8/1e300 类常量）。

**语义偏差记档（待 ISA 层拍板）**：
- **FCVT.W 是 round-to-nearest（VM 实现），非 C 的向零截断**——
  (int)3.7 → 4（变量路径）；常量路径被前端折叠成 host trunc（3）。
  同一表达式两条路径分歧（同 §1.5 char 截断双轨）。候选：ISA 加 FTRUNC
  或 tri-gen 软件截断（比较修正法）。
- -(-0.0) = +0.0（FSUB 0-x；IEEE 要求 -0.0）。
- C float 不降 TF32 档（精度损失不建模）；TF32 档（funct2=0 算术）留待后续。

**T-1.7 CSR 延后理由**：CSR 指令无 C 语言暴露面——tcc 的 CSR 生成需
inline asm（tri-asm.c，tccasm 目标后端，未写）或 __builtin_csr 钩子
（tccgen 侧新增）。ISA 层 6 条 CSR 已由 trirv_asm/trirv_csr_e2e 覆盖；
tri-tcc 侧待 inline-asm 决策后一并做。

## 11. ABI 两项拍板落地记档（2026-11-21，用户拍板：均取建议案）

**char 截断口径 = mod-243 全轨**：char=1 tryte，unsigned 0..242 / signed
-121..121（3^5 偶容量恰平衡），截断 mod-243/59049；ISO C 8-bit 语义不保留
（三值 C）。依据：硬件 SB/LB/LBU、trirv_cc:408/:2350、charsign 契约三方
本已一致，tri-tcc 是唯一例外——5 trit 容量选择是为了硬件接口与 int8 对齐
降延迟（桥接层事），C 语义跟容器（tryte）走。实现三处：gen_cast 常量折叠
（0xff 掩码→mod-243+符号阈值）、寄存器驻留值窄化 gen_cvt_tri_narrow
（REM+符号扩展；内存驻留值经 LBU 读低 tryte 天然 mod，无需扰动）、
存储路径本就 mod-243。short 同轨 mod-59049。
e2e 6 例锁定（含 (char)300→57 翻转、243 边界、R 值窄化 58、short 951）。

**struct 9-对齐**：成员起始一律 9-对齐、尺寸 9-圆整（ABI §6 = trirv_cc
struct_def 现状）。实现：TRI_ALIGN_UP/DOWN 整除式圆整宏（9 非 2 的幂，
掩码 (c+9-1)&-9 会清 bit3——struct{char c; int*p} 的 p 曾落 offset 1）
替换 tccgen 五处下行/两处上行掩码位点；type_size int/ptr/float/double
对齐 1→9；aligned(N) 属性退化为 no-op（属性编码 2^k-only 装不下 9，
predef max_align_t 的 aligned(16) 由此通过）。
e2e 6 例锁定（成员偏移 9/尺寸 18/数组步长/char 数组成员/嵌套/纯标量）。

**坑记档**：aligned 属性的 no-op 分支曾插在外层 next() 之后又包一层
if(tok=='(')——tok 已是 16，条件恒假致 16 未消费（"')' expected (got 16)"
全案例挂死）；no-op 必须自己消费 N 与 ')' 后 break。

**验证**：e2e 221 PASS / 0 FAIL / 113 用例（char 6 例 + struct 6 例）；
tri-musl 30 PASS 无回归；run_all_tests --fast 42 套全绿。
**解锁**：trirv_cc↔tri-tcc 结构体/char 语义互通成立，双工具链差分
（T-3.1）的前置已齐。

## 12. FCVT.W.T 拍板落地记档（2026-11-21，用户拍板：新增 rtz 变体）

**拍板**：新增 FCVT.W.T=(20,8,0,"R")——F-MEM funct 2-trit 恰余的最后 1 槽。
C 的 (int) cast 与未来 wasm trunc 发它；FCVT.W（round-to-nearest）保留给
数学转换。依据：RISC-V 规范对 FCVT 强制 rtz 正因 C cast 语义（gcc/llvm 发
fcvt.w.s rtz）；"参考 RISC-V 只换进制层"原则适用；round 语义当时零真实
消费者（wasm 管线没用 FCVT，浮点 libc 走整数编码）。ISA 96→97 条。

**实现**：trirv_isa.py（真相源 +1）→ gen_tri_isa_h.py 再生（守卫绿）→
两 VM（PyVM int() 截断；C VM (__int128_t)val C 式截断——**坑：computed-goto
引擎的 &&label 分发表必须同步加 [OP_FCVTW_T]，漏了 = goto NULL 段错误**，
switch 引擎的 case 体不会报错）→ tri-gen gen_cvt_ftoi 换 FCVT.W.T 一行。

**双轨分歧解决**：(int)3.7 变量路径 3 = 常量折叠路径 3（同 char mod-243
的修复模式——语义跟容器/指令走）。

**验证**：e2e 225 PASS / 0 FAIL / 115 用例（rtz 三向判别：3.7→3、
-3.7→-3、3.5→3）；tri-musl 30 PASS；run_all_tests --fast 42 套全绿
（PGO VM 重建后）。

