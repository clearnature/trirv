# TriRV —— 三进制 RISC-V 风格的指令集架构

> 名字来自 **Ternary + RISC-V**。它是一个 **ISA 规范**，当前以**软件虚拟机**的形式实现，
> 不是物理芯片，也不是编译器。最后核对：**2026-11-19 08:07**（数据均为本机实测）。

## 一、三层身份，不要混

| 层次 | 是什么 | 在哪 |
| --- | --- | --- |
| 规范 | TriRV ISA：45-trit 字、32 寄存器（x0–x31，与 RV32I 同名同号）、20-trit 定长指令、六种格式、27 syscall | `ternary-isa-design.md`、`ternary-abi-minimal.md` |
| 实现 | 虚拟机（"硅片"就是解释器）：取指/译码/执行 | `trirv_vm.py`、`trirv_vm.c`、`trirv_isa.py` |
| 工具链 | C 编译器、汇编器、编码器、libc | `trirv_cc.c`、`trirv_asm.py`、`trirv_encoder.py`、`trirv_libc.c` |

编译器的"栈式 vs 寄存器式"是**工具链内部**的代码生成策略，不是 TriRV 的属性。
TriRV 从第一天就是 Load-Store 架构，没有"栈机器"模式。

**"实现"这一层跑在二进制宿主上，不污染"规范"那一层。** 指令执行的四个步骤：

```
取指  4 个 tryte（每个 0..242）→ 合成 20 trit 指令字
译码  按 t19..t0 切字段
执行  寄存器值是 __uint128_t 容器里的 0..3^45-1，按 3 的幂做运算
写回  rd = 结果 mod 3^45
```

`__uint128_t` 是**载体的容器**，不是语义——宿主只有二进制整数类型，只好用它装
一个三进制数值；所有进出容器的运算都按 3 的幂定义（`divmod243`、`p *= 243`、
`mod 3^45`）。换成三值电路，这套语义一个字都不用改。逐条审计见规范 §1.2，
**结论：语义层已无二进制依赖**（历史上唯一一处 `AND/OR/XOR` 直调宿主 `& | ^`
已于 2026-11-19 改为逐 trit）。

> 测成本必须用 **C VM**（Python VM 的 ~76 ns/条 是解释器开销）。
> 但 C VM 的墙钟也只能比较**同一宿主上的优化**，不能论证架构成本——
> 宿主的硬件乘除法器会严重低估真实数据通路代价（`DIV` 在 C VM 上只比 `MUL` 贵
> 2.7 倍，按 tryte 原语算是 55 倍）。跨架构比较只认 tryte 原语数，见规范 §1.3。

## 二、定位与技术路线（重要）

**战略：以开源 RISC-V 为规范和标准，只替换"进制"这一层，不另建生态。**

二进制 → 三进制是**底层表示的变更**，不是重新设计一套 ISA。语义、ABI、工具链约定
应当尽量贴着 RISC-V 走，生态才有接续的可能。第一步必然是**虚拟机（软核）**先跑通，
硬件化在后。

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| 1 | 虚拟机软核 `trirv_vm`：取指 / 译码 / 执行 + 27 syscall | ✅ 已跑通 |
| 2 | **栈式 masm 先行**：手写汇编 / 宏汇编把程序跑起来 | ✅ 可用 |
| 3 | 自研 C 后端 `trirv_cc.c`：过渡实现与验证 | ✅ 覆盖 88% |
| 4 | **LLVM 寄存器式后端**：正式优化路径 | ⬜ 未开始 |

阶段 3 是自研的过渡手段（已验证寄存器式能带来 6× 级提升），最终寄存器式优化交给 LLVM，
不长期维护自研后端。

### 与 RISC-V 的差距：先分清哪本是定义账，哪本是成本账

**口径（2026-11-19 修正）**

三进制与二进制的规格差异是**预期内的**，也是**不可比的**。混在一起谈会得出错误结论：

| 账本 | 内容 | 怎么判 |
| --- | --- | --- |
| **定义层** | 基数、字长、值域、地址单位 | **不比优劣**。宽度是定义出来的，想要多大就多大；拿"能装多大的数"分高下没有意义 |
| **语义层** | RV32I 指令语义、ABI、C 语义 | **必须一致**，靠差分测试判 |
| **实现层** | 静态指令数、动态步数、墙钟、单步成本 | **必须实测**，只认可复现数字 |

