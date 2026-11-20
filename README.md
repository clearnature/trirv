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
> C VM 的**绝对墙钟**不代表硬件周期（宿主 `ADD` 一条 128 位指令，硬件是 9 个 tryte
> 的串行进位），但**相对成本与 tryte 模型同量级**（`DIV/MUL`：VM 2.7×、模型 5.0×）。
> 模型独有的价值是"成本随宽度变化"，VM 的二进制运算宽度无关。见规范 §1.3。

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
| `TERNARY_FLOAT_PROPOSAL.md` | 浮点扩展 TF 立项：格式、穿透 FPU 模型、F 指令草案、分阶段计划、待拍板 D-F1..D-F4 |

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
python3 bench_div.py                           # DIV 快路径 vs 72轮恢复除法 实测(ns/op)
python3 bench_float.py                         # TF 浮点精度/动态范围/全阶码可表示实测
python3 trirv_tritlogic_e2e.py                 # AND/OR/XOR 逐 trit 语义锁定（两套 VM 差分）
python3 trirv_tritref_test.py                  # tryte 数组参考实现：46164 项差分 + trit 级成本表
python3 trirv_tdd_contract.py                  # 契约测试（TDD，83 项：字段/执行/立即数/STRICT/代数律/struct ABI/移位）
python3 run_all_tests.py                       # 全量：一条命令跑完 41 套（32 快 + 9 慢），输出汇总表
python3 run_all_tests.py --fast                # 只跑快套件（跳过 fuzz / wasm / 46164 差分）
```

实测一条完整链路（2026-11-19 验证）：

```
$ printf 'int main(){int s=0;int i=1;while(i<=100){s=s+i;i=i+1;}return s;}\n' > /tmp/t.c
$ ./trirv_cc /tmp/t.c /tmp/t.trv /tmp/t.asm
  [trirv_cc.c] compiled /tmp/t.c: 12 instructions, 0 strings -> /tmp/t.trv
$ ./trirv_vm /tmp/t.trv
  [TriRV C VM] 指令=12 步数=609 exit=186 halted=1
