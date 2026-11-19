# ternary-isa-design

# TriRV——基于 RISC-V 哲学的原生三进制 ISA 设计

> 目标：在 45-trit 字长（9 tryte）、非平衡三进制（0/1/2）的原生 VM 上，定义一套类 RISC-V 的精简指令集。
> 命名：**TriRV**（Ternary RISC-V），基础整数扩展记作 **TI**（对应 RV32I 的 I）。

---

## 0. 从 RISC-V 继承什么（设计哲学）

RISC-V RV32I 的四条核心智慧，全部保留：

| RISC-V 原则 | TriRV 对应做法 |
| --- | --- |
| Load-Store 架构 | 只有 load/store 访存，ALU 只操作寄存器 |
| 固定指令长度（32 bit） | 固定 **20 trit（4 tryte）** |
| 寄存器字段位置在所有格式中固定 | rd / rs1 / funct / opcode 位置全格式一致 |
| 立即数符号位固定在最高位，便于并行符号扩展 | 立即数符号 trit 固定在指令最高 trit `t19` |
| 32 个通用寄存器，x0 硬连 0 | 32 个 45-trit 寄存器，x0 硬连 0（编号对齐 x0–x31） |
| 模块化扩展（I/M/A/F/C） | TI / TM / TA / TT（三值逻辑）/ TC（压缩） |

**编码差异**：RISC-V 用 5 bit 编码 32 个寄存器；三进制用 4 trit（$3^4=81$）编码同样的 32 个，
余下 49 码留作将来扩展。选择 4 trit 而非 3 trit（27 个），是为了与 RISC-V 的 x0–x31 逐一对应，
见 §9-D3。

---

## 1. 数据表示

| 项 | 定义 |
| --- | --- |
| 基数 | 非平衡三进制，trit ∈ {0,1,2} |
| tryte | 5 trit（$3^5=243$），物理存 1 字节（方案 C 打包，见 ABI 文档） |
| 字（word） | 45 trit = 9 tryte（寄存器宽度） |
| 无符号值 | $\sum d_i 3^i,\ d_i\in\{0,1,2\}$，范围 $0..3^{45}-1$ |
| 有符号值 | **3 的补码**：$-x \equiv 3^{45}-x$；范围 $\approx \pm1.48\times10^{21}$ |
| 地址 | 按 byte（tryte）编址，与 ABI 一致 |
| 立即数符号 | 最高 trit = 2 表示负（3 的补码延伸） |

> ⚠️ 非平衡三进制的 3 补码**没有干净的符号位**（正负数分界不在单一 trit 上），这与 RISC-V 二进制补码不同。符号扩展规则必须显式定义，见 §9-D2。

### 1.1 度量口径（哪些是定义，哪些才是成本）

三进制与二进制的规格差异**不是缺陷，是定义**。本项目所有取舍按下表分账，不得混谈：

| 账本 | 内容 | 判定方式 |
| --- | --- | --- |
| **定义层** | 基数、trit 数、值域、地址单位、指令字宽 | **不作优劣比较**。宽度可自由定义，值域不够加 trit 即可；"能装多大的数"推不出任何工程结论 |
| **语义层** | RV32I 指令语义、ABI、C 语义、异常/CSR 行为 | **必须一致**，由差分测试判定 |
| **实现层** | 静态指令数、动态步数、墙钟、单步成本 | **必须实测**，只认可复现数字 |

由这条口径直接得到两个结论：

- **$2^{32}$ 不是 3 的幂，所以任何三进制字宽都无法无损表示 i32 全域。** 这既不是
  "$243^4 < 2^{32}$ 吃亏"，也不是"45 trit 装得下 i64 占便宜"——两侧都只是定义选择，
  不能记成收益或亏损。
- 成本只有一种形态：**模拟外来二进制语义所需的额外指令**（mod $2^{32}$ 规整、
  base-243 打包、8-bit 位运算展开）。它按**指令条数**计量，不按位宽计量。
  反向亦然：二进制机器模拟三进制语义要付 base-243 打包税（实测 60.8ns vs 1.62ns）。

---

## 2. 指令格式（6 种，对齐 RISC-V R/I/S/B/U/J）

### 2.1 字段位置（t19 最高位 … t0 最低位，共 20 trit）

