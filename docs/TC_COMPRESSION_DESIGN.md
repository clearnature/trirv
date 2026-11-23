# TC 压缩指令扩展设计（§6.2，2026-11-23 T10a）

## 1. 动机与约束

TriRV 指令流按 **tryte（5-trit）编址**，每条 20-trit 指令占 4 tryte。
压缩目标 = 码长缩短；约束 = tryte 对齐根基不可破（16-trit = 3.2 tryte
非对齐，否决）。

**拍板格式：15-trit / 3-tryte 对齐短格式**（压缩率 75%，PC 增量
+12/+16 tryte 二值切换，取指第一 tryte 判宽）。

## 2. 指令流模型（变长流）

- imem 改为 tryte 粒度扁平数组（VM 内部仍按 word 存，取指走 tryte 偏移）。
- PC = tryte 偏移。
- 取指：读首 tryte → 判宽。首 tryte 值 **≥ 200** → 15-trit C 格式
  （首 tryte 的 t0-t2 = opcode 23-26 区间，t3-t4 = 固定标记 11₂₂₂₃₃₃
  编码内嵌）→ 消费 3 tryte；否则 20-trit 普通指令 → 消费 4 tryte。
- JAL/BRANCH 目标 = **tryte 偏移**（原 slot 偏移 × 4 等价迁移）。
  现有全量 .trv/拼接源不受影响：无 C 指令时流恒 4-tryte 对齐。

## 3. C 格式编码（15 trit = 5 tryte × 3 trit）

| 字段 | trit 位 | 宽 | 说明 |
|---|---|---|---|
| 标记 | t0-t2 | 3 | opcode = 23（固定：C 格式标识） |
| 格式 | t3-t4 | 2 | 0=C-ADDI 1=C-LW 2=C-SW 3=C-BEQ |
| rd/rs1 | t5-t7 | 3 | 0..26（常用子集静态映射） |
| rs2/imm lo | t8-t10 | 3 | 0..26 |
| imm hi | t11-t14 | 4 | 0..80（与 t8-t10 合成 7-trit imm） |

**静态寄存器子集映射**（RVC 惯例：只压高频）：
- C-ADDI：rd ∈ {t0-t3, s0-s2, a0-a2} 8 个高频槽（3-trit 索引映射表）
- C-LW/C-SW：base ∈ {sp, s0, tp} 3 个 + dst/src 8 个
- C-BEQ：rs1 ∈ {zero, t0-t3} 4 个 + 7-trit imm

不在子集/超界的情形 → 发射 20-trit 普通指令（RVC 惯例：压缩失败
静默回退全宽，零正确性风险）。

## 4. 五类指令语义（与 20-trit 等价指令逐条对齐）

| C 格式 | 20-trit 等价 | 字段映射 |
|---|---|---|
| C-ADDI rd, imm7 | ADDI rd, rd, imm7 | t5-t7=rd_s, t8-t14=imm7 |
| C-LW rd, imm7(base) | LW rd, imm7(base) | t5-t7=rd_s, t8-t10=base_s, t11-t14=imm7 |
| C-SW src, imm7(base) | SW src, imm7(base) | 同上 |
| C-BEQ rs1, imm7 | BEQ rs1, zero, imm7 | t5-t7=rs1_s, t8-t14=imm7 |
| C-BNE rs1, imm7 | BNE rs1, zero, imm7 | 同上 |

（C-BEQ/C-BNE 对 zero 比较——高频 `if (x == 0)` / `if (x)` 形态；
非零比较发全宽 BEQ/BNE。）

## 5. opcode 分配

- opcode 23 = C 格式标识（取指判宽标记）
- opcode 24 = TC 组 2（预留：C-LOAD/STORE 变体扩展）
- opcode 25 = TC 组 3（预留：C-BRANCH 变体扩展）
- opcode 26 = 机动

## 6. 子批次

| 批次 | 内容 | 验收 |
|---|---|---|
| T10b | isa/asm RED：trirv_isa.py C 格式 enc/dec + trirv_asm 变长布局 | 编解码自洽 |
| T10c | PyVM/CVM 变长取指 | 双 VM 差分 |
| T10d | trirv_cc `-mcompact` 发射 | 产物含 C 指令 |
| T10e | 码长对比 | musl_tri 热点 ≥ 20% 缩减 |
