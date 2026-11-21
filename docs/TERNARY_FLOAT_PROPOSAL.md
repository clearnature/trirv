# TriRV 浮点扩展 TF 立项书（ISA 演进提案）

> 状态：**立项（2026-11-20）** —— 本文是设计与决策记录，不是已实现功能。
> 动机、格式、穿透模型、指令草案、wasm 对接、分阶段计划与待拍板项都在本文。
> 一句话立场：**浮点数值运算穿透宿主 FPU 取极致性能，但 ISA 格式与语义是三进制自己的，
> 不把二进制的逻辑（mod 2^n 规整、位展开、NaN payload 位语义）搬进来。**

---

## 0. 为什么现在立项

| 触发 | 说明 |
| --- | --- |
| wasm 必然要浮点 | `trirv_wasm.py` 在 `f32.const`(0x43)/`f64.const`(0x44) 与所有 `f32.*`/`f64.*` 指令显式抛 `Unsupported`；Wasm 的 f32/f64 是核心类型，绕不过去 |
| libc 浮点系被永久 SKIP | `strtod`/`atof`/`ecvt`/`fcvt`/`gcvt` 因"TriRV 无浮点"被记入决策 SKIP，账本欠着 |
| 既有战略留了口子 | README §六 已写"三进制浮点格式属 ISA 演进，单独立项" —— 本文件就是这个立项 |
| 穿透哲学已有先例 | DIV 快路径用宿主 128 位整数除法（实测 3.7ns），语义仍定义为"数值÷数值向零截断"，与进制无关。浮点照此办理 |

**这不是推翻既有决策，而是把它从"永久 SKIP"升级为"演进项"**：SKIP 的理由是"没有浮点语义"，
本立项要给出浮点语义，理由消失，账本可重新打开。

---

## 1. 设计铁律（三层分账，继承 ISA §1.1/§1.2）

延续项目一贯口径，任何浮点讨论先分清三层，不得混谈：

| 层 | 是什么 | 本提案怎么做 |
| --- | --- | --- |
| **定义层** | 格式、基数、值域、特殊值编码 | 三进制自己的：**base-3 阶码 + base-3 尾数**；宽度是定义出来的，不比优劣 |
| **语义层** | 指令对数值做了什么、舍入、比较、异常 | 对标 IEEE-754 的**数学语义**（数值运算、round-to-nearest），但**不引入二进制位级语义** |
| **实现层** | 谁来算、多快 | **穿透宿主 FPU**（C VM 直接用 `double`/`float`），Python VM 用宿主 float 差分；必须实测 |

### 1.1 "不要二进制的逻辑和语义"的精确含义

用户立场：**"极致性能的三进制硬件虚拟层，跑在二进制硬件上，直接调用底层的硬件，
不要二进制的逻辑和语义。"** 拆成可执行条款：

1. **穿透是允许的、也是要追求的**：宿主 FPU 就是"底层硬件"，直接调用它做数值运算。
   这与 DIV 用宿主除法器同构 —— 数值是进制无关量，`a÷b` 的商不因容器是二进制而变。
2. **不引入二进制位级语义**：具体禁止以下四项进入 ISA 定义或编译器适配层：
   - ❌ `mod 2^32`/`mod 2^64` 的浮点位回绕（那是整数语义，浮点不需要）
   - ❌ NaN 的 **payload 位模式**语义（三进制 NaN 只有一个"非数"概念，不携带二进制位载荷）
   - ❌ 依赖二进制 subnormal 位布局的渐进下溢（三进制格式自己的下溢规则）
   - ❌ 符号位/阶码/尾数的 **二进制字段宽度**约定（3-trit 字段就是 3-trit，不凑 8/11/52 bit）
3. **舍入与特殊值按三进制定义**：round-to-nearest 是数学规则（进制无关），可用；
   ±Inf/NaN/±0 作为抽象值存在，但编码按三进制格式，不照抄 IEEE 的位花样。
4. **不拿二进制格式当参照系或天花板（2026-11-20 更正）**：
   "binary64 是 64 位"是 IEEE-754 **软件规范**，不是 CPU 的物理事实 —— 硬件提供的只是
   加法/乘法电路，没有"64 位浮点"这个物理实体。因此：
   - 格式的阶码/尾数宽度、值域**由我们在纯三进制下自主定义**，对标 f32/f64 只是背景参照，
     不是设计约束、不是验收标准、不是精度上限。
   - 不存在"TF45 超出 double 值域所以有缺口"这种论证 —— 那是把软件容器错当物理边界。
5. **执行层与表示层分离（2026-11-20 用户定调）**：
   两个正交维度必须分清，不得互相冒充（详见 ISA §1.2.1）：

   | 维度 | 硬件/运算实际在做 | 人看/输入的记数法 |
   | --- | --- | --- |
   | 二进制世界 | 二进制门电路算 `10+10` | 十进制记数（`20` 只是记号） |
   | 三进制世界 | 三进制门电路算三进制 | **十二进制记数** |

   - CPU 里没有"十进制电路"，十进制是二进制硬件的**表示层**；同理，三进制硬件的
     表示层选**十二进制**（`trirv_base12.py`）。`12 % 3 == 0` 而 `10 % 3 == 1` ——
     十二进制里 `1/3 = 0.4` 有限位、十进制 `1/3 = 0.333…` 无限，故 12 更契合三等分。
   - **精度/数值的执行层度量是 trit**（如 TF45 尾数 34 trit）；"十进制有效位""十二进制位"
     都是表示层记数换算，不作为定义依据。本文凡出现位数换算均标注"表示层记数"。
6. **数码符号与进制标注（2026-11-20 用户定调）**：表示层必须显式定义自己的数码与标注法，
   不得借用十进制写法冒充本进制定义：
   - **基数自指陷阱**："12" 两个字是十进制进位记法（`1×10+2`），不是十二进制自己的写法。
     十二进制的基数写作 `10`（`1×12+0`）；字样 `12` 在十二进制里其实等于十进制 14。
     所以本文凡写"十二进制"，都指"基数为十进制值 12 的记数法"，不是用十进制符号定义它。
   - **数码符号**：用户拍板暂用 ASCII `0-9AB` 作规范数码（`SYMBOL_SET["canonical"]`），
     这是字形层面的临时约定，不是数学定义；将来换地支/专用字形只改 `INPUT_ALIASES` 映射，
     `to12`/`from12` 调用方不动。
   - **进制标注**：数字**加下标**标明按哪个基数解读，如 `18₁₂`（十进制 20）、`20₁₀`（十进制 20）、
     `101₁₀`。下标是**元层注解**（人类读的标签），不是被注解数值的一部分；下标用十进制
     Unicode 字形书写属于注解自身的记法，不违反"表示层自主定义"。工具接口：
     `labelled(n, base)` / `parse_labelled(s)`（`trirv_base12.py`）。

> 判定标准（与 §1.2 一致）：**语义定义不得引用"二进制位"这一概念。** 载体（宿主 `double`）
> 是二进制的，但数值不是 —— 就像 `__uint128_t` 装 45-trit 整数。容器换成三值电路时，
> 这套语义一个字都不用改；穿透只是当前虚拟层的实现选择。
> 同理，**"64 位"三字不进入 TriRV 的任何定义** —— 我们的档位叫 TF20/TF45，是 wasm 档位
> 对应名（见 §2.3），不是二进制位宽承诺。十进制/十二进制只是**表示层记数法**，
> 不进入执行层度量（trit 才是）。

---

## 2. 数值格式（定义层）

### 2.1 两种候选（D-F1 已拍板：方案 A）

| 方案 | 编码 | 转换税 | 契合"三进制格式" | 契合"极致性能" | 风险 |
| --- | --- | --- | --- | --- | --- |
| **A. 三进制原生格式（已选）** | base-3 阶码 + base-3 尾数，装进 45-trit 字 | 算术零税，仅 load/store 边界转换 | ✅ 强 | ✅ 强（尾数进 FPU + 指数走整数） | 转换边界的 base-3 ↔ (mant,exp3) 编码要用整数指令 |
| B. binary64 位模式直存 | IEEE754 的 64 位模式装进 45-trit 字低 64 位 | 零 | ❌ 弱（格式是二进制的） | ✅ 强 | 违背"不要二进制语义"中的"格式"条款 |

