# solver —— 见证-校验分离求解器模块（witness-sat 形态，独立结构化）

> 产品定位（2026-11-26）：求解-校验分离 = TriRV 工具链的可见证性
> 架构核心。类比 **Lean 4 的 lean4checker**：小型独立 C 复验器不信任
> Lean 本体即可复验证明项——本模块同构：**求解器（搜索，重）与校验器
> （轻）分离，校验器可独立审计**。数学原理参照：律算合一 GF(3) 形式化
> 库（~/work/discrete-mathematics，只读）——其 Agda 证明为潜在见证
> 导出源。

## 双端架构

| 端 | 角色 | 文件 | 运行环境 |
|---|---|---|---|
| Python | 搜索原型 / oracle | `py/color3.py` | 宿主 CPython（无限精度 int） |
| C (TriRV) | 生产求解器 | `c/color3_solve_tri.c` | TriRV 双 VM（py VM + CVM） |
| 校验器 | 独立复验 | libc-musl/musl_tri/witness_tri.c | TriRV 双 VM |

双端**同搜索序**（顶点升序、色 0→1→2 升序回溯）——同输入必得同一
见证，契约逐字符对拍钉死；UNSAT = 完备枚举证明（K4 负样本）。

## v1 API：图 3-着色

```
py:  color3_solve(n, edges) → "012…" 色数字符串 | None(UNSAT)
C:   color3_solve(n, m, char *edges, char *colors_out) → 1|0
校验: w_coloring_check(n, edges, m, colors) → 0|1（witness_tri.c）
```

容量 v1：n ≤ 16、m ≤ 120（回溯最坏 3^16 ≈ 43M，TriVM 步数预算内
的典型实例 ≪ 此界）。

## 数据流（契约 tests/trirv_solver_e2e.py）

```
host 生成实例（planted 3-col / K4 UNSAT / 三角形 / 环）
  → mem 注入 INST 块（哨兵自定位）
  → guest color3_solve 逐实例求解
  → OUT 导出（flag + 见证串 + NUL）
  → host 对拍：py 见证 == C 见证；独立复验零冲突；K4 双端 UNSAT
```

## 路线 v2+

- 3-SAT 直解（DIMACS 子句格式 + 文字见证）
- 律算合一 Agda 证明项导出 → TriRV 见证复验（同 lean4checker 形态）
- 大 n 调度（迭代深化 / 生成元启发式，对接热浴 Glauber 动力学）
- W3 大乘证书（bignum_tri）→ 求解器产物全覆盖见证化
