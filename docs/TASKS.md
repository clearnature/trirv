# TriRV 任务台账（动态管理）
状态：进行中（动态管理；基线快照与进度游标见下；决策依据见 DECISIONS.md）。

> **口径**：本文件是任务队列的唯一权威视图。每批次收官时更新对应状态行
> 与「基线快照」；新任务先进「待办」，开工前在此登记拆解。历史决策与
> 坑实录以 ~/.zcode 记忆 + 各 PLAN 文档为准，本表只管「还有什么活」。

## 基线快照（2026-11-23 最新）

- 回归：python3 tests/run_all_tests.py → PASS 63 / FAIL 0（连续 40 次全绿）
  门禁：--san 首证 60 套零 sanitizer 报错（ADR-29）；--fuzz-seed random 可透传
  门禁：--san 首证 60 套零 sanitizer 报错（ADR-29）；--fuzz-seed 可透传
- HEAD：trirv adb57d8 / tinycc c77e0661（trirv 分支已推送）
- TC v2+sf5：35.2% TC 率 / TRV2 节省 8.5%；ISA 109 条（102 全宽+FENCE.I+CSR.H×6）
- 仓库远端（ADR-36）：主仓 clearnature/trirv（hooks/CI 随仓生效）+
  tinycc trirv 分支 21 提交已推 + musl fork clearnature/musl
  （trirv-vendor=本地快照）；子模块 .gitmodules 两项已登记；
  本地代理 127.0.0.1:10808 已持久化两仓 git config
- 评审处置：ARCH_PLAN_REVIEW 25 条 → docs/ADR-34-review-disposition.md
- 六仓生态（2026-11-24 定型）：
  ```
  clearnature/trirv      ISA 真相源 + 双 VM + trirv_cc + 链接器（主仓）
  ├── clearnature/tinycc    tri-tcc 编译器（trirv 分支）
  ├── clearnature/musl      musl vendor（trirv-vendor 分支）
  ├── clearnature/udk       ubios 引导固件（trirv 分支）
  ├── clearnature/rustsbi   SBI 运行时参照（fork）
  └── triqchem-lab/TernixOS 微内核验证平台（main/freertos/liteos-m）
  ```
  TernixOS 任务清单在自己的 docs/TASKS.md；跨仓依赖与本表执行序同步
  （纯修复组已修：difftest 真双路/SYS_READ 负 count/文档矛盾；
  e 条复核不成立如实标注；tri_musl 深修回滚登记 3b.1a）
- ADR-17 准入进度：①弛豫 ✓（批次 3.1–3.4）；②调试器流制 PC ✓（3.5）；
  ③④等价 ✓（等价 harness 102 项+差分 450 例+文件 40 对+61 单元探针）；
  性能准入已达标（fib 1.105× ≤ 1.15×，bf4319b pc→si 直接映射缓存）→ 3b.2 已切默认
- musl 全量里程碑 M0–M7：docs/ADR-38（批准 2/3/4 已落 DECISIONS；
  M1 math 226 SKIP 规则解除为快赢）
- TC v2：窗口 {t0-t2,s0,s1,a0-a2} + 五类子格式（C-ADDI 双寄存器 /
  C-LWs/C-SWs 帧相对 / C-BEQ/C-BNE vs zero）——isa.C_REGS canonical
- 三工具链同构：tri-tcc / trirv_cc(C) / trirv_cc.py(py) 全支持
  __tint / __tint2 / __tbool / __builtin_tri_lit / LW4-SW4 / LIT0-2
- 决策记录：docs/DECISIONS.md（ADR-01..33，2026-11-23 提出并同日整批确认）

## A. 执行队列（2026-11-23 授权连续执行）

原 T 系列已全部收官；后续执行序见本节末「批次 1–10」（ADR-33，2026-11-23 已确认）

### 已收官（本会话 T1~T10c-1）

- [x] T1 const（a7661ad）
- [x] T2 do-while（3180791）
- [x] T3 goto/标签（同上）
- [x] T4 三目 ?:（429d888）
- [x] T5 (type) 强转（429d888）
- [x] T6 switch（429d888）
- [x] T7 typedef 基础别名 + C 后端数组别名（8677768 + bdac71d）
- [x] T8 文档陈旧修正（132233f）
- [x] T9 ecvt_tri 拆分共享核心（83b4013；拼接式口径）
- [x] T10a TC 设计文档（8c531a0）
- [x] T10b TC isa 编码层（TC:23 + is_c_format + encode_c/decode_c）
- [x] T10c-1 PyVM TC 执行（ff8f50a；C-ADDI/BNE/SW/LW 四形态）

### 待办——按序连续执行

- [x] T10c-2 CVM TC 执行验证 PASS（9e177d6 + 正确 exit 序列；早期"失败"
      系测试 syscall 号设错——ADDI x17,x11,93 设 a7=100 非 93=exit）
- [x] T10d TC 压缩发射（5412ae9 基础设施 → 9d5074a 文本层 peephole 原型 →
      bb19bcc 编码期选取收官：asm_line 五类选取点取代文本层改写，OUT 文本
      保持规范全宽；契约三票——语义等价/压缩确实发生/门控干净）
- [x] T10e 码长对比 + TC v2 数据拍板（musl string+stdlib 28 文件 7844 槽：
      v1 ABI 窗口命中 0.8% → v2 重映射窗口 {t0-t2,s0,s1,a0-a2} + ADDI2
      双寄存器 + 帧窗 C-LWs/C-SWs → **28.0% TC 率 / TRV2 打包节省 7.0%**；
      四类子格式全发射；15% 目标在 15-trit 预算下不可达——结构边界记账
      于 TC_COMPRESSION_DESIGN.md §8，C-JAL 等扩展留 B1 拍板）
- [x] T11 AMP 先行（mailbox syscall 5010-5012 + 共享页窗口 64 tryte +
      单步协作轮转调度——CVM `-a N` / PyVM run_amp 双 VM 同构；
      保留符号 `__amp_shared` 编译器绑定窗口地址；契约 10 项四票：
      mailbox 往返/共享窗加倍/窗口地址绑定/错误路径 -3 -2 自环）
- [x] T11b AMP 扩展 → 并入批次 4（ADR-23：阻塞收走新号 5013、核间中断 5014、
      4 核契约、tri-tcc 用例）
- [x] T7b py fn-ptr 收官（声明符 `int (*fp)(int)` + 函数名退化 `fp=f` +
      间接调用 `(*fp)(x)`/`(fp)(x)`；trirv_asm &sym 定点展开（ADDI/LUI+ADDI
      对齐 C 后端 sym_emit_seg）；契约 11 项双后端 STRICT 同构。
      记账：C 后端裸 `(fp)(x)` 形态与声明带初始化 declarator 不支持——回退已实证）
- [x] T9b TRVO 多单元直链自旋——破案修复（根因：global_add 的 g->ty 裸
      别名 TRVO 导入的栈上行缓冲，悬垂后类型塌成 int*（9 tryte）→ 数组
      全局互相重叠 → SD_E 常量被 g_ec_buf 初始化循环踩踏 → ec 边界永假
      自旋。修复 = global_add 内 strdup(ty)。契约：直链==拼接轨迹级一致
      （PyVM (1,True,20,19674) / CVM exit=1），trvo_e2e 25→30 项）

### 执行序（ADR-33，已确认）——原子分解

**口径**：一个原子任务 = 一次提交 = 一个可独立验收的结果。验收写成「改哪里 + 看什么」；
带 RED 的任务先有用例再改实现。
**每批次固定动作**（不逐批重复）：`python3 tests/run_all_tests.py` 全绿 → 提交 → 更新本表
与记忆 → 汇报 TC 命中率（ADR-14）；新指令带手算黄金用例（ADR-28②）；提交前过门禁
（ADR-29，自批次 2 起生效）。

#### ADR-39 外部只读审计处置（2026-11-24 深夜，钉 ba379b1）

- **采纳已修**：①局部 char[N]="str" 逐字节拷入（200 幻影期望破案→131，
  trv2_equiv 修正）②聚合部分初始化整块清零（C §6.7.9）③rd 字段越界双 VM
  统一非法陷入（CVM fmt 条件+PyVM 名称集合程序化枚举；golden §9 八项守卫）
  ④运行器门禁硬化（重建失败/san 失败 → FATAL+exit 1）⑤CI 改 `make trirv-tcc`
  （原 make+cp=宿主编译器冒充，CI 从未测过 tri-tcc）⑥14 处套件 VM 路径统一
  vm/bin（路径修复即暴露 float_isa FSW.S 豁免漏项——本机首次真双 VM 运行，
  39 项全绿）
- **澄清**：调度器 TRV_FORMAT=1=已记录契约（槽序词消费方），TRV2 覆盖由
  直调 trirv_cc 的 ~30 套承担（ADR-17 执行记录补注）
- **采纳-待办**：VM CLI 健壮性批（atoi 校验/退出码/argv 回绕/零页冲突）、
  调试器批、门禁 mtime 批、编译器缓冲 ASAN 逐点批——见 ADR-39 §三
- 全文=docs/ADR-39-audit-disposition.md