| 字段 | 位置 | 宽度 | 编码容量 | 说明 |
| --- | --- | --- | --- | --- |
| opcode | t2..t0 | 3 trit | 27 | 指令大类 |
| rd | t6..t3 | 4 trit | 81（用 32） | 目标寄存器 |
| funct | t8..t7 | 2 trit | 9 | 子功能（等价 funct3） |
| rs1 | t12..t9 | 4 trit | 81（用 32） | 源寄存器 1 |
| rs2 | t16..t13 | 4 trit | 81（用 32） | 源寄存器 2 |
| funct2 / imm | t19..t17 | 3 trit | 27 | 扩展功能或立即数高位 |

> **D3（2026-11-19 撤销 27 方案）**：rd/rs1/rs2 保持 4-trit（32 个寄存器），
> 吃掉原先空闲的 t6/t12/t16 三个 trit——20 trit 恰好用满：3+4+2+4+4+3 = 20。
> 立即数布局完全不变——I 型仍占 t19..t13，S/B 型仍占 t19..t17 + t6..t3，
> U/J 型仍占 t19..t7，故汇编器标签差值与编译器跳转零改动。

> 与 RISC-V 一样：**rd、funct、rs1、rs2、opcode 在所有格式中位置固定**，解码器可并行读寄存器。

### 2.2 六种格式布局

```
R-type (寄存器-寄存器 ALU):
  t19..t17 | t16..t13 | t12..t9 | t8..t7 | t6..t3 | t2..t0
  funct2   |   rs2    |   rs1   | funct  |   rd   | opcode
  (20 trit 全部用满，无空闲位)

I-type (立即数 / load / jalr):
  t19..t13 (7 trit)   | t12..t9 | t8..t7 | t6..t3 | t2..t0
  imm[6:0]            |   rs1   | funct  |   rd   | opcode
  (imm 占 t13..t19 = rs2 位置 4 trit + funct2 位置 3 trit)

S-type (store):
  t19..t17 | t16..t13 | t12..t9 | t8..t7 | t6..t3  | t2..t0
  imm[6:4] |   rs2    |   rs1   | funct  | imm[3:0]| opcode
  (低段 imm 复用 rd 字段位置——S 型无 rd)

B-type (分支，立即数打乱以固定 rs 位置):
  t19..t17 | t16..t13 | t12..t9 | t8..t7 | t6..t3        | t2..t0
  imm[6|4:1] |  rs2   |   rs1   | funct  | imm[5|3:0]    | opcode
  (rs2/rs1 同 S-type；低段 imm 复用 rd 位置)

U-type (lui / auipc):
  t19..t7 (13 trit)   | t6..t3 | t2..t0
  imm[12:0]           |   rd   | opcode

J-type (jal):
  t19..t7 (13 trit)   | t6..t3 | t2..t0
  imm[12:0]           |   rd   | opcode
```

**立即数宽度**：I/S/B 为 7 trit（$3^7=2187\approx2^{11}$）；U/J 为 13 trit（$3^{13}\approx2^{20.6}$）。

---

## 3. 寄存器文件

| 编号 | ABI 名 | 用途 |
| --- | --- | --- |
| x0 | zero | 硬连 0 |
| x1 | ra | 返回地址 |
| x2 | sp | 栈指针 |
| x3 | gp | 全局指针 |
| x4 | tp | 线程指针 |
| x5–x7 | t0–t2 | 临时 |
| x8 | s0 / fp | 保存 / 帧指针 |
| x9 | s1 | 保存 |
| x10–x17 | a0–a7 | 参数/返回值 |
| x18–x27 | s2–s11 | 保存 |
| x28–x31 | t3–t6 | 临时 |

共 **32 个**，编号与别名**与 RISC-V 完全一致**（`zero ra sp gp tp t0-t6 s0-s11 a0-a7`），
可直接复用 RISC-V 的 ABI 文档、汇编语法与 LLVM 后端。另有 PC（45 trit），**按 tryte 地址编址**：
指令 `i` 位于地址 `4*i`（20 trit = 4 tryte），顺序执行 `pc+=4`；分支/跳转立即数为指令槽偏移，
详见 §11.3。

---

## 4. 基础整数指令集 TI（对应 RV32I 的 40 条）

### 4.1 ALU（R-type，opcode=0）

`ADD, SUB, SLL, SLT, SLTU, XOR, SRL, SRA, OR, AND, TMIN, TMAX`（后两条属 TT 扩展）

