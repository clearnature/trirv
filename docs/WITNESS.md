# WITNESS —— TriRV 见证能力 ABI（可见证性 = 产品要求）

> witness-sat 形态产品化：昂贵求解产「结果 + 见证」，独立廉价校验器
> 验见证不重做搜索。本文件 = 对外能力规格（v1，2026-11-26）。
> 实现：libc-musl/musl_tri/witness_tri.c；契约：tests/trirv_witness_e2e.py
> （**14 PASS / 0 FAIL**，py VM + CVM 双跑）。

## 对外承诺（产品要求）

1. **确定性**：校验器纯函数，同输入同输出，py VM / CVM 双引擎逐位一致。
2. **独立性**：校验器不重做求解——验见证 O(m)（着色）/ O(p)（LL）。
3. **可证伪**：任何被篡改的见证必被拒绝（契约含篡改用例钉死）。
4. **对外可用**：任何外部求解器（任何语言/体系）产出的见证，只要
   符合下述格式，TriRV 双 VM 即可独立校验。

## 返回码（全局约定）

| 码 | 名 | 含义 |
|---|---|---|
| 0 | W_OK | 见证成立 |
| 1 | W_REJECT_COLORING | 着色见证被拒（存在同色相邻边） |
| 2 | W_REJECT_LL | 梅森 LL 证书末项不符 |
| 4 | W_REJECT_HASH | 数据完整性见证不符 |

## ABI v1（witness_tri.c）

### W1 图 3-着色见证校验
```c
int w_coloring_check(int n, char *edges, int m, char *colors);
```
- `edges`：2m 个 tryte（u,v 交替，顶点号 < 128）；`colors`：n 个 tryte
- O(m)；任何边两端同色 → 1（拒绝）

### W2 梅森素数 Lucas-Lehmer 证书
```c
int w_ll_cert(int p, int claim);
```
- `claim`：LL 序列末项期望值（M_p 素 ⟺ 0；M_p 合 ⟺ 非零末项）
- p ≤ 31 原生单字安全（s² ≤ 4.6e18 < 3^45/2）；O(p) 次单字乘除

### W4 数据完整性见证（三值 FNV）
```c
int w_hash_check(char *data, int len, int claim);
```
- `h₀ = 2166136261`；`h = (h XOR tᵢ) × 16777619 mod 3^45`
  （XOR = 逐 trit (ta+tb)%3）
- `claim` = 完整 45-trit 字值

### W3 大乘积证书（对接 bignum_tri）
```c
int w_bn_product_check(int *a, int la, int *b, int lb,
                       int *claim, int lcl, int *work);
```
- recompute a·b（bn_mul_big）并逐肢比对 claim（bn_cmp）
- work = 调用方缓冲 ≥ la+lb 肢；O(la·lb)
- 需链接 bignum_tri.c（bn_mul_big/bn_cmp 跨 TU 绑定）

## 已验证案例（契约钉死）

| 案例 | 见证来源 | 结果 |
|---|---|---|
| 有效着色（planted 3-col，host Python 求解） | 外部求解器 | 0 ✓ |
| 篡改一色 | — | 1 ✓ 独立拒绝 |
| LL M_31 素证书（claim = 0） | 理论 | 0 ✓ |
| LL M_11 合证书（claim = 1736） | 理论 | 0 ✓ |
| LL 错误声明（claim = 1） | — | 2 ✓ 拒绝 |
| 哈希见证（边集数据，host 独立算 claim） | 外部求解器 | 0 ✓ |
| 大乘 100×200=20000 | host INITLIST | 0 ✓ |
| 大乘篡改 20100 | — | 3 ✓ 拒绝 |
| 跨肢 1594322×2 | host INITLIST | 0 ✓ |
| 跨肢篡改 | — | 3 ✓ 拒绝 |

## 路线（v2+）

- W3 大乘积证书（对接 bignum_tri：a·b = c 逐肢复验）
- LL 大 p 证书（bignum mod，M_61+）
- 见证 blob 线格式（便携证书文件）
- FADD/FSUB 对阶整数化（M5）后：浮点计算见证