> **2026-11-20 拍板 D-F1 = A**：战略底线是"ISA 语义与格式必须三进制、不照搬二进制"；
> 方案 B 等于把 IEEE754 写进 ISA，不可逆。配套 §3.1 的**尾数/指数分离穿透模型**
> （mant∈[1,3) 进宿主 FPU、exp3 走整数 ALU）使算术指令零转换税，
> 转换只在 load/store 边界，兼顾极致性能 —— 且 double 的值域从不约束整数指数。
> P1 仍须实测边界转换税：若 FLW/FSW 的 base-3 ↔ (mant,exp3) 编码超过 ~50 条整数指令，需复议布局。

### 2.2 三进制原生格式布局（双档 TF20/TF45，宽度待拍板 D-F2）

> **重命名记档（2026-11-21 拍板）**：原 TF64/TF32 → **TF45/TF20**——数字 = 总
> trit 数（45/20），与 ISA 命名法（45-trit 字、20-trit 指令）同一套；旧名的
> "64/32" 是二进制量纲数字，违背"二进制位宽不进入我们的定义"。指令助记符
> 保留 RISC-V 式字母后缀（.S = TF20 档、无后缀 = TF45 档）；wasm 侧 f32/f64
> 类型名不动（wasm 规范名，仅存在于桥接层）。

**D-F3 已拍板：双档 TF20 + TF45**，仿 wasm f32/f64 两档，wasm 对接零换算。
两档共用一套布局规则，只是 trit 数不同：

```
TF20（20 trit = 4 tryte，wasm f32 档位对应）：
  t19       t18..t14 (5 trit)     t13..t0 (14 trit)
  符号       阶码 E (base-3)        尾数 M (base-3)

TF45（45 trit = 9 tryte，wasm f64 档位对应）：
  t44        t43..t34 (10 trit)     t33..t0 (34 trit)
  符号        阶码 E (base-3)         尾数 M (base-3)
```

| 项 | TF20 值 | TF45 值 | 理由（纯三进制自主定义） |
| --- | --- | --- | --- |
| 符号 | 1 trit | 1 trit | 3 补码无干净符号位，浮点用显式符号 |
| 阶码 | 5 trit（243 级） | 10 trit（59049 级） | 阶码宽度由档位字长余量与动态范围需求定，非对标二进制位宽 |
| 尾数 | 14 trit | 34 trit | 尾数占满剩余 trit（20/45 字长），精度是定义结果不是对齐目标 |
| 值 | $(-1)^s \times (M/3^{k-1}) \times 3^{E-\text{bias}}$ | 同左 | 基数是 3 —— "三进制浮点"的核心 |
| 正规化 | $M \in [3^{k-1},3^k)$，前导 trit ∈{1,2} | 同左 | k trit 恰好装下，隐含前导 1（base-3 版） |
| 特殊值 | E 全最大 + M=0 → ±Inf；M≠0 → NaN；E=M=0 → ±0 | 同左 | 抽象特殊值，编码是 base-3，不照抄 IEEE 位花样 |

> 宽度（5/14、10/34、bias）由**字长与档位需求自主定义**；P1 实测了精度与动态范围（§6.1），
> 但不以"接近 f32/f64"为验收标准 —— 二进制位宽不进入我们的定义。
> 双档带来指令集翻倍（每个 F 指令分 `.S`/`.D` 两版），这是 wasm 档位对应的代价，按实现层账计。

### 2.3 与 wasm f32/f64 的对应关系（D-F3 已拍板：双档）

| wasm 类型 | 映射 | 换算 |
| --- | --- | --- |
| f32 | TF20（20 trit） | 零换算，精度档对齐 |
| f64 | TF45（45 trit） | 零换算，精度档对齐 |

双档使 `trirv_wasm.py` 的 f32.*/f64.* 指令各自直通对应的 TF 档，无精度损失、无额外换算指令。
代价是 F 扩展指令数量翻倍（P2 实现时按 `.S`/`.D` 后缀区分）。

---

## 3. 穿透执行模型（实现层）

### 3.1 指令怎么跑（对齐 ISA §1.2 的叙述方式）

**关键模型：尾数与指数分离穿透**（2026-11-20 更正，取代早先"寄存器即 double 载体"的错误设想）：

一个 TF 值的规范表示是三元组 `(sign, mant, exp3)`，值 = `sign × mant × 3^exp3`：

- **mant ∈ [1,3)** 是唯一送进宿主 FPU 的量 —— 它恒在 double 窗口内，永远装得下；
- **exp3 是 base-3 整数指数**，走整数 ALU（对阶、归一化 +1/−1），**不进 FPU**；
- 宿主 double 的 3^646 上限只约束"能不能把整个值塞进一个 double"，
  而我们的模型从不这么做 —— **double 值域不约束整数指数，全阶码可表示，无缺口**。

```
FADD/FMUL/FDIV/FSQRT（穿透，零转换税）：
  1. 对阶   整数运算比较两个 exp3，把较小操作数的 mant 按 3^Δ 平移（Δ 为整数差）
  2. 尾数运算  两个 mant∈[1,3) 送宿主 FPU 做 + − × ÷ sqrt → 结果 mant'
  3. 归一化 整数运算调整 exp3 使 mant'∈[1,3) → 写回 (sign, mant', exp3)

FLW / FSW（唯一转换边界）：
  FLW: 内存 9 tryte 的 base-3 编码 → 解码成 (sign, mant, exp3) → 装入浮点寄存器
  FSW: (sign, mant, exp3) → 编码成 base-3 → 写入内存 9 tryte
```

可执行证明：`trirv_float.py` 的 `decompose()`/`compose()` 已实现该分离表示 ——
`trirv_float_e2e.py` 验证了 TF45 最大正规阶码 exp3=29523（3^29523，远超 3^646）
仍能拆成有限 mant + 精确 exp3，往返阶码不丢。`python3 bench_float.py` 的 C 节可复现实测。

这才是"极致性能的三进制虚拟层直接调用底层硬件"的正确形态：
**穿透的是底层硬件的加法/乘法电路（通过 mant 这个恒在窗口内的载体），
不引入任何二进制格式语义；base-3 格式只存在于内存/ABI 边界。**
硬件没有"64 位浮点"这个物理实体，binary64 位宽是软件规范，不进入我们的定义。

> 精度边界（诚实记账）：TF45 尾数 34 trit ≈ 53.9 bit，宿主 double 只有 53 bit 有效位，
> `decompose()` 把 34-trit 尾数装进 double 会有 ~1 ulp 尾数误差。这是穿透的物理代价，
> 属实现层账；阶码与特殊值不受影响。若 P5 评估认为不可接受，用两个 double（double-double）
> 承载尾数即可，不改 ISA 语义。

### 3.2 两套 VM 的分工（继承既有约定）

| VM | 角色 | 浮点实现 |
| --- | --- | --- |
| C VM（`trirv_vm.c`） | 性能基准、穿透主路径 | 尾数走宿主 `double` 运算 + 整数指数 ALU + base-3 编解码 |
| Python VM（`trirv_vm.py`） | 语义差分、参考实现 | 宿主 `float` 尾数 + Python 整数指数（行为对齐 C VM） |

差分测试必须覆盖：特殊值（±0/±Inf/NaN）、舍入边界、下溢/上溢、极端 exp3（远超 double 值域）、f32/f64 档位映射。
三路差分（参考解释器 × PyVM × CVM）与现有 wasm e2e 同构。

### 3.3 编码空间（对齐 ISA §6）

opcode 3-trit 共 27 槽（0..26），已用 0–18，19–26 预留给 TC。
F 扩展从预留区分配（建议 opcode 19–21 给 TF，TC 顺延），具体分配在指令实现时锁定并同步 ISA §6。