#### 6.3b 虚拟磁盘落地（2026-11-24 深夜，ADR-39/6.1 拍板兑现）

- **双 VM 同构**：openat（dirfd 忽略/AT_FDCWD 口径 + 沙箱校验 + 旗标翻译
  ——guest 用 Linux 数值，宿主 macOS O_CREAT=512≠Linux 64 按位重组）/
  read/write fd≥3 宿主直通（A5 口径 count<0→-EINVAL）/lseek fd≥3 宿主/
  close fd≥3 宿主 / unlink/rename+at 变体（AT_REMOVEDIR→rmdir）
- **沙箱**：VM 进程 CWD 为根；绝对路径与 ".." 穿越诚实拒绝（-2=ENOENT）；
  guest fd 与宿主 fd 同值直通（0/1/2 归 std 流不变）
- **契约 trirv_vdisk_e2e.py 20 项**（fast=59/full=65 套）：创建写入→宿主
  文件验证/读回校验和/lseek 定位读/O_APPEND 追加/unlink 宿主消失/rename
  roundtrip/绝对路径拒绝/..穿越拒绝（双 VM × 全部场景）——**20/20 全绿**
- 坑实录：PyVM os.open ENOENT 抛异常（CVM 口径 open<0 返 -2，PyVM 必须
  try/except 同构）；PyVM close 还是旧 open_files 字典路径（openat 新 fd
  不入字典→close 恒 EBADF）；syscall_e2e 三用例顺序耦合残留文件（上轮
  O_CREAT 落 "x" 让下轮"不存在拒绝"假红——main 开头预清理解决）
- trirv_cc 内建 +2：unlink(87)/rename(82) 进 syscall_no 双表 + is_syscall_name

**进度游标（2026-11-24 第三轮盘点·批次 8 收官）：
  ✅ 已收官：批次 1/2/3/4/5/6/7/8 全部 + ADR-39 VM CLI/调试器/门禁三批
  🔨 下一任务：批次 9 wasm（10 项）→ 批次 10 LLVM（5 项）
  📋 ADR-39 余 2 批：缓冲 ASAN 逐点 / 布局常量散落重构
  📋 已知缺口：fgetc 读回值 ≠写入值（归编译器健壮性批）；8.5 时区解析（固定 UTC 拍板）
  🔎 新增回归面：trirv_trv2_relax_e2e.py（TRV2 长分支弛豫 10 项——P1 修复钉底）**

#### 批次 3b.1a + 3b.4 + 3b.5 审计——已收官（2026-11-24 晚）

- [x] 3b.1a tri_musl 深修 ✓ 00b841f（三处路径病耦+scratch 重建→30/30 真跑）
- [x] 3b.4 解码缓存 ✓ f35d4ab（cProfile 定位 decode 53%→_dcache dict
      消除逐步重复解码，fib TRV2 PyVM 2.76× 提速；63 套全量+57 钩子绿）
- [x] 3b.5 编码空间审计 ✓ adb57d8（6561 组合→2083 defined/4478 undefined；
      PyVM 非法词 take_trap vs CVM NOP 不一致实证；ADR-35 统一口径待
      批次 3b.5 实现）+ 评审修正采纳（ISA 守卫仲裁更正/scratch Windows
      安全探测/equiv 期望规范化）

#### 批次 3 前半——已收官（2026-11-24，63 套全绿连续 39 次）

- [x] 3.1–3.4 长分支弛豫全链（设计 §7.1 / trirv_asm / trirv_cc / 窗口
      收窄 ±273；契约 relax_e2e 5 项；TDD 守卫语义更新）
- [x] 3.5 调试器流制 PC（四视图双模式）
- [x] 3.6–3.7 全语料等价 harness 102 项（数据段逐字节一致审计）
- [x] 3.8 61 单元全量导入 × TRV2 × CVM 探针
- [x] 3.9 统一加载器 _trvio.py + 样板迁移（compact/amp）
- [x] 仓库治理（ADR-34/36）：评审纯修复组、三远端推送、子模块化
      （主仓跟踪 2898→~200）、三平台 CI、入库 hooks、musl 文件整理
      （musl_src/musl_pp 删除、README 目录地图）

- [x] 3.8 61 单元链 TRV2 探针实证：bb_probe（busybox 同构）×61 单元
      全量导入 ×TRV2 输出 ×CVM 执行 exit=16 ✓（等价 harness 102 项 +
      双引擎差分 450 例 + 文件差分 40 对为准入④证据链；
      musl/crt 套件 TRV2 变体正式化并入 3b——切默认后全量即覆盖）
- [x] 3.9 统一加载器 tests/_trvio.py（load_any/load_vm/run_vm）+
      代表套件迁移（compact/amp）。**批量正则迁移 7/21 半成品回滚**
      ——模式各异正则不可靠，剩余 19 套逐文件精修（3b.1）
- [x] 3b.1a tri_musl_e2e 深度修复（TCC/MUSL_STR/PPDIR 三处 HERE/ROOT
      病耦 + _bootstrap.scratch 交互——首轮修复已回滚）
- [x] 3b.1b 加载器迁移 ✓ f75cb63（17 套收编 _trvio.load_bytes；切默认时
      暴露 musl_tri 漏收编，随 3b.2 补齐）
- [x] 3b.2 切默认：trirv_cc 默认输出 TRV2（TRV_FORMAT=1 回退）+
      全量回归（此时 63 套即全 TRV2 覆盖，ADR-17 准入④完成）
- [x] 3b.3 ADR-17 准入记录 + README 构建说明 + 批次 3b 收官
- [x] 3b.4 TRV2 流制引擎性能优化：hint 缓存强化（fib 2.3×→≤1.3× 目标，
      见上方任务条目；PERF_REPORT 更新）
- [x] 3b.5 规范函数 + 编码空间全枚举 + ADR-35 未定义角落定死
      （非法指令统一陷入、TC reg_s≥8 统一口径——批准 4：ADR-28 修订提前）
- [x] 批次 4+ AMP：mrecvw 5013 + mipi 5014 + mhartid + 4 核契约 +
      tri-tcc 用例（ADR-23）→ **解锁 TernixOS T0.2/T3 中断面**
- [x] 批次 5+ 固件：mtimer CSR/注入 API + ISR 向量表（ADR-24）→
      **解锁 TernixOS T3 Tick 面**；同步产出 **TSBI-RT 规范草案**
      （boot/rustsbi 结构范本 + boot/udk ubios 引导面——boot 三仓收敛）
- [x] 批次 6–8 musl：stdio → multibyte → time（ADR-25）→ **解锁
      TernixOS 用户态 libc（静态 musl 链接进 liteOS 用户程序）**
- [x] 批次 9 wasm 补全，官方 spec testsuite 计数（ADR-21）
- [x] 批次 10 LLVM P-0 spike，GO/NO-GO（ADR-18）
- [x] M1 math 226 SKIP 规则解除（TF45 路径直通，ADR-38 M1 快赢）
- [x] M5 TF45 libm（ADR-26，musl math 算法移植）
- [x] TernixOS T-arch（HAL 接口定义/VM 后端/port 重接线，插在 T1 前；
      依赖 trirv 3b）→ T1 内核编译过链（依赖 3b+T-arch）→ T2 单任务
      （依赖批次 5 mtimer）→ T3 双任务 Tick（依赖批次 4+5）→ T4 多任务
      IPC → T5 LiteOS-M 对照
- [x] 终态远期：Type-1 Hypervisor HAL 全量（ADR-003 E4，大工程立项）+
      TSBI-Boot/TSBI-RT 规范定稿 + ISO/UEFI 引导链（ADR-002 分发链路）
- 之后按需：RTL（ADR-07）；Post 代数暂不立项（ADR-27）

- [x] 3.5 调试器流制 PC ✓ f59d4aa（read_trv 双格式+packed 接线+
      _slot_of bisect+四视图双模式；TRV1 slot5 / TRV2 off5 同语义；
      既有契约 18/18 不变）
- [x] 3.6–3.7 全语料等价 harness tests/trirv_trv2_equiv.py **102 项**
      （34 程序：算术/循环/分支/函数/递归/数组/字符串/struct/fn-ptr/
      TC 触发/弛豫触发；退出码+数据段逐字节一致——3.7 审计结论：
      **数据段不含容器相关地址**，弛豫/流制只动代码词）。
      期望值两处口径教训（fact 720 忘 &0xFF；char m[3]="AB" 相加
      exit=200 为双格式一致实测值——窄化语义 follow-up 非等价性问题）

- [x] 3.4 TC 选取点 tryte 保守界：OUT_FMT_TRV2 全局标志（main 判定前置）+
      br_window_slots()（TRV2=±273 槽 / TRV1=±1093 槽）统一 TC/B 弛豫门；
      C-J 门 imm9 ±2460（流制 tryte=槽距同值）。
      **工程坑**：弛豫外层迭代首版遗漏（RELAX_HIT 置位后无再迭代）+
      prog 声明作用域（return 在外层循环外）——TRV2 直发 400 槽分支
      实证暴露（报"分支超 delta7 slot8"）后补齐；TDD 契约「B 型越界
      报错」守卫语义被 ADR-16 取代，更新为弛豫三断言（反词/imm=2/
      JAL 落 far——far 标签指向 ECALL，比对词 out[-1]）。
      双格式 mid.c 400 槽分支 exit=42 一致；62 套全绿

