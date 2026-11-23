状态：草案（评审意见，待用户逐项裁决；本文不含已生效决策，采纳项按 ADR-32 追加记录）

# TriRV 架构与计划评审（参照数学库工程纪律）

> 撰写日 2026-11-24。事实截至 trirv `170f65f`、tinycc `21410af4`（另有未提交的 `tri-isa.h`）。
> 采集期间另一会话在同一工作树连续提交（`598a243` → `2869d9e` → `76f7995` → `170f65f`），
> `docs/TASKS.md` 随之改写多次——对该文件只按任务编号引用，行号仅用于文件顶部的基线快照。
> 参照对象：`~/work/discrete-mathematics`（`be52347`，下称「数学库」）、
> `~/work/math/hs/sov-math`（下称 sov-math）、`~/work/udk`（`fd631bc`）。
>
> **证据标记**：【亲核】本人读文件或跑命令所得；【取证】只读取证清点所得，未逐行复核；
> 【推断】由前两者推理；【未执行】只读代码、没有运行。文中的行号以 trirv `170f65f` 为准。
> **本次没有跑 `tests/run_all_tests.py`**：另一会话正在同一工作树工作，多套件共用 `/tmp` 固定
> 文件名，并行跑可能互相干扰。文中所有「N 套全绿」均引自 `docs/TASKS.md`，未复核。

## 0. 一页结论

1. **证据层**：「全绿」这个数字有几处不实——1 套自 11-22 起实跑 0 断言、「四路一致」实为两路、
   ISA 守卫的 C VM 段绑定 `/tmp` 里 11-18 的旧二进制。都是小修，但它们决定其余结论的可信度。
2. **规范层**：两个参考实现在未定义角落给出相反结果（非法指令、TC 越窗寄存器号），规范目前
   等于各自的实现。批次 3 正在把默认格式切到「同一编码、立即数单位随容器而变」的新模型，
   是最需要独立规范模型的时候。
3. **计划层**：批次 3 的准入没有性能条件（现有数据：TRV2 引擎分支密集慢 2.3×）；批次 5
   「固件」运行在哪一层没有定义；批次 6–8 没有量化基线（现有规则下 stdio 116 个文件 86 个直接 SKIP）。
4. 最紧急的一条与代码无关：主体仓没有远端，tinycc 的 TriRV 后端有 20 个提交未推送。

| 编号 | 级别 | 问题 | 主要处置 |
|---|---|---|---|
| A1 | P0 | 主体仓无远端；tinycc 分支领先远端 20 提交；子模块未登记 | 拍板异地副本与推送 |
| A2 | P1 | 基线快照记的两仓 HEAD ≠ 被测工作树 | 提交 `tri-isa.h`；运行器记录脏树 |
| A3 | P1 | 恒绿空转与静默跳过的套件 | 修路径；SKIP 不计 PASS；逐套最小用例数 |
| A4 | P1 | 验收口径与实际检查不一致（8 处） | 逐条对齐口径或对齐实现 |
| A5 | P1 | 两个参考实现在未定义角落相反 | 定义编码合法性；全枚举双解码对拍 |
| A6 | P1 | 权威文档内部矛盾清单 | 承诺类/快照类二分；数字单一来源 |
| A7 | P2 | 「连续 N 次全绿」无机器记录 | 运行日志追加写；计数由脚本算 |
| A8 | P2 | 长期知识存在仓外的智能体记忆里 | 迁入 `docs/LESSONS.md` |
| B1 | P1 | 没有独立于 VM 的规范模型 | 纯 Python 规范函数 + 编码空间全枚举 |
| B2 | P1 | 黄金与差分的检出力没有被度量 | 终态全量比对；变异检出率设阈值 |
| B3 | P2 | F 扩展算术语义由宿主载体定义 | 立 ADR 定口径 |
| B4 | P2 | 「RISC-V 对齐」没有 RISC-V 参照；§7.1 差异表不全 | 补差异行；宽度无关域对拍 |
| C1 | P1 | 代码与数据分属两个地址空间，文档未明文 | 拍板内存模型与布局策略 |
| C2 | P1 | 没有平台规范层：syscall 表 4 处手维护、无特性发现、与 UBIOS 的关系未立项 | 平台规范 + 单一来源表 |
| C3 | P1 | 已知号未实现的 syscall 返回 0 | 实现度分级；默认 −ENOSYS |
| C4 | P2 | 静态数据窗 ≤ 4095 tryte | 批次 6 开头参数化布局 |
| D1 | P0 | 批次 3 准入缺性能条件 | ADR-17 增补第 ⑤ 条 |
| D2 | P1 | 批次 5「固件」运行层未定义；框架不在回归里 | 定交付形态；先补基线 |
| D3 | P1 | 批次 6–8 无量化基线，口径是编译率 | 函数级证据表；惯用法门；引入 libc 测试集 |
| D4 | P2 | 批次 9 的 f32 档位与官方测试集冲突 | ADR-21 增补 f32 口径 |
| D5 | P2 | ADR 里的「当前值」与缺失的复核触发 | 改「截至」；加触发条件 |
| D6 | P3 | 部分验收条目不可判定 | 改写为命令 + 期望输出 |
| E1 | P2 | Python 回退默认开启 | 默认 STRICT |
| E2 | P2 | 三个编译器二进制；新克隆不可复现 | 统一构建入口；测试只认一份 |
| E3 | P3 | 批量脚本硬编码本机路径；`-l` 产物只压不修 | 相对路径；修参数解析 |

级别：P0 = 下一次提交或切默认之前；P1 = 本批次内；P2 = 对应批次开工前；P3 = 持续。

---

## 1. 参照库给了什么

### 1.1 可移植的做法

| 做法 | 出处 | 对应本文 |
|---|---|---|
| 证据分级 `proves`（内核回执）＞ `weak`（oracle）＞ `unverified`；「模型自报 = 未验证」 | 数学库 `docs/principles.md:26` | B1、B2 |
| 回执 = 源码文本的 sha256；改源码即失效并降级 | 数学库 `docs/ledger/README.md:3-4, 27-29` | A2、A7 |
| 否证必须带机器反例，只有结构论证不得标 `refuted` | 数学库 `docs/migration/needs_review-清单-2026-10-19.csv:61`【取证】 | B2 |
| 散文断言规则：每条可验证断言要么指向具体定理名，要么标 `[直觉·未验证]`；路线判断同样要标 | 数学库 `AGENTS.md:109-152` | 全文体例 |
| 「索引，不是验证」；权威顺序：类型检查器 > 台账 > 索引 | 数学库 `engineering/index_presentations.sh:2,15,95`【取证】、`AGENTS.md:34` | D3 |
| 文档分「承诺类」与「快照类」：前者未命中是错位要修，后者未命中是历史、改了是篡改 | 数学库 `memory/doc-code-drift-scan.md:19-30` | A6 |
| 「别把『扫描通过』当成『文档正确』」 | 同上 `:7` | A4、A6 |
| 「未做」列表不是可靠事实源；判断被推翻后要清理所有引用处 | 数学库 `memory/lessons-agda-traps-2026-10.md:71, 79-83` | A6 |
| 闸门的「过」必须是命令 + 期望输出；降级必须写明，不许静默 | 数学库 `docs/plans/m3c-atomic-plan.md:30, 207-209` | A3、A4、D6 |
| 「闭环」的三种含义必须分开写，否则属虚报 | 数学库 `docs/decisions/本源-投影分层-A1线-2026-10-17.md:60-69` | D2、D3 |
| 不许静默返回默认值：默认值会让错值伪装成正确 | 数学库 `docs/principles.md:66, 73` | C3、E1 |
| 有界实例不证明泛化；oracle 只用于发现反例 | 数学库 `docs/principles.md:51, 53` | B1、B2 |
| 先算后验证：陈述拿不准时先全枚举 | 数学库 `PROVEN.md:217-218` | B1、D3 |
| `PROVEN.md`：已做清单，防止重复实现 | 数学库 `PROVEN.md:1-11, 323-331` | §4 |
| SpecAnchor 模式：规范值 ↔ 实现导出逐项对拍，输出 `ORACLE-MANIFEST{basis,domain,points,claim}` | 数学库 `bridge-from-sov-math/crosscheck.py:1-46`、`SpecAnchor.agda:2-10` | B1 |
| Z/3^k 环的值语义与环律，配全域 oracle；并如实记下「按位算法 ≡ 值语义」未做 | 数学库 `bridge-from-sov-math/PATCHES.md` §5 | B1 |
| 等价性分级表：全域对账 / 完整论域 / 结构化对账 / 近似（显式命名）/ 未对账 | sov-math `README.md:54-62` | B1、B3、D3 |
| 契约头是单一事实源，导出面由它生成；符号基线门禁 | sov-math `README.md:154-166` | C2 |
| 变异测试：「一个从不说『不』的核对器等于没有核对器」，篡改必须全中，篡改未生效本身算失败 | sov-math `engineering/negative_controls.py:2-5, 44-66` | B2 |
| 文档表与头文件逐名、逐计数对账，不符即退出 1 | sov-math `engineering/doc_drift_check.py:18-59` | A6、C2 |
| 规范分章 → 结构定义 crate（`no_std`）+ 编译器 + 固件实现；CI 对三个目标构建 | udk `README.md:9-11`、`.github/workflows/ubios.yml` | C2 |

