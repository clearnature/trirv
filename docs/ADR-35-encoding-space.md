# ADR-35 编码空间审计与未定义行为定死（2026-11-24，批次 3b.5）

状态 ✅（审计完成，未定义口径待双 VM 统一后关闭）

## 1. 编码空间覆盖

全交叉扫描（opcode 0-26 × funct 0-8 × funct2 0-26 = 6561 组合）：
- **defined: 2083**（对应 109 条已注册指令的合法编码）
- **undefined: 4478**（decode→None，PyVM 触发 CAUSE_ILLEGAL trap）

## 2. 未定义角落——双 VM 行为不一致（ADR-35 定死清单）

| 未定义角落 | PyVM 行为 | CVM 行为 | 统一口径（待批次 3b.5 实现） |
|---|---|---|---|
| 非法指令（decode→None） | take_trap(CAUSE_ILLEGAL) | NOP（跳过继续） | **统一为 take_trap**（RISC-V 同款） |
| TC reg_s≥8 | 落 x0（编码器保证≤7 不触发） | 回绕 cregs[rd&7] | **统一为落 x0**（保守：不误写窗口寄存器） |
| mtimer 注入 | 步末 MIE=1+irq_timer → trap | 同 | ✅ 一致（批次 3b.4 实证） |
| 负 JALR 目标 | (int)align_down4 → 可能负 pc → halt | 同 | ✅ 一致（双 VM 对齐） |
| read 负 count | -EINVAL（评审 A5 修复） | -EINVAL（同修） | ✅ 一致（评审 A5 修复） |

## 3. PyVM 非法指令实证

```python
prog = [0x00000000]  # 全零词
# PyVM: decode→None → _take_trap(CAUSE_ILLEGAL=2) → mtvec=0（复位向量）→ 重启循环
# → 步耗尽停机 (halted=False, exit=0, pc=0)
```

## 4. 关闭条件

双 VM 统一为「非法编码 → take_trap(CAUSE_ILLEGAL)」口径后，
守卫用例验证：同非法词 → 双 VM 均 halted=True 且 mcause=CAUSE_ILLEGAL。