代价只出现在第三本账上，且只有一种形态：把**外来的二进制语义**（wasm 的 mod 2^32、
8-bit 编址）搬到三进制机器上时，那条语义必须靠**额外指令**去模拟。反方向同理——
把三进制语义搬到二进制机器上要付 base-243 打包税（实测逐 trit 60.8ns vs 128 位打包 1.62ns）。
**两笔税都按指令计，不按位计。**

**A. 定义层差异（预期内，不作优劣比较）**

| 项 | RISC-V RV32I | TriRV |
| --- | --- | --- |
| 指令字 | 32 bit 定长 | 20 trit 定长（3²⁰≈2³¹·⁷） |
| 数据字 | 32 bit | 45 trit = 9 tryte |
| opcode | 7 bit 定值（LUI=0x37） | 3 trit（LUI=7） |
| 移位 | ×2 / ÷2 | ×3 / ÷3（三进制下移一位就是乘除 3） |
| 立即数 | 12 / 20 bit | 7 / 13 trit |

**B. 已解决的偏离**

| 项 | RISC-V | TriRV | 状态 |
| --- | --- | --- | --- |
| 寄存器数 | 32 个（5-bit 字段） | **32 个**（4-trit 字段，吃掉原空闲的 t6/t12/t16） | ✅ 2026-11-19 已对齐 |
| 别名编号 | x0–x31 | **x0–x31 完全一致**（t3-t6=28..31、s2-s11=18..27） | ✅ 已对齐 |
| LB/LH | 符号扩展 | **符号扩展**（5 trit −121..121、10 trit −29524..29524） | ✅ 2026-11-19 已对齐 |
| LBU/LHU | 零扩展 | **零扩展** | ✅ 一致 |

**C. 不是偏离的地方（容易误判）**

`char` 在 TriRV 里是**无符号**的 0..242。这看起来像偏离 RISC-V，其实完全一致——
RISC-V psABI 规定 plain `char` 默认无符号（GCC riscv 后端 `DEFAULT_SIGNED_CHAR = 0`），
TriRV 的 tryte 值域与它天然吻合。编译器据此对 `char`/`unsigned char` 发 `LBU`，
只有显式 `signed char` 才发 `LB`。

**D. 唯一的语义偏离：位运算**

RISC-V 的 `AND/OR/XOR` 是**二进制**按位；三进制寄存器里没有二进制位，
所以 TriRV 把它们定义为**逐 trit 三值逻辑**（2026-11-19 决议 B）：

| 指令 | 语义 | 同义 |
| --- | --- | --- |
| `AND`/`ANDI` | 逐 trit $\min$ | ≡ `TAND`/`TMIN` |
| `OR`/`ORI` | 逐 trit $\max$ | ≡ `TOR`/`TMAX` |
| `XOR`/`XORI` | 逐 trit $(a+b)\bmod 3$ | 三进制的"不进位加法" |

实测：`AND 12,10 = 9`、`OR = 13`、`XOR = 22`（二进制会是 8/14/6）。
这不是偷懒——二进制按位在三进制硬件上要么拆位（wasm 端实测 372–436 条指令/次），
要么根本不存在。连带后果：**C 的 `& | ^` 需要二进制语义，编译器现在明确报错**
（`UNSUPPORTED: 二进制位运算 &`），而不是静默算成三值 min。详见
`ternary-isa-design.md` §5.1。

**至此，TriRV 与 RV32I 在编码格式、寄存器命名、ABI、指令语义四个层面全部对齐，
剩下的差异只有三进制物理决定的位宽与进制换算，以及上面这一条位运算语义——
都属于定义层，不作优劣比较。**

指令语义覆盖率：**RV32I 40 条全部有对应**。

扩展现状：TM 乘除 8/8 ✅、Zicsr 6/6 ✅、TA 原子 9/11（缺 AMOMINU/AMOMAXU）、
特权仅 M 态（缺 SRET/URET、S 态、SFENCE.VMA）、无 FENCE.I、TC 压缩未实现、无 F/D/V。

## 三、目录导航

| 文档 | 内容 |
| --- | --- |
| `ternary-isa-design.md` | ISA 规范：格式、opcode、编码、§9 五项设计决策 D1–D5 |
| `ternary-abi-minimal.md` | ABI：类型宽度、寄存器别名、调用约定 |
| `trirv-unified-memory-abi.md` | 统一内存模型、栈帧、编译器 C 子集能力矩阵 |
| `trirv-firmware-architecture.md` | 跑在 TriRV 上的实时固件（RMS 调度、零运行期分配） |
| `PERF_REPORT.md` | 性能：编译期 Python→C 后端、运行期按 VM 代价定向 |
| `CC_寄存器式编译器说明.md` | 编译器内部：可写性约定、目标驱动赋值、寄存器池 |
| `WASM_ROUTE_A.md` | Wasm 栈式字节码 → TriRV 翻译器：内存模型取舍、指令映射、实测与限制 |