### 1.2 参照库自身的裂缝（同样值得参照）

参照库不是没有问题。它的现行纪律多数是自身事故之后的补救，而它的落地方式恰好是 TriRV
正在复制的风险。

- **纪律住在仓外。** `engineering/check_duplicate.sh:2-12` 是指向
  `~/.dsh/.agent-presets/math-proof/scripts/gates.mjs` 的薄壳，目录不存在即 `exit 2`；
  本机该目录不存在，`.git/hooks` 里没有任何活动钩子，唯一的 workflow 只编译一个 C 文件
  【亲核】。取证清点 25 个门禁脚本中 8 个是这种薄壳，`proof_dag` 台账也不在仓内【取证】。
  换一台机器，纪律整体不可执行。——对应 A8。
- **「自动生成，勿手动编辑」，但生成器不在仓里。** `maps/M2`、`M6` 声明由 `gen_m2.py`、
  `gen_m6.py` 生成，`find` 找不到这两个文件；地图停在 502 模块，而 `AGENTS.md:9` 写 823、
  `docs/quality-batch0-stats.md:10` 写 836 【亲核】。——对应 A6、C2。
- **判据在验收当日被改宽。** 2026-07-26 对抗审计写「七个问题中, 仅 1 个完全通过」
  （`docs/七问题对抗审计报告.md:121`），验收标准 v2 要求证明体「不能是空、不能是注释」
  （`docs/七问题验收标准-v2.md:10-11`）；次日的 vFinal 把检查改成
  `grep -c "= refl\|= λ ()" > 0`，把六条判为「伪标准」，结论变为「7/7」，并声明
  「验收标准已固定，不再修订」（`docs/七问题验收标准-vFinal.md:14, 30-41, 57`）【亲核】。
  两天之间代码是否也补齐了我没有核对；文档能确定的是判据与结论同日同向变化。
  该库 10 月的红线（「把 C 级报 A 级」禁止、`principles.md:55`）是这之后的补救。
  ——对应 A4。
- **黄金快照只告警。** sov-math 三实现逐字节一致即过，与 golden 不同只打印 ⚠
  （`engineering/ffi_conformance.sh:32-35`）【亲核】；测试入口打印失败清单但不设退出码
  （`test/Sov/Tests/Core.hs:733-744`）【亲核】。共同回退的错误三实现一起放行。

---

## 2. A 组：证据与声明

### A1 [P0] 全部工作只存在于本机

**事实**
- trirv：`git remote -v` 为空，180 余个提交【亲核】。
- tinycc 分支 `trirv`：本地领先 `origin/trirv` 20 个提交、落后 0
  （`git -C tinycc rev-list --left-right --count HEAD...origin/trirv` → `20 0`）【亲核】。
  未推送的包括三值本征 `88f861a3`、`__tint` `f296b758`、LW4/SW4 `cdb11a68`、
  `__tint2` `1d9d5e76`、`__tbool` `aecfa0ec`、`__tri_syscall` `a65ccd1e`、T-LIT 两个提交。
- `tinycc` 是 gitlink（`git ls-files -s tinycc` → `160000 21410af4…`），仓库里没有
  `.gitmodules`；`git submodule status` 报 `no submodule mapping found`【亲核】。
- ADR-30 的前提是「仓库目前没有远端……托管即生效」（`docs/DECISIONS.md:283-285`）。
  workflow 文件在仓内，但没有任何宿主运行过它；「Linux 由 CI 覆盖」目前不成立，
  平台覆盖只有本机 macOS【推断：由无远端推出】。
- 「不 push」曾是明文 DoD（`docs/T1_TDD_PLAN.md` DoD 第 2 条「两仓中文 commit（不 push）」），
  所以这里按「有意选择、请复核」提出，不按疏漏。

**参照** sov-math 把证据快照入库（`engineering/results/*.json`）；数学库的教训是住在仓外的
纪律换机器即失效（§1.2 第一条）。

**建议（拍板）**
1. 是否建立远端或至少一份异地镜像；若暂不公开，私有仓库或另一块盘均可。
2. 推送 tinycc `trirv` 分支；补 `.gitmodules`；trirv 更新 gitlink。
3. 在此之前，文档里的「CI 双平台」改写为「CI 配置（未托管）」。

**验证** `git remote -v`；`git -C tinycc rev-list --count origin/trirv..HEAD`。

### A2 [P1] 基线快照记的两仓 HEAD 不等于被测工作树

**事实**
- `docs/TASKS.md:12` 记「HEAD：trirv d5ca505 / tinycc 21410af4」。
- tinycc 工作树里 `tri-isa.h` 有 28 行未提交改动：`git -C tinycc status --short` → ` M tri-isa.h`；
  提交版 `HEAD:tri-isa.h` 里 `TRI_FENCE_I_*` 宏 0 个、CSR 高区 `_H_OP` 宏 0 个，工作树分别
  4 个和 6 个【亲核】。批次 1 的 1.7、1.9 写的是「tri-isa.h 重生成（102→103→109，守卫逐字一致）」，
  再生结果没有进 tinycc 的提交。
- 采集期间 trirv 工作树多次出现未提交改动（先后见到 `cc/trirv_cc.c`、`tests/trirv_tdd_contract.py`、
  `debugger/trirv_dbg.py`）【亲核】。

**后果** 按快照检出两仓，得到的不是被测的那棵树；`gen_tri_isa_h.py --check` 的「绿」依赖一个
未提交文件。

**参照** 回执哈希绑定源码、改源码即失效（数学库 `docs/ledger/README.md:3-4`）。

**建议** 提交并推送 `tri-isa.h`；`run_all_tests.py` 开头把两仓的 `HEAD` 与脏树标志写进运行
记录（见 A7），`--san` 口径下脏树标 UNCLEAN。

### A3 [P1] 恒绿空转与静默跳过的套件

**事实**
- `tests/run_all_tests.py:150` 注册的套件显示名是「musl→tri-tcc 多TU 3 组 30 断言」。
  实际执行：`python3 tests/trirv_tri_musl_e2e.py` 输出
  「tri-tcc 未构建（…/tests/tinycc/trirv-tcc 不存在）—— 全部 SKIP /
  tri-musl 契约：0 PASS / 0 FAIL / 3 组 SKIP」，退出码 0【亲核，已执行】。
  脚本里 `TCC` 指向 `tests/tinycc/trirv-tcc`，第二道守卫指向 `tests/musl/src/string`
  （`tests/trirv_tri_musl_e2e.py:24, 28, 198-205`），两处都不存在；真实二进制在
  `tinycc/trirv-tcc`，musl 在 `third_party/musl`。该文件最后一次改动是目录重组 `f941adb`
  （2026-11-22）【亲核】。此后每一次「全绿」里这一套都是 0 断言。
  `docs/PROJECT_AUDIT_BRIEF.md:109-110` 的「musl 多 TU 30 断言」以此为据。