---

## 4. F 扩展指令草案（命名借鉴 RISC-V F 扩展，语义纯三进制）

| 指令 | 语义 | 命名参考 |
| --- | --- | --- |
| `FLW` / `FSW` | 浮点 load/store（9 tryte = 1 寄存器） | RV32F FLW/FSW |
| `FADD.S` / `FSUB.S` / `FMUL.S` / `FDIV.S` | 四则，穿透宿主 FPU | RV32F 同名 |
| `FSQRT.S` | 平方根，穿透 | RV32F FSQRT.S |
| `FEQ.S` / `FLT.S` / `FLE.S` | 比较，写整数寄存器 | RV32F 同名 |
| `FCVT.S.W` / `FCVT.W.S` | 整数↔浮点转换（整数按 45-trit 三进制读法） | RV32F FCVT |
| `FCVT.W.T` | 向零截断变体（rtz）——C 的 (int) cast / wasm trunc 语义；RISC-V FCVT 强制 rtz 的先例。2026-11-21 拍板：F-MEM funct 8 | RV32F FCVT rtz |
| `FMV.X.W` / `FMV.W.X` | 位模式搬运（若采用方案 B 才需要；方案 A 下退化为编码转换） | RV32F FMV |

> 特殊值处理：NaN 比较全部返回 false（`FEQ`/`FLT`/`FLE` 对 NaN 均为 0），
> ±0 相等 —— 这些是 IEEE 的**数学语义**，与位布局无关，予以保留；
> 但不暴露 NaN payload 操纵指令（那是二进制位语义，禁止）。

---

## 5. 分阶段计划（立项后按技术推进）

| 阶段 | 内容 | 交付物 | 验收集 |
| --- | --- | --- | --- |
| **P0 立项** | 本文 + 待拍板项裁决 | `TERNARY_FLOAT_PROPOSAL.md` | — |
| **P1 格式与转换** ✅ 2026-11-20 | base-3 ↔ (mant,exp3) 编解码 + 分离穿透模型 + 实测 | `trirv_float.py` + `trirv_float_e2e.py`（144 PASS）+ `bench_float.py` | 往返/边界/特殊值/极端阶码；精度与全阶码可表示实测 |
| **P1b 表示层记数** ✅ 2026-11-20 | 十二进制记数 + 数码符号定义 + 进制下标标注（执行层/表示层分离铁律落地） | `trirv_base12.py` + `trirv_base12_e2e.py`（72 PASS） | to12/from12/frac_to12、SYMBOL_SET 数码定义、基数自指（'12'₁₂=14、基数写作'10'）、labelled/parse_labelled（18₁₂）、round-trip 0 错 |
| **P2 F 扩展指令** ✅ 2026-11-20（TF45 子集） | 两套 VM 加 freg + F 指令 + C 浮点头（分离表示穿透宿主 FPU） | `trirv_float.h` + `trirv_vm.c/.py` + `trirv_isa.py` + `trirv_float_isa_e2e.py`（27 PASS） | 编码往返无撞码 + PyVM 语义 + PyVM×C VM 字节差分；**范围：TF45 四则+sqrt+load/store+比较，TF20 编码预留未接** |
| **P3 wasm 对接** ✅ 2026-11-20 | 解除 `f32.const`/`f64.const` 与算术指令的 `Unsupported`，编译期穿透 IEEE 位模式 → TF raw | `trirv_wasm.py` 扩展 + `trirv_asm.py` F 助记符 + C VM 档位参数化 + `tf_ratio` 舍入修复 | `trirv_wasm_e2e.py` 112 例（含 19 浮点）+ `trirv_float_isa_e2e.py` 39 项；三路差分全一致（详见 §5.2） |
| **P4 libc 浮点系** ✅ 2026-11-20 | 重开 5 项 DECISIONED SKIP（理由已被 P2/P3 推翻），musl_tri 三进制原生重写：strtod+atof（十→TF45，正确舍入）、ecvt+fcvt+gcvt（TF45→十进制，musl 怪癖逐条镜像）（详见 §5.6/§5.7） | `musl_tri/strtod_tri.c` + `ecvt_tri.c` + `trirv_float_libc_e2e.py` | Fraction 精确 oracle × 双 VM 字节差分全绿；批量管线 65/66 |
| **P5 穿透收益评估** ✅ 2026-11-20 | 实测转换税 vs 穿透收益；裁决方案 B/分离表示/double-double/硬件直通（详见 §5.5） | `bench_float.py` F/G/H 节 + PERF_REPORT + §5.5 | Q1/Q2/Q3 全部实测回答；四候选裁决入账 |

**优先级**：P1→P2→P3 是打通"wasm 能跑浮点"的最短路径；
P4/P5 视 P1 实测转换税结果调整。阶段 4 LLVM 后端仍在其后，不因本立项改变既有路线。

### 5.1 P2 详细执行计划（2026-11-20 写定，按此执行）

**决策冲突更正（必须先讲明）**：P0 阶段曾口头拍板"独立 freg[32]，元素是 host double 数组"。
该设想与 §1.1 铁律 4 / §3.1 的**框架更正**冲突——裸 `double` 容器把指数卡在 `3^646`，
等于把已推翻的"寄存器即 double 载体"错误模型重新装回。P2 按最新框架实现：

> **freg[i] 存分离表示 `(sign, mant∈[1,3) 的 host double, exp3 整数)`，不是裸 double。**
> mant 是唯一进 FPU 的量（恒在窗口内），exp3 走整数 ALU（无范围约束）。
> 这正是 `trirv_float.decompose()/compose()` 定义的可执行形态。

**编码分配**（20-trit 指令字，opcode 19–21；18 已被 A 扩展占用，19+ 空闲）：

| opcode | 格式 | 指令 | funct（2 trit） | funct2（3 trit） |
|---|---|---|---|---|
| 19 | R | `FADD.S/.D` `FSUB` `FMUL` `FDIV` `FSQRT` | 0=ADD 1=SUB 2=MUL 3=DIV 4=SQRT | 0=S 1=D（档位） |
| 20 | I | `FLW` `FSW` `FCVT.W.S` `FCVT.S.W`（档位由 funct2） | 0=LW 1=SW 2=CVTW 3=CVTF | 0=S 1=D |
| 21 | R | `FEQ.S` `FLT.S` `FLE.S`（结果写整数 rd） | 0=FEQ 1=FLT 2=FLE | 0=S 1=D |

寄存器字段：F-R 型 rd/rs1/rs2 都是 **freg 编号**（0–31，复用现有 4-trit 字段，独立寄存器堆）；
F-I 型 FLW/FSW 的 rs1 是**整数**地址寄存器，rd 是 freg（复用字段，含义按 opcode 区分）。
档位：先用 TF45（45 trit，单寄存器）打通，TF20（20 trit）同编码 funct2=0，后续补齐。

**改动文件清单（按依赖顺序）**：
1. `trirv_isa.py` — OPCODE 加 19/20/21，INST 加 F 指令，encode/decode 已通用无需改
2. `trirv_vm.py` — VM 加 `self.freg` 分离表示数组；step() 加 F 指令分支；`decompose/compose` 复用 `trirv_float.py`
3. `trirv_vm.c` — `Op` 枚举 + `IDEFS` 加 F；VM 加 `FReg freg[32]`（struct `{sign,mant,exp3,cls}`）；`step()` 与 `vm_run_cg()` **两个分发点都要改**；FLW/FSW 用 `trirv_float` 的 C 等价编解码
4. `trirv_float.py` — 补 C VM 需要的 C 版编解码（或把 Python 的移植到 C，二选一，见下"实现选择"）
5. `trirv_asm.py` — 加 F 助记符与 f 寄存器别名（`f0`–`f31`）
6. 新契约 `trirv_float_isa_e2e.py` — ISA 编码 + 双 VM 差分 + 算术正确性
7. `run_all_tests.py` 注册第 43 套；`README.md` 同步