## 四、快速开始

```bash
./build.sh                                  # 构建 trirv_cc + trirv_vm（PGO 三阶段）

./trirv_cc foo.c foo.trv foo.asm            # C -> TRV 二进制（第 3 参数可选，输出汇编清单）
python3 export_trv.py foo.c foo.trv         # 同上，但经 trirv_cc_backend（默认仍调 C 后端）
TRV_CC=py python3 export_trv.py foo.c foo.trv   # 强制 Python 参考后端做对照

./trirv_vm foo.trv                          # 执行
./trirv_vm -n 40 -t 1 foo.trv               # 多实例多线程压测
./trirv_vm -o 1000 foo.trv                  # dump 地址 1000 起的 9 tryte

python3 trirv_wasm.py foo.wasm foo.trv foo.s   # Wasm 字节码 -> TRV（第 3 参数可选，输出汇编）
python3 trirv_wasm_e2e.py                      # 93 例三路差分（参考解释器 / PyVM / C VM）
python3 bench_wasm.py                          # Wasm 直译 vs C 寄存器式 的代价对比
python3 bench_bitops.py                        # 位运算在三进制上的真实成本（拆位数/op）
python3 trirv_tritlogic_e2e.py                 # AND/OR/XOR 逐 trit 语义锁定（两套 VM 差分）
python3 trirv_tritref_test.py                  # tryte 数组参考实现：43456 项差分 + trit 级成本表
```

实测一条完整链路（2026-11-19 验证）：

```
$ printf 'int main(){int s=0;int i=1;while(i<=100){s=s+i;i=i+1;}return s;}\n' > /tmp/t.c
$ ./trirv_cc /tmp/t.c /tmp/t.trv /tmp/t.asm
  [trirv_cc.c] compiled /tmp/t.c: 12 instructions, 0 strings -> /tmp/t.trv
$ ./trirv_vm /tmp/t.trv
  [TriRV C VM] 指令=12 步数=609 exit=186 halted=1
```

## 五、当前状态（2026-11-19 08:07 复测）

| 套件 | 结果 |
| --- | --- |
| `test_isa_completeness.py`（编解码往返 + 语义 + D1–D5 守卫） | **86/86** |
| `trirv_difftest.py`（Python VM vs C VM） | 11/11 |
| `trirv_mdiff.py`（M 扩展差分，含 3⁴⁵ 边界） | 700/700 |
| `trirv_csr_diff.py`（CSR/trap 差分） | 4/4 |
| `trirv_charsign_e2e.py`（char 符号性 / LB-LBU 语义） | 21/21（含 C VM 交叉 21/21） |
| `trirv_tritlogic_e2e.py`（AND/OR/XOR 逐 trit 语义锁定） | 42/42（期望值 × PyVM × C VM） |
| `trirv_tritref_test.py`（tryte 数组参考实现，无二进制容器） | 43456/43456（差分）+ 3 个端到端程序 |
| `*_e2e.py`（C 编译到执行全链路） | 14/14 套（`TRV_CC_STRICT=1` 下 12/14，缺 struct 按值） |
| `trirv_wasm_e2e.py`（Wasm 三路差分：参考解释器 / PyVM / C VM） | **93/93** |
| `trirv_fuzz.py` / `_mem` / `_csr` | 1500 / 1000 / 800 case，字节级 MATCH |

性能（对比 Python 栈式后端，32 寄存器 + LB/LH 对齐后复测）：

```
源程序            Python 编译   C 编译    加速    指令(Py)  指令(C)   收缩
小(2 行)           125.6ms   2.4ms   52.0x      133      22    6.05x
中(41 函数)        125.0ms   3.0ms   41.4x     4768     631    7.56x
大(201 函数)       128.1ms   5.5ms   23.1x    23328    2476    9.42x

热循环 20 万次 s=s+i*3-i/2：2020 万步 0.2368s → 240 万步 0.0380s（6.23×）
```

## 六、已知缺口

1. **C 后端覆盖 130/148 = 88%**：剩下 18 例全是 struct 按值传参 / sret 返回，
   代码里显式 `exit(2)` 拒绝而非静默错编，走 Python 兜底（约 100 行工作量）。
   > ⚠️ **验证陷阱**：默认模式下 e2e 全绿，是因为失败用例悄悄回退到 Python 后端。
   > 考核 C 后端必须加 `TRV_CC_STRICT=1`（禁回退）。实测：14 套里 12 套纯 C 通过，
   > 2 套（`byvalue`、`stackargs` 的 struct 用例）因上述缺口失败——与 88% 覆盖率一致。