- 三套在 tri-tcc 未构建时打印 SKIP 并 `exit(0)`：`tests/trirv_tri_tcc_e2e.py:417-420`、
  `tests/trirv_tri_isa_guard.py:43-46`、`tests/trirv_difftest_tcc.py:99-101`【亲核】。
  `tools/build.sh` 不构建 tinycc【取证】。本机 `tinycc/trirv-tcc` 存在，这三套在本机实际在跑；
  换到任何新克隆或 CI，它们计 PASS 而什么都不测。
- `isa/test_isa_completeness.py:148` 写死 `CVM = "/tmp/trirv_vm_test"`，§C「Python VM vs C VM
  差分对照」仅在该文件存在时运行，E5 段回退到同一路径（`:237-239`）。仓库里没有任何脚本
  构建它【亲核】。本机该文件日期 11-18 21:24、51,696 字节；当前的 `vm/bin/trirv_vm` 是
  118,456 字节，`vm/trirv_vm.c` 自 11-19 起有 10 个提交【亲核】。也就是说这一段的
  「C VM 一致」是对着六天前的 VM 验的，换机器则整段不跑。
- `tests/trirv_crt_e2e.py:174` 的「双 VM 一致」段以 CVM 文件存在为前提；
  同文件 `:65-69` 的 `run_cvm` 是恒返回 `(0, b"")` 的死桩【亲核】。
- 运行器只看退出码与 `FAIL_PAT`（`tests/run_all_tests.py:176-178, 236-239`），`SKIP` 不在
  失败模式里；`SUITES` 也不带每套的最小用例数。

**参照** 「降级必须写在此，不许静默」（数学库 `docs/plans/m3c-atomic-plan.md:207-209`）；
门禁脚本自身过期要明写「不是被测对象的错」（数学库 `memory/lessons-agda-traps-2026-10.md:84-86`）。

**建议**
1. 修 `trirv_tri_musl_e2e.py` 的三处路径（改动很小，建议作为下一个提交）。
2. `trirv_difftest_tcc.py`、`trirv_tri_tcc_e2e.py`、`trirv_tri_isa_guard.py` 的
   「前提缺失」改为非零退出，或由运行器给单独状态 SKIP 且不计入 PASS。
3. `test_isa_completeness.py` 的 CVM 路径改指 `vm/bin/trirv_vm`，由运行器的
   `_build_binaries` 负责构建。
4. `SUITES` 每项带最小通过数，运行器用已有的 `NUM_PAT` 解析并断言；「0 PASS」直接判 FAIL。

### A4 [P1] 验收口径与实际检查不一致

每条都是「文档说查了 X，代码查的是 Y」。第 2–6 条都在同一个文件里。

| # | 文档/名称说的 | 代码实际做的 | 锚点 |
|---|---|---|---|
| 1 | 「四路一致」（双工具链 × 双 VM） | `run_vm` 只要 CVM 输出含 `exit=` 就返回 CVM 的退出码；`/CVM` 与 `/PyVM` 两列调用同一个函数、同一参数，PyVM 只是 CVM 无输出时的后备 | `tests/trirv_difftest_tcc.py:80-85, 115-116`；`run_all_tests.py:151`；`TASKS.md` 2.8；`PROJECT_AUDIT_BRIEF.md:109-110`【亲核】 |
| 2 | 文件差分「比较退出码、输出、步数」 | 只取 stderr 里 `exit=` 一个词 | `tests/trirv_dual_fuzz.py:149-153`；`TASKS.md` 2.5【亲核】 |
| 3 | 引擎差分的终态比对 | 只比 `(exit_code, halted, steps)`，寄存器与内存不在内 | `tests/trirv_dual_fuzz.py:96`【亲核】 |
| 4 | 「变异自检（7/30 检出率证明能红）」 | 通过条件是 `detected != 0`，30 例里 1 例即过；实测 7 例，另 23 例变异未被检出 | `tests/trirv_dual_fuzz.py:117`；`TASKS.md` 2.4–2.5【亲核】 |
| 5 | 生成器护栏 | 越窗程序被 `continue` 跳过，`agree` 没有下限 | `tests/trirv_dual_fuzz.py:90-93, 102`【亲核】 |
| 6 | 生成器声称覆盖 TC | 词表只有 C-ADDI、向前 C-BEQ/C-BNE、link=0 固定 −4 的 C-J；全宽只有 ADDI/ADD/SUB/SLTU/XOR/SLLI；寄存器只有 5–12。帧相对访存、C-JAL、向后条件分支、JALR、访存、乘除都不在随机差分里 | `tests/trirv_dual_fuzz.py:22-61`【亲核】 |
| 7 | ADR-31「每档 5 次取中位数」、基准集「fib、qsort、串操作、memcpy」 | 取最小（打印也是「取最小」，docstring 写中位数）；实际基准是 fib/sumloop/axpy | `tests/run_perf.py:2, 23, 40`；`DECISIONS.md:290-291`；`PERF_REPORT.md:483`【亲核】 |
| 8 | 「全部 docs 首部加状态行」，验收是「前 10 行含『状态：』」 | 11 份文档的状态行是同一句模板「进行中（ADR-32 批次 2.15 统一补标——该文档未逐份定稿，收官时更新）」；`README.md`、`cc/CC_寄存器式编译器说明.md`、`docs/PROJECT_AUDIT_BRIEF.md` 没有状态行 | `TASKS.md` 2.15；`grep -l "统一补标" docs/*.md` → 11【亲核】 |

另有一处覆盖面未声明：`--san` 只把 ASan/UBSan 版写到 `cc/bin/trirv_cc` 与 `vm/bin/trirv_vm`
（`tests/run_all_tests.py:80-92`）【亲核】，而 21 个测试文件经 `trirv_cc_backend` 使用未插桩的
`cc/trirv_cc`【亲核：按 import 检索计数】。「首次全量 60 套零 sanitizer 报错」不覆盖这 21 套里的
编译器调用。

**参照** §1.2 第三条（判据被改宽）；负控制要看判别内容而不只看是否变红
（数学库 `AGENTS.md:7`）；sov-math 的变异必须全中（`negative_controls.py:44-66`）。

**建议** 1 改成 PyVM 列真走 PyVM；2、3 改成终态全量（32 个寄存器 + halted + steps +
被写内存摘要）；4、5 设阈值（见 B2）；7、8 把文档改成与实现一致或反之；`--san` 的覆盖面
在 ADR-29 里写明，或让 backend 在 `--san` 下也指向插桩二进制。

### A5 [P1] 两个参考实现在未定义角落给出相反结果

四条都是读代码所得【亲核·未执行】。

| 角落 | PyVM | CVM | 锚点 |
|---|---|---|---|
| 译不出的 20-trit 指令字 | `_take_trap(isa.CAUSE_ILLEGAL, …)` | 译成 `OP_NOP`，两条引擎都当空操作执行；`CAUSE_ILLEGAL` 在 C 文件里只有定义、没有使用 | `vm/trirv_vm.py:538-541`；`vm/trirv_vm.c:57, 188, 269, 1079` |
| TC 词的寄存器号 ≥ 8（字段 3 trit，窗口 8 个，27 个取值里 19 个未定义） | 落到 x0 | `cregs[rd & 7]` 回绕到窗口内某个真实寄存器 | `vm/trirv_vm.py:233, 235`；`vm/trirv_vm.c:983-985, 1212-1214` |
| `read(0, buf, 负数)` | `n` 取负值后 `mem[a1:a1+n] = chunk` 成了插入：在 `a1` 处插入数据并使 `mem` 变长，其上地址整体后移；同时返回负数、消耗 stdin（Python 切片赋值语义已单独验证，VM 未执行） | `a2` 转成 `size_t` 后读满剩余，返回非负 | `vm/trirv_vm.py:324-331`；`vm/trirv_vm.c:717-722` |
| 停机后仍有挂起的定时器中断 | — | `step()` 先查 `!v->halted`，`NEXT()` 宏不查；两条 C 引擎在 exit 之后的 CSR 终态可能不同（可观测性未验证） | `vm/trirv_vm.c:1016, 1094-1097` |