**实现选择（C VM 编解码归属）**：C VM 不能 import Python。两条路：
- (a) 把 `trirv_float.py` 的编解码移植成 `trirv_float.c/.h`（一份真相源，Python 侧保留做差分 oracle）
- (b) 在 `trirv_vm.c` 内联一份 C 编解码
选 (a)，避免两套 C 副本漂移；Python 版继续做参考 oracle。

**TDD 顺序**：先写 RED 契约（编码往返 + 双 VM 差分 + `1.5+2.25=3.75` 语义）→ 确认失败 → 按上表顺序实现 → GREEN → 全量回归。

**验收标准**：
- ISA 完整性测试覆盖 F 指令编码/解码往返
- 双 VM（PyVM/C VM）对同一 F 程序差分一致（含 ±0/±Inf/NaN、极端 exp3）
- 语义断言：`1.5+2.25=3.75`、`2×3=6`、`1/3` 舍入、`sqrt(9)=3`（在格式精度内）
- 全量套件保持全绿（第 43 套新增）

**范围边界（本阶段不做）**：
- 不做 wasm f32/f64 对接（P3）
- 不做 C 编译器 float 类型（属 libc/编译器，P4 之后）
- 不做 FSQRT 的硬件级收敛迭代（先用宿主 `sqrt`，穿透 FPU）
- 不做双档全部指令，先把 TF45 四则 + load/store 打通，档位字段预留

---

### 5.2 P3 详细执行计划（2026-11-20 写定，按此执行）

**目标**：让 `trirv_wasm.py` 能把 wasm 的 f32/f64 常量与算术字节码翻译成 TriRV F 指令，
做到"wasm 能跑浮点"，同时**全程不引入二进制逻辑与语义**。

**必须先讲明的三件事（框架纠偏）**：

1. **穿透点在哪**：wasm 的 `f32.const`(0x43)/`f64.const`(0x44) 后面跟的是 IEEE 位模式
   （4/8 字节）。 TriRV 不执行 IEEE 语义——在**编译期**由宿主 Python 用 `struct.unpack`
   把位模式解成一个 host double，再用 `trirv_float.encode()` 量化成 TF20/TF45 的 45-trit raw，
   写进数据段。运行时 VM 只见 TF raw，只跑 F 指令。IEEE 只出现在 wasm 字节码的编码里，
   是"外部输入的表示"，不是 TriRV 的运算规则。这正是执行层/表示层分离铁律的又一次落地。

2. **不做 IEEE 逐位差分，做 TF 语义差分**：三路差分（参考解释器 × PyVM × C VM）的
   期望值必须由 `trirv_float` 的 TF 语义算出，而不是 Python `float` 的 IEEE 结果。
   理由：TF20 尾数 14 trit ≈ 22.2 bit < binary32 的 24 bit，TF45 34 trit ≈ 53.9 bit
   > binary64 的 53 bit——两者的舍入点不同，拿 IEEE 当 oracle 必然在中间值上错。
   用户铁律 4"不拿二进制格式当参照系"在此处直接适用。
   参考解释器对浮点指令直接调用 `trirv_float.f_*`，与 VM 共享同一语义真相源。

3. **精度不对等进行诚实记账**：`f64.const` 的 IEEE double → TF45 时尾数 53→34 trit
   （丢 ~0.9 bit 尾数，但指数无损）；`f32.const` 的 IEEE float → TF20 时尾数 24→14 trit
   （丢 ~1.8 bit）。这是档位自主定义的结果，不声称"零损失"——实测数值入账（§7 已有框架）。

**P2 遗留缺口必须在 P3 补掉（否则 P3 无法真跑双档）**：
- `trirv_asm.py` 没有 F 助记符与 `f0..f31` 寄存器别名。P2 的 e2e 用 `isa.encode` 直接造
  二进制绕过了汇编器，但 wasm 编译器产出的是**汇编文本**再交给 `trirv_asm.assemble`——
  这是 P3 的硬依赖，必须先补。
- `trirv_vm.c` 的 F 指令硬编码 `TF45_MAX_EXP` / `TF45_MANT_TRITS`（`step()` 与
  `vm_run_cg()` 两个分发点都是），`TFFmt` 结构虽参数化了 P_MANT/P_NORM/P_SIGN，
  但 bias/max_exp/mant_trits 仍是宏。要兑现 D-F3 的 f32→TF20，必须把这三个也参数化，
  加 `tf20_init()`，VM 里放两套 `TFFmt`（tf20/tf45），按 `funct2` 选。Python VM 已支持档位。

**改动文件清单（按依赖顺序）**：

| # | 文件 | 改动 |
|---|---|---|
| 1 | `trirv_float.h` | `TFFmt` 加 `bias/max_exp/mant_trits` 字段；`tf_dec/tf_compose/tf_norm/tf_add/...` 用 `f->` 字段替代宏；加 `tf20_init()` |
| 2 | `trirv_vm.c` | VM 加 `TFFmt tf20`；`step()` 与 `vm_run_cg()` 两个分发点按 `in->funct2` 选档；FLW/FSW 按档位选 raw 宽 |
| 3 | `trirv_vm.c` 重建 | 重编 `trirv_vm` 二进制，跑 `trirv_float_isa_e2e.py` + `trirv_csr_diff.py` 确认 TF45 回归不破 |
| 4 | `trirv_asm.py` | `REG` 加 `f0..f31`（映射到 0..31，**独立寄存器堆**靠 F 指令的 opcode 区分，编号可复用）；FLW/FSW 的 I/S 解析让 rd/rs2 走 freg；F-R 型用 f 寄存器 |
| 5 | `trirv_wasm.py` | 栈槽加类型标签 `vtype[j] ∈ {i32,f32,f64}`；`f32.const/f64.const` 编译期穿透；f32/f64 算术/比较/转换分派；`f32.load/store`/`f64.load/store`；freg 栈驻留池与 spill |
| 6 | `trirv_wasm_e2e.py` | 参考解释器加浮点（调 `trirv_float`）；`W` 构造器支持 f32/f64 类型与常量；增补 f64/f32 用例 |
| 7 | `run_all_tests.py` | wasm 套件用例数更新（原 93 例 → 增补后重数）；确认第 43 套仍是 wasm，不新增套件编号 |
| 8 | 文档 | 本文件阶段表 + §8 关系；`WASM_ROUTE_A.md` §类型表 f32/f64 从"未实现"更新为"TF 映射（P3 已通）"；`README.md` 同步 |

**wasm 浮点操作码映射（本次实现范围）**：

| wasm 操作码 | 语义 | TriRV 指令 | 档位 |
|---|---|---|---|
| 0x43 `f32.const` | 编译期 IEEE→TF20 | 数据段 raw + `FLW.S` | TF20 |
| 0x44 `f64.const` | 编译期 IEEE→TF45 | 数据段 raw + `FLW` | TF45 |
| 0x92..0x95 `f32.add/sub/mul/div` | TF 四则 | `FADD.S/FSUB.S/...` | TF20 |
| 0xA0..0xA3 `f64.add/sub/mul/div` | TF 四则 | `FADD/FSUB/...` | TF45 |
| 0x91 `f32.sqrt` / 0x9F `f64.sqrt` | TF sqrt | `FSQRT.S` / `FSQRT` | 对应档 |
| 0x5B..0x60 `f32.eq/lt/gt/le/ge` | TF 比较 | `FEQ.S/FLT.S/FLE.S` + 取反 | TF20 |
| 0x61..0x66 `f64.eq/lt/gt/le/ge` | TF 比较 | `FEQ/FLT/FLE` + 取反 | TF45 |
| 0x2A/0x2B `f32.load`/`f64.load` | 读 TF raw | `FLW.S`/`FLW` | 对应档 |
| 0x38/0x39 `f32.store`/`f64.store` | 写 TF raw | `FSW.S`/`FSW` | 对应档 |

**TDD 顺序**：
1. RED：先在 `trirv_wasm_e2e.py` 加 f64 常量+四则+比较的用例（期望值由 `trirv_float` 算），
   确认当前抛 `Unsupported`。
