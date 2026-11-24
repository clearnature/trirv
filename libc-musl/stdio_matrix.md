# musl stdio 目录依赖矩阵（批次 6.1，ADR-25/38 M2 前置盘点）

2026-11-24。对象 = `third_party/musl/src/stdio/` 全 118 个 .c（上游 vendor 只读）。
本矩阵是 6.2（桩层）与 6.3（FILE 底座）的验收基线。

## 1. 分层总览

| 层 | 文件数 | 内容 | 批次落点 |
|---|---|---|---|
| T1 核心字节 stdio | **73** | fopen 族/fread/fwrite/缓冲/定位/fmemopen/open_memstream/锁桩 | **批次 6 本体** |
| T2 printf/scanf 包装 | **16** | printf/scanf/snprintf/sprintf/sscanf/asprintf/dprintf/v-包装 | 批次 6.8–6.9（薄壳，全部委托 vfprintf/vfscanf） |
| T3 宽字符/多字节 stdio | **25** | fgetwc 族/fputws/fwprintf/swscanf/open_wmemstream + **vfprintf.c/vfscanf.c 本体**（因 %ls 依赖 wchar.h） | 批次 7（多字节 M4 前置）；vfprintf/vfscanf 的宽格式段按 6.8 探针裁决 |
| T4 进程族 | **2** | popen.c / pclose.c（fork/exec/wait） | **不移植**（ADR-38：微内核无 POSIX 进程模型） |

T1 清单（73）：`__fclose_ca __fdopen __fmodeflags __fopen_rb_ca __lockfile __overflow
__stdio_close __stdio_exit __stdio_read __stdio_seek __stdio_write __stdout_write
__toread __towrite __uflow clearerr ext ext2 fclose feof ferror fflush fgetc fgetln
fgetpos fgets fileno flockfile fmemopen fopen fopencookie fputc fputs fread freopen
fseek fsetpos ftell ftrylockfile funlockfile fwrite getc getc_unlocked getchar
getchar_unlocked getdelim getline gets getw ofl ofl_add open_memstream perror putc
putc_unlocked putchar putchar_unlocked puts putw remove rename rewind setbuf
setbuffer setlinebuf setvbuf stderr stdin stdout tempnam tmpfile tmpnam ungetc`

## 2. 依赖类 → 处置

### 2.1 pthread/锁（5 文件 + stdio_impl.h 锁宏）
`__lockfile.c flockfile.c ftrylockfile.c funlockfile.c freopen.c`（freopen 仅调用方）。
- **桩层口径（6.2）**：TriRV VM 单核协作调度（AMP 单步轮转，无真并行）→
  `__lockfile` 恒返回 1（持有成功、无争用）；`flockfile/funlockfile` 空实现；
  `ftrylockfile` 恒 1。stdio_impl.h 的 `LOCK_FILE/UNLOCK_FILE` 宏随之退化为
  顺序代码。真并行（B3 SMP）立项时按 hub 步进锁回填，宏面不变。
- 验收对账：桩清单 = 本节清单，6.2 落地后逐一对名。

### 2.2 syscall 直呼（15 文件）
`__stdio_read __stdio_write __stdio_seek __stdio_close __stdout_write __fdopen
__fopen_rb_ca fopen freopen tmpfile rename remove tempnam tmpnam(pclose/popen 见 T4)`。
- 映射到 TriRV syscall 面（ADR-09 冻结号）：read 63 / write 64 / close 57 /
  lseek 62 / openat 56 / fstat 80 —— 全部已实现（VM 侧）。
- **虚拟磁盘面（6.3 设计决定，按 B0 虚拟化优先）**：musl stdio 的文件语义是
  平台真实能力；VM 平台应有虚拟磁盘。落地 = VM 提供**宿主沙箱目录透传**
  （VM 工作目录为根，openat/read/write/lseek/close 直通宿主同名文件，
  越界路径诚实拒绝）。当前 VM 的 openat 发假 fd、read 假 fd 恒 EOF——
  6.3 升级为透传实现（一次性小改，双 VM 同构），契约=写文件→关→重开→读回一致。
