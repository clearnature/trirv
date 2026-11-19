# TriRV —— 三进制 RISC-V 风格的指令集架构

> 名字来自 **Ternary + RISC-V**。它是一个 **ISA 规范**，当前以**软件虚拟机**的形式实现，
> 不是物理芯片，也不是编译器。最后核对：**2026-11-19 08:07**（数据均为本机实测）。

## 一、三层身份，不要混

| 层次 | 是什么 | 在哪 |
| --- | --- | --- |
| 规范 | TriRV ISA：45-trit 字、27 寄存器、20-trit 定长指令、六种格式、27 syscall | `ternary-isa-design.md`、`ternary-abi-minimal.md` |
| 实现 | 虚拟机（"硅片"就是解释器）：取指/译码/执行 | `trirv_vm.py`、`trirv_vm.c`、`trirv_isa.py` |
| 工具链 | C 编译器、汇编器、编码器、libc | `trirv_cc.c`、`trirv_asm.py`、`trirv_encoder.py`、`trirv_libc.c` |

编译器的"栈式 vs 寄存器式"是**工具链内部**的代码生成策略，不是 TriRV 的属性。
TriRV 从第一天就是 Load-Store 架构，没有"栈机器"模式。

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

### 与 RISC-V 的差距，必须分成两类看

**A. 三进制物理决定，不可回收**

| 项 | RISC-V RV32I | TriRV |
| --- | --- | --- |
| 指令字 | 32 bit 定长 | 20 trit 定长（3²⁰≈2³¹·⁷） |
| 数据字 | 32 bit | 45 trit = 9 tryte |
| opcode | 7 bit 定值（LUI=0x37） | 3 trit（LUI=7） |
| 移位 | ×2 / ÷2 | ×3 / ÷3（三进制下移一位就是乘除 3） |
| 立即数 | 12 / 20 bit | 7 / 13 trit |

**B. 与"以 RISC-V 为标准"冲突，待决策**

| 项 | RISC-V | 当前 TriRV | 代价 |
| --- | --- | --- | --- |
| 寄存器数 | 32 个（5-bit 字段） | **27 个**（D3 决策，3-trit 字段） | 破坏 ABI 对齐与 LLVM 后端复用 |
| LB/LH | 符号扩展 | **零扩展**（char = 0..242） | 与 RISC-V 语义不一致 |

这两条是目前唯一挡住"符合 RISC-V 规范"的地方。改回 32 寄存器要动编码字段、
两套 VM、汇编器、编译器与全部 golden 测试——成本高，但一次到位后才能接 LLVM。

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

## 四、快速开始

```bash
./build.sh                                  # 构建 trirv_cc + trirv_vm（PGO 三阶段）

./trirv_cc foo.c foo.trv foo.asm            # C -> TRV 二进制（第 3 参数可选，输出汇编清单）
python3 export_trv.py foo.c foo.trv         # 同上，但经 trirv_cc_backend（默认仍调 C 后端）
TRV_CC=py python3 export_trv.py foo.c foo.trv   # 强制 Python 参考后端做对照

./trirv_vm foo.trv                          # 执行
./trirv_vm -n 40 -t 1 foo.trv               # 多实例多线程压测
./trirv_vm -o 1000 foo.trv                  # dump 地址 1000 起的 9 tryte
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
| `test_isa_completeness.py`（编解码往返 + 语义 + D1–D5 守卫） | **73/73** |
| `trirv_difftest.py`（Python VM vs C VM） | 11/11 |
| `trirv_mdiff.py`（M 扩展差分，含 3⁴⁵ 边界） | 700/700 |
| `trirv_csr_diff.py`（CSR/trap 差分） | 4/4 |
| `*_e2e.py`（C 编译到执行全链路） | 13/13 套 |
| `trirv_fuzz.py` / `_mem` / `_csr` | 1500 / 1000 / 800 case，字节级 MATCH |

性能（对比 Python 栈式后端）：

```
源程序            Python 编译   C 编译    加速    指令(Py)  指令(C)   收缩
小(2 行)           126.7ms   2.5ms   50.1x      133      22    6.05x
中(41 函数)        126.6ms   3.1ms   40.6x     4768     631    7.56x
大(201 函数)       130.2ms   5.8ms   22.6x    23328    2476    9.42x

热循环 20 万次 s=s+i*3-i/2：2020 万步 0.247s → 240 万步 0.0397s（6.2×）
```

## 六、已知缺口

1. **C 后端覆盖率 130/148 = 88%**：剩下 18 例全是 struct 按值传参 / sret 返回，
   走 Python 兜底（约 100 行工作量）。
2. **VM 的 DIV 慢**：无硬件除法器，走 72 步逐位恢复除法（~150ns）。优化方向是乘法逆元估商，
   预计 1.5–2×。
3. **动态指令少 8.4× 但墙钟只快 6.2×**：说明单步成本随指令类型差异极大（MUL ~70ns、DIV ~150ns），
   后续要么优化 VM 除法，要么让编译器继续避开贵指令。

## 七、最近话题（2026-11-19）

- **战略定调**：以开源 RISC-V 为规范和标准，只做进制层面的底层替换，不另建生态；
  软核（虚拟机）先行，栈式 masm 先跑通，LLVM 寄存器式后端后期接入。
- 澄清 TriRV 定位：ISA 规范 / 虚拟机 / 工具链三层，不是"编译器"。
- 逐条核对 RISC-V 符合性：40 条语义全覆盖；差距分两类——物理必然 5 项、待决策 2 项
  （27 寄存器 vs 32、LB/LH 零扩展 vs 符号扩展）。
- 同步文档：§6 opcode 表补齐 13–18 实际分配（旧版整段写"保留"，与代码矛盾）；刷新 4 处过时计数。
- 修复 `trirv_csr_diff.py` D3 用例构造错误：`mtvec=8` 指向非法指令自身形成自陷循环、
  `mepc+1` 应为 `+4`。修后两版 VM 一致，CSR 差分 3/4 → 4/4。