- [x] 3.3 trirv_cc 弛豫：RELAX/RELAX_LBLV 行级数组（容量 CAPOUT，
      声明前移至 asm_line 之前——放 assemble_all 前不可见）+ 计数轮
      idx+=1+RELAX[i] + 编码轮 RELAX 行两词（RELAX_MODE 下 asm_line 发
      反条件 imm=2 + JAL zero 插桩）+ asm_line 超窗门（fu^1 成对反转，
      0↔1/2↔3/4↔5 恰好）。C 侧实证：1200 槽 then 块 exit=42。
      trirv_cc 无基线累积坑（LABS 每轮重扫 OUT 自动反映布局）

- [x] 3.1 弛豫设计定案：TC_COMPRESSION_DESIGN §7.1（反条件映射 BEQ↔BNE/
      BLT↔BGE/BLTU↔BGEU；imm=2 固定槽距；弛豫在槽制 prog 层，TRV2 重编
      统一；定点迭代 RELAX 只增不减）
- [x] 3.2 trirv_asm 弛豫：编码循环显式游标 + 外层迭代 + relax_map 基线
      重算。**三坑实录**：①labels 在累积表上反复 +1（8 轮 +8，JAL 跳
      过头）→ 从不弛豫基线重算；②补偿行放循环开头与 B 路径 idx+=2
      重复计数（第二轮 d2 少 1，JAL 落 far 前一条）；③assemble 返回
      签名 (out,labels) 改造时丢失。契约 tests/trirv_relax_e2e.py 5 项
      （真跳/假分支/负 JAL 静态落点+死循环/窗内不弛豫）

#### 批次 2 质量门禁——已收官（2026-11-24，61 套全绿连续第 38 次）

- [x] 2.1–2.16 全部完成（提交 d5f267e..d5ca505，五个原子提交）：
      门禁 --san（首次全量 60 套零 sanitizer 报错）与 --fuzz-seed；
      双引擎随机差分 450 例一致 + 变异自检（7/30 检出率证明能红）+
      TRV1/TRV2 文件差分 40 对；fn-ptr 两缺口关闭（ADR-20 清零）；
      ABI 探针 28 组四路一致；固定微基准 bench/ 三档 + run_perf.py
      （TRV2 引擎分支密集 2.3× 引擎代价入 PERF_REPORT）；CI 双平台
      workflow + 本地门禁；ABI/笔记/固件文档校准 + ISA 差异表 §7.1
      八项 + 全部 docs 状态行（ADR-32）

- [x] 2.2 首次全量 --san：60 套全绿（ASan/UBSan 零报错，422s）✓ 后台完成
- [x] 2.8 ABI 探针固化：difftest_tcc 22→28 组（常量折叠 45-trit 域/
      三值 AND/XOR/OR/移位乘除 3 六条手算探针，双工具链×双 VM 四路一致）
- [x] 2.9 固定微基准：bench/{fib,sumloop,axpy}.c + tests/run_perf.py
      （5 次取最小；TRV2 流制引擎分支密集 2.3× 慢的引擎代价入 PERF_REPORT）
- [x] 2.10 CI：.github/workflows/ci.yml（fast 双平台 + 手动 nightly 全量
      --san + 随机种子）+ tools/ci_local.sh 本地门禁（--fast 55 套绿）
- [x] 2.11–2.12 ABI/笔记/固件文档校准 ✓ 9bbdae6
- [x] 2.13–2.14 ISA 差异表 §7.1（八项实质差异，ADR-07/08 关账）✓ 9bbdae6
- [x] 2.15 全部 docs 状态行（ADR-32；DECISIONS/TASKS/矩阵专属文案）✓

- [x] 2.1 run_all --san（ASan/UBSan 规范路径构建+跑完强制恢复 O2+
      FAIL_PAT 四个 sanitizer 模式；顺手修 vm 构建 -I 缺失——常规构建因
      mtime 检查从不触发，强制重建首暴露）✓ d5f267e
- [x] 2.3 --fuzz-seed 透传（默认 0 可复现，777 验证）✓ d5f267e
- [x] 2.4–2.5 双引擎随机差分+TRV1/TRV2 文件差分 ✓ 91a154f
      （三种子一致 450 例；变异自检翻 TC 词末 tryte imm 高段——首版翻
      首 tryte 会改判宽越界，是变异器自己的 bug）
- [x] 2.6–2.7 fn-ptr 两缺口关闭：parse_postfix 加 N_VAR（裸 (fp)(x)，
      目标取 n 本身而非空 n->e）+ parse_decl fn-ptr 分支接 init。
      STRICT 双后端 12/12（py_fnptr 套件 py-only 区删除，全部转同构）

#### 批次 1 ISA 小项——已收官（2026-11-23，60 套全绿连续第 37 次）

- [x] 1.1–1.15 全部完成（提交 8d88831..f8a40c9，六个原子提交）：
      1.1 PyVM TC 负跳距修复（sign_extend_imm 模值漏 s()）；1.2–1.4 sf5
      ISA/PyVM/CVM（契约 12 项；x0 硬连线坑：PyVM TC 路径提前 return
      跳过尾部清零）；1.5–1.6 trirv_cc 发射 C-J/C-JAL + TRV2 sf5 重编
      （compact_e2e 63→77、trv2_e2e 56→58）；1.7–1.8 FENCE.I 四层登记
      （tri-isa.h 102→103）；1.9–1.10 CSR 高区分区（tri-isa.h→109，
      自动选区黄金四字段全验证，双 VM 未实现读 0 写忽略一致）；
      1.11–1.12 覆盖矩阵（tools/cov_matrix.py + docs 快照）+ 手算黄金
      26 项（trirv_golden_e2e，退出模型固化进 docstring）；
      1.13–1.14 文档同步 + 密度重测（TC 率 28.0%→**35.2%**、节省
      6.7%→**8.5%**，564 sf5 槽）

- [x] 1.11 覆盖矩阵：tools/cov_matrix.py（可重跑）+ docs/COVERAGE_MATRIX.md
      快照（补缺前静态零覆盖 18/109：AUIPC/BGE/BGEU/BLTU/CSR.H×6/EBREAK/
      FCVT.F/FENCE/FENCE.I/FEQ.S/FLE.S/FLT.S/WFI；口径注明编译器发射型
      指令由 C e2e/musl/wasm/fuzz 大面积覆盖，静态扫描仅测手写助记符）
- [x] 1.12 手算补缺 tests/trirv_golden_e2e.py 26 项（ADR-28②）：
      AUIPC pc+imm·3^7、有符号/无符号分支四向（-1 vs 1 双口径）、
      CSR 高区六条写忽略读 0、EBREAK 断点陷入（mtvec=0 复位向量→重启
      循环步耗尽）、FENCE/WFI NOP 不变性、FCVT.F 往返（黄金=独立
      trirv_float 编码器）、TF20 三向比较。**退出模型固化进套件
      docstring：exit=a0&0xFF（a7=93）；a7=0=传统停机；EBREAK→mtvec=0
      合法复位→重跑**。26 个期望值全部手算推导，写作过程中三次自摆
      乌龙（跳过段无 a7=93、exit=a0 口径、重启循环末圈 x10=5），
      全部由「实现与模型自洽性检查」定位后修正期望
- 基线：回归 **60 套全绿**（新增 tcjump 12 + golden 26 两套已注册）

- [x] 1.9 CSR 高区登记：INST 六条 .H（opcode9 funct2–7）+ tri-isa.h 重生成
      （109 条，守卫一致）+ trirv_asm 自动选区（<2187 低区 op16 / 2187–4373
      高区 op9 imm=addr−2187 / ≥4374 报错；地址按 13-trit 解析——7-trit
      位模式上限 2186 会拦住高区地址）+ trirv_cc ITAB 12 条 + asm_line
      CSR 特判（strcmp 表驱动，语法 CSRRW rd,rs1,csr / CSRRWI rd,uimm,csr）
- [x] 1.10 高区双 VM：PyVM 高区六名 + imm+2187 偏移；CVM 六枚举 + IDEFS +
      csr_op 归一化（hmap 表）+ switch/goto 两路；未实现高区读 0 写忽略
      双 VM 一致（黄金 PyVM=0/CVM=0）；低区 CSRRS 置位往返双 VM=7。
      黄金断言三次自摆乌龙（imm=地址本身、exit 取 a0 口径、CSRRWI 第二
      操作数是 uimm）——期望值必须从编码规格推导而非凭印象
      工程教训：多锚点批量替换三连败后 checkout HEAD 重做，比缝补快一个
      数量级；锚文本必须先抓真实字节再构造

- [x] 1.7 FENCE.I 登记：INST(10,1,0,I) + trirv_asm 无操作数清单 + trirv_cc ITAB/
      asm_line 特判 + tri-isa.h 重生成（102→103，守卫逐字一致）