2. 按上表 1→7 顺序实现（先 VM 档位参数化，再汇编器，再 wasm 翻译器）。
3. GREEN：全部浮点例通过，且 **PyVM × C VM 字节差分一致**。
4. 全量回归（43 套，第 42/43 套 wasm/float 都绿）。

**验收标准**：
- `f64.const 1.5 + f64.const 2.25 → 3.75`、`f32.const 3.0 * f32.const 2.0 → 6.0` 等
  语义断言通过（由 TF oracle 给出期望）
- 双档在同一 wasm 模块里共存（一个函数用 f64、另一个用 f32），验证档位切换不串
- 三路差分：参考解释器（TF oracle）× PyVM × C VM 全部一致
- 全量 43 套保持全绿（不新增套件编号，wasm 套件扩例）
- **回归红线**：`trirv_float_isa_e2e.py`（27 项）与 `trirv_float_e2e.py`（144 项）必须仍 PASS

**范围边界（本阶段不做）**：
- 不做 float 与 i32 的互转（`f64.convert_i32_s` / `i32.trunc_f64_s`，0xB2..0xBF）——
  虽然有 FCVT 指令，但 wasm 转换的舍入/截断语义需单独定义，放 P4 与 libc 一起处理
- 不做 `f32.min/max/neg/abs/ceil/floor/trunc/nearest/copysign`（0x8B..0x98 除 add/sub/mul/div/sqrt）
- 不做 NaN payload / 非规格化数的 IEEE 语义（D-F4 已定抽象特殊值）
- 不做 wasm SIMD（v128 浮点）——本切片无 SIMD 类型
- 不做 C 编译器 float 类型（P4 之后）
- 不做浮点**形参**（需要 freg 传参 ABI，显式 `Unsupported`）；浮点**局部变量**已支持

### 5.3 P3 实施记录（2026-11-20，实测数据）

**交付结果**：
- `trirv_wasm_e2e.py`：112 PASS / 0 FAIL（原 93 例 + 19 浮点例：f64/f32 四则+sqrt、
  六比较、浮点局部变量 set/get、双档共存；三路差分参考解释器×PyVM×C VM 全一致）
- `trirv_float_isa_e2e.py`：39 PASS / 0 FAIL（原 27 + 10 TF20 档位 + 2 舍入回归）
- 全量 43 套 43/43 PASS
- C VM `TFFmt` 已参数化（bias/max_exp/mant_trits 全部走字段），`tf20_init()` 与
  `tf45_init()` 共用同一套 `tf_*` 函数；`step()` 与 `vm_run_cg()` 两个分发点都按
  `funct2` 选档；汇编器补 `f0..f31` 别名与 FLW.S/FSW.S/FSQRT.S/FEQ.S 等 F 助记符

**重要发现：`tf_ratio` 舍入修复（P2 遗留 bug，P3 暴露并修掉）**
- 现象：`f64.const 5.0 + f64.const 3.0` 在 wasm e2e 里 PyVM 输出最低 tryte=+2、
  C VM=-1，字节差分不一致（虽然两者 `decode` 都四舍五入到 8.0）。
- 根因：C `tf_dec` 用 `q + r/P_NORM` 拆法算 mant，5.0 的 `M=5×3^32` 走这条路得
  `1.6666666666666665`，Python `int.__truediv__` 给 `1.6666666666666667`，差 1 ulp。
  P2 注释声称"q+r/P 只舍入一次"是错的——它把 q 和 r/P 各自舍入后再相加，是二次舍入。
- 修复：新增 `tf_ratio(M,P)` 用整数移位 + round-to-nearest-even 一次到位：
  `e=floor(M/P)∈{1,2}`（正规化尾数 M∈[P,3P)），`q=round(M/P×2^(52-e))` 为
  53-bit 可精确转 double，再乘 `2^-(52-e)`（2 的幂，精确）。与 Python 大整数真除法
  逐位一致。
- 回归锁：`trirv_float_isa_e2e.py` 加 `FADD`/`FADD.S` 的 5.0+3.0 双 VM 字节差分断言，
  修复前必 FAIL、修复后 PASS。

**编译期穿透的诚实记账**：wasm `f32.const`/`f64.const` 的 IEEE 位模式在编译期由
宿主 Python `struct.unpack` 解成 host double，再由 `trirv_float.encode()` 量化成
TF raw 写进数据段（FCONST_BASE=267000），运行时 VM 只见 TF raw、只跑 F 指令。
IEEE 全程只是 wasm 字节码的**外部输入编码**，不进入 TriRV 运算语义——这是执行层/
表示层分离铁律在 wasm 边界的落地。精度损失按档位自主定义计（TF20 尾数 14 trit、
TF45 34 trit），不声称与 binary32/binary64 逐位等价。

---

### 5.4 P5 详细执行计划（2026-11-20 写定，按此执行）

**目标**：把立项书里三个悬着的评估问题全部用**实测数字**回答，据此裁决
"方案 B / 分离表示改造 / double-double / 硬件 FPU 直通"四个候选各自是否需要：

| # | 待答问题 | 出处 | 裁决所需数字 |
|---|---|---|---|
| Q1 | 转换税是否吃掉穿透收益？ | §7 风险 1、§2.1 的 ~50 条整数指令阈值 | F 指令单条成本的 VM 实测（ns 与 ×ADD 当量），及其构成分解 |
| Q2 | TF45 尾数装 double 的 ~1 ulp 误差是否可接受？要不要 double-double？ | §3.1 精度边界注、§7 风险 3 | decompose→compose 往返精确率 + 最大尾数误差（尾数量子计） |
| Q3 | 硬件 FPU 直通方案要不要立专项？ | §5 阶段表 P5 行 | 现模型的成本构成 + 真硬件路径的账（定义层，不立项则记录理由） |

**必须先讲明的实现事实（调研确认，2026-11-20）**：

P2 实现的 freg 与本文件 §5.1 计划写的分离表示**不一致**——实际实现是
**raw-in-freg**（`__uint128_t freg[32]` 存 45-trit raw，不是 `(sign, mant, exp3)` 三元组）。
连带后果（这就是 Q1 的测量主体）：

- `FLW`/`FSW` 是纯 raw 搬运（`load_word`/`store_word`），**零转换税**，成本 ≈ `LW`/`SW`；
- 转换税没有消失，而是**搬进了每条算术指令**：`FADD` = 2×`tf_dec`（raw→mant double）
  + 宿主 FPU 加法 + 1×`tf_norm/tf_compose`（mant double→raw）+ `%MOD_X`。
- §3.1 的"算术零转换税、只在 load/store 边界转换"是**分离表示**才有的性质。
  P5 诚实记账这个偏差，并用 M2 量化"分离表示能省多少"，裁决要不要改造。

**测量方案（三组，全部本机实测、命令可复现）**：

| 组 | 方法 | 产出 |
|---|---|---|
| **M1 VM 级单条成本** | 直线展开热循环（N=200k 条热指令，无循环体开销），变体：ADD（基线）/ LW / SW / FLW / FSW / FADD / FADD.S / FMUL / FMUL.S / FDIV / FDIV.S / FSQRT / FSQRT.S / FEQ / FEQ.S。程序用 `isa.encode` 直接构造（与 `trirv_float_isa_e2e.py` 同法），C VM 自带 `时间=/步数=` 计时，每项 3 次取最好，单条成本 = 时间/步数 | 每条 F 指令 ns 与 ×ADD 当量 → 回答 Q1 的"贵不贵" |
| **M2 宿主分解微基准** | 编译一个小 C 程序 include `trirv_float.h`（POW3 本地重建），直接计时 `tf_dec` / `tf_compose` / `tf_add` 整体 / 裸 `double` 加法（同依赖链防闭式） | FADD 成本 = 分发 + 2×tf_dec + FPU + compose 的构成表；**分离表示的潜在节省 = 2×tf_dec/条** → 回答 Q1 的"贵在哪"、§3.1 改造值不值得 |
| **M3 尾数承载精度** | Python 固定种子采样 5 万个随机正规 TF45 raw（M∈[3³³,3³⁴)），decompose → compose 往返；统计 raw 精确率与 \|mant_double×3³³ − M\| 最大误差（尾数量子计）；TF20 同法（预期 100% 精确，double 53 bit ≫ 22.2 bit） | 往返率 + 最大误差 → double-double 判定 → 回答 Q2 |

