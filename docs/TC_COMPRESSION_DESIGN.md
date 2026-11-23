# TC 压缩指令扩展设计（§6.2，2026-11-23 T10a 立基；T10e 数据拍板 TC v2）

## 1. 动机与约束

TriRV 指令流按 **tryte（5-trit）编址**，每条 20-trit 指令占 4 tryte。
压缩目标 = 码长缩短；约束 = tryte 对齐根基不可破（16-trit = 3.2 tryte
非对齐，否决）。

**拍板格式：15-trit / 3-tryte 对齐短格式**（压缩率 75%，PC 增量
+12/+16 tryte 二值切换，取指第一 tryte 判宽）。

## 2. 指令流模型（变长流）

- imem 改为 tryte 粒度扁平数组（VM 内部仍按 word 存，取指走 tryte 偏移）。
- PC = tryte 偏移。
- 取指：读首 tryte → 判宽。首 tryte % 27 == 23（opcode 23）→ C 格式
  → 消费 3 tryte；否则 20-trit 普通指令 → 消费 4 tryte。
  （全宽词首 tryte % 27 == opcode ≤ 22 ≠ 23，判宽无歧义。）
- JAL/BRANCH 目标 = **tryte 偏移**（原 slot 偏移 × 4 等价迁移）。
  现有全量 .trv/拼接源不受影响：无 C 指令时流恒 4-tryte 对齐。

## 3. C 格式编码（15 trit；t0-t2=opcode 23，t3-t4=子格式）

**寄存器窗口（TC v2 canonical，isa.C_REGS）**：

| 索引 | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|---|
| 寄存器 | t0(5) | t1(6) | t2(7) | s0(8) | s1(9) | a0(10) | a1(11) | a2(12) |

> **v2 拍板依据（T10e 实测，2026-11-23）**：v1 采用 RVC ABI 窗口
> {a0-a3,s0,s1,x14,x15}，对 musl string+stdlib 语料（28 文件 7844 槽）
> 命中率仅 **0.8%**——本代码生成的热点寄存器是 t 系临时（表达式求值）
> 与 s0 帧（落槽局部），不是 ABI 参数窗。重映射到代码生成热点后，
> 五类合计命中 28.0%（TRV2 打包节省 7.0%）。

## 4. 五类指令语义（TC v2，与 20-trit 等价指令逐条对齐）

| 子格式 | C 格式 | 20-trit 等价 | 字段映射 |
|---|---|---|---|
| sf0 | C-ADDI rd_s, rs1_s, imm4 | ADDI rd, rs1, imm4 | t5-t7=rd_s, t8-t10=rs1_s, t11-t14=imm4（±40） |
| sf1 | C-LWs r_s, off7 | LW r, off(s0) | t5-t7=r_s, t8-t14=off7（±1093） |
| sf2 | C-SWs r_s, off7 | SW r, off(s0) | 同上 |
| sf3 | C-BEQ r_s, delta7 | BEQ r, zero, delta | t5-t7=r_s, t8-t14=delta7 |
| sf4 | C-BNE r_s, delta7 | BNE r, zero, delta | 同上 |

设计要点（均为语料实测驱动）：
- **sf0 双寄存器**：rd==rs1 即自增形态（`i=i+1`），无需独立自格式；
  imm4 ±40 覆盖循环步长/tryte 步幅（9/18/27）。v1 的单寄存器 imm7
  自形态只值 2.9%，双寄存器形态值 7.6%。
- **sf1/sf2 帧相对**：基址隐含 s0（帧指针）。落槽局部访存是最大
  可压缩面（LW_s0+SW_s0 = 16.4% 语料槽）；v1 的 rd==base 自形态在
  本代码生成下几乎不出现（目的寄存器恒为新配临时）。
- **sf3/sf4 对 zero 比较**：gen_brfalse 把全部关系分支汇入 t0
  （`SLT t0,a,b; BEQ t0,zero`），窗内命中 6.4% 语料槽。
- 语料域宽验证：分支步距 max 219 ≤ ±1093 ✓；帧偏移 100% 在 ±1093 内 ✓。

不在子集/超界的情形 → 发射 20-trit 普通指令（RVC 惯例：压缩失败
静默回退全宽，零正确性风险）。

## 5. opcode 分配

- opcode 23 = C 格式标识（取指判宽标记）
- opcode 24 = TC 组 2（预留：C-JAL 等扩展——3-trit 子格式重编（imm 降到
  6 trit，delta ±364 槽仍覆盖语料 max 219）可再开 4 槽，但与 sf0 双寄存器
  布局互斥（payload 预算），留待 B1 拍板）
- opcode 25 = TC 组 3（预留：C-BRANCH 变体扩展）
- opcode 26 = 机动

## 6. 子批次

| 批次 | 内容 | 验收 |
|---|---|---|
| T10b | isa/asm RED：trirv_isa.py C 格式 enc/dec + trirv_asm 变长布局 | 编解码自洽 |
| T10c | PyVM/CVM 变长取指 | 双 VM 差分 |
| T10d | trirv_cc TRV_COMPACT=1 编码期发射 | 契约 42 项 |
| T10e | 码长对比 + TC v2 数据拍板 | musl 语料 28.0% / 节省 7.0%（实测） |

## 7. TRV2 打包格式原型验证（2026-11-23）

- TRV2 头：`TRV2` magic + n_trytes + n_seg（兼容 TRV1 三字段布局）
- 体：tryte 流（C 指令 3 tryte / 全宽 4 tryte，首 tryte 判宽）
- 原型：4 指令程序 TRV1=32B → TRV2=31B（1/4 C 指令比 → +3% 节省）；
  30% C 占比 → ~10% 节省
- 解包器：tryte 流 → word[] imem（C→低 15 trit payload + 高 5 零填充）
  word 级一致 ✓；PyVM 可执行 ✓

## 8. T10e 码长对比结论（2026-11-23，musl string+stdlib 28 文件 7844 槽）

| 方案 | TC 率 | TRV2 打包节省 |
|---|---|---|
| v1 五类 + ABI 窗口（已废） | 0.8% | ~0.2% |
| v1 五类 + 窗口重映射 | 9.3% | ~2.3% |
| **v2 五类（窗口重映射+ADDI2+帧窗）** | **28.0%（实测）** | **7.0%（实测）** |
| 理论上限（+帧地址 ADDI 等） | ~30% | ~7.6% |

**15% 目标在 15-trit 预算下不可达**：剩余大头是非窗内双寄存器 ADDI
（33.6%——基址在 s2-s6 持久寄存器，payload 装不下 rd+rs1+有效 imm）、
JAL（7.5%，子格式槽满）、其他基址访存（6.2%）。进一步覆盖需 3-trit
子格式重编（牺牲 imm 宽度，且与 sf0 双寄存器互斥）→ B1 拍板项。
TC 机器（ISA/双 VM/编译器/契约 56 项）已全链路就绪，收益随代码生成
寄存器模型演进自动放大（如寄存器分配向窗口倾斜）。
