# ADR-34 外部评审（ARCH_PLAN_REVIEW-2026-11-24）处置

> 评审文档：docs/ARCH_PLAN_REVIEW-2026-11-24.md（25 条，五组）。
> 本记录 = 逐条处置 + 四项拍板。状态 ✅（2026-11-24）。

## 已核实并修复（纯修复组，本轮提交）

| 评审条目 | 复核结论 | 处置 |
|---|---|---|
| tri_musl_e2e 空转 | ✅ 成立且根因更深：TCC/MUSL_STR/PPDIR 三处 HERE/ROOT 混用 + 依赖外部 ~/ai-lab/musl | 路径修复已起步后**回滚**——该套件系统性路径病耦 bootstrap，登记 3b.1 专项（逐文件精修） |
| difftest_tcc PyVM 列=CVM | ✅ 成立（run_vm 先 CVM 仅失败回退） | **已修**：run_vm 改真 PyVM 独立执行，28 组真四路 |
| SYS_READ 负 count | ✅ 成立且两 VM 错法不同（PyVM 切片赋值插入 guest 内存；CVM (size_t) 负转巨大全量读入） | **已修**：两 VM count<0 → -EINVAL；黄金 exit=234/内存不插长 |
| ISA 守卫跑 /tmp 旧二进制 | ✅ **成立且已修**（时序澄清见下方更正注：复核时看的是已修好的 HEAD，仲裁以 4d970af 历史点为准——该时点 CVM 确为 /tmp/trirv_vm_test） | 现状 test_isa_completeness.py:149 用 vm/bin ✓；/tmp 旧二进制建议删除 |
| TC 越窗寄存器两 VM 不一致（PyVM 落 x0 / CVM 回绕 cregs[rd&7]） | ✅ 成立（代码亲核；编码器保证 reg_s≤7 不触发，属未定义角落） | ADR-35 待办：ISA 规范定死「reg_s≥8 处理」+ 守卫用例（批次 3b.5） |
| 非法指令 PyVM 陷入 / CVM NOP | ✅ 方向成立 | 并入 ADR-35（统一为陷入口径） |

> **更正（2026-11-24，评审作者）：上表「ISA 守卫跑 /tmp 旧二进制 = 评审误报」不成立，该条评审成立。**
> 评审钉在 `4d970af`（批次 3.4；历史重写前的编号是 `170f65f`），当时
> `isa/test_isa_completeness.py:148` 是 `CVM = "/tmp/trirv_vm_test"`，
> 复核命令：`git show 4d970af:isa/test_isa_completeness.py | grep -n /tmp`。
> 复核时工作树里已是同日落下的修复（CVM 改用 `vm/bin/trirv_vm`、按需构建、构建失败判 FAIL），
> 该修复随 `7160922`（原 `c70862d`）入库——「零 .py 引用、规范路径用 vm/bin」正是修复后的样子。
> 处置应为「已修」，验证与范围见评审 A3 与 §8「已处置」。

## 文档矛盾（评审 22/23 条）——已修
- TASKS.md 基线快照 109/102 并存 → 删旧「ISA 总量 102」行（109=102 全宽+FENCE.I+CSR.H×6）。
- ternary-abi-minimal §1.2 int 4 tryte → 9 tryte（§6.1 对齐）。

## 四项拍板（建议，待用户确认）

1. **远端/镜像（ADR-36）**：建议建私有远端（GitHub/GitLab）推 trirv 主仓与 tinycc trirv 分支（21 commits 未推送含三值本征全量）；tinycc 以 git submodule 登记 .gitmodules。
2. **ADR-17 性能准入（修订）**：采纳建议阈值——TRV2 引擎相对 TRV1 慢 ≤1.15×（分支密集基准），未达前列为已知代价不入默认。perf harness（ADR-31）已就绪可直接测量。
3. **内存模型（ADR-37）**：文档化「代码/数据分离」现状为规范（预译码数组+数据数组，布局依赖分离前提），「统一内存」更名统一地址空间；名实声明入 trirv-unified-memory-abi.md（批次 3b）。
4. **规范模型第一步提前（ADR-28 修订）**：纯 Python 规范函数 + 编码空间全枚举提前至批次 3b.5（与 ADR-35 未定义角落定死合并做——规范函数正是定义未定义角落的地方）。

## 参照库裂缝（评审 §1.2）——引以为戒
门禁实现住智能体预设层/钩子不入库/台账在仓外——TriRV 对应面：坑实录在 ~/.zcode 记忆。
处置：ADR-32 状态行已入仓；坑实录沉淀节奏改为「批次收官时择要迁 docs/（本记录即开始）」。