### 4.2 ALU 立即数（I-type，opcode=1）

`ADDI, SLTI, SLTIU, XORI, ORI, ANDI, SLLI, SRLI, SRAI`

### 4.3 Load（I-type，opcode=2）

`LB, LH, LW, LBU, LHU` —— 按 tryte/2tryte/9tryte 取数。

> **符号性约定（与 RISC-V 完全一致）**：`LB`/`LH` **符号扩展**，`LBU`/`LHU` **零扩展**。
> 5-trit tryte 的符号区间是 −121..121（半阈值 `(3⁵−1)/2 = 121`），
> 10-trit 半字是 −29524..29524（半阈值 `(3¹⁰−1)/2 = 29524`）。
>
> 至于 `char` 本身：**它是无符号的 0..242**，但这不是对 RISC-V 的偏离——RISC-V psABI
> 同样规定 plain `char` 无符号（GCC riscv 后端 `DEFAULT_SIGNED_CHAR = 0`），TriRV 的
> tryte 值域与之天然吻合。因此编译器对 `char`/`unsigned char` 发 `LBU`，
> 只有显式 `signed char` 才发 `LB`。`TSEXT` 因此退化为可选的便利指令（TT 扩展成员），
> 不再是 char 语义的必需品。

### 4.4 Store（S-type，opcode=3）

`SB, SH, SW`

### 4.5 Branch（B-type，opcode=4）

`BEQ, BNE, BLT, BGE, BLTU, BGEU`

### 4.6 Jump（opcode=5/6）

`JAL`（J-type）、`JALR`（I-type）

### 4.7 高位立即数（U-type，opcode=7/8）

`LUI`（load upper immediate）、`AUIPC`（add upper immediate to PC）

### 4.8 系统（opcode=9）

`ECALL, EBREAK, FENCE`

---

## 5. 三值逻辑扩展 TT（TriRV 独有，这是亮点）

非平衡三进制的 0/1/2 天然支持**三值逻辑**，这是二进制 ISA 没有的能力。借鉴 Kleene 强三值逻辑（0=假, 2=真, 1=未知）：

| 指令 | 语义（逐 trit） | 逻辑含义 |
| --- | --- | --- |
| `TMIN` | $\min(a_i,b_i)$ | Kleene AND（假优先） |
| `TMAX` | $\max(a_i,b_i)$ | Kleene OR（真优先） |
| `TNOT` | $2-a_i$ | 逻辑非（0↔2, 1→1） |
| `TOR` | Kleene 或 | 见真值表 |
| `TAND` | Kleene 与 | 见真值表 |
| `TNEG` | 3 补码取负 | 算术求反 |
| `TPACK` | 5 trit → 1 byte | 方案 C 打包 |
| `TUNPACK` | 1 byte → 5 trit | 方案 C 解包 |
| `TSEXT` | 取 rs1 **低 5 trit（1 tryte）**按 3 补码符号扩展到 45 trit | 便利指令（`LB` 已能直接符号扩展）；保留供 TT 扩展与手工位操作使用 |

**Kleene 真值表**（AND = min, OR = max, NOT = 2−x）：

| a | b | TAND | TOR |
| --- | --- | --- | --- |
| 0 | 0 | 0 | 0 |
| 0 | 1 | 0 | 1 |
| 0 | 2 | 0 | 2 |
| 1 | 1 | 1 | 1 |
| 1 | 2 | 1 | 2 |
| 2 | 2 | 2 | 2 |

> 这些指令让 VM 能原生做不确定逻辑、三值神经网络激活、量化权重运算——正是你的 2bit/1trit 场景。

---

## 6. Opcode 分配（3 trit，十进制 0..26）

| 值 | 助记 | 格式 |
| --- | --- | --- |
| 0 | OP | R |
| 1 | OP-IMM | I |
| 2 | LOAD | I |
| 3 | STORE | S |
| 4 | BRANCH | B |
| 5 | JAL | J |
| 6 | JALR | I |
| 7 | LUI | U |
| 8 | AUIPC | U |
| 9 | SYSTEM | I |
| 10 | FENCE | I |
| 11 | T-LOGIC | R |
| 12 | T-PACK | R |
| 13 | M-EXT（MUL/MULH/MULHSU/MULHU） | R |
| 14 | DIV-EXT（DIV/DIVU） | R |
| 15 | REM-EXT（REM/REMU） | R |
| 16 | ZICSR（CSRRW/S/C + 立即数版） | I |
| 17 | PRIV（MRET/WFI） | I |
| 18 | ATOMIC（LRW/SCW/AMO*） | R |
| 19–26 | 保留（TC 压缩扩展预留） | — |

