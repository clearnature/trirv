# ADR-38 musl 全量移植与 liteOS 用户态基座（2026-11-24）

状态 ✅（战略定案 + 里程碑定义；执行分批见 TASKS 批次 6–8 与 3b 后）

## 1. 战略定位

**TriRV-FW liteOS（微内核验证）与 musl 是两层关系：**

- **内核侧（TriRV-FW liteOS）不用 musl**：微内核验证（RMS 调度/IPC/内存管理/
  看门狗）用 C 子集 + 既有固件框架自足（trirv_firmware.c 已实测可行）。
  内核不需要"版本移植兼容性灾难"的解药——灾难在用户态生态。
- **用户态 musl 是 OS 的全部 libc 依赖**：跑在 liteOS 上的用户程序以**静态
  musl** 链接。选 musl 的理由与上游哲学同构：静态优先、源码级最小依赖、
  无发行版补丁地狱——glibc 的兼容性问题在 musl 上不存在（一份源码 +
  syscall ABI 即世界）。
- **musl 的两层兼容性在 TriRV 的落点**：
  1. syscall ABI 层 → **已照抄 Linux 编号**（ADR-09 冻结），musl 零改动；
  2. 内核行为层（进程/内存/信号）→ liteOS 微内核自实现，musl 对非 Linux
     内核的可移植性是它的强项（seL4/NuttX 先例）——实现 shim 即可。

## 2. "全部编译"的距离（2026-11-24 全量预测，不含已完成 string+stdlib 49 文件）

现有预处理规则下 13 目录 540 文件：**直通 212（39%）/ SKIP 328（61%）**。

| 目录 | 直通 | SKIP | SKIP 主因 | 解法 |
|---|---|---|---|---|
| math | 6 | **226** | "no FP on TriRV" **过时规则**（TF45/F 扩展/FCVT 已全链落地） | 解除规则，改 TF45 路径（ADR-26 TF45 libm） |
| stdio | 30 | **86** | 依赖 __stdio_read/write 内部结构层 | musl_tri 桩层（批次 6：锁桩+底层 I/O） |
| multibyte/time/legacy/locale/env/search/fenv/prng/malloc | 86+20 | 10 | 散点 | 批次 7–8 顺带 |
| ctype | 35 | 2 | **编译过但 16 个算错**（\|32 位掩码惯用法） | musl_tri 查表化重写（三值 \| 语义下 \|32 不成立） |

（已完成参照：string+stdlib 49 文件 0 命中、65/66 编译、差分 0 错——上游源
在该子集上**无**二进制敏感惯用法，基线干净。）

## 3. 障碍分类学（决定工作性质）

- **A 规则过时**：SKIP 规则引用已实现的特性（math/FP）→ 改规则即快赢。
- **B 桩层缺失**：上游依赖的内部结构（stdio internals）→ musl_tri 建桩
  （结构性工作，批次 6 主体）。
- **C 语义重写**：二进制位惯用法在三值下静默错 → musl_tri 查表化重写
  （ctype 系；**编译过 ≠ 算对**，验收必须双指标：编译率+差分正确率）。
- **D 结构缺口**：信号/clone/vDSO/动态加载 ldso → TriRV 进程接口扩展
  （ADR-23 延伸的虚拟 syscall 面）或**明确不支持清单**（微内核初期全
  静态，ldso 不需要——与 musl 静态哲学一致）。

## 4. 里程碑（编译率 × 差分正确率双指标）

| 里程碑 | 内容 | 对应批次 |
|---|---|---|
| M0（已达） | string+stdlib 49 文件 0 命中、65/66 编译 | 已完成 |
| M1 | math SKIP 规则解除（TF45 路径直通 226）+ 差分抽检 | 批次 6 前置快赢 |
| M2 | stdio 桩层+写读回环（musl_tri 桩 ≥ 90% 编译率） | 批次 6 |
| M3 | ctype/wctype 查表重写（正确率 100%——零静默错） | 批次 7 |
| M4 | multibyte/time 覆盖 | 批次 7–8 |
| M5 | TF45 libm（musl math 算法移植） | 批次 8 后（ADR-26） |
| M6 | 进程/线程 syscall 扩展（clone/spawn/mmap/信号 shim） | ADR-23 延伸 |
| M7 | 动态加载 ldso | 远期（微内核初期全静态） |

## 5. 与现有决策的关系

- ADR-09（syscall 冻结）：M6 的新号走 5000 族扩展登记。
- ADR-26（TF45 libm）：M5 展开为独立批次。
- ADR-25（musl 目录序）：本记录的里程碑取代其"开放式扩展"表述；
  linux/network/process 目录**维持不支持**（微内核无 POSIX 进程模型——
  musl 该部分源码不编译，不是移植失败）。
- 批次 6–8 目标口径升级：从"覆盖扩展"改为"M2/M3/M4 里程碑"。
