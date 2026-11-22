# 工作交接：ecvt 调用面批次（2026-11-22 会话收尾）

> 交接原因：会话上下文泄漏污染，用户重开会话。本文档 + 持久记忆
> （memory/trirv-ternary-project.md，已同步更新）为恢复工作的完整依据。
> **重启第一件事：`python3 tests/run_all_tests.py`（trirv 根）→ 提交两仓未提交改动。**

## 一、当前状态总览

| 项 | 状态 |
|---|---|
| __tint 批次 C1 | ✅ 已提交（tinycc f296b758 + trirv de536f4），TINT 组 15 用例 GREEN，契约 273 PASS/0 FAIL/141 用例 |
| 调试器 MVP | ✅ 已提交（trirv f941adb 批内），debugger/trirv_dbg.py，契约 18 PASS，已注册进 50 套回归 |
| 目录重组收尾 | ✅ 已提交（trirv f941adb），链接层入库、垃圾清理、MISS 清零 |
| **ecvt 调用面** | 🟡 **代码已修通、验证全绿、未提交**（见下） |
| 50 套全量回归 | ⏳ 修复后未跑（被打断；修复前基线 49 PASS/0 FAIL/XFAIL 1/MISS 0） |
| 链接器独立化 | 📋 登记专项，不阻断 |

## 二、ecvt 批次：改了什么、为什么

### 1. tinycc/tccgen.c —— LLONG→INT 窄化 45-trit 毁值（核心 bug，未提交）

**现象**：`int prod = d1 * 10000000000000; prod % 242;` 产出全错（bis13 探针
out[1]=垃圾 ≠ 184）；`int p = 30000; p % 242;` 正确。ecvt 的走位提取链全断。

**根因**：字面量 `10000000000000 > 2^31` → C 标准 long long 提升（`d1*1e13`
是 LLONG 表达式）→ 赋值给 `int prod` 触发 `gen_cast_s` 的 LLONG→INT 窄化 →
上游 32 位域算法生成 `<<32 → SAR32 → SHR0` 序列 → TriRV 45-trit 域里
`×2^32` **回绕毁值**（3e13×2^32=1.29e23 ≫ 3^45）。

**修复**（gen_cast_s 窄化段，`bits = (ss - ds) * 8;` 之前）：
```c
#if defined TCC_TARGET_TRIRV
        if (dbt_bt == VT_INT && ss == 8) {
            /* TriRV：int 与 long long 同宽（9 tryte = 45 trit）——LLONG→INT
               窄化无操作。坑实录：上游 32 位域的 <<(ss-ds)*8 / SAR / SHR
               序列在此域 = ×2^32 后回绕毁值（字面量 >2^31 触发 LLONG 提升
               即踩：d1*10000000000000 → int prod → prod%242 全错）。char/
               short 的 mod 窄化已被上方 gen_cvt_tri_narrow 分支截走。 */
            goto done;
        }
#endif
```
TRIRV 的 int 与 long long 同宽 9 tryte，窄化=无操作（符合"char mod-243 全轨"
同款拍板精神：C 语义跟容器走）。char/short 窄化已有 gen_cvt_tri_narrow 分支，
勿动。

### 2. libc-musl/musl_tri/ecvt_tri.c（trirv 仓，未提交）

- 入口签名：`ecvt/fcvt/gcvt(int raw,...)` → **`double value` + punning**
  `raw = *(int *)&value`。原因：musl 正语义 double 形参经 ABI 走 freg，
  旧 int 形参读 ireg 得垃圾（探针 ecvt(3.5,2) exit=208 ≠ 35 实锤）。
  tri-tcc 的 punning（freg→栈→ireg）已实证一致。
- 常量运行时化：`SD_E_P44 = P34×P10`、`SD_E_P2M44 = P44+P44`，
  `ec_big_init()` 幂等惰性（ecvt/fcvt/gcvt 入口各调）。

### 3. libc-musl/musl_tri/strtod_tri.c（trirv 仓，未提交）

同款 >uint64 字面量截断修复：SD_P44/SD_INF/SD_NINF/SD_NAN 4 处运行时合成
（`sd_big_init()` 在 strtod 入口调）。**为什么必须**：tri-tcc 前端 CValue.i
是 uint64，装不下 9.85e20 → 截断仅 warning；`==比较`两处同截断巧合自洽、
`rest >= SD_P44` 这类数学界则错位。

### 4. cc/trirv_cc.c（trirv 仓，未提交）

collect_decls 补 `case N_CAST: collect_decls(n->e); break;`——
`*(int*)&x` 的取址藏在 cast 里漏预标 spilled → trirv_cc 报
"UNSUPPORTED: address of register-allocated local"（int 形参取址也踩，
与 double 无关）。修复后 float_libc 契约从 2512 FAIL 恢复 **4928 PASS**。

### 5. debugger/trirv_dbg.py（trirv 仓，未提交）

fa0-fa7 显示修正：`freg[0..7]`（tri-gen `freg(r)=r-8`：tcc 浮点槽 r8-15
直落 freg 0-7），此前误读 freg[10..]（RISC-V fa0=f10 惯例不适用于本目标）。

## 三、验证状态（修复后已跑，全绿）