现有测试没有一处触达这些角落：随机生成器只用 `isa.encode` 产生的合法编码，TC 寄存器号只取
0–7。`tests/trirv_csr_diff.py` 的非法指令用例两 VM 退出码相同，因为只比退出码【取证】。
`docs/TC_COMPRESSION_DESIGN.md` §3–§4 只有字段映射表，没有「合法/非法编码」一节。
ADR-01 要求扩展落地四件套里有「规范条目」，TC 的这一条不完整。

这也是 B1 的直接证据：两个实现同源于一份理解时，一致只说明它们碰巧都没碰到的角落多。

**建议（拍板）** 定一条统一规则：未定义编码一律非法指令陷入；TC 越窗寄存器号、未定义子格式
同此。CVM 补非法指令陷入（批次 5 的 5.5「非法指令路径可恢复」在 CVM 上目前无法触发）。
负长度按 ADR-09「语义照 Linux」返回 −EINVAL；PyVM 那一处是参考实现自身的内存破坏，建议先修。

### A6 [P1] 权威文档内部的矛盾

| 位置 | 一边 | 另一边 |
|---|---|---|
| `docs/TASKS.md:13` 对 `:20`（同一份基线快照） | ISA 109 条 | ISA 总量 102 条 |
| `docs/ternary-abi-minimal.md`（状态「✅ 已收官（校准）」）`:47` 对 `:129` | `int` 4 tryte / 20 trit | `int / long / pointer` 9 tryte |
| 同上 `:134-141` 对 `:129` | §7「内存对齐」按 byte：char 1、int 4、long 9 byte | §6.1 按 tryte |
| 同上 `:72-74, 83-87` 对 `:112-118` | §3–§5：16 个寄存器、r3–r7 传参、栈向下增长 | §6：a7 调用号、a0–a5 参数 |
| 同上 `:121` 对 `vm/trirv_vm.c:690-694` | 「现状 29 个 Linux 号」 | 枚举 27 个 |
| `docs/ternary-isa-design.md:667` 对 `:659` | TC「16-trit 短格式」 | TC「15-trit/3-tryte」 |
| 同上 `:596, 701, 870-872` | 「T-LIT…未实现」「74 条指令」「方案 A：精确 72 位视图…`<< >>` 仍硬报错」 | 均为 11-22、11-23 之前的状态 |
| 同上 `:912-913` 对 `docs/TC_COMPRESSION_DESIGN.md:20` | 「立即数单位保持『槽』而非改为『tryte』」 | TRV2 流制：跳转目标是 tryte 偏移 |
| `docs/TC_COMPRESSION_DESIGN.md:68-70` 对 ADR-03/04 | 「opcode 24 = TC 组 2（预留：C-JAL…留待 B1 拍板）」 | 已定：走 sf5、不开 opcode 24 |
| `docs/COVERAGE_MATRIX.md:2` 对 `:10-12, 29` | 「快照（批次 1.11 补缺前）」 | 表里已有 `trirv_golden_e2e.py` 的命中；「→ 1.12 手算补缺」一节是空的 |
| `docs/TASKS.md` 批次 1 的原始清单、B4a–d、C1–C5 | 全是 `[ ]` | 对应事项均已决定或完成 |
| `docs/TASKS.md` 归档表 | 两行都叫「trirv_cc 内建」，提交号不同（`0ed086e` / `0fa2417`） | — |
| `README.md` | `:4` 最后核对 11-19；`:111-112`「C 的 `& \| ^`…编译器现在明确报错」；`:121-122`「无 FENCE.I、TC 压缩未实现、无 F/D/V」；`:137`「LLVM…已搁置」；`:163`、`:177`、`:181` 三个套数 | `:267-272`（位运算直发）；FENCE.I/TC/F 均已落地；ADR-18 批次 10；实际 62 套 |
| `docs/DECISIONS.md:17`（ADR-01）、`:147`（ADR-15） | 「102 条」「58 套」；「当前 28.0% / 6.7%」 | 109 条、62 套；35.2% / 8.5% |

`docs/PROJECT_AUDIT_BRIEF.md` 是给外部模型的入口，仍写 HEAD=`66a8e6e`、「无 CI」（`:199`）、
fn-ptr 缺口未关（§7.3），并在 `:33-34, :178, :231-232` 引用 `~/.zcode` 记忆——外部读者打不开。
`cc/CC_寄存器式编译器说明.md` 最后修改于 11-19，却被 `PROJECT_AUDIT_BRIEF.md:112` 引作
C 方言限制的诚实清单。

**参照** 承诺类/快照类二分（数学库 `memory/doc-code-drift-scan.md:19-30`）；文档与源逐名逐
计数对账（sov-math `doc_drift_check.py`）；「未做」列表不是可靠事实源（`lessons-agda-traps-2026-10.md:79-83`）。

**建议**
1. 文档二分。承诺类（`README.md`、`ternary-isa-design.md`、`ternary-abi-minimal.md`、
   `trirv-unified-memory-abi.md`、`DECISIONS.md`、`TASKS.md`）逐条校准到与实现一致；
   快照类（`PERF_REPORT.md` 各节、`HANDOFF-*`、`T1_TDD_PLAN.md`、`PROJECT_AUDIT_BRIEF.md`、
   `COVERAGE_MATRIX.md`）不改内容，首部加「截至 X；已被 Y 取代」。
2. 校准范围是全文。`ternary-abi-minimal.md` 的 §1–§5、§7 要么删除、要么标「历史草案」，
   否则不要写「已收官」。
3. 计数类数字只在一处：新增一份由脚本生成的 `docs/SNAPSHOT.md`（套数、指令数、TC 率、
   节省率、syscall 数），生成脚本入仓，其余文档只引用。
4. ADR 正文不写「当前 X」，写「截至 2026-11-23：X（出处）」。
5. `TASKS.md` 删除已完成的原始未勾清单，或移入归档节；已决的 B/C 节条目改为指向 ADR。
6. `README.md` 先修——它是外部入口，也是 ADR-32 漏掉的两份文件之一。

### A7 [P2] 「连续 N 次全绿」是人工计数

`docs/TASKS.md:10` 写「PASS 61 / FAIL 0（连续 38 次全绿）」；`tests/run_all_tests.py` 没有任何
落盘逻辑【亲核：检索 `open(.*'a'`、`.jsonl` 无命中】。A3 的空转套件在这 38 次里每一次都计 PASS。

**参照** 数学库的追加式台账与「改源码即失效」；sov-math 的 `ORACLE-MANIFEST`。

**建议** 运行器在每次结束追加一行 JSONL：日期、两仓 HEAD 与脏树标志、种子、每套状态与用例数、
耗时、宿主编译器版本。「连续全绿」由脚本从日志算出。

### A8 [P2] 长期知识存在仓库之外

`docs/TASKS.md:5-6`：「历史决策与坑实录以 ~/.zcode 记忆 + 各 PLAN 文档为准」；
归档节末：「更早里程碑：见 ~/.zcode 记忆」。悬垂指针、oracle 口径、exit 约定、双点白名单
这些教训目前只存在于工具的私有目录。

**参照** 数学库把 `memory/` 与 `PROJECT_MEMORY.md` 放在仓内；而它的门禁实现与台账放在仓外，
结果见 §1.2 第一条。TriRV 现在的安排是同一种结构。

**建议** 新建 `docs/LESSONS.md`（坑实录、回归口径、排障方法论），入库、随代码走；
记忆目录只留指向它的索引。`PROJECT_AUDIT_BRIEF.md` 下次送审前去掉 `~/.zcode` 引用。

---

## 3. B 组：规范的权威

### B1 [P1] 没有独立于 VM 的规范模型

**事实**
- ADR-28② 写「PyVM 用 Python 大整数写成的算术是规范定义。形式化模型列为 LLVM 立项的前置」
  （`docs/DECISIONS.md:268-269`）：规范就是一份实现，形式化模型排在批次 10。
- 同一套语义现在有这些份：PyVM `step`/`_exec_c`、CVM switch 引擎、CVM computed-goto 引擎
  （后两者各自手写语义，`vm/trirv_vm.c` 内 `IDEFS[]` 手维护，注释说它镜像 `INST`）；
  `cc/trirv_cc.c` 另有手维护的 `ITAB[]`（77 项，含 2 个非 ISA 占位），没有任何测试或生成器
  引用它【取证】。