- `remove/rename` → unlink 87 / rename 82（Linux 标准号，VM 透传同沙箱约束；
  ADR-09 只增不改：两号为标准区空号，不侵占 5000 族）。
- `tempnam/tmpnam`：宿主 TMPDIR 语义 → 沙箱 tmp/ 下（getcwd 已有）。

### 2.3 internal 头（stdio_impl.h / libc.h / locale_impl.h）
- stdio_impl.h：FILE 结构（read/write 指针、缓冲、锁、cookie）—— musl 原样
  编译的锚点，**不改结构**（凡改结构即违背「上游 vendor 只读 + musl_tri 重写层」
  分工——本批次策略：stdio 优先原生直编，编译不过的文件才进 musl_tri 重写层）。
- libc.h：仅 `LFS64(x)` 弱别名宏 —— 预处理规则剥别名（参照 preprocess_musl 现行面）。
- locale_impl.h：仅 T3 文件引用 → 批次 7。

### 2.4 宽字符/多字节（T3 25 文件）
依赖 mbstate 状态机（wctomb/mbtowc）与 locale 表 → 批次 7（M4）前置。
**vfprintf/vfscanf 特别注**：字节格式段不依赖宽字符（wchar.h 只为 %ls/%lscan
引入）——6.8 探针若判「宽段可剥离」，T2 全族即随 6.9 在批次 6 落地；
否则 T2 顺延批次 7。探针结论记录于 6.8。

### 2.5 浮点格式化（vfprintf 内联）
vfprintf.c `#include <math.h>` + 内部 long-double 位数组（musl 的
`__floatsend`/整数分解路径）→ 三值浮点需映射 TF45（ADR-26 M5）。探针预期：
**字节版浮点转换可先走 fmt_e2e 既有 TF45 格式化实现**（trirv_libc 已有
契约 15 项），musl 原生浮点段（long double 位数组）依 6.8 探针结论裁决
——原生编译不过则该段走 musl_tri 重写（TF45 直连），不阻塞 T1/T2 主线。

## 3. 批次 6 执行序（依本矩阵定档）

1. 6.2 桩层（2.1 清单，逐名对账）→ 2. **6.3 FILE 底座 + VM 虚拟磁盘透传**（2.2）
   → 6.4 写路径 → 6.5 读路径 → 6.6 打开关闭（fmemopen/open_memstream 在 T1，
   无宽依赖）→ 6.7 定位 → 6.8 vfprintf 探针 → 6.9 按探针落地 → 6.10 批量编译
   （目标 ≥90% = T1 73 + T2 16 ≈ 75%原生直编 + T3 宽族顺延 7 的口径换算：
   **T1+T2 全过 = 89/116 = 78%；加 T4 2 文件不支持 = 按 116 可移植基数算
   78%，T3 25 文件回批次 7 后可达 100%（T4 除外）**——6.10 的 ≥90% 验收
   基数按「T1+T2 应编尽编」执行，比例目标记 100%/可移植基数）。
2. 批量编译走 `preprocess_musl.py` 现行管线 + trirv_cc（TRV2 默认）×双 VM。

## 4. 风险登记

- stdio_impl.h 的 `struct _IO_FILE` 含函数指针与 union 布局 —— trirv_cc 的
  struct 能力（按值/嵌套/成员指针）已在 link/struct 契约覆盖，FILE 跨 TU
  字段访问是第一批真实压力面；失败文件降级 musl_tri 重写层，逐个入册。
- `gets.c`（usleep 级危险 API）照编（musl 原样），契约不调用。
- `fopencookie/ext/ext2`：cookie IO 面罕见但纯函数指针 —— 纳入 6.10 批量编译。