```

## 五、当前状态（2026-11-20 全量复测：`run_all_tests.py` 41 套全 PASS；真 musl string+stdlib 双目录 60/66 编译、6 项决策 SKIP、0 失败）

| 套件 | 结果 |
| --- | --- |
| `run_all_tests.py`（全量入口，三票否决：退出码 / `[FAIL]` / Traceback；STRICT 默认开启） | **41/41 PASS** |
| `test_isa_completeness.py`（编解码往返 + 语义 + D1–D5 守卫） | **86/86** |
| `trirv_tdd_contract.py`（契约测试：寄存器字段 / 立即数越界 / STRICT / 代数律 / struct ABI / 嵌套调用 / 移位） | **83/83** |
| `trirv_crt_e2e.py`（**crt0 + musl 启动链**：全局变量 / argc-argv / `__libc_start_main` / 函数指针 / 双 VM 零页 ABI 差分） | **29/29** |
| `trirv_musl_e2e.py`（**musl 风格 libc 层 + mini-busybox**：typedef/const/do-while/三目/复合赋值/cast、string+stdlib API、8 个 applet、stdin 文件/管道/argv 三通道） | **48/48** |
| `trirv_link_e2e.py`（**多文件链接**：`-l` 库文件拼接、跨文件调用、重复定义检测、原型声明、busybox 正式分层 `-l musl -l crt`） | **14/14** |
| `trirv_octet_e2e.py`（**8-bit 视图层**：read8/write8 边界编解码，octet↔双 tryte，全 256 值无损往返，musl 的 CHAR_BIT 依赖在此满足） | **13/13** |
| `trirv_trvo_e2e.py`（**TRVO 目标文件**：中间格式导出/导入、未定义引用检测、跨单元标签改写 `_L*`→`_X<单元>_<n>`、musl+crt 预编译为 .trvo 增量链接） | **10/10** |
| `trirv_musl_real_e2e.py`（**真 musl 源码编译试点**：musl 1.2.5 官方源码 string/stdlib 6 文件原样编译（仅预处理），15 项行为断言 + strstr 集成场景，双 VM） | **11/11** |
| `trirv_musl_wide_e2e.py`（**musl string/ 全目录覆盖**：46 文件批量管线——40 编译成功 / 6 语义不适用 SKIP（字节位图打包依赖二进制位运算）/ 0 失败；strsep/strlcpy/memmove/strcasecmp/memccpy 功能断言） | **7/7** |
| `trirv_switch_e2e.py`（**switch 契约**：匹配+break / fall-through / default / 无匹配 / 负 case 值 / 嵌套 switch / 乱序声明 / CVM 一致） | **9/9** |
| `trirv_compound_e2e.py`（**复合字面量契约**：return/初始化/实参三语境、负值截断、嵌套表达式成员、CVM 一致） | **6/6** |
| `trirv_qsort_tri_e2e.py`（**qsort 三进制重写契约**：乱序/已序/逆序/重复/单元素/负数/大数组/char 数组，双 VM） | **4/4** |
| `trirv_strtol_tri_e2e.py`（**strtol 三进制重写契约**：各 base/0x 自动判定/正负号/溢出钳制/end 写回/坏输入，双 VM） | **4/4** |
| `trirv_difftest.py`（Python VM vs C VM） | 11/11 |
| `trirv_mdiff.py`（M 扩展差分，含 3⁴⁵ 边界） | 700/700 |
| `trirv_csr_diff.py`（CSR/trap 差分） | 4/4 |
| `trirv_charsign_e2e.py`（char 符号性 / LB-LBU 语义） | 21/21（含 C VM 交叉 21/21） |
| `trirv_tritlogic_e2e.py`（AND/OR/XOR 逐 trit 语义锁定） | 42/42（期望值 × PyVM × C VM） |
| `trirv_bitview_e2e.py`（C 的 `& | ^ ~` 与 `<< >>`，方案 A 72 位视图） | **245/245**（PyVM + C VM 双跑） |
| `trirv_cc_py_bitop_e2e.py`（Python 后端位运算/移位差分，方案 A 对齐） | **15/15**（含负数 raw 化、`~0!=-1`、变量移位） |
| `trirv_float_e2e.py`（TF 浮点 P1：base-3 ↔ (mant,exp3) 分离穿透，双档 TF32/TF64） | **144/144**（含 ±0/±Inf/NaN、3^e、舍入 1 ULP、极端 exp3=29523 分离往返） |
| `trirv_tritref_test.py`（tryte 数组参考实现，无二进制容器） | 46164/46164（差分）+ 3 个端到端程序 |
| `*_e2e.py`（C 编译到执行全链路） | **16/16 套**（`TRV_CC_STRICT=1` 下同样 16/16） |
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

0. **musl 是"风格对齐的 libc 层"而非 musl 源码**（2026-11-19 步骤 7 落地）：
   启动链（`trirv_crt.c`）+ libc API 层（`trirv_musl.c`：string/stdlib/errno）
   + mini-busybox（`trirv_busybox.c`：echo/cat/wc/head/dirname/basename/true/false，
   按 argv[1] 分派 applet，退出码 Unix 惯例 0/1/127）已全部打通并有 e2e 锁定。
   **链接器已到位（mini 形态）**：`trirv_cc 主文件 -l 库文件...` 按序拼接成
   单一翻译单元，支持跨文件调用、先原型后定义、重复定义检测（link error），
   busybox 已改为正式分层构建 `-l musl -l crt`（`trirv_link_e2e.py` 14 项锁定）。
   **CHAR_BIT 冲突已解（视图层方案，2026-11-19）**：`read8`/`write8`（syscall
   5000/5001，TriRV 扩展区）在 syscall 边界做 octet↔双 tryte 编解码
   （o → [lo=o%243, hi=o/243]），内存保持纯三进制，全 256 个 octet 无损往返，
   双 VM 一致。musl 的 `read/write` 内部换用这对原语即可满足 CHAR_BIT==8。
   **TRVO 目标文件已到位（2026-11-19）**：`trirv_cc src.c out.trvo` 导出文本
   行式中间格式（FUNC/PARAM/LINE/GLOB/STR），`-l xxx.trvo` 导入链接——
   musl+crt 可**预编译一次、多次链接**（增量构建）；可执行 .trv 链接期做
   undefined reference 检测（目标文件豁免）；跨单元 `_L*` 标签自动改写
   `_X<单元>_<n>` 防冲突；重复定义检测对 TRVO 导入同样生效。
   与真 musl 的剩余差距：musl 的 arch 层（内联汇编——改写为 read8/write8
   形态 + TRVO 分发）、弱符号语义。
   **musl 覆盖总账（2026-11-20）**：src/string + src/stdlib 共 66 文件
   （非宽字符），批量管线 = 60 编译 OK / 6 决策 SKIP / 0 FAIL。
   **musl_tri/ = 三进制原生改编层**：上游依赖二进制位运算语义的文件
   在此重写（纯三进制原则：语义适配硬件，不跳过），每文件注释逐条
   标注与上游差异。已重写：memset/memcpy/strcspn/strspn/strstr/memmem/
   div/ldiv/lldiv/imaxdiv/qsort/strtol/strsignal（13 文件，均有行为
   断言锁定）。
   **决策记录：浮点系 → ISA 演进立项（2026-11-20）**（strtod/atof/ecvt/fcvt/gcvt）。
   原为"永久 SKIP：TriRV 无浮点"，因 wasm 必然涉及 f32/f64 且用户定调"三进制浮点格式
   属 ISA 演进"，已立专项：**尾数[1,3)穿透宿主 FPU + 指数走整数 ALU，ISA 格式与语义
   保持三进制**。关键更正（2026-11-20）：binary64 的"64 位"是 IEEE 软件规范而非物理事实，
   不拿 double 值域当格式天花板——全阶码可表示，无"穿透缺口"。立项书见
   `TERNARY_FLOAT_PROPOSAL.md`（D-F1..D-F4 已拍板，P1 编解码已落地 144 项契约）。
1. ~~**C 后端覆盖 130/148 = 88%**：struct 按值传参 / sret 返回未实现~~ ——
   **2026-11-19 已补齐**，C 后端 148/148。约定与 Python 后端一致：调用方传结构体
   源地址、被调方入口逐字拷进本地副本（修改完全隔离）；sret 由调用方在帧内预留缓冲、
   `a0` 传地址、`return` 逐字拷入。每个 sret 调用点各占一块独立缓冲，`f(g(),h())`
   不会互相覆盖。验证：`byvalue` 17/17、`stackargs` 11/11，**两者都在
   `TRV_CC_STRICT=1` 下通过**（此前是 9/11 与整套失败）。
   > ⚠️ **验证陷阱仍在**：默认模式下 e2e 全绿，可能是因为失败用例悄悄回退到 Python
   > 后端。考核 C 后端必须加 `TRV_CC_STRICT=1`，`run_all_tests.py` 已默认带上。
2. ~~**二进制位运算 / 移位只在 C 后端**~~ —— **2026-11-20 已对齐**：`& | ^ ~ << >>`
   的方案 A 适配层现在 **C 后端（`trirv_cc.c`）与 Python 参考后端（`trirv_cc.py`）都有**，
   15 项差分锁定（`trirv_cc_py_bitop_e2e.py`），含负数 raw 化、`~0 != -1`、
   变量移位与组合表达式。两套后端语义一致，不再需要为位运算专门切 C。
3. ~~**VM 的 DIV 慢**~~ —— **2026-11-19 已优化，DIV 不再是热点**（`python3 bench_div.py`
   实测，含循环/调用开销，Apple M2）：
   - 默认快路径（宿主 128 位整数除法）：**3.7 ns/op**（纯指令 ~0.7ns）
   - `TRIRV_NO_HW_DIV`（72 轮逐位恢复，三值电路参考）：**117.7 ns/op**，慢 31.6×，
     仅给无 128 位除法的宿主用，不进默认路径
   - `>>` 的代价仍主要落在 DIVU 上（`a>>3` 19 步里 11 步是 DIVU），但 DIVU 本身已快。
   - **估商方案的探索结论（2026-11-20，避免重复踩坑）**：长浮点估商 + 有限修正
     在**全 2^128 域**最坏修正步数达 87890（截断误差无界，退化比恢复除法还慢）；
     逐 tryte 分块（5 bit/轮）虽正确但每轮减法循环平均 16 次、总步数反超 72 轮。
     两者都不满足"确定性、步数有界"的要求，故**不采用**。宿主 128 位除法快路径
     已是工程最优，乘法逆元估商不再有收益。
4. **动态指令少 8.4× 但墙钟只快 6.5×**：单步成本随指令类型差异极大（MUL ~70ns、
   DIV 已降到 ~3.7ns）。DIV 已优化，剩余差距主要来自 MUL 与访存，后续由编译器
   避开贵指令（如常量乘展开成加法链，已在用）。
5. **1-tryte 局部变量一律落栈**：`char`/`signed char`/`unsigned char` 不再走寄存器分配。
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