- 项目自己记录过的同类盲区：CVM 解码器字段宽度停在 3 trit，「只有 x27..x31 才会解错」
  （`docs/ternary-isa-design.md:873-879`）；`u()` 负数语义在 11 项差分全用正数时未暴露
  （`docs/trirv-unified-memory-abi.md:231`）；「此前 OpenMP 多实例各有私有 `v->mem`，原子操作
  无从验证」（`docs/trirv-firmware-architecture.md:372`）。
- A5 的四个角落是新增的四例。
- `tools/trirv_tritref.py`（tryte 数组参考实现）结构上独立于两个 VM，只被它自己的测试引用
  【取证】——它是现成的种子。
- 批次 3 改的正是最需要独立模型的层面：PC 与立即数单位（槽 → tryte）、分支窗、长分支弛豫。
  ADR-17 准入 ④「TRV1 与 TRV2 退出码一致」仍是同一 VM 家族的自比。

**参照** SpecAnchor 模式与 Z/3^k 环的值语义（§1.1）。数学库给 Z/3^11 环配了全域 oracle，
并明确写下「按位进位算法 ≡ 值语义」未做——TriRV 需要的恰好是这一步：VM 的逐 trit/逐 tryte
算法对值语义的一致性。

**建议（分三步，第一步请拍板是否提前到批次 3）**
1. **S1（纯 Python，建议放批次 3）**：`spec/trirv_spec.py`——每条指令一个纯函数，直接按
   `ternary-isa-design.md` 的文字定义写（toℕ/repr 互逆、向零截断、逐 trit 表），不 import VM。
   - 编码空间：全宽 3^20 的译码在 CVM 侧做一次全枚举（`--decode-sweep`），TC 3^15 全枚举
     （编解码往返、判宽、两个 VM 的译码器逐词对拍——A5 第二行会被这一步直接抓到）。
   - 寄存器域：每条指令 × rd/rs1/rs2 各取 32 个寄存器（针对 `:873-879` 那类缺陷）。
   - 边界值集：0、1、−1、HALF−1、HALF、3^45−1、3^k±1 的全叉积。
   - TC ↔ 全宽：每个 TC 词的执行结果等于 `decode_c` 给出的等价全宽指令。
2. **S2**：黄金用例由 S1 批量生成，手算只留每类一两个做交叉核对（见 B2）。
3. **S3（可选）**：用 SpecAnchor 模式把 S1 的整数运算锚到数学库的 Agda 内核
   （Z/3^45 的值语义与环律、Kleene 格的分配律、`SLL_k(x) ≡ x·3^k`、移位 ≡ 截断除法）。
   这是 ADR-28② 所说「形式化模型」的最小形态，不必等到 LLVM。

### B2 [P1] 黄金与差分的检出力没有被度量

**事实**
- ADR-28② 要求「每条指令和扩展都要有不靠运行 VM 得出的期望值」。落地情况：专门套件
  `tests/trirv_golden_e2e.py` 是 26 项、覆盖 18 条指令【取证】；指令总数 109。
- 黄金与实现出现分歧的记录共 7 次，全部以修正期望值收场：1.12「三次自摆乌龙……全部由
  『实现与模型自洽性检查』定位后修正期望」；1.10「黄金断言三次自摆乌龙」；1.5「oracle 手算错
  9→12 由双档一致定位」（均见 `docs/TASKS.md` 批次 1 的完成记录）。记录里没有一次
  是黄金用例抓到实现缺陷。这不说明期望值改错了，但说明这套黄金至今没有展示过检出力。
- A4 的 2–6 条是差分侧的同一问题：观测面三项、变异检出 7/30、通过阈值 ≥1、无下限。

**参照** 变异必须全中且「篡改未生效」本身算失败（sov-math `negative_controls.py:44-66`）；
否证要带机器反例（数学库 `needs_review-清单…csv:61`【取证】）。

**建议**
1. 终态全量比对（寄存器 + 内存摘要），文件差分比较 exit、steps、stdout。
2. 生成器按「子格式 × 方向」分桶，每桶设下限；`agree` 设下限。
3. 变异自检改为有阈值的指标：只对确认执行过的指令注入变异，按类别统计检出率并写入运行日志。
   建议阈值 90% 以上；低于阈值算套件失败。
4. 黄金套件加负控制：每条指令的黄金对一个「故意写错的规范函数」跑一遍，必须变红，结果记档。

### B3 [P2] F 扩展的算术语义由宿主载体定义

**事实**
- `float/trirv_float.h:2`：「与 trirv_float.py 逐位对齐；Python 版作差分 oracle」——F 的参照是
  另一份实现。
- `docs/TERNARY_FLOAT_PROPOSAL.md:184-187`：TF45 尾数 34 trit ≈ 53.9 bit，宿主 `double` 只有
  53 bit，「属实现层账……不改 ISA 语义」；`:498-502`：TF45 往返 60.97% 精确、最大漂移 2.0 个
  尾数量子，「两套 VM 同源承载 mant → 结果逐位一致」；`:510`：double-double「暂不引入」；
  `:691`：「舍入的双语义……tie 点可能不同」。
- ADR-01 冻结「现有指令的编码和语义」。`docs/DECISIONS.md` 里浮点只有 ADR-26（libm 立项），
  没有舍入与精度口径的决策。

**含义** 对约四成的 TF45 尾数，FADD 等的结果不是 TF45 精确运算的正确舍入结果；两 VM 同源，
差分恒绿。「实现层账」这个说法成立的前提是有一份独立于实现的规范语义可对照，而目前没有。
wasm 的 f64 路径（53 bit 尾数可被 TF45 精确承载）不受影响【推断，未实测】。

**参照** sov-math 把「近似」在名字和文档里显式标出（`…Approx`，`README.md:61`）。

**建议（拍板）** 立 ADR「F 扩展算术语义口径」：规范语义定为 TF 精确运算 + 向最近舍入；
VM 现行为登记为已知偏差并给出误差界；用 `tests/trirv_float_libc_e2e.py` 已有的 Fraction 精确
oracle 给 F 指令补独立参照（TF20 的尾数域只有 242 个值，可以按尾数全枚举）。

### B4 [P2] 「RISC-V 对齐」没有 RISC-V 参照；差异表不全

**事实**
- `README.md:64`：语义层「必须一致，靠差分测试判」。现有差分轴是 PyVM↔CVM、
  tri-tcc↔trirv_cc、wasm 参考解释器↔双 VM（`docs/PROJECT_AUDIT_BRIEF.md:151-153`），
  没有一条以 RISC-V 实现为参照。`README.md:115-119` 的「四个层面全部对齐」与
  `docs/ternary-isa-design.md:648-659` 的八项实质差异并存。
- §7.1 差异表之外，至少还有四项实质差异未登记【亲核】：
  - 除零：商与余数均返回 0（`vm/trirv_vm.c:110`）；RISC-V 的 DIV 除零返回 −1、REM 返回被除数。
  - 访存越界静默：越界读返回 0、写被丢弃（`vm/trirv_vm.c:451, 495-497`）。
  - M 态 ECALL 直连 syscall、不陷入（`vm/trirv_vm.c:1197`；`docs/trirv-firmware-architecture.md:334`）。
  - A 扩展 9 条对 20 条（`docs/trirv-firmware-architecture.md:290`）。

**建议** 四项补进 §7.1；`README.md` 的「靠差分测试判」改成实际存在的三条轴。若要保留
「与 RISC-V 一致」的主张，最便宜的实际检查是在宽度无关的域上（结果不回绕的算术）用
qemu-riscv32 跑同一批程序对拍；位运算、移位、回绕域明确排除。【建议，未做】

---

## 4. C 组：平台模型

### C1 [P1] 代码与数据分属两个地址空间，文档没有明文

**事实**
- CVM：代码在预译码数组 `Inst *prog`，数据在 `uint8_t mem[1<<20]`；取指读 `v->prog[si]`，
  访存走 `mem_ref`（`vm/trirv_vm.c:28, 333-334, 837`；加载时译码 `:1277`）。PyVM 同构：
  `VM(program, mem_init=…)`，`self.mem = bytearray(1 << 20)`（`vm/trirv_vm.py:79, 94`）【亲核】。