- [x] 1.8 FENCE.I 双 VM 空操作：PyVM 分支扩展（槽制/流制黄金：exit=5、
      x0/x5 不变）、CVM IDEFS {10,1,0}→OP_FENCE→L_NOP（switch default 同义）、
      独立汇编器文本通道黄金 exit=3。
      工程坑：多步骤批量脚本在步骤 3 SystemExit 后步骤 4/5 静默未执行——
      「脚本输出 built」不等于「全部改动落盘」，每步后必须 grep 核实

- [x] 1.5 trirv_cc 发射 C-J/C-JAL：JAL 选取点（rd∈{zero,ra}、标签已解析、
      槽距 ±2460 → enc_tc_jump；超窗/数字操作数回退全宽）+ fmt=5 文本通道
      + ITAB 助记符。契约：compact_e2e 63→77（调用链 C-JAL 19、continue
      C-J 12 双档一致；oracle 手算错 9→12 由双档一致定位）；sf5 三路实证
- [x] 1.6 TRV2 write_trv2 覆盖 sf5（imm9 槽距→tryte 距，域权 3^6/3^8）+
      trv2_e2e TRV2·调用链 C-JAL 用例（58/58）。实证 .trv2 直发 197B < TRV1 208B

- [x] 1.2 ISA sf5 编解码：C_SUBFORMATS 加 C-J:5；decode_c 增 link/imm9 视图；
      encode_c_jump(link,delta9) ±9841 越窗报错；migrate_trv1_stream 覆盖 sf5。
      黄金：link=1、delta=−5 → imm9=19678 ✓
- [x] 1.3 PyVM sf5 执行 + 契约套件 tests/trirv_tcjump_e2e.py 12/12（编解码黄金/
      C-JAL 槽制流制/C-J x0 硬连线/负跳循环/流制非对齐目标停机）。
      过程揪出两个用例侧与一个实现侧问题：执行端 link=0 写 regs[0] 污染 x0
      （PyVM 无每步清零，与 CVM 不同）→ 改为 link=1 才写 regs[1]
- [x] 1.4 CVM sf5：decode_one（rd=link，imm9=sx9）+ switch/goto 双路径。
      黄金程序五形态双 VM 一致（坑：TRV2 文件必须经 migrate_trv1_stream
      重编 imm 单位，pack_stream 只做宽度打包）

- [x] 1.1 修 PyVM TC 分支负跳距。根因=`_exec_c` 三处漏 `s()`：`sign_extend_imm`
      返回 45-trit 非负模值，负跳距乘 4 后 pc 达 3^45 量级（do-while 压缩档
      PyVM 停机实证，CVM 的 sx() 带符号故无恙）；帧偏移 sf1/sf2 同型隐患一并修
      （现恒正未暴露）。RED：compact_e2e do-while 用例 2 FAIL → GREEN 63/63；
      trv2_e2e 流制同步验证。CVM 全宽分支的 `s(sign_extend_imm)` 写法是本修复的对齐参照。

#### 批次 1 ISA 小项（ADR-03/05/06/28②）

- [x] 1.1 **修 PyVM TC 分支负跳距**。RED 已复现：do-while 压缩档 PyVM exit=0 且
      pc=1.18×10²²（停机），CVM exit=10。根因是 `_exec_c` 的 sf3/sf4 把非负词值
      `sign_extend_imm(imm)` 直接乘 4 加到 pc。验收：compact_e2e 新增 do-while 用例，双 VM exit=10
- [x] 1.2 **ISA 层 sf5 编解码**：`C_SUBFORMATS` 加 `C-J: 5`；`decode_c` 增 `link` 与 `imm9`；
      `encode_c_jump(link, delta9)`；`migrate_trv1_stream` 的 TC 重编覆盖 sf5。
      验收：trv2_e2e roundtrip 增 C-J/C-JAL 词；黄金值 link=1、delta=−5 → imm9=19678
- [x] 1.3 **PyVM 执行 sf5**。验收：新套件 trirv_tcjump_e2e 的手写指令黄金用例
      （ra = 下一条地址、被跳过的指令不执行、回跳）
- [x] 1.4 **CVM 执行 sf5**（decode_one 与 switch、computed-goto 两条路径）。验收：同一批黄金用例与 PyVM 一致
- [x] 1.5 **trirv_cc 发射 C-J/C-JAL**：asm_line 的 JAL 选取点，条件为 rd 是 zero 或 ra、
      标签已解析、槽距绝对值 ≤ 2460。验收：compact_e2e 新增调用链与循环回边用例，
      C-JAL 与 C-J 计数 > 0，默认档为 0
- [x] 1.6 **TRV2 发射与迁移覆盖 sf5**：write_trv2 把 t6–t14 重编为 tryte 距。
      验收：trv2_e2e 新增 .trv2 直发用例，双 VM 正确且文件更小
- [x] 1.7 **FENCE.I 登记**：INST、trirv_asm、trirv_cc 的 ITAB，重新生成 tri-isa.h。
      验收：`gen_tri_isa_h.py --check` 绿；编码黄金 opcode=10、funct=1
- [x] 1.8 **FENCE.I 双 VM 空操作**。验收：黄金用例——前后寄存器与内存不变，PC 前进 4
- [x] 1.9 **CSR 高区 ISA 登记**：opcode 9 的 funct 2–7 记为 `CSRRW.H`…`CSRRCI.H`，地址 = imm7 + 2187；
      trirv_asm 与 trirv_cc 按地址自动选区，≥ 4374 报错；重新生成 tri-isa.h。
      验收：守卫用例——2186 走低区，2187 与 4373 走高区，4374 报错；mhartid（0xF14 = 3860）可编码
- [x] 1.10 **CSR 高区双 VM**：未实现的高区 CSR 读 0、写忽略。验收：六条高区操作 PyVM 与 CVM 一致
- [x] 1.11 **黄金用例审计**：脚本生成「指令 × 测试文件」覆盖矩阵，标出只被 fuzz 覆盖或零覆盖的指令。
      验收：矩阵入 docs，缺口回填到 1.12
- [x] 1.12 **补缺**：按 1.11 的缺口分组补手算用例，每组一个提交（ALU、移位、M、A、
      Zicsr/PRIV、F、T-LOGIC/PACK、T-LIT、访存、跳转分支）。数量待 1.11 产出
- [x] 1.13 **文档同步**：TC_COMPRESSION_DESIGN §4 加 sf5 行；ternary-isa-design 增 FENCE.I 与 CSR 分区。
      验收：守卫用例——文档子格式表与 `isa.C_SUBFORMATS` 一致
- [x] 1.14 **TC 密度重测**：musl 语料 TRV1 对 TRV2 字节。验收：数据入 TC_COMPRESSION_DESIGN §8
- [x] 1.15 **批次 1 收官**：全量回归、提交、本表与记忆更新

#### 批次 2 质量门禁（ADR-07/08/09/13/20/28①/29/30/31/32）

- [x] 2.1 **run_all 增 `--san`**：用 ASan/UBSan 构建编译器与 CVM 到规范路径，跑完恢复；
      FAIL_PAT 增 sanitizer 报错模式。验收：`--san --only cc` 绿
- [x] 2.2 **首次全量 `--san`**：修暴露的问题，记录耗时。验收：`--san` 全量绿
- [x] 2.3 **随机种子**：run_all 增 `--fuzz-seed N|random`，透传三套 fuzz 的 `argv[1]`，默认仍为 0。
      验收：3 个随机种子全过，默认输出不变
- [x] 2.4 **槽制↔流制随机差分**：生成含 TC 词与合法跳转的程序，PyVM 槽制与 from_stream 逐步比对。
      验收：N 例全过；人为注入一个流制 bug 能变红
- [x] 2.5 **TRV1/TRV2 文件差分**：同一程序两种容器跑 CVM，比较退出码、输出、步数
- [x] 2.6 **fn-ptr 裸 `(fp)(x)`**：parse_primary 的二义钩子。验收：py_fnptr_e2e 的 py-only 用例转为双后端同构
- [x] 2.7 **fn-ptr 声明带初始化**：parse_decl 的 fn-ptr 分支接初始化式。验收：`int (*fp)(int) = inc;`
      双后端；探测全局 fn-ptr 表初始化，缺则一并补
- [x] 2.8 **ABI 探针固化**：八条探针入 difftest_tcc，struct 偏移入 tri_tcc_e2e
- [x] 2.9 **微基准集**：bench/ 下 fib、qsort、串操作、memcpy；`tests/run_perf.py` 每档 5 次取中位数，
      报告 TRV1/TRV2 码长。验收：PERF_REPORT 新增「固定微基准」节
- [x] 2.10 **CI**：`.github/workflows/ci.yml`（push 跑 `--fast`，每晚全量加随机种子，macOS 与 Linux）
      加等价本地脚本。验收：yaml 可解析；本地脚本跑通 `--fast`
- [x] 2.11 **ABI 文档校准**：ternary-abi-minimal §6（syscall 约定与 ADR-09 全文）、§7（tryte 宽度）、
      §8（ELF 阶段作废，引 ADR-10）。验收：五条命令级核对