> **分配现状（以代码为唯一真相源，2026-11-19 核对）**：3-trit opcode 共 27 个槽位
> （0..26），已用 19 个（0–18），余 8 个（19–26）留给 TC。
> 明细见 `trirv_isa.py` 的 `OPCODE` / `INST` 表，当前共 **74 条指令**。
> 校验：`python3 -c "import trirv_isa as i; print(len(i.INST))"` → `74`。
>
> 本节旧版仅列出 0–12，把 13–26 整段标为"保留"，与 §2「M/A/Zicsr/PRIV 扩展两套 VM 均已实现」
> 相矛盾；现已按实际分配补齐，与代码一致。

---

## 7. 与 RISC-V 的逐项对应

| RISC-V | TriRV | 差异原因 |
| --- | --- | --- |
| 32-bit 指令 | 20-trit 指令 | $3^{20}\approx2^{31.7}$，编码密度对等 |
| 5-bit 寄存器字段（=32） | 4-trit 字段（=81，用 32） | 编号与别名保持 x0–x31 一致，余码留扩展 |
| 12-bit 立即数 | 7-trit 立即数 | $3^7\approx2^{11}$，略小但同量级 |
| 二进制补码 | 3 的补码 | 基数变化 |
| AND/OR/XOR | TMIN/TMAX/TNOT + XOR | 三值逻辑更丰富 |
| 符号位在 bit31 | 符号 trit 在 t19 | 保持并行扩展设计 |

---

## 8. 扩展机制（继承 RISC-V 模块化）

- **TI**（基础整数，必需）
- **TM**（乘除法，MUL/DIV/REM 的三进制版）
- **TA**（原子操作，LR/SC）
- **TT**（三值逻辑，§5）
- **TC**（压缩指令，16-trit 短格式，对应 RVC）

ISA 字符串示例：`TI-TM-TT`（基础整数 + 乘除 + 三值逻辑）。

---

## 9. 设计决策（已拍板，2026-11-18）

> 这五项曾长期悬置。2026-11-18 首次拍板；**2026-11-19 按"以 RISC-V 为规范标准"的战略撤销 D3**
> （回到 32 寄存器 / 4-trit 字段）。D1/D2/D4/D5 维持现状（D5 数据字长锁定 **45 trit = 9 tryte**）。
> 决策固化在 `trirv_isa.py:ISA_DECISIONS`（机读常量）与
> `test_isa_completeness.py` §G（一致性守卫，74/74 通过）；改动任一参数须先更新决策常量与本文档。

| 编号 | 决策点 | 拍板结论 | 被否决方案与否决理由 |
| --- | --- | --- | --- |
| **D1** | 指令字长 | **20 trit**（= 4 tryte 容量，恰好填满） | ~~21 trit~~：3²¹ ≈ 2³³·²⁸ 并非"精确 32-bit"；改 21 需 5 tryte 容器，PC 从 `4*i` 变 `5*i`，编码/跳转/对齐/全部 golden 失效。 |
| **D2** | 符号扩展 | **3 补码 + 显式 `s(u(v))` / `sign_extend_imm()`** | ~~ALU 内部平衡三进制 (-1,0,1)~~：模 3⁴⁵ 为奇数、范围对称，无 `INT_MIN/-1` 溢出；显式规整已被 ALU/访存/原子/CSR 四类差分 fuzz（累计 2 万+ case）验证；改平衡三进制需重写两套 VM 算术核心且无正确性收益。 |
| **D3** | 寄存器字段 | ✅ **已撤销 27 方案，回到 32 寄存器 / 4-trit 字段**（2026-11-19） | ~~27 寄存器 / 3-trit 字段~~（2026-11-18 曾实施，零浪费）：破坏 RISC-V x0–x31 ABI 与 LLVM 后端复用，与"以 RISC-V 为规范标准"的战略冲突。改回 4-trit 后 20 trit 恰好用满（原 t6/t12/t16 空闲位并入），立即数布局零改动，别名表恢复 RISC-V 标准编号。 |
| **D4** | 立即数位宽 | **I 型 7 trit / U 型 13 trit** | ~~加宽字段~~：大常数已由 `LUI+ADDI`（编译器 `load_imm` 用 3×15-trit 分段）覆盖全 45-trit 幅值并经全幅 fuzz 验证；加宽字段会重排全部编码格式。 |
| **D5** | 数据字长 | **45 trit = 9 tryte**（`long = pointer`，已同宽） | ~~40 trit~~：曾为迎合 LP64 试改，但**三进制硬件无 64 位概念**，LP64 是宿主二进制 ABI 惯例、不构成 TriRV 约束；45 trit 下 long=pointer 本就同宽。2026-11-18 短暂试改后**已撤回**，全链路恢复 45-trit，73/73 + 全 fuzz 绿。**更正**：PC 编址独立记 §11.3，D5 仅指数据字长。 |