- 文档标题是「统一内存」，正文列的都是数据：`docs/trirv-unified-memory-abi.md:4, :41`；
  `:7` 称「语义等价于真实硬件」。检索 docs 与 README，没有一处说明代码空间与数据空间的关系【亲核】。
- 链接布局：代码从 0 起，全局从 200 起（`cc/trirv_cc.c:1037`），栈基址 4096（`:1419`）；
  链接期检查只管数据（`:4059`）。它能工作的前提就是两个空间分离——代码超过 50 条指令后，
  「地址 200」在代码空间和数据空间指向不同的东西。
- FENCE.I 是空操作（`vm/trirv_vm.c:214`）；ADR-05 的理由是「WASM/JIT 路线和自修改代码需要它」
  （`docs/DECISIONS.md:51-52`），而 store 写不到取指空间，JIT 与动态加载在现有模型里没有入口。

**后果** 真硬件通常是统一编址；届时当前布局在代码超过 50 条指令时就与数据重叠【推断：布局
常量是事实，「真硬件统一编址」是假设】。TRV1 二进制没有重定位信息，布局烘焙在每个二进制里，
批次 3 切默认之后存量只会增大。

**参照** 数学库的层序规则明文且可机检，错位只报不改（`AGENTS.md:47`）。

**建议（拍板）** 立 ADR「内存模型」：平台口径是代码/数据分离还是统一。若统一，链接布局改为
「数据跟在代码末尾之后」，全局基址与栈基址随代码长度走；FENCE.I 与 JIT 的地位随之改写。
无论哪种，在 `ternary-isa-design.md` 里写明。

**验证** 对地址 0 起做 LW，读到的应是数据段内容而非指令 tryte。

### C2 [P1] 没有平台规范这一层

**事实**
- B0 把 syscall 表定为「虚拟设备接口」（`docs/TASKS.md` B0 节），但它手维护在 4 处：
  `vm/trirv_vm.c:690-700`、`vm/trirv_vm.py:22-73`、`cc/trirv_cc.c:1769-1771`、
  `libc-musl/musl_tri/arch_tri/syscall_tri.h`，另有 3 份文档。记忆里两次踩到「白名单双点」
  就是这种结构的症状。
- 容器的版本字段读入后从不比较（`vm/trirv_vm.c:1243-1244, 1275`）【亲核】。
- 没有任何特性发现机制：ADR-06 规定身份类 CSR 读 0 即合规（`docs/DECISIONS.md:59`），
  guest 查不到 VM 支持哪些扩展、有几个核。CVM 的 CSR 数组只到 900，高区一律读 0【取证】。
- 内存布局只存在于链接器常量和文档散表里：数据 ≤ 4095、栈 4096、堆 1<<19、mmap 自顶向下、
  AMP 窗口在顶部 64 tryte。
- 与 UBIOS 的关系只在两行背景里出现（`docs/trirv-unified-memory-abi.md:6`、
  `docs/trirv-firmware-architecture.md:44`），没有对应的 ADR 或任务。udk 显示 UBIOS 的接口是
  按 module_id / function_id / information_id / notify_id 组织的消息模型，加上 §11.3 的系统信息表
  （`udk/ubios/src/lib.rs:10-204`、`tables.rs:1-394`【取证】）。TriRV 的 syscall 表与它是什么
  关系、批次 5 的固件是不是一个需要 module_id 的 UBIOS 模块，目前没有文字。

**参照** sov-math 的契约头是单一事实源，导出面与文档表都由它对账；udk 把规范章节映射成
结构定义 crate。反例也在 udk：规范文本不在仓内，`ModuleId` 的掩码与自己的注释对不上
（注释写 0x0000–0xEFFF，`(id & 0xC000) == 0` 实际是 0x0000–0x3FFF；`udk/ubios/src/lib.rs:91-101`）
而没有测试能发现【亲核】。

**建议（拍板）**
1. 新建承诺类文档 `docs/ternary-platform-spec.md`：内存映射、设备与定时器模型、陷入与向量、
   启动协议、syscall ABI、容器格式与版本规则。
2. syscall 表改为单一来源 `platform/syscalls.yaml`（号、名字、族、实现度），生成或对账
   上面 4 处代码与文档表。
3. 加只读特性 CSR（版本、扩展位图、核数）。属加法扩展，符合 ADR-01/06。
4. 明确 TriRV 与 UBIOS 规范的关系：对齐、桥接，还是不相关。

### C3 [P1] 已知号但未实现的 syscall 返回 0

`vm/trirv_vm.c:816-821`：MUNMAP、MPROTECT、FUTEX、YIELD、FSTAT、FSTATAT、LSEEK、SIGACTION、
SIGPROCMASK、SIGRETURN、GETCWD、READV、WRITEV 一律 `ret=0`；OPENAT 返回一个自增的假 fd，
不打开任何文件；CLOSE 返回 0【亲核】。文档只标了「文件（stub）」
（`docs/trirv-unified-memory-abi.md:100`）。ADR-09 对未知号规定 −ENOSYS（`docs/DECISIONS.md:90`）。

**后果** fstat、lseek、readv、writev 是 stdio 批次 6.3–6.7 的核心路径。桩返回成功而不填
结构体、不移动文件位置，libc 层得到的是「成功」的假象。6.2 的验收只覆盖 pthread 桩清单。

**参照** 「不可达分支……不许静默返回默认值——默认值会让错值伪装成正确」
（数学库 `docs/principles.md:66` 及 `:73` 的判例）。

**建议** 桩分三级并登记在 C2 的表里：真实 / 桩（写明可观测行为）/ −ENOSYS。调用方依赖
输出结构体的号（fstat、lseek、readv、writev、getcwd）默认改 −ENOSYS，批次 6 逐个转真实。
musl 对这些号的 ENOSYS 降级路径需要逐个核对【未验证】。PyVM 同步。

### C4 [P2] 静态数据窗与对齐规则

数据区上限 4095 tryte（`cc/trirv_cc.c:1415-1419, 4059-4063`），可用约 3.9K；struct 成员起始
一律 9-tryte 对齐（`docs/ternary-abi-minimal.md:130`）。musl 的 stdout 自带 `BUFSIZ+UNGET`
的静态缓冲，`BUFSIZ` 为 1024（`third_party/musl/src/stdio/stdout.c:5`、`include/stdio.h:50`）
【亲核】。两个标准流的缓冲加 FILE 本身会占去窗口相当一部分【推断，未量】。

**建议** 批次 6 开头加一个任务「链接布局参数化」，与 C1 的布局决策合并；量出 `FILE` 的
实际占用。

---

## 5. D 组：计划

### D1 [P0] 批次 3 的准入没有性能条件

**事实**
- `docs/PERF_REPORT.md:486-491`：fib(24) TRV1 52.3 ms、TRV2 118.1 ms（2.3×）；sumloop
  4.2 对 5.5 ms；axpy 3.8 对 3.2 ms；码长 600 B → 562 B（6.3%）。原因是「packed 取指 hint
  对递归回跳二分回退」，并注明「ADR-17 准入为行为一致」。
- ADR-17 的四条准入都是功能与等价项（`docs/DECISIONS.md:162-165`）。
- 采集时进度游标到 3.5，3.10 是切默认。
- 8.5% 的节省是弛豫落地之前对 28 个文件测的（`docs/TC_COMPRESSION_DESIGN.md:114-121`）；
  TRV2 的分支窗是 ±273 槽，弛豫的触发面更广，对码长与步数的影响还没有在全集上重测。

**含义** 默认路径将在递归与分支密集的代码上付出 2.3× 的解释开销，换来 6–8.5% 的码长。
代价来自引擎的数据结构，不是语义，可以修。把「修到与 TRV1 相当」放进准入，比切完再补便宜。

**参照** 「降级必须写在此，不许静默」（数学库 `docs/plans/m3c-atomic-plan.md:207-209`）；
ADR-31 自己规定性能主张必须出自固定微基准。