**决策账（M4，用既有实测数字，不新测）**：

- **方案 B（IEEE 位模式直存）**：D-F1 已按战略底线否决；P5 补上性能账——B 下每条算术
  都要在三进制指令上抽二进制位段，既有实测代理：wasm 位运算 372–436 条动态指令/op、
  C 72 位视图 753 条/op。即使最乐观的"只抽两个位段"，也远贵于 A 的实测税。
- **硬件 FPU 直通**：真三值硬件没有宿主 FPU 可穿透，也不需要"直通"——内存本就是
  base-3，原生 base-3 FP 单元读 raw 即是分离表示的硬件形态。结论预期：不立专项，
  作为 ISA 语义不变的硬件化注记写入 §7。

**改动文件清单**：

| # | 文件 | 改动 |
|---|---|---|
| 1 | `TERNARY_FLOAT_PROPOSAL.md` | 本节（计划）→ 实施后补 §5.5 实施记录；§7 风险表逐条用数字回答 |
| 2 | `bench_float.py` | 现有 A–E 节保留；新增 F 节（M1）、G 节（M2）、H 节（M3）、结论节重写 |
| 3 | `PERF_REPORT.md` | 增"浮点穿透成本"小节（引用 bench_float.py 数字） |
| 4 | `README.md` | 浮点小节同步 P5 结论一行 |

**验收标准**：
- `python3 bench_float.py` 一条命令复现全部数字（含 C VM 计时与 C 微基准编译运行）
- 每个结论都有实测数字支撑，无估计值；实现偏差（raw-in-freg）诚实入账
- 全量回归不破（bench 不进 `run_all_tests.py`，但 float 两套 e2e 必须仍绿）

**范围边界（本阶段不做）**：
- 不实现分离表示 freg 改造（只量化其潜在收益，作为后续优化项记录）
- 不实现 double-double 尾数承载（除非 M3 数据判定必须，则只立项不实施）
- 不做 F 指令的指令选择/调度优化（属编译器，阶段 4 LLVM 的事）
- 不改 ISA 语义与编码（P5 是评估，不是实现阶段）

---

### 5.5 P5 实施记录（2026-11-20，实测数据，`python3 bench_float.py` 一条命令复现）

**交付结果**：`bench_float.py` 新增 F/G/H 三节（M1/M2/M3），float 三套契约回归不破
（`trirv_float_isa_e2e.py` 39、`trirv_float_e2e.py` 144、`trirv_wasm_e2e.py` 112）。

**M1 —— VM 级单条成本**（直线 20 万条热指令，C VM 自带计时，3 次取最好，Apple M2）：

| 变体 | ns/条 | ×ADD | 变体 | ns/条 | ×ADD |
|---|---:|---:|---|---:|---:|
| ADD（基线） | 11.5 | 1.0 | **FADD** | **105.0** | **9.1** |
| LW | 10.0 | 0.9 | FADD.S | 107.0 | 9.3 |
| SW | 14.0 | 1.2 | FMUL | 102.0 | 8.9 |
| **FLW** | **10.5** | **0.9** | FMUL.S | 106.5 | 9.3 |
| **FSW** | **14.0** | **1.2** | FDIV | 102.0 | 8.9 |
| FSQRT | 62.0 | 5.4 | FDIV.S | 106.0 | 9.2 |
| FSQRT.S | 47.0 | 4.1 | FEQ | 101.0 | 8.8 |
| | | | FEQ.S | 105.0 | 9.1 |

两个直接读数：① **FLW/FSW ≈ LW/SW**——边界零转换税证实（raw 纯搬运）；
② **四则与比较全部聚在 ~102–107ns（8.8–9.3×ADD），与具体运算无关**——
FPU 加/乘/除/比较本体都是 ~1ns 级，聚簇说明贵的是公共路径（base-3 编解码）。
FSQRT 62ns < FADD 105ns 是内部一致性的活证据：它只解 1 个操作数（1×tf_dec），不是 2 个。

**M2 —— 宿主分解**（noinline 防闭式 + 变化实参，-O3 -march=native）：
`tf_dec` 独立 52.0ns；**VM 内交叉推导 43.0ns**（= FADD 105 − FSQRT 62，同一 PGO
二进制内差分，口径一致）；`tf_compose` 6.2ns；裸 double add/mul 0.9ns。
→ **FADD 的 ~88% 花在 base-3 编解码（2×解码+编码），FPU 运算本身 ~1ns。**

**M3 —— TF45 尾数 double 承载精度**（5 万随机正规 raw，种子 20261120）：
TF20 往返 **100.00% 精确**（double 53 bit ≫ 22.2 bit，承载网格更细）；
TF45 往返 **60.97% 精确，最大漂移 2.0 个尾数量子**（= 末 1~2 个 trit；根源：
double 间隔 2^-51/[2,3] 与 2^-52/[1,2] 比格式量子 3^-33 粗——承载网格比格式粗）；
exp3/sign 全程精确。两套 VM 同源承载 mant → 结果逐位一致（wasm e2e 112 例已证），
漂移只发生在"raw → 算术重组 raw"路径，FLW/FSW 直通的值不受影响。

**三问裁决（Q1/Q2/Q3，全部实测）**：

| 问题 | 裁决 | 依据 |
| --- | --- | --- |
| **Q1 转换税 vs 穿透收益** | 穿透收益成立但被编解码吃掉大头（88%）；税未超阈值但**位置错了**——raw-in-freg 把边界税搬进了每条算术指令（§2.1 的 ~50 条阈值当时问的是"边界编码贵不贵"，实测 FLW/FSW 零税、税在算术里 9.1×ADD） | M1+M2 |
| **Q2 double-double 要不要** | **暂不引入**，记演进项。漂移 ≤2 量子（末 1~2 trit），双 VM 同源逐位一致，e2e 差分不受影响；需要完整 34-trit 实现精度时再做 | M3 |
| **Q3 硬件 FPU 直通** | **不立专项**。真三值硬件没有宿主 FPU，也无此税——base-3 内存 + 原生 base-3 FP 单元读 raw 即分离表示的硬件形态，ISA 语义不变 | 定义层 |

**四候选裁决汇总**：
- **方案 B（IEEE 直存）**：双重否决维持——战略上违铁律（D-F1），性能上新账：每条算术
  要在三进制指令上抽二进制位段，既有实测代理 372–753 条动态指令/op，比 A 的 9×ADD
  税贵约两个数量级。
- **分离表示 freg 改造（§3.1 原设计）**：**列为后续 VM 优化项**（不改 ISA 语义与编码）。
  潜在收益：每条算术省 2×解码 ≈ 82% FADD（105→~20ns 级），代价是 FLW/FSW 各付一次
  解码/编码——算术密集链净赚，访存密集链持平。P5 只量化不实施。
- **double-double**：暂不引入（见 Q2）。
- **硬件 FPU 直通**：不立专项（见 Q3）。

**实现偏差诚实记账**：§5.1 计划写的 freg 是分离表示 `(sign, mant, exp3)`，P2 实际实现
为 raw-in-freg（`__uint128_t` 存 45-trit raw）。P5 量化了两者差距（上表），功能语义
无差（e2e 全绿），性能账记入上表。此项不回改 P2 文档——偏差由 P5 记录并给出裁决。

**方法论坑（记录，防复发）**：M2 首版用全常量实参调 `static noinline` 函数，
clang 做 IP 常量传播把调用整个编译期求值，测出 0.12ns 的假数字——与 PERF_REPORT
"MUL 与 ADD 同值"的测量污染同类。修复：实参逐轮变化（`raws[i % 1000]`）。
顺带修掉 `mants[j & 1023]` 对 1000 元素数组的越界读。