- bis13 探针 out[1] = **184** ✓（`/tmp/bis13.c`）
- ecvt_green 探针（tri-tcc 链路 ecvt(3.5,2)，按契约 9 tryte/字读）= **35** ✓（`/tmp/ecvt_green.c`）
- 浮点 libc 契约 **4928 PASS / 0 FAIL** ✓
- tri-tcc 契约 **273 PASS / 0 FAIL / 141 用例**（TINT 组仍绿，窄化修复无回归）✓
- **50 套全量回归：未跑** ← 重启第一件事
- riscv64 冒烟：建议提交前补跑（窄化修复有 TRIRV 守卫，上游目标不受影响）

## 四、重启操作清单（按序）

```bash
cd ~/ai-lab/trirv
python3 tests/run_all_tests.py          # 期望 50 套 PASS 49 / FAIL 0 / XFAIL 1 / MISS 0
cd tinycc && make riscv64-tcc && ./riscv64-tcc -c -o /tmp/rv.o /tmp/任意.c   # 上游冒烟
git add -A && git commit -m "fix(trirv): gen_cast_s LLONG→INT 窄化 45-trit 毁值——同宽无操作守卫（TRIRV）
  字面量 >2^31 触发 LLONG 提升即踩：<<32/SAR32 序列在 45-trit 域 = ×2^32 回绕。
  TriRV int/long long 同宽 9 tryte，窄化无操作；char/short 走 gen_cvt_tri_narrow 不变。"
cd ~/ai-lab/trirv
git add -A && git commit -m "feat(trirv): ecvt 调用面修复——double+punning 入口 + >uint64 常量运行时化 + trirv_cc N_CAST 预标
  ecvt/fcvt/gcvt 正语义 double 形参（freg）+ *(int*)&value punning 读 raw；
  SD_E_P44/SD_P44 级 >uint64 字面量运行时化（截断坑：==巧合自洽、数学界错位）；
  collect_decls 补 N_CAST（取址藏 cast 漏预标 → UNSUPPORTED，契约 2512 FAIL→4928 PASS）；
  调试器 fa0-fa7 显示修正（freg[0..7]）。"
# 然后更新 memory/trirv-ternary-project.md 的「未提交」段为「已提交 + 提交号」
```

## 五、本轮新坑记档（防复发，已同步进持久记忆）

1. **LLONG→INT 窄化坑**（本文档 §2.1）——凡字面量 >2^31 的表达式赋给 int 都踩。
2. **>uint64 字面量截断坑**：截断仅 warning；`==`两处同截断巧合自洽、数学界错位。
   修法=musl_tri 常量运行时化（小因子编译期 + 45-trit MUL 运行时合成）。
3. **ecvt 契约口径**：char* 视图读 int 数组必须按 9 tryte/字（契约 rd_word 同款）；
   按连续 char 读会把 buf[0] 的 tryte1 当 buf[1]。
4. **trirv_cc 的 double**：形参 double 可编译（全 ireg raw ABI）；局部 double 不支持。
5. **VM 语义备查**：JAL/BRANCH 落点 = old_pc + 4*imm（非 RISC-V 的 +2 槽）；
   imm 三补码按字段宽（OP-IMM/I/S 7-trit：2142=-45、2160=-27、2186=-1；
   B = hi(t17-19)*3^4+lo(t3-6)；J/U 13-trit）；手动单步 imem 索引 = pc//4。

## 六、排障方法论（本轮实证有效）

1. 先写 python 逐行模拟 oracle，期望值先跑 python 再下结论（本轮三次被手算期望值坑）。
2. PyVM 与 CVM 同错 = 编译产物错（先排除 VM）。
3. bisect 逐段对拍：E/M 拆解 → M 合成 → 走位 → 归一 → 提取；
   内联版对 + 函数版对 + 仅完整链错 → 定位在类型转换层。
4. fprintf 三件套：gen_op 入口打 op/类型/返回地址、调用点行号标记、
   移位段打 op/k——定位到 gen_cast_s 窄化段仅三轮。
5. git worktree 在旧提交上重建对照（pre-TINT 同错 → 排除新 patch 引入；
   注意 worktree 需手拷 config.mak + config.h）。

## 七、修复后的资产形态（ecvt 链路全景）

```
C 调用方（musl 正语义）
  ecvt(3.5, 2, &dp, &sg)          ← double 实参 → freg fa0
      │ tri-tcc: FLW 常量池 → fa0 ✓
      ▼
ecvt(double value,...)            ← FSW value→栈形参槽
  raw = *(int*)&value             ← punning LW：freg→栈→ireg（raw 一致 ✓）
  ec_big_init()                   ← P44 = P34×P10（45-trit MUL 精确）
      ▼
ec_core(raw, n, decpt, sign)      ← 6 字大数走位（原逻辑，契约已证对）
      ▼
"35" + decpt=1 + sign=0           ← buf 按 9 tryte/字读（契约 rd_word 同款）
```

trirv_cc 链路（契约）：调用方 int raw → ireg a0（trirv_cc 全 ireg raw ABI，
double 形参同槽）→ punning 读回同值——二进制 ABI 一致，契约 4928 全绿。