**建议（拍板）** ADR-17 增补第 ⑤ 条：TRV2 引擎在固定微基准三档上相对 TRV1 的耗时比不超过
阈值（建议 1.15），未达标不切默认。`tests/run_perf.py` 纳入批次 3 的固定动作；弛豫落地后
重测 TC 率与码长。

### D2 [P1] 批次 5「固件」运行在哪一层没有定义

**事实**
- 现有框架是宿主侧程序：`cc/trirv_firmware.c:23-31` 包含宿主头文件与 `"trirv_vm.c"`，
  `:262` 用 `__builtin_ctz(g_ready_mask)`，`:409` 是 `int main`，由
  `bench/trirv_firmware_bench.sh:11` 以 `gcc -O2 -fopenmp` 构建【亲核】。它调度的是 VM 实例
  （`:237-239`）。文档却写「固件是跑在 TriRV 上的一个特殊程序」
  （`docs/trirv-firmware-architecture.md:48`）。
- `tests/run_all_tests.py` 不引用它【亲核】；它的实测结论只有 bench 脚本为据。
- ADR-24 写「在已有框架上推进……建 ISR 向量表」（`docs/DECISIONS.md:236-238`）；5.4 的验收是
  「既有 RMS、PIP、看门狗实测结果不变」（`docs/TASKS.md` 5.4），5.6 才把固件契约套件入册；
  5.3、5.5 的任务（trap 入口上下文保存、按 mcause 分发的向量表）在 guest 内。

**含义** 「固件」至少有三种所指：宿主侧调度器（现状）、guest 内的 RTOS、UBIOS 规范下的
平台固件。若要把 RMS/PIP 移进 guest，现有代码用到的位图、ctz、十六进制魔数都要按三值本征
重写，计划里没有对应任务。5.4 以「既有结果不变」验收，而改动前没有回归基线。

**参照** 「闭环」的三种含义必须分开写（数学库 `本源-投影分层-A1线-2026-10-17.md:60-69`）。

**建议** ADR-24 增补一句批次 5 的交付形态；批次 5 第 0 步给现有框架补回归基线，或明确把它
冻结为「宿主侧演示」并不再作为 5.4 的验收基准；新增任务「guest 内的 ISR 入口与上下文保存」。

### D3 [P1] 批次 6–8 没有量化基线，口径是编译率

**测量**（只读；把现有 `preprocess_musl.py` 规则施加到仓内 vendored 的 musl 1.2.5 源码；
方法见附录 A）【亲核】

| 目录 | 文件数（不含宽字符） | 现规则直接 SKIP | 过预处理 | 过预处理且含二进制敏感惯用法 |
|---|---|---|---|---|
| string | 46 | 6 | 40 | 0 |
| stdlib | 20 | 11 | 9 | 0 |
| ctype | 34 | 1 | 33 | 16（46 行） |
| stdio | 116 | 86 | 30 | 1（5 行） |
| multibyte | 14 | 0 | 14 | 6（37 行） |
| time | 38 | 4 | 34 | 8（14 行） |

「含惯用法」是词法筛查，十六进制常量之类无害的行也会计入——线索池，不是判决。

**读数**
- stdio：现规则下 86/116（74%）在预处理阶段即被 SKIP，其中 82 个的原因是「依赖 musl stdio
  内部结构」。6.10 的目标是 ≥ 90%，起点是 26%。阻塞面集中在少数内部实现文件
  （FILE、`__stdio_*`、`__intscan`、`sh_*`），6.1 的依赖盘点应先列出这个核心集。
- ctype：33 个「过预处理」的文件里 16 个含会算错的惯用法。例：
  `isalpha.c` 的 `return ((unsigned)c|32)-'a' < 26;`——三值 `|` 是逐 trit max，`'A'`(65) 与 32
  逐 trit 取值得 68，68 − 97 < 26 为假，`isalpha('A')` 返回 0（手算，未上机）；
  `isascii.c` 的 `!(c&~0x7f)`；`__ctype_b_loc.c` 的 `(x/256 | x*256) % 65536` 打包表。
  这些文件会编译通过。
- time：`__year_to_secs.c` 的 `int leaps = (y-68)>>2;`——三值 `>>2` 是 ÷9。它正好落在 8.2 的
  验收用例上。
- multibyte：`internal.c` 的 `R(0x80,0xc0) | 0x2` 类型表。
- string 与 stdlib：49 个过预处理的上游文件 0 命中。现有 65/66 这一批没有这类问题（见 §6）。
- `libc-musl/preprocess_musl.py:159-176` 按正则整行删除含 `& 数字`、`&= 数字` 的行，注释称
  「只影响性能不改变语义」。六个目录实测命中 2 行：`string/memset.c` 的 `n &= -4;`、
  `ctype/towctrans.c` 的 `c &= 255;`——后者是截断，不是对齐。
- 同文件 `:154-155` 的浮点守卫仍输出 `DECISIONED: no FP on TriRV`，理由已过时。

**口径** 6.10 的验收是编译率；`README.md:241-242` 的「65 编译 OK」也是。行为断言只覆盖少数函数。

**参照** 「索引，不是验证」（数学库 `engineering/index_presentations.sh:2, 15`【取证】）；
sov-math 的等价性分级；ADR-21 给 wasm 换了与实现无关的刻度，libc 应当同理。

**建议**
1. 6.1 的验收物改为「函数级证据表」：每个函数 × {编译通过 / 行为断言 / 与上游测试集对拍 /
   musl_tri 重写}。
2. 引入与实现无关的刻度：musl 自带的 libc-test 或 picolibc 测试集的通过数【建议】。
3. 二进制敏感惯用法改成编译期诊断或预处理期 SKIP（现在是放行），至少覆盖 `|32`、`&~0x`、
   `>>n`/`<<n` 的非移位用途、`(x/256 | x*256)` 这几种形状。
4. 删行规则改为报错并要求显式重写。
5. 上面的基线表写进批次 6、7、8 的开头。

### D4 [P2] 批次 9 的 f32 档位与官方测试集冲突

ADR-21 把验收改为「官方 spec testsuite 的通过数」（`docs/DECISIONS.md:206-208`）；D-F3 把
wasm f32 对应 TF20、f64 对应 TF45（`docs/TERNARY_FLOAT_PROPOSAL.md:113, 659`；实现
`wasm/trirv_wasm.py:549, 567`）。TF20 的尾数是 5 trit，约 7.9 bit；官方测试集的 f32 用例断言
IEEE 位精确结果（24 bit 尾数）。f64 值在 TF45 里可精确承载【推断，未实测】；f32 不可能位精确。
ADR-21 没有说明通过数如何分口径汇报。

**建议** 9.1 的基线表按 i32/i64/f32/f64 分列；f32 明确走「TF45 承载 + 出入口舍入」，或登记为
已知不符项；ADR-21 增补一句。9.1 本来就要跑官方集，f32 单项计数即可证实或推翻本条。

### D5 [P2] ADR 里的「当前值」与缺失的复核触发

ADR-14 冻结 TC 寄存器窗口，依据是单一代码生成器在 28 个文件上的寄存器分布
（`docs/TC_COMPRESSION_DESIGN.md:31-35`）；维护者自己把「窗口被单一实现绑架」列为待拷问的
第一条（`docs/PROJECT_AUDIT_BRIEF.md` §9.1）。ADR-15 文内有「当前 28.0% / 6.7%」。
ADR-01..33 同日提出、同日整批确认（`docs/DECISIONS.md:10`）。

**建议** 给 ADR-14、15、17 各加一行「复核触发」：出现第二个代码生成器（LLVM P-3）或语料
扩到 stdio 时重测窗口命中率与 TC 率，低于约定值即重开。

### D6 [P3] 部分验收条目不可判定

例：3.6「可执行用例抽样 ≥ 30」没有种子与抽样框；3.8「全过」没有列 busybox 的 applet 与
输入集；6.8「结论记录」（均见 `docs/TASKS.md` 对应条目）。

**参照** 「过」的判据必须是命令 + 期望输出（数学库 `docs/plans/m3c-atomic-plan.md:30`）。

---

## 6. E 组：工程与可复现

### E1 [P2] Python 回退默认开启

