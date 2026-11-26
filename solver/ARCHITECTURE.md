# solver/ARCHITECTURE.md —— 见证-校验分离求解器完整架构

> 设计基准：MiniSat (Een & Sörensson, 2003) 架构 + Lean 4 lean4checker
> 校验分离模式 + TriRV 三进制原生约束。产品要求：工业可用、可校验
> Z3 输出、双 VM 确定性。

## 一、总体架构

```
┌──────────────────────────────────────────────────────────────┐
│                    TriRV 见证体系全景                          │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  外部求解器（Z3 / CPLEX / 任何 DIMACS 兼容求解器）              │
│       │                                                      │
│       │  产出: sat + assignment（见证）                        │
│       │         或 unsat（需证明，v2）                         │
│       ▼                                                      │
│  ┌───────────────────────────────┐                           │
│  │  Z3 输出校验器 (Z3 verifier)   │  ← O(Σ|C|) 独立复验       │
│  │  cnf_verify_assign()          │    不信任 Z3 本体          │
│  └───────────────┬───────────────┘                           │
│                  │ ✓ / ✗                                     │
│                  ▼                                           │
│  ═══════════ TriRV 双 VM (py VM + CVM) ═══════════           │
│                                                              │
│  ┌─────────────────────────────────────────────────┐         │
│  │           TriRV 原生求解器 (solver/c/)            │         │
│  ├─────────────────────────────────────────────────┤         │
│  │  Layer 3: DPLL 内核 (dpll_tri.c)                 │         │
│  │  ┌─────────────────────────────────────────┐     │         │
│  │  │  unit propagation + backtracking        │     │         │
│  │  │  v2: CDCL + clause learning + restarts  │     │         │
│  │  └─────────────────────────────────────────┘     │         │
│  │  Layer 2: CNF 存储 (cnf_core_tri.c)              │         │
│  │  ┌─────────────────────────────────────────┐     │         │
│  │  │  clause storage / DIMACS parse          │     │         │
│  │  └─────────────────────────────────────────┘     │         │
│  │  Layer 1: 见证校验 (witness_tri.c)                │         │
│  │  ┌─────────────────────────────────────────┐     │         │
│  │  │  w_coloring_check / w_ll_cert           │     │         │
│  │  │  w_bn_product_check / w_hash_check      │     │         │
│  │  └─────────────────────────────────────────┘     │         │
│  │  Layer 0: bignum (bignum_tri.c)                  │         │
│  │  ┌─────────────────────────────────────────┐     │         │
│  │  │  base-3^13 多肢四则 / 规范化 / 十进制串   │     │         │
│  │  └─────────────────────────────────────────┘     │         │
│  └─────────────────────────────────────────────────┘         │
│                                                              │
│  ┌─────────────────────────────────────────────────┐         │
│  │           Python 求解器 (solver/py/)              │         │
│  │  color3.py / cnf.py / graph.py / witness.py     │         │
│  │  = 搜索原型 + oracle（同搜索序 → 同见证逐字符对拍）  │         │
│  └─────────────────────────────────────────────────┘         │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

## 二、DPLL 内核数据结构（dpll_tri.c）

### 2.1 编码

| 概念 | 表示 | 约束 |
|---|---|---|
| 变量 | 1..nv（1-indexed） | nv ≤ 48 |
| 正文字 | +v | |
| 负文字 | −v | |
| 赋值 | VAL[v−1] ∈ {−1 未定, 0 假, 1 真} | |
| 子句 | LITS[start..start+len−1] | 每子句 ≤ 16 文字 |
| 层 | LEVEL[v−1] = 决策深度 | 回溯恢复用 |

### 2.2 传播算法（当前 = 全扫 + 定点迭代；v2 = watched literals）

```
propagate():
  changed = 1
  while changed:
    changed = 0
    for ci in 0..nclauses:
      scan clause ci:
        undef = count of unassigned literals
        sat   = any true literal?
      if !sat and undef == 0: CONFLICT
      if !sat and undef == 1: UNIT → assign, changed = 1
  return OK
```

### 2.3 回溯策略

全量保存/恢复 VAL[]（O(nv) 每次回溯）。
v2 路线：trail-based 回溯 + 回跳（non-chronological）。

### 2.4 容量

| 参数 | 值 | 内存（trytes） |
|---|---|---|
| MAX_VARS | 48 | VAL/LEVEL/TRAIL/SAVE ≈ 1728 |
| MAX_CLAUSES | 128 | CL_START/CL_LEN ≈ 2304 |
| MAX_LITS | 512 | LITS ≈ 4608 |
| **合计** | | **≈ 9200 < 16183** ✓ |

## 三、Z3 输出校验（对外能力）

### 3.1 SAT 见证校验

Z3 产出 `sat` + 变量赋值 → 独立复验：
```
for each clause C:
    satisfied = 存在 lit ∈ C 使 (lit>0 ∧ val[var]=1) 或 (lit<0 ∧ val[var]=0)
    if 未满足: REJECT
return W_OK
```
- 复杂度 O(Σ|C|)——不信任 Z3，不重做搜索
- 即使 Z3 有 bug，只要赋值不满足某子句即被拒绝

### 3.2 UNSAT 验证（v2）

Z3 的 UNSAT 需要证明。当前方案：
- 独立重跑 TriRV DPLL（如实例 ≤ 48 变量）
- 或 Z3 产出 DRAT proof → DRAT checker 移植（v2+）

## 四、层间接口

### 4.1 CNF 存储层 → DPLL 层

```c
int cnf_parse_dimacs_char(char *text, int textlen,
                          int *lits, int *clstart, int *cllen,
                          int *nv, int *ncl);
int cnf_verify_assign(int nclauses, int *lits, int *clstart, int *cllen,
                      int *assign);
```

### 4.2 DPLL 层 → 契约

```c
int dpll_solve(int nv, int ncl, int *lits, int *clstart, int *cllen,
               int *assign_out);
int dpll_verify(int nv, int ncl, int *lits, int *clstart, int *cllen,
                int *assign);
```

## 五、确定性保证

1. 变量决策序：升序（v=1,2,...nv）
2. 赋值尝试序：0 → 1
3. 传播序：子句索引升序
4. 无随机数、无时钟依赖
5. **py VM / CVM 双引擎逐位一致**（契约钉死）

## 六、v2 路线（工业级差距收敛）

| 优先级 | 技术 | 来源 | 效果 |
|---|---|---|---|
| P1 | watched literals | MiniSat §4.1 | 传播 O(1)/替代 O(n) |
| P2 | CDCL（1UIP learning） | MiniSat §4.2 | 指数级加速 |
| P3 | VSIDS + phase saving | MiniSat §4.3 | 启发式质量 |
| P4 | DRAT proof checker | DRAT spec | UNSAT 独立复验 |
| P5 | 增量求解（assumptions） | MiniSat/CaDiCaL | 多次求解不重置 |

## 七、与 Lean 4 的类比

| Lean 4 | TriRV solver/ |
|---|---|
| Lean prover（重，策略搜索） | 外部求解器（Z3/自定义 DPLL） |
| proof term（见证） | assignment + certificate |
| lean4checker（轻，C 独立复验） | cnf_verify_assign() + w_*_check() |
| kernel axioms（最小可信基） | GF(3) 全表 + 有限群枚举（M3 契约钉死） |