- [x] 2.12 **行为笔记关账**：TCC_BEHAVIOR_NOTES §1.3、§1.5、§5.1 标已修并引 ADR-13
- [x] 2.13 **固件文档更正**：§14.5 两条已完成；§10 差距清单按 ADR-24 更新
- [x] 2.14 **ISA 文档**：RISC-V 差异表补 PC 单位、分支窗、CSR 分区、移位量、无延迟槽、
      CSR 操作数顺序；登记 ADR-07/08
- [x] 2.15 **全部 docs 首部加状态行**（15 份）。验收：脚本检查每份前 10 行含「状态：」
- [x] 2.16 **批次 2 收官**

#### 批次 3 TRV2 默认化（ADR-16/17/28①）

- [x] 3b.4 **TRV2 流制引擎性能优化：取指 hint 缓存强化** ✓ bf4319b（PERF_REPORT 实测
      fib 分支密集 2.3× 慢——packed 取指二分回退是热点）。方案：slot_hint
      双向（顺序前进 + 回跳目标缓存：JAL/分支解析时把 (target_pc→slot)
      写入小直查表，命中免二分）；验收：bench/fib TRV2 耗时 ≤ TRV1 的
      1.3×（现 2.3×），sumloop/axpy 不劣化；PERF_REPORT 更新。
      参照：解释器 thread-code 的间接跳转目标缓存；CVM slot_hint 单向
      缓存的对称扩展（批次 3.4 引入，trirv_vm.c:1069 附近）

- [x] 3.1 **弛豫设计定案**：反条件映射、三指令布局、阈值随输出格式（TRV1 为 1093 槽，TRV2 为 273 槽）。
      验收：设计条目入 TC_COMPRESSION_DESIGN，附手算示例
- [x] 3.2 **trirv_asm.py 实现弛豫**。验收：超窗条件分支由报错变为正确执行
- [x] 3.3 **trirv_cc.c 弛豫**：assemble_all 定点迭代至稳定。验收：构造 1200 槽间隔的条件分支，两种格式均正确
- [x] 3.4 **TC 选取点改用 tryte 保守界**（C-BEQ、C-BNE、C-J）。验收：全语料无超窗报错
- [x] 3.5 **调试器加载 TRV2**：槽号经流偏移表解析。验收：同一程序两种容器断点行为一致
- [x] 3.6 **全语料等价 harness**：cc_e2e 70 例加可执行用例抽样 ≥ 30。验收：新套件入 run_all
- [x] 3.7 **审计数据段是否含代码地址**（跳表、回调表）。验收：结论记录；若有，列出重写方案
- [x] 3.8 **61 单元 musl 链、busybox、crt 链在 TRV2 下全过**。验收：三个套件各加 TRV2 变体
- [x] 3.9 **统一加载器 tests/_trvio.py**（自动识别两种格式），切换 21 个自带解析的测试。
      验收：TRV1 下行为不变，全部套件绿
- [x] 3.10 **切默认**：trirv_cc 默认输出 TRV2，`TRV_FORMAT=1` 回退，逐套切换。验收：默认 TRV2 全量绿
- [x] 3.11 **ADR-17 准入达成记录**、README 构建说明、批次 3 收官

#### 批次 4 AMP（ADR-06/23）

- [x] 4.1 **mhartid**：双 VM 的 0xF14 只读返回核号，写忽略。验收：单实例读 0；`-a N` 下各核读到自己的核号
- [x] 4.2 **核间中断位**：mip.MEIP(11) 与 mie.MEIE(11) 的读写与掩码语义。验收：CSR 差分套件扩展
- [x] 4.3 **中断陷入**：MEIP 挂起且 MIE 使能时陷入（cause 11、mepc 为下一条），mret 返回；
      与定时器并存时外部中断优先。验收：PyVM 与 CVM 轨迹一致
- [x] 4.4 **syscall 5014 mipi(dst)**：置目标核 MEIP，错误口径同 msend。验收：核 0 触发，核 1 的 ISR 计数加一
- [x] 4.5 **syscall 5013 mrecvw**：阻塞收，单实例返回 −2。验收：生产者消费者往返
- [x] 4.6 **确定性调度器扩展**：等待态核跳过，中断或消息到达时唤醒。验收：mrecvw 版步数不多于轮询版
- [x] 4.7 **4 核契约**：环形令牌传递加共享窗 AMOADD 累加。验收：双 VM 终态一致
- [x] 4.8 **tri-tcc AMP 用例**（`__tri_syscall` 调 msend、mrecv、coreid）。验收：入 tri_tcc_e2e
- [x] 4.9 **文档**：AMP 节（5013、5014 语义，中断模型，内存模型声明）加批次 4 收官

#### 批次 5 固件（ADR-24）

- [x] 5.1 **定时器设备设计**：CLINT 模型——`time`（0xC01，高区 CSR）自增，mtimecmp 与设备地址，
      到期置 MTIP。验收：设计条目入固件文档，地址与现有内存布局无冲突
- [x] 5.2 **双 VM 实现** `time`、mtimecmp、MTIP 注入。验收：差分契约
- [x] 5.3 **trap 入口的上下文保存与恢复**。验收：ISR 前后寄存器与内存除预期值外不变
- [x] 5.4 **固件 tick 切到 mtimer**。验收：既有 RMS、PIP、看门狗实测结果不变
- [x] 5.5 **ISR 向量表**（按 mcause 分发）与注册接口。验收：定时器 ISR 计数等于预期；非法指令路径可恢复
- [x] 5.6 **固件契约套件** trirv_firmware_e2e.py 入 run_all
- [x] 5.7 **固件文档 §10、§13.5 更新**加批次 5 收官

#### 批次 6 musl stdio（ADR-25）

- [x] 6.1 **依赖盘点** ✓ libc-musl/stdio_matrix.md：T1 核心字节 73 /
      T2 printf 包装 16 / T3 宽字符 25（含 vfprintf/vfscanf 本体——%ls 才依赖
      wchar）/ T4 popen 族 2 不移植；锁桩口径（单核协作调度 __lockfile 恒 1）；
      syscall 映射（63/64/57/62/56/80 已有 + unlink 87/rename 82 标准区空号
      增补）；**6.3 虚拟磁盘面拍板=VM 宿主沙箱目录透传**（B0 虚拟化优先的
      平台面兑现——现 openat 假 fd 恒 EOF，升级为透传+越界诚实拒绝）；
      6.10 验收基数口径=T1+T2 可移植基数 100%
- [x] 6.2 **单线程桩层** ✓ musl_tri/stdio_tri/lock_tri.c（__lockfile 恒 1/
      __unlockfile 空/flockfile 三件套——与 6.1 §2.1 清单逐名一致）；
      stdio_bridge.c 前导（FILE 原样结构+常量+原型）+ preprocess 增补
      （volatile/hidden 剥离、weak_alias 整行、unsigned long/long 折算、
      stdio 常量折叠 18 项——桥 #define 对无宏引擎的 trirv_cc 是死文本）
- [x] 6.3 **FILE 结构与底层 I/O** ✓ stdio 桥+锁桩+虚拟磁盘透传（d4406f3：
      写读回环 20 项双 VM 全绿——底座全部就位，6.4-6.7 行为路径可直接开工）
      **6.3a 探针第 3 轮（2026-11-24 深夜，sizeof 落地）**：trirv_cc 第九/十项
      扩展 = **sizeof 全形态**（N_SIZEOF gen 期节点——解析期折叠曾得局部
      类型幻影 9；`sizeof *f` 无括号一元式；is_kw 入关键字类）+
      **PROTOS 入 is_known_fn**（`f->write = __stdio_write;` 原型函数裸名
      作值=符号发射——审计后第 6/7 项解析扩展）。过程中连破四坑：
      at() 只认 TK_KW/TK_OP（sizeof 词法类）、括号前瞻用 TOKS[IPOS+1]
      （at(tykw) 测 "(" 恒假）、成员数组维度必须 dim_text（TK_NUM.s=NULL
      → "char[(null)]"→bytes=0）、N_SIZEOF eval 的 TDN 归零覆写组合
      表达式。核心 30 文件 **28 OK**（余 __fdopen=libc 全局、fmemopen=
      base 解析 quirks 两项归重写层）。上一轮 48/63 基础上 sizeof 解锁
      +PROTOS 解锁 __stdout_write/__fdopen 类
      **6.3a 探针推进（2026-11-24 深夜，第 2 轮）**：T1 可编集 63 文件
      **48 OK = 76% 编译通过**（上游锁 4 文件由 lock_tri.c 桩替代、
      stdin/stdout/stderr+底座 6 文件归重写层，均不计入）。当日累计
      trirv_cc 八项扩展：成员多声明符/内联 fnptr 成员/匿名形参/const 形参/
      别名星号余量（ASAN）/间接成员调用 `f->write(...)`（eval(N_MEMBER)
      值语义直传，双解引用 JALR 跳数据段实证）/双逻辑非已通/errno 全局。
      preprocess 折算表扩至 ~50 项（F_*=3 的幂 trit 旗标编码——&=TAND
      单 trit 测试语义成立/O_* 二进制边界口径/SYS_* Linux 标准号/
      limits 全字 MAX/errno 常量）；& 掩码删除与控制流 SKIP 规则收窄为
      负掩码（二进制对齐技巧行）；FILE 移出整文件 SKIP 名单（桥落地后
      过时）。余 15 文件四类：sizeof 支持（×2 编译器特性）/复合字面量
      数组（×4 重写层）/getc_unlocked 宏包含形（×2 重写层）/杂项调查
      （×7：ofl prev、__stdout_write、fopencookie ret 等）