`cc/trirv_cc_backend.py:105, 116-129`：默认模式 `c`，C 后端抛任何异常即回退 Python，
只有设置了 `TRV_CC_STRICT` 才抛出；捕获的是 `except Exception`，内部错误与「不支持」一并回退；
失败路径的 stdout 被丢弃，只在 stderr 保留每个原因的第一行。7 个测试文件（含运行器）设置了
STRICT，运行器默认设置（`tests/run_all_tests.py:220`）；`tools/export_trv.py:16, 22` 的直接
使用者拿到的是回退路径。同文件 `:102` 引用的 `CC_覆盖率.md` 不存在【亲核】。

**参照** 不许静默返回默认值（数学库 `docs/principles.md:66`）；本项目 wasm 路线自己的规矩
是「都是显式 `UNSUPPORTED`，绝不静默出错」（`docs/WASM_ROUTE_A.md:152`）。

**建议** 回退改为显式开启（如 `TRV_CC_FALLBACK=1`），默认 STRICT。

### E2 [P2] 三个编译器二进制；新克隆不可复现

- 同一份 `cc/trirv_cc.c` 有三个二进制：根目录 `./trirv_cc`（`tools/build.sh:22`）、
  `cc/trirv_cc`（`trirv_cc_backend.py`）、`cc/bin/trirv_cc`（`run_all_tests.py`）。
  采集时根目录那份的日期是 11-23 17:21，源文件是 11-24 05:06【亲核】——`README.md:144` 的
  快速开始让用户运行的正是这份过期二进制。
- 根目录 `trirv_vm` 等 4 个符号链接未被跟踪（`.gitignore:2-3`）；多个套件默认使用根目录路径
  【取证】。`tools/build.sh` 只能从根目录经符号链接运行【取证】。新克隆加构建跑不起全量回归。

**建议** 测试只认 `cc/bin` 与 `vm/bin` 一份；`build.sh` 也写这两个路径；README 指向同一份。

### E3 [P3] 批量脚本硬编码本机路径；`-l` 产物只压不修

`libc-musl/batch_musl_compile.py:10-11, 49, 70`、`batch_musl_tri_tcc.py:25-27` 写死
`/Users/zesheng/ai-lab/trirv/…`。`.gitignore:12-13` 用 `**/-l` 压掉批量脚本误生成的文件，
注释写「反复出现，根治」；根因是测试把 `"-l"` 传到了 `trirv_cc` 的第 3 个参数（汇编清单输出）
位置（`tests/trirv_link_e2e.py:170`；`cc/trirv_cc.c:4146-4148`）【取证】。仓库根目录现存一个
85 KB 的 `-l`【亲核】。

**建议** 路径相对脚本位置解析；修参数解析或改测试的调用方式，然后删掉那条 ignore。

---

## 7. 已核无问题（记下，免得重查）

1. **string + stdlib 过预处理的 49 个上游文件：二进制敏感惯用法 0 命中。** 筛查对已知含位运算的
   `strspn.c`、`qsort.c` 能报出命中（负向控制通过）。现有 65/66 这一批没有 D3 所述的问题。
2. **CVM 的 syscall 面没有宿主文件系统或网络直通。** OPENAT 返回假 fd（`vm/trirv_vm.c:820`），
   READ/WRITE 只认 fd 0/1/2 且走预载的 stdin 缓冲（`:711-722`）。PyVM 的 openat 同构
   （`vm/trirv_vm.py:463-464`；其余未逐项核）。
3. **musl 批量管线直接调用 C 二进制**（`libc-musl/batch_musl_compile.py:49, 70`），
   「编译通过」不受 E1 的 Python 回退影响。
4. **`IDEFS[]` 与 `INST` 当前一致**：都是 109 行，(op, funct, funct2, 格式) 多重集相同
   （`2869d9e` 时比对）【取证】。没有任何测试守护这一点。

---

## 8. 建议的落地顺序

**下一个提交（纯修复，不需要拍板）**
- A3-1：`trirv_tri_musl_e2e.py` 三处路径。
- A4-1：`trirv_difftest_tcc.py` 的 PyVM 列真走 PyVM。
- A3-3：`test_isa_completeness.py` 的 CVM 路径。
- A2：提交 `tri-isa.h`。

**批次 3 内**
- D1：ADR-17 增补第 ⑤ 条（拍板阈值）。
- A5：定义编码合法性，CVM 补非法指令陷入。
- B1-S1：规范函数 + 编码空间全枚举（拍板是否提前）。
- C1：内存模型 ADR（切默认之前）。
- A3-2、A3-4：SKIP 不计 PASS；逐套最小用例数。
- A6：README 与 `ternary-abi-minimal.md` 校准。

**对应批次开工前**
- 批次 4：C2（平台规范、syscall 单一来源、特性 CSR）。
- 批次 5：D2（交付形态与回归基线）。
- 批次 6：D3、C3、C4。
- 批次 9：D4、B3。
- 任何时候：A1、A7、A8、E1。

**待拍板项（采纳后按 ADR-32 追加记录）**

| 项 | 内容 |
|---|---|
| P-1 | 远端或异地镜像；推送 tinycc `trirv`；补 `.gitmodules`（A1） |
| P-2 | ADR-17 第 ⑤ 条与阈值（D1） |
| P-3 | 内存模型：代码/数据分离还是统一；布局策略（C1） |
| P-4 | 未定义编码的语义：一律非法指令陷入（A5） |
| P-5 | 平台规范立项；syscall 单一来源与实现度分级；特性 CSR；与 UBIOS 的关系（C2、C3） |
| P-6 | F 扩展算术语义口径（B3） |
| P-7 | 批次 5 的交付形态（D2） |
| P-8 | 规范模型 S1 是否提前到批次 3；S3 是否对接数学库（B1） |
| P-9 | 回退默认改 STRICT（E1） |
| P-10 | wasm f32 的口径（D4） |
| P-11 | ADR-14/15/17 的复核触发条件（D5） |
| P-12 | 坑实录迁入仓内；运行日志追加写（A7、A8） |

---

## 附录 A：D3 基线表的测量方法

只读，不写仓库。对 `third_party/musl/src/<目录>` 下每个 `.c`（排除 `wc*`、`wcs*`、`wmem*` 前缀，
与 `batch_musl_compile.py:42-43` 的过滤一致）调用 `libc-musl/preprocess_musl.py` 的 `preprocess()`：

1. **SKIP**：输出首行以 `#error TRIRV_SKIP` 开头。
2. **静默删行**：给模块的 `re.search` 加垫片，记录命中 `&\s*-?\s*[0-9]` 且同一行不含
   `if`/`while`/`return` 的行，以及命中 `&=\s*-?\s*[0-9]` 的行。
3. **惯用法**：对输出逐行匹配 `>>|<<|\^|[^|]\|[^|]|~|0[xX][0-9a-fA-F]+|UINT_MAX|ULONG_MAX|CHAR_BIT`，
   跳过以 `#`、`/*`、`*`、`//#` 起头的行。
4. **负向控制**：对 `string/strspn.c`、`stdlib/qsort.c` 跑同一正则，分别报出 1 行与 9 行命中。

复现任何一行数字只需按上面四步重跑；`isalpha('A')` 的 0 是手算，上机命令是把这个文件过
预处理后用 `trirv_cc` 编译并返回 `isalpha('A')`。

## 附录 B：复核命令

```bash
cd ~/ai-lab/trirv
git remote -v                                         # A1：应为空
git -C tinycc rev-list --left-right --count HEAD...origin/trirv   # A1：20 0
git -C tinycc status --short                          # A2：M tri-isa.h
python3 tests/trirv_tri_musl_e2e.py; echo $?           # A3：0 PASS / exit 0
ls -la /tmp/trirv_vm_test                              # A3：11-18 的旧二进制
grep -n "cregs\[rd&7\]" vm/trirv_vm.c                  # A5
grep -n "CAUSE_ILLEGAL" vm/trirv_vm.c                  # A5：只有定义
grep -l "统一补标——该文档未逐份定稿" docs/*.md | wc -l        # A4：12（11 份模板 + 本文的引文）
grep -L "^状态：" docs/*.md README.md cc/CC_寄存器式编译器说明.md  # A4：无状态行的 3 份
```