### 5.6 P4 详细执行计划（2026-11-20 写定，按此执行）

**目标**：重开 strtod/atof/ecvt/fcvt/gcvt 五项 DECISIONED SKIP（"TriRV 无浮点"的
理由已被 P2/P3 推翻——F 扩展与 TF 格式已在 ISA 上），以 **musl_tri 三进制原生重写**
落地，输出/输入均为 **TF45 raw**（45-trit 字，对 guest 就是普通 int）。

**必须先讲明的三件事**：

1. **为什么不需要编译器 float 类型**：这五个函数在 ABI 上全部是"字符串 ↔ 45-trit
   字"的转换——`double` 形参/返回值就是 TF45 raw，guest 里用 `int` 承载即可。
   trirv_cc 依旧零浮点支持；纯整数 C 子集可实现全部语义（3 字小数大数 + 走位）。
2. **正确舍入的算法责任在 guest**：strtod 的 round-half-even 不靠宿主——musl 用
   stdio 内部大数（`__floatscan`，这是它被 SKIP 的原因），TriRV 重写为 3 字
   （base-10¹⁶，容量 10⁴⁸≈3¹⁰⁰）小数大数走位算法，见下。
3. **与 musl 的语义对齐点**（逐条镜像，契约锁定）：
   `ecvt` 的 n∈{1..16} 保留否则 15 的无符号钳制怪癖、`fcvt` 的 n≤lz 返回静态零串
   切片（`"000…0"+14-n`）、`gcvt` 的 %g 规则（指数 <−4 或 ≥n → e 形态、去尾零）。
   ±Inf/NaN 在 ecvt/fcvt/gcvt 里 musl 本身未定义（走 sprintf 是垃圾路径）——
   **TriRV 定义**：数字串全 0、decpt=0、sign=符号位，契约锁定不再漂移。

**实现文件（按依赖顺序）**：

| # | 文件 | 内容 |
|---|---|---|
| 1 | `musl_tri/strtod_tri.c` | `strtod`+`atof`。十进制串 → TF45 raw。支持：空白/±/`inf`/`infinity`/`nan`（不区分大小写）/小数/e 指数；end 写回语义同 strtol_tri（无有效数字 end=nptr）。不支持（文档注明）：十六进制浮点 `0x1p3`、errno/ERANGE |
| 2 | `musl_tri/ecvt_tri.c` | `ecvt`+`fcvt`+`gcvt`。TF45 raw → 十进制数字串。静态 buf 用**全局数组**（TriRV C 子集无 static 局部；单线程假设与 musl 相同） |
| 3 | `trirv_float_libc_e2e.py` | 契约：**Fraction 精确 oracle**（宿主 Python 用纯整数算 round-half-even——这就是"正确舍入"的定义本身）× guest 输出 × 双 VM 字节差分 |
| 4 | `run_all_tests.py` | 注册新套件 |
| 5 | `batch_musl_compile.py` | TRI_REWRITE 加 5 项（strtod.c/atof.c→strtod_tri.c；ecvt.c/fcvt.c/gcvt.c→ecvt_tri.c），重算总账 |
| 6 | `README.md` §五/§六 | SKIP 5→0；浮点系决策记录从"演进立项"更新为"P4 已落地" |

**核心算法（两文件共用思路，方向相反）**：

- **定长走位**：6 字数组 `w[6]`（小端 base-10¹⁶，容量 10⁹⁶≈3²⁰¹），只做
  `字×小因子`（10、10¹³、3ᵏ≤3⁹ 时单字积 <3⁴⁵ 不溢出）与 `÷小因子带余数`——
  零 90-trit 乘法。选 6 字不是 3 字的理由（tie 间隙分析）：十进制输入（≤20 位
  有效数字）与 TF45 半点（分母 2×3³³）之间的最小可能距离 ≥ 10⁻³⁷·⁶（值域单位），
  走位误差必须压到 10⁻³⁸ 以下才能在对抗性输入上保证正确舍入——6 字窗口纪律
  （只在 acc≥10⁸⁰ 时做有损步，每步相对丢失 ≤10⁻⁵⁴~10⁻⁶⁹，步数 ≤3300，总预算
  ≤10⁻⁵¹）比阈值低 13 个数量级。
- **strtod（十→三）**：解析 ≤20 位有效数字进 acc（此后数字只置 sticky）；
  不变式 `R = A×10^P/3^t`，四类步：×10¹³（精确，P−13）、×3ᵏ（精确，t+k）、
  ÷3ᵏ（有损，t−k，仅 A 满 时）、÷10¹³（有损，P+13，仅 A 满时）——一切有损步
  都使 R 从下方单调逼近真值；P 归零后一次性**精确终除**：u = |A|₃−33，
  (M₀, disc) = A ÷ 3ᵇ（÷3 共 u 步、disc 累加 r·3ⁱ 精确回收），按 2·disc vs 3ᵘ
  half-even + sticky 舍入。|P| 捷径界 ±14107（TF45 值域 10^±14086 的数学必然，
  非估算）。
- **ecvt（三→十）**：raw 拆 (s,M,e)；不变式 `V = A×3^f/10^g`（A=M 起），吸收 f→0
  （同窗口纪律，每步丢失 ≤10⁻⁷⁰，步数 ≤29523）；A 归一到 [10⁴⁴,10⁴⁵) 后
  decpt=45−g；逐位提取 = A×10 后做 45 步 ÷10 带余数累加（精确，disc 即余数），
  末位按 2·disc vs 10⁴⁵ half-even（此时 tie 可能出现：V 的分母含 3ⁿ 时十进制
  展开不终止——sticky 进位裁决）。
- **正确性公理**：十进制串无法精确落在两个 TF45 值正中（tie 需分母含 3³³ 且
  3 不整除分子 → 十进制展开不终止），strtod 的 tie 分支只由 sticky 驱动；
  e2e 用 Fraction 精确 oracle 千例级随机 + 构造用例验证——oracle 即"正确舍入"
  的定义本身，不依赖任何浮点实现。

**验收标准**：
- `trirv_float_libc_e2e.py`：strtod ≥300 例（随机数量级/指数/长度 + inf/nan/±0/
  粘着位构造 + end 语义）、ecvt/fcvt/gcvt ≥300 例（全阶码随机 raw + 特殊值 +
  musl 怪癖边界 n=0/16/17、lz 路径），Fraction oracle 全对 + 双 VM 字节差分全一致
- `batch_musl_compile.py`：浮点 5 文件从 SKIP 转 `[tri]` 编译 OK，总账重算
- float 既有三套契约（144/39/112）不破；全量回归不破

**范围边界（本阶段不做）**：
- 不做 strtof/strtold（TF20 变体，按需后续）
- 不做十六进制浮点 `0x1p3`（IEEE 位语义的表示层，TriRV 不认）
- 不做 errno/ERANGE（TriRV libc 层无 errno 传递，同 strtol_tri 先例）
- 不做 trirv_stdio 的 %f/%e/%g（gcvt 自拼串；printf 集成属 stdio 层后续）
- 不做 <stdlib.h> 头文件系统（契约用原型声明，同既有 e2e）

### 5.7 P4 实施记录（2026-11-20，实测数据）

**交付结果**：
- `trirv_float_libc_e2e.py`：**7447 PASS / 0 FAIL**（strtod 344 例全 corpus、
  ecvt/fcvt/gcvt 全 corpus 随机 raw + 全部特殊值 + musl 怪癖边界，
  Fraction 精确 oracle × PyVM/CVM 双 VM 字节差分；--quick 模式 4256/0 约 21 秒）
- 批量编译管线：musl string+stdlib 66 文件 **65 编译 OK / 1 SKIP / 0 FAIL**
  （浮点 5 文件 SKIP→[tri]；唯一剩余 SKIP = explicit_bzero 内联汇编屏障）
- 全量回归：run_all_tests 38 套全 PASS（第 44 套注册）