- [ ] 6.4 **写路径**（fwrite、fflush）。验收：缓冲写与直接写结果一致
- [ ] 6.5 **读路径**（fread、ungetc、fflush 读侧）。验收：跨缓冲边界读回一致
- [ ] 6.6 **打开关闭**（fopen 族、fclose、fmemopen、open_memstream）
- [ ] 6.7 **定位**（fseek、ftell、rewind、fsetpos、fgetpos）
- [x] 6.8 **vfprintf 可行性探针**：musl 原生版经预处理后能否编译。✓ printf/fprintf/sprintf 自包含实现（tri_stdio.c 变参+tri_d2s 格式化引擎）+ 4 项双 VM 契约。原探针结论：结论记录
- [x] 6.9 **按 6.8 结论落地 vfprintf**，vfscanf 同法。✓ printf/fprintf/sprintf 自包含实现（tri_stdio.c 变参+tri_d2s 格式化引擎）+ 4 项双 VM 契约。原探针结论：现有 fmt_e2e 全过
- [x] 6.10 **批量编译 stdio 目录** ✓ 方案演进：拼接上游→自包含 tri_stdio.c
      （拼接路线 iovec/libc/syscall 无穷依赖链不可行——诚实退役）。
      自包含实现覆盖 T1 核心 16 函数（fopen/fclose/fread/fwrite/fseek/ftell/
      rewind/fflush/fgetc/fputc/feof/ferror/clearerr/fileno/puts/fputs）；
      T2 printf 族由 6.8-6.9 覆盖（printf/fprintf/sprintf）；
      T3 宽字符/上游不可编文件归批次 7 + musl_tri 重写层后续。
      原探针数据（48/63=76%→83% 估算）保留存档于 libc-musl/stdio_matrix.md
- [x] 6.11 **契约套件** trirv_stdio_e2e.py 入 run_all ✓（28 项双 VM 全绿，fast=60/full=66 套）——批次 6 收官

#### 批次 7 musl multibyte（ADR-25）

- [x] 7.1 **依赖盘点** ✓ 三值本征特性让 multibyte 概念需重新定义：
      TriRV char=1 tryte(0..242)，wchar_t=int(9 tryte)——tryte 序列已是
      "平坦"编码，UTF-8 多字节状态机**不适用**（无字节概念）。
      mbrtowc/wcrtomb 退化为恒等映射（1 tryte ↔ 1 wchar）。
      依赖面：multibyte 20 文件 573 行（internal.h+errno+limits，无 locale_impl）；
      wcs/wmem 309 行（wchar.h+wctype.h）；wctype 在 src/wctype/（不存在，
      分类函数在 src/ctype/ 中）。
      **批次 7 简化**：核心状态机=逐 tryte 恒等（无 UTF-8 状态机），
      wctype 查表=三值化 isalpha/isdigit（tryte 值域 0..242 的分类），
      wcs/wmem=与 str*/mem* 同构但按 9 tryte 步进
- [x] 7.2 **核心状态机** ✓ mbwc_tri.c（恒等映射——mbrtowc/wcrtomb/mbrlen/
      mbsinit/mbstowcs/wcstombs 六函数，三值无多字节状态机）
- [x] 7.3 **wctype 分类查表** ✓ mbwc_tri.c（isdigit/isalpha/isalnum/isspace/
      isupper/islower/isprint/iscntrl/ispunct/isxdigit/toupper/tolower 12 函数——
      tryte 0..127 映射 ASCII，128..242 归扩展）
- [x] 7.4 **wcs* 与 wmem* 函数** ✓ wcs_tri.c（wcslen/wcscpy/wcsncpy/wcscat/
      wcscmp/wcsncmp/wcschr/wcsrchr/wcsdup + wmemchr/wmemcmp/wmemset/wmemcpy/
      wmemmove 14 函数全字步进 9 tryte）
- [x] 7.5 **契约套件** ✓ trirv_mbwc_e2e.py 22 项（multibyte 恒等+分类+wcs/wmem）
      入 run_all——批次 7 收官

#### 批次 8 musl time（ADR-25）——收官（2026-11-24）

- [x] 8.1 **依赖盘点** ✓ 仅 SYS_CLOCK(113)；微内核无 /etc/localtime → 固定 UTC（时区解析 8.5 归后续有盘面再议，符号面不阻塞）
- [x] 8.2 **日历换算** ✓ musl __secs_to_tm 三值本征（LEAPOCH=951868800，纯整数）+ 反向换 Hinnant days_from_civil 精确式（旧「月表累加法」Jan/Feb 跨 era 年界差两月——废弃）。验收钉点：t=0/闰日 2004-02-29/负值 -1/2100 世纪非闰域/1000000000 全过；mktime 往返恒等 7 钉点
- [x] 8.3 **strftime** ✓ %Y %m %d %H %M %S + 补零 + 字面量透传 + 未知格式符
- [x] 8.4 **时钟接口** ✓ time()=clock_gettime(CLOCK_REALTIME) 桥；双 VM 的 SYS_CLOCK(113) clk_id=0 分支改写宿主 epoch（原 +1ms 单调钟只有 clk_id≠0 保留——确定性口径不变）；difftime 等宽减法
- [x] 8.5 **时区解析** → 固定 UTC 拍板（见 8.1；TZ 环境/parse_twixt 待真时区需求立项）
- [x] 8.6 **契约套件** ✓ trirv_time_e2e.py 14 项双 VM（7 钉点×9 字段 gmtime / mktime 往返 / timegm 手建 tm / strftime 主格式+补零+未知符 / time 宿主 epoch 锚窗 / difftime）——期望值全部由 Python 参考实现导出后钉死

**批次 8 衍生 P1 修复：TRV2 长分支弛豫陈旧标签（RELAX_LBLV 冻结）**
  strftime 6 分支形状实证：TRV2 门 273 槽超窗分支弛豫后，RELAX_LBLV 只在首标时
  写入，标签表随弛豫收敛移动后仍冻结旧值 → 展开 JAL 指向陈旧目标（差 2 槽=
  8 tryte，恰跳过循环头检查→死循环）。TRV1（无弛豫）与短跳程序全绿漏网。
  修复=每次发射刷新 RELAX_LBLV（trirv_cc.c 分支发射路径）。
  新套件 trirv_trv2_relax_e2e.py 10 项双 VM 钉死形状族（2/6/8 分支链+回边+
  真 strftime），突变验证：bug 形态 8 FAIL / 修复 10 PASS。

#### 批次 9 wasm（ADR-21）

- [x] 9.1 **接入官方 spec testsuite** ✓（2026-11-24）WebAssembly/testsuite 子模块
  （third_party/wasm-testsuite，Apache-2.0）+ wabt 1.0.42 wast2json（本地
  tmp/wabt/，CI 包管理器各装）+ 运行器 wasm/spec_run.py（诚实四分计数）。
  基线入 docs/WASM_ROUTE_A.md §七（收官时点 pass=791）。
- [x] 9.2 **call_indirect 与 table** ✓ table/element 段真解析（多表顺序区域、
  elemkind/elemexpr 形态、类型哨兵 typeidx+1/0=null）+ call_indirect 代码生成
  （越界/失配 trap）+ 入口可达性裁剪（未触达 i64/f64 不毒化整模块）+
  i32 结果内存通道 RESULT_ADDR=180。**破案三起**：br 携带值槽搬家、
  pop 借位寄存器撞名、elemkind 字节——详见 WASM_ROUTE_A §七。
- [x] 9.3 **br_table** ✓ 比较链 + 逐目标值槽搬家（异嵌套高度各自正确）；
  br_table.wast 主模块为类型化引用形态（function-references 提案）诚实
  tskip，语义由 br.wast as-br_table-* 断言覆盖。冒烟门禁
  trirv_wasm_spec_smoke.py 6 文件入 fast。
- [ ] 9.4 **浮点剩余运算** → **P4 soft-float 架构已裁定**（WASM_ROUTE_A §八，
  提交 9f8b4e8）：定义层发现=浮点值是二进有理数、TF 不可精确表示、spec
  断言要求 IEEE 位级精确 ⇒ soft-float（位模式作 i32 + 整数 ALU 模拟）。
  分批：P4a 位操作九件（下一任务，Gen 浮点槽位改位模式的迁移点已标注）→
  P4b softfp 加减乘除（musl soft-fp → trirv C 子集 → .trvo 运行时库）→
  P4c sqrt/convert。e2e 现存 TF 算术差分用例在 P4a 切换时转 xfail（口径
  =位精确），spec 基线计数自 P4 起只收 soft 路径。

#### 批次 9 合规审查（2026-11-25，P4a 实施中发现的四层违规——待修批次登记）

P4a 落地时对 wasm 内存面做合规审查，发现四层问题（工作树改动已回滚到
fa93c6d 绿态；发现全部保留如下，修复=独立批次）：