**D1 措辞更正**：原文"20 trit（对齐 tryte）"不准确——tryte = 5 trit，20 trit 对应 4 tryte 是因为 4×5=20 恰好填满容器，**不是**因为 20 是 3 的倍数（20 并非 3 的整数倍）。指令按 4 tryte 容器存储，PC 按 tryte 地址 `4*i` 编址（§11.3）。

---

## 10. 实现顺序

> 状态（2026-11-18）：步骤 1–6 已完成并通过全量回归；步骤 7 为后续工程目标。

1. ~~拍板 D1–D5~~ ✅ 已拍板，见 §9
2. ~~写指令编码表（生成机器码的 Python 脚本）~~ ✅
3. ~~实现 VM 取指/译码/执行~~ ✅ 74 条指令，Python/C 双实现
4. ~~实现汇编器（编码 20-trit 指令）~~ ✅
5. ~~跑通最小程序（加法 + 分支 + ECALL 退出）~~ ✅ 13 个 e2e 全通过
6. ~~加 TT 扩展（三值逻辑 + 打包/解包）~~ ✅ 含 T-LOGIC/T-PACK/TSEXT
7. 接 crt0 + musl（见 ABI 文档）—— 后续目标

---

## 11. 实现状态（2026-11-18 更新）

设计文档与代码已对齐，`trirv_isa.py`（编码真源）、`trirv_vm.py`（Python VM）、
`trirv_vm.c`（C VM）、`trirv_asm.py`（汇编器）四者一致。要点：

- **指令编码**：`INST` 共 74 条（TI 40 + TT 9 + M8/A9/Zicsr6/PRIV2），全部
  encode→decode 往返自洽。`EBREAK` 用 `funct=1`（t7..t8）区分，不占用与 imm 高位
  重叠的 funct2 区；`TSEXT` 落为 `opcode=11,funct=2` 的单操作数 R 型指令。
- **SRA/SRAI**：定义为有符号值 ÷ 3^sh **向零取整**（与 DIV 及 C VM `divmod_word`
  语义统一），sh≥45 时结果为 0。两版 VM 实现一致。
- **SLL/SLLI**：结果 mod 3^45；用恒等式 `(w·3^sh) mod 3^45 = (w mod 3^(45-sh))·3^sh`
  规避大移位量下的 128 位溢出。
- **MUL**：乘积在 `__int128` 上直接相乘会溢出（45-trit × 45-trit → 90 trit ≫ 128 bit）。
  两版都改走 `mul90()` 取低半 `lo = (a·b) mod 3^45`（与 MULH 共用 90-trit schoolbook 乘
  法，零硬件除法、零溢出）。Python 侧大整数 `u(a*b)` 本就精确，作为参考实现。
- **MULH 系列**：高半按 floor(积 / 3^45) 取值，负数与 Python `//` 一致。
- **寄存器字段/扩展**：M/A/Zicsr/PRIV 扩展两套 VM 均已实现。
- **CSR 越界**：7-trit CSR 字段超界按"未实现 CSR"处理（读 0、写忽略）。
- **汇编器**：U/J 型立即数按 13 trit 解析（`LUI`/`AUIPC` 可加载大常数）；
  `CSRRW rd,rs1,csr` 沿用项目既有操作数顺序（与 RISC-V 惯例不同，为兼容既有代码保留）。

### 11.1 第二轮深度审查修复（2026-11-18）

针对"合法但非典型输入"边界的 5 个真实 bug 已修复并回归：