**实现要点**：
- `strtod_tri.c`：≤20 位有效数字的 6 字小数大数走位（base 10^16，容量 10^96）+
  LSB-first 加权 disc 累加（修正版：旧 disc×3+r 位序反了）+ 精确终除
  half-up 舍入；值域捷径 exp10+sig ≥ 14100 → ±Inf、≤ −14130 → ±0
- `ecvt_tri.c`：同构走位反向（TF45 raw → 十进制），S' = Σ(2−r)·3^i 补数
  同步累加免存 3^u；decpt = 45−g；±Inf/NaN/±0 按合同 §5.6 TriRV 定义
- inf/infinity/nan 接受（不区分大小写）；end 指针 POSIX 语义
  （'-0' 是合法转换 end=2；纯空白/裸符号无转换 end=nptr）

**重要发现：链接器全局槽不重定位（跨文件）**
- trirv 链接器对 `-l` 多单元的全局槽各自从 207 号字起分配，**不重定位**：
  主文件的提升局部量/全局数组与库单元全局在同一槽碰撞。实测：ecvt chunk
  主文件 IN[40] 数组恰好覆盖 ecvt_tri.o 的全部全局量（g_ec_w/g_ec_buf/
  g_ec_zeros/SD_E_* 共 57 槽中的前 40），输入装载把库的全局态踩烂 →
  ec_core 死循环（ec_mul 热点 300 万步不 halt）
- 规避：harness 全局最小化（raw 内联为字面量、结果逐 call write）+
  per-call 隔离（每 call 一个独立 guest 程序，48/48 bisector 已证安全）
- 待办（编译器工程，非 P4 范围）：跨单元全局重定位

**重要发现：strtod tie-rescue 的 sticky 误设**
- 终除 ÷3 循环里 sticky=1 是错的：÷3 的余数 r 已加权进 disc（无丢失），
  sticky 应只标记真丢失（parse 丢弃数字、P<0 走位 ÷10 截断）。误设后
  sticky 恒 1，rescue（gap≤2 半升）对 frac∈(0.49, 0.5) 的正常下取整误升格
  → 尾数 +1（实测 '529675342618594954' frac=40/81=0.4938 误升）
- 移除后 400 例随机 + tie 专项全绿；ecvt 的 sticky 语义不变（其 ÷3 真丢失）

**负 raw 归一（INF_NEG 类）**：s=1 且 E 大的 raw（≥1.477e21）在 45-trit
平衡解读下为负数——ecvt/fcvt/gcvt 入口先归一（raw<0 → +2×3^44）再拆符号。

**Python oracle 自校验**：exact_round（bit_length 跳步归一 + 单位步校正，
denormal 0.0001s）对 300 随机 double 逐位匹配 trirv_float.encode（0 mismatch）。

**方法论坑（TDD 教训，防复发）**：
- Python 字符串 replace 静默 no-op（目标文本已变）——补丁后必须 grep 验证
- 采样诊断要先确认进程身份（pkill 后老进程可能残留，ps 双跑混淆判断）
- per-(raw,nn) bisector 单测全过 ≠ chunk 组合正确（全局态残留只在大
  程序暴露）——最小复现必须含真实状态链

---

## 6. 待拍板决策项

| 编号 | 决策点 | 选项 | 结论 | 状态 |
| --- | --- | --- | --- | --- |
| **D-F1** | 格式方案 | A 三进制原生 / B binary64 位模式直存 | **A** | ✅ 2026-11-20 拍板 |
| **D-F2** | 阶码/尾数宽度 | 双档各一套 / 其他 | **TF20=5+14、TF45=10+34** | ✅ 2026-11-20 P1 实测锁定（见下） |
| **D-F3** | 精度档位 | 单档 TF / 双档 TF20+TF45 | **双档 TF20+TF45** | ✅ 2026-11-20 拍板（wasm 零换算） |
| **D-F4** | NaN/下溢语义 | 抽象特殊值 / 照搬 IEEE 位语义 | **抽象特殊值** | ✅ 2026-11-20 P1 落地（±0/±Inf/NaN 抽象值，无 payload） |

### 6.1 P1 实测结果（`python3 bench_float.py`，2026-11-20）

| 项 | TF20 | TF45 |
| --- | --- | --- |
| 尾数精度（执行层度量 = trit） | 14 trit | 34 trit |
| 尾数精度（表示层换算，非定义） | ≈ 6.2 十二进制位 | ≈ 15.0 十二进制位 |
| 动态范围（执行层 = base-3 整数指数） | exp3 ∈ [-120,120] | exp3 ∈ [-29523,29523] |
| 往返契约 | 144 PASS / 0 FAIL（含 ±0/±Inf/NaN、3^e、舍入、极端阶码分离往返） | 同左 |
| 全阶码可表示 | PASS（mant 恒有限、exp3 精确） | PASS（含 exp3=29523 远超 3^646） |

**裁决 D-F2 = 5+14 / 10+34**：宽度由字长与档位需求自主定义，P1 实测数值已入账。
二进制 f32/f64 的位数与值域**不作为验收标准**——"64 位"是 IEEE 软件规范，不是物理约束。
若 P3 wasm 对接发现精度需求变化，按实现层账调整，不改架构。

> **框架更正记录（2026-11-20）**：P1 初稿曾把"TF45 有 28877 个阶码超出 double 上限"标为
> "重大风险：穿透缺口"。该论证错误地把 IEEE binary64 的软件值域（3^646）当成格式天花板。
> 更正后的正确模型是**尾数/指数分离**（§3.1）：mant∈[1,3) 进 FPU、exp3 走整数 ALU，
> double 值域从不约束整数指数。`decompose()`/`compose()` 已实现并实测：
> TF45 最大正规阶码 3^29523 仍能拆成有限 mant + 精确 exp3，**不存在穿透缺口**。
> 原来的 (a) 超限返 Inf / (b) tagged 联合 / (c) 软件路径三选项全部作废 —— 问题本身不成立。

---

## 7. 风险与开放问题

- **转换税可能吃掉穿透收益** ✅ **P5 已实测回答（§5.5）**：税真实存在——FADD 105ns
  （9.1×ADD），其中 ~88% 是 base-3 编解码、FPU 本体 ~1ns；但它没吃掉穿透（对比方案 B
  的位段抽取 372–753 条动态指令/op 仍便宜两个数量级），只是**位置错了**：raw-in-freg
  把边界税搬进了每条算术指令。分离表示改造列为 VM 优化项（省 2×解码 ≈ 82% FADD）。
- **舍入的双语义**：三进制 round-to-nearest 与 IEEE round-to-nearest-even 在 tie 点可能不同
  （base-3 的"中点"与 base-2 不同）。P1 已明确定义并测试（144 项），不照搬 IEEE。
- **尾数穿透的物理代价（诚实记账）** ✅ **P5 已实测回答（§5.5）**：TF45 decompose→compose
  往返 60.97% 精确、最大漂移 2.0 个尾数量子（TF20 100% 精确）；exp3/sign 全程精确；
  两套 VM 同源承载、逐位一致，e2e 差分不受影响。**double-double 暂不引入**，
  记演进项——需要完整 34-trit 实现精度时再做，不改 ISA 语义。
- **wasm 档位对接**：wasm f32/f64 的值转成 TF20/TF45 时的精度差异按各档自主定义计，
  不以二进制位宽为参照，也不声称"零损失"——实测为准。
- **真值电路路径**：本文的穿透是虚拟层实现选择；换成真三值硬件时 FPU 不存在，
  需要原生 base-3 浮点电路。本立项不改 ISA 语义，硬件化在后 —— 与整数 DIV 的处理一致。

---

## 8. 与既有文档的关系

- ISA 规范 `ternary-isa-design.md`：本立项通过后，新增 F 扩展章节与 opcode 分配。
- README §六：浮点系从"永久 SKIP"更新为"演进立项，见本文件"。
- `WASM_ROUTE_A.md` §类型表：f32/f64 从"未实现"更新为"TF 映射（P3 后）"。
- 精神继承：ISA §1.2（载体 vs 语义）、§1.1（三本账）、DIV 快路径的穿透先例。