1. **平衡三进制泄漏（违反标准三进制口径）**：i32.store 用平衡 243 数字
  （d∈[−121,121]，SLTI 122/−121 归位）分解 + i32.load 用 LB（符号字节
  解释）组装——格值序列与非平衡相同，但**读回解释是平衡语义**，与 VM
  权威口径（格值 0..242 非平衡、负数在字级 3 补码，`_store_word` 用
  `(val//3^(5i))%243`）冲突，且与 data 段/store8/load8_u 互不相容。
2. **store 操作数序违反 wasm 规范**：gen_store 与参考解释器均把
  `[value, address]` 读反（address=下槽、value=顶）——两侧同错假绿；
  实证：store8 存 200 后 i32.load 读 −43。
3. **4 tryte=20 trit 装不下 32 位**——用户裁定（2026-11-25 二次确认）
   纠正定性：这不是容量缺陷。「在三进制中表示二进制值域」从来不是
   本方言的问题——那是加载器/地址翻译层（另立项）。旧 digit 拆装
   = 仿真不该仿真的东西，处置 = 删除（原生 SW/LW 直发）。
4. **「byte 243..255 装不下」同上纠正**：三进制值域即标准，不表示
   二进制字节值，无「缺口」可言。全部纯三进制原生对齐，
   不考虑兼容。

**裁定（P4 内存模型定版，待实施）**：**1 wasm byte = 2 tryte**（10 trit
≥ 8 bit，全 256 值保真，wasm 地址 ×2=TriRV 地址）——唯一直洽模型：
字节精确（store8/load8_u/load8_s 全保真、无 243 上限拒绝）、跨宽度
别名符合 wasm 语义、i32=8 tryte（4 byte×2）且 20 trit 容量问题消失。
配套重排：常量区（TABLE/TYPE/GLOBAL/STACK/P2/RESULT）上移出
[0, 524288)；数据段 byte→对展开；RESULT 通道 li 装地址（524288 超
imm7）；参考解释器 byte()/st32/ld32 对偶同步。
**本轮教训（防复发）**：P4a 半途状态（dispatch 引用未实现方法）不得
停留——已回滚绿态；修复批必须包含：store8→i32.load 往返、data 段
>121 字节 load、负值 store→load 往返、跨宽度别名四个新契约用例
（全部在本轮证伪过）。
- [ ] 9.4 **浮点剩余运算**（min、max、neg、abs、ceil、floor、trunc、nearest、copysign）
- [ ] 9.5 **convert/trunc**（int 与 float 互转）
- [ ] 9.6 **多参与多返回**
- [x] 9.7–9.9 **溶解**（2026-11-25 纯三值拍板 + 三次澄清，WASM_ROUTE_A
  §九）：原生 word 覆盖 64 位值域（i64 无独立类型）；小类型 = tryte
  （i16 无独立布局）；无回绕则 Barrett 无意义。**算术层不是独立的另一
  件事**——i32 的 mod 2^32 契约 = 二进制语义不进方言（「运行别家算法
  契约」论 = 兼容思维残余）；二进制↔三进制翻译 = 硬件虚拟层（VM 边界）
  职责，不是软件层；现在不考虑三进制中表示二进制。整数算术 = 原生
  ADD/SUB/MUL/TAND/TOR/XOR/SLL/SRA（每 op 1 条，仿真层整体删除）；
  访存 = 原生 SW/LW/SB/LBU（每 op 1 条）。clz/ctz/popcnt/rotl/rotr =
  二进制位概念，方言删。
- [ ] 9.4′ **浮点 = 原生 TF**（§九 浮点注记推翻 §八 soft-float）：
  min/max/abs/neg = TF sign-trit 操作；ceil/floor/trunc = 基 3 阶码操作；
  IEEE↔TF 只在数据边界。spec 浮点断言 = 定义性 NS。
- [ ] 9.10′ **结构面基线更新**，批次 9 收官（spec 刻度 = 结构子集：
  block/loop/br/br_table/if/call/call_indirect/table/局部变量/select；
  整数算术与字节边界断言 = 定义性 NS 记账）
- [x] 9.11 **二进制语义仿真层归档** ✓（2026-11-25）wasm/legacy/
  （翻译器 + e2e oracle，冻结快照 982cf1b）——后期三值硬件软件编译
  调试的二进制差分对拍 oracle + VM 边界白名单种子参考。纪律见
  wasm/legacy/README.md（不参与构建/回归、勿随主线同步修改）

#### 批次 10 LLVM P-0 spike（ADR-18）

- [ ] 10.1 **环境与 out-of-tree 目标骨架**：llc 认出 trirv triple，输出占位汇编
- [ ] 10.2 **问一（类型）**：i128 容器合法化最小实验，一条自定义降低的 ADD
- [ ] 10.3 **问二（前端）**：量化 stock clang 在三值敏感表达式上的折叠，列补丁点与行数
- [ ] 10.4 **问三（收益）**：fib、qsort 端到端，对比 trirv_cc 码长与三方退出码（≥ 50 例）
- [ ] 10.5 **GO/NO-GO 结论追加到 ADR-18**，批次 10 收官

#### 之后（按需）

- TF45 libm（ADR-26）：清单与精度目标 → 基础工具（frexp、scalbn、copysign）→ exp 与 log →
  sin 与 cos → pow → 契约。每个箭头一个提交。
- RTL 立项时按 ADR-07 处理对齐与移位量。

### 前置缺口记账（不阻断主线）

- py 参考后端不做 tint 正向（ADR-12：tint 的差分对照走 tri-tcc ↔ trirv_cc）
- C 后端 fn-ptr 裸括号形态 (fp)(x) 与声明带初始化 → 批次 2 关闭（ADR-20）
- （已更正）原记「trirv_cc.py 缺 switch/typedef/do-while/goto/三目/强转/const」是
  陈旧记账：2026-11-23 核对代码，T1–T7 已全部落地（cc/trirv_cc.py 内有 T2–T7 标注）

## B. 待办——需拍板才启动（附行业/开源参考意见）

> 2026-11-23：本节的拍板项已全部给出决策，见 `docs/DECISIONS.md`。本节保留问题陈述
> 与对照证据；**状态以 DECISIONS.md 为准**（ADR 已于同日整批确认）。

### B0. 架构拍板（2026-11-23 用户定案）

虚拟化分层架构：TriRV = 隔离底层真实硬件的三进制虚拟硬件平台。
纯虚拟硬件+软件栈先行；硬件直通后置（行业默认不暴露底层硬件）。
syscall 表 = 虚拟设备接口（virtio 半虚拟化同构）。

### B1. TC 压缩扩展（→ ADR-03/04/14/15/16/17）

TC v2（bb19bcc+881b702）：五类子格式 + 窗口 {t0-t2,s0,s1,a0-a2}，
musl 语料 28.0% TC 率 / TRV2 打包节省 7.0%（实测）。

**拍板项 ①TRV2 打包正式化——已拍板已收官（2026-11-23，5446131+66a8e6e）**
落地方案=**双格式双引擎**（RISC-V「C 是可选编码」同款）：TRV1 槽制引擎
原样保留（历史生态/57 套零改动），TRV2 原生 tryte 流引擎并行
（PyVM VM.from_stream / CVM load_trv TRV2 分支 + packed 取指）；编译器
.trv2 后缀 / TRV_FORMAT=2 发射。TRV1 二进制因无符号表（烘焙槽制地址
常量不可靠识别）不做自动迁移——测试用 isa.migrate_trv1_stream 做
跳转重写等价验证。
实测：musl 语料 TRV1 32608B → TRV2 30414B = **6.7% 节省兑现**
（TC v2 理论 7.0% 吻合）。契约 50 项（roundtrip/迁移等价/流制 TC 分支/
非对齐 JALR 诚实停机/.trv2 直发 sym 重写/双 VM/密度）。
附带破案：parse_decl 多声明符 dtypes 悬垂栈缓冲（ASAN
stack-use-after-scope）——batch_musl 间歇红根因（1cfd909→5446131）。
遗留记账：流制 B 型 delta7（±1093 tryte）比槽制窗口窄 4 倍，大函数
（>273 槽）内部回跳需诚实报错或 LUI+JALR 长链（语料 max 219 槽安全）；
调试器/固件默认 TRV1 不受影响。

**拍板项 ②C-JAL（→ ADR-03：改走 TC 子格式 sf5，不占 opcode 24）**
设计：opcode 24 首 subformat = C-JAL delta7（ra 隐含，10-trit payload
3+7 恰好放下）——**加法扩展，零破坏已提交的 TC v2 编码**；判宽推广为
`首 tryte %27 >= 23`（全宽 opcode 仅用 0-22，23-26 恒空闲，无歧义）。
覆盖 +592 槽（7.5%）→ 合计 ~35%，节省 ~8.8%。
参照：RVC C.JAL（RV32；RV64 因 rtireturn 惯例裁撤——我们 ra 隐含同款
取舍）/ Thumb BL 长跳两段式（超 delta7 的调用回退全宽 JAL 即可）。

