# Z3-INTEGRATION —— Z3 对接架构决策（不编译 Z3，校验 Z3）

> 用户问：Z3 是 C++ 实现，我们的 C 编译器怎么编译它？要实现 C++ 前端吗？
> **回答：不实现 C++ 前端。Z3 在 x86 宿主上运行（C++ 编译器编译，与我们
> 无关），TriRV 只做 Z3 输出的独立校验。**（2026-11-26 用户拍板确认）

## 一、Lean 4 lean4checker 模式

Lean 4 的架构启示：**不信任 prover，只信任 checker**。

| 组件 | Lean 4 | TriRV + Z3 |
|---|---|---|
| 求解器（重） | Lean 4 prover（Haskell，~30 万行） | Z3（C++，~50 万行） |
| 求解产物 | proof term | DIMACS + assignment |
| 校验器（轻） | lean4checker（C，~2000 行） | cnf_verify_assign（C，~30 行） |
| 运行环境 | 目标架构 | TriRV 双 VM |
| TCB（可信计算基） | lean4checker | cnf_verify_assign |

**关键：求解器和校验器运行在不同的编译器和体系上。**
Z3 用 C++ 编译器编译（x86）；校验器用 trirv_cc 编译（TriRV）。
二者的实现语言、编译器、运行环境完全独立——一个出 bug 不会传染另一个。

## 二、为什么不实现 C++ 前端

| 维度 | 工作量 | 收益 |
|---|---|---|
| C++17 全集（模板/异常/STL/RAII/constexpr） | **人年级** | 0（Z3 的求解算法在 x86 上跑得更好） |
| C++ 子集（够编译 Z3） | **人年+** | 0（子集不够 Z3 用） |
| CNF 校验器（我们已有的 ~30 行 C） | **0** | **完全覆盖 SAT 校验需求** |

**结论：C++ 前端是沉没成本陷阱——投入巨大但产品价值为零。**
我们的产品能力是**校验**，不是**编译 Z3**。

## 三、Z3 对接数据流

```
┌─────────────────────┐
│  Z3（x86，C++）       │
│  输入: DIMACS cnf    │
│  输出: sat/unsat     │
│        + assignment  │
└──────────┬──────────┘
           │ DIMACS + assignment
           ▼
┌──────────────────────────────┐
│  TriRV cnf_verify_assign      │
│  输入: clauses + assignment   │
│  逐子句检查满足性 O(Σ|C|)      │
│  独立于 Z3，不信任 Z3 本体      │
│  运行于 TriRV 双 VM            │
│  编译器 = trirv_cc (C 子集)    │
└──────────┬───────────────────┘
           │ 0 = W_OK（Z3 结论确认）
           │ 1 = W_REJECT（Z3 有 bug！）
           ▼
┌──────────────────────────────┐
│  可审计的科学计算管线           │
│  任何外部求解器的结论都可被      │
│  TriRV 三进制世界独立确认       │
└──────────────────────────────┘
```

## 四、产品化路线

### v1（已落地）
- [x] `cnf_verify_assign`：DIMACS 子句 + 赋值 → 逐子句独立复验
- [x] 契约 trirv_sat_e2e 19/0（含有效/篡改赋值双向测试）
- [x] DIMACS 格式对齐

### v2（下一步）
- [ ] DIMACS 文件 I/O（guest 读文件，不只是 INITLIST 硬编码）
- [ ] Z3 产出文件解析（.sat output 格式）
- [ ] 批量校验管线（多个实例逐个校验）
- [ ] 性能基准（vs Z3 自带 model checker）

### v3（远期）
- [ ] DRAT proof checker（UNSAT 独立复验，~2000 行 C）
- [ ] SMT 理论见证校验（对接 Z3 的理论层输出）

## 五、six-repo 生态中的定位

| 仓库 | 语言 | 运行环境 | 与 TriRV 关系 |
|---|---|---|---|
| Z3 | C++17 | x86 宿主 | **被校验方**（外部求解器） |
| trirv | C 方言 | TriRV VM | **校验方**（cnf_verify_assign） |

Z3 fork（clearnature/z3）作为**参照实现**保留（研究其算法/数据结构），
但**不编译到 TriRV**。TriRV 的求解能力 = 自研回溯求解器（solver/c/）
+ Z3 等外部求解器的结论校验。

## 六、如果将来需要 TriRV 原生 C++

前提条件：trirv_cc C 前端覆盖率 ≥ C99 全集 + 工业级优化器。
当前状态：C89 子集（无 unsigned/bit ops/宏）。
路径：C89 → C99 → C11 → 预 C++（classes without templates）→ ...
**这不是当前优先级，且可能永远不需要**——校验器用 C 子集就够了。