- **编译器大栈帧踩栈（P0）**：根因不止 prologue 的 `ADDI sp`，而是**所有访存偏移
  都按 7-trit（±1093）立即数发出**。当单函数局部变量区超过 1093（`int a[200]`
  偏移达 1800），`prologue` 抬栈、`SW/LW` 偏移、数组清零、`copy_struct`、结构体
  返回缓冲清零全部溢出并被 VM 解成负数。修法：`func_prologue` 抬栈改 `load_imm+ADD`；
  新增 `_mem_addr()` helper，偏移超范围时用空闲寄存器 `t3` 算 `base+off` 再以 0 偏移访存，
  `load_ty`/`store_ty`/`emit_zero`/`copy_struct` 统一走它。
- **编译器大常量 ≥ 3^20**：`load_imm` 改为 20-trit **平衡分段**（每段 seg ∈
  [-(3^20-1)/2, +]，避免普通取模在段上界借位使 `hi` 溢出 13-trit LUI 字段）。
  现可正确装载到 3^45-1（=-1）。
- **短路求值**：`&&`/`||` 改为条件跳转实现 C 语义短路（左边定结果时不求值右边，
  如 `p && *p`），此前两侧都求值。
- **负移位立即数**：`SLLI/SRLI/SRAI` 的移位量统一为**无符号 7-trit**解释（与寄存器版
  `umod2187` 及 Python 一致）；C VM 原用带符号 `imm_s`，负立即数结果与原版不符，
  改用 `imm_raw`。移位量 ≥45 时结果 0。
- **负 PC**：Python/C VM 取指前统一检查 `pc < 0`，原 Python 负索引回绕、C 越界读。
  非法 PC 现在停机。
- **write 负 count**：Python/C VM 统一返回 `-EINVAL`（-22），原 Python 返回 0、C 返回负值。

验证：`test_isa_completeness.py` **73/73 PASS**（含 C VM 边界差分与 PC 编址专项），
`trirv_difftest.py` 11/11 PASS，13 个 `*_e2e.py` 全部通过（2026-11-19 复测）。

### 11.1.1 指令级随机差分 fuzz 与 MUL 溢出修复（2026-11-18）

新增 `trirv_fuzz.py`：在一个程序里批量生成随机 ALU 操作，结果写入内存后由
`write` syscall 整块输出，Python VM 与 C VM 对比字节流，差异时逐 case 定位。
操作数池覆盖全 45-trit 范围（`±HALF_X`、`±3^44`、word 全 1/全 2、随机 word），
`emit_load()` 用 3×15-trit 分段装载任意 45-trit 常量。

- **发现的真 bug —— MUL 在 128 位上乘溢出**：`MUL(-3^44, HALF_X-1)` 的乘积
  ≈ `-1.45e42`，远超 `2^127≈1.7e38`。C VM 两处引擎的 `case OP_MUL` 原用
  `u(a*b)`，`__int128` 相乘溢出得到错误值（Python 大整数正确返回 0，因乘积恰为
  `3^45` 的倍数）。修法：两处均改用 `mul90()` 取低半 `lo=(a·b) mod 3^45`，
  与 MULH 系列共用 90-trit schoolbook 乘法，零溢出。
- **澄清**：此 bug 与"除法溢出"无关。TriRV 模 `3^45` 为奇数，3 补码范围对称
  `[-HALF_X, HALF_X]`，`MIN÷-1=MAX` 仍在范围内，不存在二进制补码 `INT_MIN/-1`
  那种溢出。`divmod_word()` 的"无除法器"指用逐位恢复除法实现（零硬件 `div`），
  是**实现手段**；此处 MUL 是**数值幅值超出 C 宿主类型**（`__int128`），二者正交。

验证：8 组 × 800 case（共 6400 条全幅随机指令）Python/C 字节流完全一致；
修复后 `test_isa_completeness.py` 73/73、difftest 11/11、CSR 13/13、13 个 e2e 全绿。

新增 `trirv_fuzz_mem.py`（访存 + 原子差分 fuzz）：覆盖 ALU fuzz 未涉及的访存语义——
SW/SH/SB 写入与 LW/LB/LBU/LH/LHU 读回的 tryte 序与零扩展、有符号 base + 7-trit
立即数的地址计算（含负/越界地址）、A 扩展 LRW/SCW/AMO* 的保留集与 old-value 返回。
验证：6 组 × 800 case（含 25% 原子序列）Python/C 字节流完全一致，未发现新差异。