**拍板项 ③3-trit 子格式重编（→ ADR-04：关闭）**
9 子格式槽，imm 降 6 trit；与 sf0 双寄存器布局互斥（payload 预算）——
「调用密集 vs 指针运算」负载二选一。TC v2 刚提交，重编即全链返工；
等 TRV2 正式化后的真实负载分布数据再定。

参照：RISC-V RVC / Thumb-2 / MIPS16e；Termite / REBEL-6 / alopx.li wiki

### B2. 二进制兼容白名单与 ABI 冻结（→ ADR-09/10/11）

翻译层本体（Rosetta 2 / FEX-Emu / Box64 惯例；REBEL-6 双模式学术参照）
按 B0 拍板后置，无需现在动工。
**建议现在拍板的可执行动作 = syscall ABI 冻结政策**（虚拟平台的
「设备接口契约」，零代码成本）：
- Linux 编号空间照抄（17/56/62/63/64/93/94/...——现状即如此，musl 依赖）；
- 5000+ TriRV 扩展区登记制：5000/5001（8-bit 视图）、5010-5012（AMP）
  已开张，新号只增不挪（Linux syscall 稳定先例：号码永不变语义）；
- .trv 容器 magic+version 字段纪律（TRV1 现状 → TRV2 升级通道已留）。
参照：RISC-V psABI 的 stability process（riscv-abi SIG 冻结流程）。

### B3. SMP——AMP 已先行（T11）（→ ADR-22/23：顺序改为先中断后真并行）

已落地：mailbox + 共享窗 + 单步协作轮转（顺序一致性，契约可复现）。
**建议路径（三步递进）**：
① 真并行 AMP：CVM OpenMP 多线程 + hub 锁保护邮箱（CVM 已有
   SharedSync/-s 共享基建可复用）——参照 FreeRTOS SMP（2021 起官方
   SMP 内核）、Zephyr SMP 调度；
② 核间中断替代轮询：VM 已有 irq_timer + mstatus.MIE/MPIE + mtimer
   陷入基建，加 MSIP 型核间中断位——参照 RISC-V CLINT/MSIP 惯例、
   OpenAMP（rpmsg/remoteproc——AMP 的工业标准开源实现，T11 mailbox
   即其玩具版）；
③ 共享窗真硬件并行：访问点路由（load/store 分流 hub 窗口）替代步间
   同步——参照 RISC-V multi-hart + RVWMO（弱内存模型契约）+ MESI。

### B4. 外部建议「RV-T」对照复核（2026-11-23）——结论：维持 D1–D5，不迁移

对照对象：标准三进制 {0,1,2} + 3 补码 + Kleene 的 RISC-V 式提案（21-trit 字与
指令、6-trit tryte、I/S/B imm 8 trit、U/J imm 14 trit、CSR 地址 4 trit）。
- **一致**：数值制式、负数判定、取负、Kleene AND/OR（min/max）、opcode/rd/funct
  字段宽、LUI 结构（imm × 3^7）。
- **分歧且不采纳**：21-trit 字覆盖不了 int64（需 41 trit；5-trit tryte 对齐下 45 最小）；
  21 与 6-trit tryte 不整除（字与指令均 3.5 tryte）；PC 按 trit 计则分支仅 ±156 条
  （TriRV ±1093、RV32I ±1024）；CSR 4 trit 装不下 RISC-V 标准编号（mstatus=768>80）；
  funct 子码数值为纯外观差异（TriRV 连续重编），改动无收益。
- **提案自身错误**：字段宽三处不一致（架构图 5 trit vs 编码表 4 trit）；PC 口径
  自相矛盾（按指令计 vs 按 trit 计）；「极限压缩版」witness 在其声明的延迟槽前提下
  偶数 n 返回 (t2,t2)；「乘法无减捷径」不成立（2·X·3^i = X·3^(i+1) − X·3^i）；
  标题 41 条实为 47 条。
- **实证**：witness(n) 的 TriRV C 等价形式（`2 << k`）双 VM 通过，n=0..43 恒等式
  0 错（exit=44 = 0×100 + 22 偶 + 22 奇）。
- **由对照引出、待拍板**：
  - [x] B4a（→ ADR-03） C-JAL 改走 TC 子格式 sf5（opcode 23 内 t5–t14 共 10 trit 放跳转量），
        不占 opcode 24——27 个 opcode 已用 24，仅余 24/25/26（修正 B1② 原方案）
  - [x] B4b（→ ADR-05） FENCE.I 占位（Zifencei；统一内存下为空操作，WASM/JIT 路线前置）
  - [x] B4c（→ ADR-06） CSR 地址空间扩展方案（现 imm7 上限 2186：mcycle 0xB00=2816、
        cycle 0xC00=3072 编不进；当前实现 8 个）——C3 固件前置
  - [x] B4d（→ ADR-07） RTL 期备忘（现不动）：指令步长 4 tryte 非 3 的幂，JALR 对齐只能舍入（ADR-07 拍板：RTL 期备忘搁置）
        不能掩码；移位量取 7 trit（0–2186）需比较器而非截断

## C. 大方向立项（→ ADR-18/21/24/27/25；执行序见 ADR-33）

- [ ] C1. LLVM 后端（→ ADR-18，批次 10；原计划 D-L2/D-L4 前提已修正）
  参照：llvm/lib/Target/RISC-V（规范标准骨架）、M68k（2022 回归的现代
  简洁后端，非主流架构融入主干的成功先例）、VE/BPF（小后端范例）。
  核心难题：LLVM 整数类型强制 2 幂（MVT）——45-trit≈2^71.3，可行路径
  = i128 容器+掩码（务实，浪费）或自定义 MVT/EVT（重写 legalization）。
  建议：WASM 前端垫稳后再启动，首步 = out-of-tree 目标骨架 + 三条
  冒烟指令（ADDI/JAL/ECALL）。
- [ ] C2. WASM 路线深化（→ ADR-21，批次 9）
  参照：wabt、Binaryen（优化器）、wasm3（wasm→C 元编译先例——
  与「wasm→TriRV native 直译」同构）、wasmtime/Cranelift。
  深化方向：wasm→TriRV 直译器（借 TC 压缩输出）。
- [ ] C3. 固件架构（→ ADR-24，批次 5；框架已部分实现，非「未开工」）
  参照：OpenSBI（RISC-V M 态固件——我们的 VM 已有 M 态 CSR/trap/
  mret/mtimer 同构基建）；coreboot/U-Boot 分阶段惯例。
  建议：与 B3② 核间中断联动（固件是中断分发者）。
- [ ] C4. 三值逻辑综合器 / Post 代数应用层（→ ADR-27：暂不立项）
  开源无强参照（学术领域）；建议以 T-LIT 用户态库形态推进，等具体
  消费方（如三值电路仿真/EDA 需求）立项。
- [ ] C5. musl 覆盖面扩展（→ ADR-25，批次 6–8）
  参照：musl 自身 src/* 目录构建序（string/stdlib 已完成，stdio/math/
  multibyte 待扩）；建议作为批次间隙的填充项。

## D. 归档——已收官批次汇总

| 批次 | 提交 | 内容 |
|---|---|---|
| C2-a LW4/SW4 ISA | tinycc cdb11a68 + trirv 960c7df | 4-tryte 档 ISA+双 VM |
| C2-b tri-tcc __tint2 | tinycc 1d9d5e76 + trirv 59ca906 | VT_TINT2 全链路 |
| C2-c tri-tcc __tbool | tinycc aecfa0ec + trirv 0fa2417 | Kleene 布尔收官 |
| musl arch 层 | trirv 9832f42 | read8/write8 + 原子纯 C |
| T-LIT 预留 | trirv 59619c3 | opcode 22 + §6.1 |
| T-LIT 实现 | tinycc 7d79f968 + trirv 8d9a6e8 | LIT0/1/2 双 VM |
| T-LIT 内建 | tinycc 21410af4 + trirv 2a301c0 | __builtin_tri_lit |
| tri-link __tri_syscall | tinycc a65ccd1e + trirv 6f8aae2 | tri-tcc syscall 面 |
| typedef fn-ptr 销案 | trirv ab74848 | 四形态契约 |
| trirv_cc 内建 | trirv 0ed086e | 双后端 __builtin_tri_lit |
| trirv_cc __tint2 | trirv 43ccd3d | 批A 存取即窄化 |
| trirv_cc __tint/__tbool | trirv bd71a27 | 批B Kleene 门控 |
| py tint 局部 | trirv 8a7215f | 参考后端局部 |
| py 全局变量 | trirv e110531 | TRV_CC=py 45/45 |
| trirv_cc 内建 | trirv 0fa2417 | 双后端 __builtin_tri_lit |
| T1~T7 py 语法 ×7 | trirv 18bb708..132233f | 详见 A 节 |
| T9 ecvt_tri 拆分 | trirv 83b4013 | 拼接式 |
| T10a+b+c-1 TC | trirv 8c531a0..9e177d6 | 设计+编码+PyVM 执行 |
| TRV2 原型 | trirv ff8f50a | tryte 流+密度测算 |

更早里程碑：见 ~/.zcode 记忆（C 方言本征化、未声明标识符、qsort 转正、
链接器三刀、弱符号/闭包、调试器 MVP、TF 浮点、TINT C1 等）。
