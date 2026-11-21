# T-1 收尾 TDD 执行计划（2026-11-21，主智能体亲自执行）

> 依据：`TCC_BEHAVIOR_NOTES.md`（四视角精读 + 独立核实，每条带 path:line）。
> 纪律：每批先写 RED 用例跑出失败（实证缺陷存在，不采信文档结论），再修复转 GREEN，
> 全程以 `python3 trirv_tri_tcc_e2e.py`（双 VM）为门控。

## B1 一行级错码速修（D1-D5、D8 + D7 门控暂缓）

| 缺陷 | 位置 | 修复 | RED 用例（期望值 = C 语义） |
|---|---|---|---|
| D1 实参倒装 | tri-gen.c:545-549 | 对齐 riscv64-gen.c:716-723 配对 | sub(10,4)→6；id(5,3)→5；id3 返回第三参→3 |
| D2 物化比较表 | tri-gen.c:381-392 | GT(1,0)/GE(0,1)/LE(1,1) + UGT/UGE/ULE 同构 | 3>3→0；3>=4→0；4<=3→0；unsigned 三例 |
| D3 SNEZ 反序 | tri-gen.c:117 | SLTU rd,zero,rd（rs1=0,rs2=rd） | 3!=4→1；3==4→0；3==3→1 |
| D4 NE/EQ 发成 SLL | tri-gen.c:398/402 | 改 TRI_SUB_* 宏（同 644 行） | （与 D3 同用例） |
| D5 load() fc 未初始化 | tri-gen.c:280,410-416 | 开头 `fc=(int)(int64_t)sv->c.i`；VT_JMP 真假序对照上游核实 | (a&&a<5)→1/0 两路；(a>5\|\|a==1)→1 |
| D8 JALR rd==rs1 | tri-gen.c:522-536 | docall 分支 mv t0,ra; JALR ra,t0 | 函数指针 fp()→7 |
| D7 变量移位静默三进制 SLL | tri-gen.c:651-653 | B4 方案 A 真实现（本轮不做 tcc_error 门，直接到位） | s=10,i=2: s<<i→40 |

## B2 D6 常量装载

- 修复：通用分段装载器 `tri_load_imm_hw(hw, unsigned __int128 v)`（v<3^45，平衡分段
  借位双向归一），load() 非 ADDI 常量全走它（删除 LUI 两槽错误路径）；
  负 int64 = 3^45+v 转正处理。顺带修 gen_opil fuse 的 `fc=(int)` 截断（long long 常量误融合）。
- RED：int x=-1300→52；x=-1094→70（lo<-1093 借位）；x=2000000007→7（窗口溢出）；
  x=-2000000007→7；运行时 -a→251（顺带验证一元负号栈序）。

## B3 链接器/数据编码

1. **base-243 统一**（笔记 §1.6）：init_putv TRIRV 分支 VT_INT/VT_LLONG → 9 tryte
   base-243（负数 = 3^45+v，__int128）；short → 2 tryte base-243。
2. **text 基址**（§3.3）：tri_get_section_base 设 `text_section->sh_addr = 20`；
   删 tri-link.c:114/:190 两处散落 +20（LUI 分支自动修复）。
3. **符号校验**：tri_sym_addr/tri_apply_data_relocs 补 UNDEF（weak→0，否则
   'unresolved reference' 报错）+ st_shndx 界检查 + COMMON 干净报错。
4. **未覆盖 ALLOC 节**诚实报错（不再静默丢弃）。
5. tccgen.c:8830/8787 补 `|| defined TCC_TARGET_TRIRV`。
- RED：int g=1000→232；int g=-5→5；long long g=5000000007→7；fp==&f→7；
  NEG：extern zzz 编译报错含 unresolved；自定义节 .foo 编译报错含 section。

## B4 方案 A 适配层（C 位运算/移位二进制语义）

依据 trirv_cc.c:1354-1500 参考实现 + ternary-isa-design.md §5.1：
- **位游标**：raw 值即位模式；p 从 2^71 走 72 位（DIVU/2 递推）；ta/tb 经
  SLTU+SLTIU 逐位剥离；& = TMIN、| = TMAX、^ = |ta−tb|（SUB+TMAX+TMIN）。
  工作寄存器：x3,x4,x6,x7,x9（BIT[5]）+ x5（scratch）——位游标块是直线代码，
  无 load/store/调用，不需 callee-saved 预扫描。
- **~**：tccgen 把 ~a 折叠成 a^(-1)——gen_opil 拦截 `^ + const(-1)` →
  load TILDE（(2^72-1) mod 3^45）+ SUB。
- **常量移位**：SHL = MUL by 2^k（保留）；SHR unsigned = DIVU（k≥72→0）；
  SAR = DIV+REM floor 修正（修 -65>>3→-8 错值）；k∈[46,71] 用 128 位 pow2_mod；
  k<0 / k>4096 编译期 tcc_error（NEG 用例）。
- **变量移位**：单步循环（emit_shl_v/emit_shr_v 移植；SH_R=x3/x4，
  有符号借 x5 做 REM 修正；负移位量按 0 次处理）。
- **守卫**：方案 A 落地后 gen_opil 不再发 SLL/SRL/SRA/ANDI/ORI/XORI(位运算用途)。
- RED：6&4→4；2|1→3；3^1→2（翻转既有锁定值）；0x1234&0xFF→52；
  0x1200|0x34→52；0xFF^0x0F→240；(a&b)|(a&c)→240；~(~a)==a→1；
  (a&a)==a→1；(~0)==(-1)→0（锁定回绕后果）；a=-65,a>>3→247；
  变量移位 4 例；NEG：x<<5000 / x>>-1 编译报错含 shift。

## B5 T-1.8 ISA 一致性守卫（B1-B4 全绿后）

- trirv 仓库新增 `gen_tri_isa.py`：从 trirv_isa.py 生成 tri-isa.h + `--check` 模式
  （diff 现文件），消除"自动生成但生成器不存在"的不可复核声明（笔记 §4.5）。
- 验收：check 模式全绿 + 手改 tri-isa.h 一处后 check 立刻报错。

## 明确不在本轮（记档待拍板/排期）

- char 截断口径（0xff vs mod-243，§1.5）与 struct 9-对齐（§5.1，需 tccgen 掩码圆整
  重造）——需用户拍板 ABI 后另开一轮；
- long/LP32 口径维持现状并文档化（§5.3）；
- F 扩展（T-1.6）、CSR（T-1.7）、MULH 三条、A 扩展、-run/-shared、多 TU/-c、T-2 musl；
- struct 按值传参/返回（tri-gen 诚实报错，保持）。

## DoD

1. e2e 全绿（38 旧 + ~35 新 + NEG 机制）且 run_all_tests --fast 40 套全绿；
2. 两仓中文 commit（不 push）；
3. TINYCC_PORT_PLAN.md §7 补本轮坑记档；TCC_BEHAVIOR_NOTES.md 缺陷表标注已修。