2. **VM 的 DIV 慢**：无硬件除法器，走 72 步逐位恢复除法（~150ns）。优化方向是乘法逆元估商，
   预计 1.5–2×。
3. **动态指令少 8.4× 但墙钟只快 6.5×**：说明单步成本随指令类型差异极大（MUL ~70ns、DIV ~150ns），
   后续要么优化 VM 除法，要么让编译器继续避开贵指令。
4. **1-tryte 局部变量一律落栈**：`char`/`signed char`/`unsigned char` 不再走寄存器分配。
   原因是寄存器里没有"截断到 5 trit"的归一化点，`signed char c=200` 会原样返回 200 而不是 −43。
   落到栈上后，写用 `SB`（mod 243 截断）、读用 `LB`/`LBU`（按符号性扩展），语义与 C 一致。
   代价是每次读写多一次访存，但这些变量本来就在热循环里很少见。

## 七、Wasm 前端（路径 A，2026-11-19 落地）

三进制能不能跑栈式字节码？**能，已跑通。**

- `trirv_wasm.py`：Wasm 二进制 → TriRV 汇编。**操作数栈驻留寄存器**（t0–t6），
  leaf 函数的局部变量驻留 s1/s2，`local.get x … op … local.set x` 就地更新
- 三路差分 93/93：参考解释器 × Python VM × C VM，含 hello world 中文输出字节一致
- 关键选择：**1 wasm byte = 1 tryte，地址零偏移**（寻址零换算指令）；
  值域差（243 vs 256）属定义层，不计成本，**成本只算"模拟 mod 2^32 / mod 2^8 的规整指令"**
- 实测（求和 1..100000）：动态步数 4,700,071 → **2,245,770（−52.2%）**
  - 对 C 寄存器式（int=45 trit）：4.49× 动态 / 2.99× 墙钟 —— **含语义税，非对等比较**
  - 对 C 寄存器式（手写 i32 归正）：**1.50× 动态 / 1.45× 墙钟** —— 公平对照
  - 单步成本 7.7 vs 8.0 ns，差距全在指令条数，VM 没变慢
- **位运算已支持**（and/or/xor/shl/shr/rot/clz/ctz/popcnt）：32 轮无除法拆位，
  代价 372–436 条/op —— 基数转换的物理成本，实测见 `bench_bitops.py`
- 不支持：i16/i64、br_table、call_indirect
- 详见 `WASM_ROUTE_A.md`

## 八、最近话题（2026-11-19）

- **战略定调**：以开源 RISC-V 为规范和标准，只做进制层面的底层替换，不另建生态；
  软核（虚拟机）先行，栈式 masm 先跑通，LLVM 寄存器式后端后期接入。
- **D3 撤销，寄存器 27 → 32**：4-trit 字段吃掉原空闲的 t6/t12/t16，20 trit 恰好用满，
  立即数布局零改动；别名表恢复 RISC-V 标准编号（t3-t6=28..31、s2-s11=18..27）。
  改动落在 `trirv_isa.py`（字段/NREG/决策）、`trirv_asm.py`（别名表）、两套 VM、`trirv_cc.c`
  （regname、REGTAB、TPOOL、PP_POOL、内部编码器 encR/I/S/U）。
- **LB/LH 改回符号扩展，与 RISC-V 语义对齐**：原先 `LB`≡`LBU`、`LH`≡`LHU` 全零扩展，
  是为了配合"char = 0..242 无符号"的 ABI；但 RISC-V psABI 本身就规定 plain `char` 无符号，
  所以 char 保留无符号、指令语义回归 RISC-V，两者并不冲突。现在 `char`/`unsigned char` 走
  `LBU`，只有 `signed char` 走 `LB`（−121..121）。
- 澄清 TriRV 定位：ISA 规范 / 虚拟机 / 工具链三层，不是"编译器"。
- 逐条核对 RISC-V 符合性：40 条语义全覆盖；差距只剩物理必然 5 项，已对齐 5 项、待决策 0 项。
- 同步文档：§6 opcode 表补齐 13–18 实际分配；刷新过时计数。
- 修复 `trirv_csr_diff.py` D3 用例构造错误：`mtvec=8` 指向非法指令自身形成自陷循环、
  `mepc+1` 应为 `+4`。修后两版 VM 一致，CSR 差分 3/4 → 4/4。
