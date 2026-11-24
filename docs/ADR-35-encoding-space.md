# ADR-35 编码空间审计与未定义行为定死（2026-11-24，批次 3b.5）

状态 ✅（已关闭：双 VM 统一实现 + 守卫常绿）

## 1. 编码空间覆盖

全交叉扫描（opcode 0-26 × funct 0-8 × funct2 0-26 = 6561 逻辑组合）：
- **defined: 2083**（对应 109 条已注册指令的合法编码）
- **undefined: 4478**（decode→None）

规范函数（真相源成员判定，双口径互为镜像）：
- U/J 格式：opcode 命中即定义（funct/funct2 不参与，243 组合）
- R 格式：(opcode, funct, funct2) 精确对
- 其余（I/S/B）：(opcode, funct) 命中、funct2 任意（27 组合）
- 词级打包：t0-2=opcode, t7-8=funct, t17-19=funct2（trit 权重 3^0/3^7/3^17，
  rd/rs1/rs2=0）——4478 个未定义逻辑组合的打包词全部 decode→None
- 常绿守卫：tests/trirv_tri_isa_guard.py（审计数锁定 + 词级自洽，漂移即 FAIL）

## 2. 未定义角落——双 VM 行为统一（3b.5 实现记录）

| 未定义角落 | PyVM 行为 | CVM 行为（3b.5 前） | 统一口径 ✅ 实现 |
|---|---|---|---|
| 非法指令（decode→None） | take_trap(CAUSE_ILLEGAL) | NOP（跳过继续） | ✅ **统一 take_trap**：CVM 新增 OP_ILLEGAL（decode_one 未命中→OP_ILLEGAL，双执行路径 take_trap(CAUSE_ILLEGAL,0,oldpc)），NOP 伪装退役 |
| TC reg_s≥8 | 落 x0 | 回绕 cregs[rd&7] | ✅ **统一落 x0**：窗口访问改 `rd<8?cregs[rd]:0`（写两处+读两处；3-trit 字段下不可达，防御性形状统一） |
| mtimer 注入 | 步末 MIE=1+irq_timer → trap | 同 | ✅ 一致（批次 3b.4 实证） |
| 负 JALR 目标 | (int)align_down4 → 可能负 pc → halt | 同 | ✅ 一致（双 VM 对齐） |
| read 负 count | -EINVAL（评审 A5 修复） | -EINVAL（同修） | ✅ 一致（评审 A5 修复） |

## 3. 未定义编码实证（v2，订正初稿口误）

```python
isa.decode(0)  # = ADD（全零词是合法 ADD x0,x0,x0——初稿"全零词→None"系口误）
# 真实未定义样例：(opcode=0,funct=0,funct2=2) → 词 2·3^17 = 258280326 → decode→None
# PyVM: _take_trap(CAUSE_ILLEGAL=2) → mtvec=0（复位向量）→ 重启循环
#       → 步耗尽 (halted=False, exit=0)
# CVM（3b.5 起）: take_trap(CAUSE_ILLEGAL,0,oldpc) → 同款重启循环
# 守卫：tests/trirv_golden_e2e.py §8（双 VM × 2 样例；NOP 落穿会 exit=7，即退路退役证明）
```

## 4. 关闭记录

- 双 VM 统一为「非法编码 → take_trap(CAUSE_ILLEGAL)」✅（3b.5，2026-11-24）
- 守卫用例：golden_e2e §8 双 VM 同非法词 → 同款重启循环 → 步耗尽 exit=0
  （mtvec=0 复位向量语义， halted=True 形态仅当 mtvec 指向停机 handler——
  守卫以「一致重启循环 + 非 NOP 落穿」为不变量）
- TC reg_s≥8：形状统一（不可达字段，防御性对齐 PyVM 落 x0 口径）