新增 `trirv_fuzz_csr.py`（CSR 差分 fuzz）：覆盖 CSRRW/RS/RC 及立即数版的旧值返回、
CSR 地址越界读 0 写忽略、mepc/mtvec 写入 4 对齐。

- **发现的真 bug —— mepc/mtvec 4 对齐前缺模规整**：`CSRRS` 的 `old|src` 是按位或，
  结果可 `≥ 3^45`（如 `4.57e21 > 2.95e21`）。Python `_csr_write` 用 `s(u(val))` 先
  模 `3^45` 再对齐；C `csr_write` 原用 `align_down4(s(val))`，`s()` 对 `≥ MOD_X`
  的值只减一次 `MOD_X`，3 补码规整错误，导致对齐后与 Python 差 1。修法：对齐前先
  `u(val)` 规整——`align_down4(s(u(val)))`，与 Python 一致。非对齐 CSR 写入本就由
  `umod_M(val)` 兜底，不受影响。
- **设计限制（非 bug）**：立即数版 CSR 的 uimm 字段（4 trit，0..80）与 rs1 共用
  `<32` 钳制，uimm≥32 时两版都钳为 0，CSRRWI 无法写入 ≥32 的立即数。两版一致，
  暂不改 ISA 语义。

验证：10 组 × 800 case（共 8000 条 CSR 操作）Python/C 字节流完全一致。

### 11.2 仍为设计约束/已知限制（非 bug）

- **分支/JAL 立即数位宽**：B 型 7 trit（±1093 槽）、J 型 13 trit，超出静默回绕，
  属 D4 位宽设计约束。
- **ISA §9 D1–D5 已全部拍板并代码固化**（字长、符号扩展、寄存器字段、立即数、数据字长）；
  D1/D2/D4/D5 维持现状，**D3 已于 2026-11-19 撤销（回到 32 寄存器 / 4-trit 字段）**，见 §9。
- **CSR 操作数顺序**：汇编器 `CSRRW rd,rs1,csr` 与 RISC-V 惯例相反，为兼容既有代码保留。

### 11.3 PC 编址（已实现）

PC 按 **tryte 地址**编址，与 §1「地址按 byte/tryte 编址」一致。指令定长 20 trit = 4 tryte，
故**指令 `i` 的 tryte 地址 = `4*i`**。两套 VM（Python/C）语义完全一致：

| 项 | 语义 |
| --- | --- |
| 取指 | `slot = pc // 4`；`pc < 0`、`pc % 4 != 0` 或越界 → 停机 |
| 顺序执行 | `pc += 4` |
| 分支目标 | `pc = old_pc + 4*sign_extend(imm)`（立即数仍是**指令槽偏移**，保持 ±1093 条跳转范围） |
| `JAL` | 链接地址 `rd = old_pc + 4`；目标 `old_pc + 4*imm` |
| `JALR` | 链接 `rd = old_pc + 4`；目标 `align_down4(rs1 + imm)`（向下对齐到 4，对标 RISC-V 清低位） |
| `AUIPC` | `rd = old_pc + imm*3^7`（基于 tryte PC） |
| `mepc`/`mtvec` | 存 **tryte 地址**，写入时 `align_down4`（4 对齐）；陷入/返回时 `//4` 校验槽范围 |

> 关于 4 对齐：4 不是 3 的幂，无法用位掩码对齐，故用 floor 除法 `addr//4*4`。
> C VM 整数除法向零截断，专门用 `align_down4()` 修正负值，与 Python `//` 一致。
> 立即数单位保持"槽"而非改为"tryte"：若改 tryte 偏移，7-trit B 型跳转范围会从
> ±1093 条缩到 ±273 条；保持槽偏移则工具链（汇编器标签差值、编译器跳转）零改动。

验证：`test_isa_completeness.py` §F 覆盖 JAL 链接、分支、AUIPC、负 JALR 对齐、mepc/mtvec
tryte 保存及 C/Python 差分；`trirv_csr_e2e.py` 13/13 通过（trap 向量按 tryte 地址）。

---

*参考：RISC-V Unprivileged ISA Manual（RV32I Base）；TriRV 为原创设计，参数基于本项目 45-trit / 0-1-2 non-balanced 前提。*
