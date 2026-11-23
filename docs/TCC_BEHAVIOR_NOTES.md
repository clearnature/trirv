状态：进行中（ADR-13 关账后维护；三条 ⚠ 已于 2026-11-23 探针验证关闭）

# tcc 行为理解笔记（TriRV 后端实现者守则）

> 用途：TriRV 后端（tri-gen.c / tri-link.c）与 tccgen 前端改造的实现者执行前必读。
> 每条结论带 `path:line`；标注 ✗ 为已实证缺陷，⚠ 为结构性分歧/口径问题，✅ 为已核实无需改动。
>
> **修复状态（2026-11-21 T-1 收尾轮，见 TINYCC_PORT_PLAN.md §8）**：
> ✗ D1-D8 全部修复（各带 e2e 探针，163 PASS / 0 FAIL / 84 用例）；§1.6 base-243 静态
> 初值已修；§3.3 text 基址 / UNDEF / COMMON / 未覆盖节已修；§4.1/4.2 方案 A 已落地
> （位游标 + ~ + 移位 floor/变量移位，产物零 SLL/SRL/SRA）；tccgen 8830/8787 已豁免。
> 已修（2026-11-21 拍板后）：§1.5 char 截断口径 = mod-243 全轨、§5.1
> struct 9-对齐（整除式圆整）。仍未修：
> §1.9 的 (int)p bits=40 往返（小地址凑巧正确）、§1.3 LP32 口径（维持并文档化）、
> §2.3 大帧/变参可达性、F 扩展/CSR/MULH/A 扩展。
>
> **来源与可信度**：
> - 四视角研究材料（值栈与类型系统 / 参考后端惯例 / ELF 与重定位 / TriRV 现状与规范）；
> - 独立核实 pass：下列全部结论判定为**成立**，7 条细节修正已按 corrections 改写进本文（见 §8 修正记录）；
> - 本会话复核（综合员）：抽查行号 30+ 处逐行吻合——`tcc.h:483`、`tccgen.c:2363-2372/3415-3434/3920-3950/8787/8830/8876-8890`、`tccelf.c:3228-3231`、`tri-gen.c:104/116-117/280/310-322/378-404/408-417/522-537/541-558`、`tri-link.c:43-61/107-127/186-207/222-228`、`tri-isa.h:33-42/442-443`；并重跑 e2e（见 §0）。
> - 探针数值（如 `id(5,3)→3`、`(char)300→44`、`int g=1000→exit 193`）出自核实 pass 的双 VM 实测（trirv-tcc -nostdlib + trirv_vm.c / trirv_vm.py），本会话未逐一重跑，仅在 §0 重跑 e2e 套件确认基线。
>
> **引用约定**：无目录前缀 = tinycc 仓库（`/Users/zesheng/ai-lab/tinycc/`）；`trirv/` 前缀 = trirv 仓库（`/Users/zesheng/ai-lab/trirv/`）。

---

## 0. 基线与总体状态

- e2e 契约：`python3 trirv/trirv_tri_tcc_e2e.py` → **76 PASS / 0 FAIL / 38 用例**（双 VM）。本会话重跑输出一致。
- **该全绿是覆盖盲区所致，不代表后端正确**：核实 pass 的 54 项探针中 20+ 项 FAIL/崩溃，全部落在套件盲区（≥243/负数静态初值、函数指针、物化比较、负常量、自定义节、多 TU/-c）。套件自注释承认常量用例被前端折叠测不到生成代码（trirv/trirv_tri_tcc_e2e.py:42-46,54）。
- 计划完成度：T-0 全部完成；T-1.1/T-1.3 完成；T-1.2 半成（位运算/移位未过方案 A）；T-1.4 半成；T-1.5 5/8；T-1.6/T-1.7/T-1.8 未做；T-2 全线未做；T-3 半成（TINYCC_PORT_PLAN.md:53-83,120-182，trirv/TINYCC_PORT_PLAN.md）。
- git 佐证：tinycc 分支 trirv，29607487（T-0 spike）、77fe1128（R 型编码改走 tri-isa 真相源 + 二进制移位语义 + 数据重定位 + CValue 符号修复）。

---

## 1. tccgen 前端契约（值栈与类型系统）

### 1.1 CValue.i 不变量 —— 后端铁律

- `CValue.i` 是 `uint64_t`（tcc.h:479-489，483 行 `uint64_t i;`）。核心不变量：**32 位类型的常量以「64 位符号扩展（有符号）/零扩展（无符号）」位模式存进 c.i，符号信息只存在于 type.t**。归一化由 value64() 统一完成（tccgen.c:2363-2372）：VT_LLONG（及 PTR_SIZE==8 的 VT_PTR）保持 64 位；VT_UNSIGNED 截 32 位零扩展 `(uint32_t)l1`；其余 `(uint32_t)l1 | -(l1 & 0x80000000)`。
- 推入端：vpush64 `cval.i = v`（int 实参定义性符号扩展，tccgen.c:954-962，960 行）、vpushi（965-968）、vset `cval.i = v`（982-987，985 行）。gen_cast 源端归一同式（tccgen.c:3415-3420）。折叠结果按**左操作数类型**回写 `v1->c.i = value64(l1, v1->type.t)`（tccgen.c:2449）。
- **tri-gen 一切读 c.i 的地方必须按「先 (int64_t) 再用」解释**（tri-gen.c:223 `*fc = (int)(int64_t)sv->c.i`、305、566、604 已遵守）。tri-gen.c:310-315 坑实录：裸写 `sv->c.i >= -(int64_t)2147483647` 触发 usual arithmetic conversions 使负常量分支恒 FALSE——**移植任何新后端代码，此双重转换是硬规则**（本会话已核实该注释在 tri-gen.c:310-315 原文存在）。

### 1.2 c.i 按带符号读取的位置全集（tccgen.c 全文 58 处 c.i 引用）

1. `expr_const64()` `int64_t c = vtop->c.i`（tccgen.c:7652-7661，7658 行）。**实际调用点**（correction ①改写）：枚举 tccgen.c:4824、case 标签/范围 8276/8279、`__builtin_choose_expr` 6665、expr_const 7668。*（_BitInt 宽度不经 expr_const64——见第 2 条。）*
2. 显式 `(int)` 截断：tccgen.c:2236（PTR_SIZE==4 的 gen_opl 移位计数，TriRV 不编译）、**5143 `_BitInt` 宽度 `n = (int)vtop->c.i`（经 expr_const1，5133 解析）**、8812（compound-literal 拷贝偏移）。
3. 数组尺寸：tccgen.c:5579 `n = vtop->c.i`（n 为 int）→ 5580 `if (n < 0)` 报错——≥2^31 常量被截成负数拒绝。
4. 符号 ± 常量：tccgen.c:2506-2518——l2 经 value64 归一，2511 `l2 = -l2`（uint64 二补码取负，语义正确），2515 `(int)l2 != l2` 把加 gend 限幅 int32。
5. 有符号比较：gen_opic_sdiv（tccgen.c:2374-2378，`>>63` 判号）与 gen_opic_lt（2380-2383，XOR `1<<63` 技巧）只在符号扩展不变量下成立。
6. int→float 常量：tccgen.c:3389-3399 用 `c.i >> 63` / `c.i >> 31` 判号。
7. is_cond_bool：tccgen.c:7468 `(unsigned)sv->c.i < 2`。
8. is_null_pointer：tccgen.c:2849-2852。
9. 词法器往返：tccpp.c:2996 `tokc.i = n`；token 流序列化只存低 32 位 `tab[0]`（tccpp.c:1143-1154，1152 行）；读回时 TOK_CINT 用 int 符号扩展（tccpp.c:1231 `cv->i = *p++;`）、TOK_CUINT 用 `(unsigned)` 零扩展（tccpp.c:1237）。

**后端契约**：c.i 的 64 位模式对 VT_INT 恒为 32 位值的符号/零扩展，VT_LLONG 恒为全 64 位。TriRV 自造常量（tri-gen 内部 vpush）也必须维持该不变量。将来若把 int 提升到 45-trit 常量语义，value64/gen_cast/tccpp 词法器三处都要重做（不建议，见 §5.3）。

### 1.3 gen_opic 常量折叠 = 宿主 mod-2^32，与 45-trit 运行时分歧（✅ 已修——2026-11-23 探针 100000*100000%1000=0 双侧一致，45-trit 域折叠落地）

- gen_opic 在 uint64 上做全套算术/比较（tccgen.c:2402-2448），移位掩码 32 位化 `int shm = (t1 == VT_LLONG) ? 63 : 31;`（tccgen.c:2397），结果按左操作数类型 value64 回写（2449）→ **TriRV 的 VT_INT 常量折叠一律 mod-2^32 截断**，而运行时 int 是 45-trit（mod-3^45）。
- 实测（核实 pass，双 VM）：`return 100000*100000 % 1000` → 152（折叠成 1410065408 后 %1000=408）；`int a=100000,b=100000; return a*b % 1000` → 0（运行时 3^45 域 10^10%1000=0）；`return 100000*100000 == 1410065408` → 1。同一表达式折叠与非折叠结果必有一假。
- **口径决策**：短期接受 LP32 口径并文档化（LONG_SIZE=4 已由 tcc.h:426-430 固化 long=32 位语义）；长期改 45-trit 常量需动 value64/gen_cast/tccpp 三链，工作量与风险远大于收益，建议维持 LP32 并让 musl 移植遵守。`long long`（VT_LLONG）保 64 位：实测 `long long x=5000000000; x%1000` → 0 正确；`long l=5000000000` → 192（32 位截断值）。

### 1.4 gen_cast 常量路径（tccgen.c:3415-3434，本会话逐行核实）

- 顺序：源端归一（3415-3420）→ 目标 VT_LLONG 或（PTR_SIZE==8 且 VT_PTR）不动（3422-3423）→ VT_BOOL → `(c.i != 0)`（3425）→ 否则按 btype 掩码 0xff/0xffff/0xffffffff（3427-3429 `uint32_t m = dbt_bt == VT_BYTE ? 0xff : dbt_bt == VT_SHORT ? 0xffff : 0xffffffff;`）→ 有符号目标符号扩展（3430-3432 `vtop->c.i |= -(vtop->c.i & ((m >> 1) + 1));`）。
- 0x80000000 边界全部符合 C（实测通过）：十六进制 ≥2^31 无后缀 → TOK_CUINT（tccpp.c:2974-2983，2978-2979 行）→ VT_INT|VT_UNSIGNED（tccgen.c:6237-6239）→ value64 零扩展；`-0x80000000` = 0-0x80000000（tccgen.c:6846-6856）折叠为 unsigned、位型 0x80000000，`-0x80000000 < 0` 为假；十进制 2147483648 升位 TOK_CLLONG（tccpp.c:2967-2971）。probe：u80000000_gt0=1、neg0x80000000_lt0=0、`(char)300`→44、`(int)-1u`→255。
- ⚠ TriRV 落点：PTR_SIZE=9 时 VT_PTR 目标落入 0xffffffff 掩码分支（3422 条件为假）——对 VM 地址 <2^20 数值无害，但与 9-tryte 指针模型不一致；`char* p = (char*)0x123456789` 类常量会被截断。

### 1.5 char/short 截断双轨分歧（✅ 已定案关闭——ADR-13：mod-243 口径；2026-11-23 八条探针双工具链一致，(char)300=57 双侧）

- 显式 cast 走 gen_cast 常量掩码 0xff（8-bit 口径）；赋值/初始化走 vstore 的 delayed_cast（tccgen.c:3920-3950，本会话核实：3928 行 `delayed_cast = 1`，3943 `gv(RC_TYPE(dbt))`，3946-3950 设 VT_MUSTCAST、不做 gen_cast 截断，靠后端 store 指令自身截断）——x86 存 1 字节=8-bit 截断，TriRV SB 存 1 tryte=mod-243 截断。force_charshort_cast 在 tccgen.c:3285-3293。
- 实测分歧：`return (char)300` → 44（&0xff）；`char c = 300; return c` → 57（300 mod 243）；`static char m[6]; m[0]=300` → 57。**同一程序两条路径给出 44 与 57 两个值。**
- 字符串初始化走 memcpy 原始字节拷贝（tccgen.c:9006-9009）：`char msg[6]="AB"; return msg[0]+msg[1]` → 131 正确（ASCII <243 与 tryte 域相容）。
- 拍板建议：按 trirv/trirv-unified-memory-abi.md §1（char=1 tryte，0..242）取 **mod-243 口径**——delayed-cast/SB 路径是对的，gen_cast 的 0xff 掩码需加 TriRV 豁免（VT_BYTE 掩码改 242 边界处理）。

### 1.6 静态初始化 base-256 vs base-243（✗ critical 缺陷）

- init_putv 的 TRIRV 豁免分支（tccgen.c:8876 `#if PTR_SIZE == 8 || defined TCC_TARGET_TRIRV`）对 VT_INT 用 `write32le(ptr, val)`（tccgen.c:8885-8887，本会话核实）写 **4 字节小端 base-256**；TRV1 writer 原样直写段字节（tri-link.c:226 `fwrite(sec->data, 1, sec->data_offset, f)`，本会话核实）；而 VM 字语义是 `word = Σ tryte[i]·3^(5i)`（trirv/trirv_vm.py:150-155；trirv/trirv-unified-memory-abi.md:43-47）。
- 同一文件两种基数：tri_apply_data_relocs 对重定位槽按 base-243 写 9 tryte（tri-link.c:118-125，`v % 243` 循环，本会话核实）。
- 实测：`int g = 1000` → 段字节 [232,3,0,...]，VM 读 961，exit=193≠232；`int g=-5` → exit=92；`int g=7`、`int ga[3]={10,20,30}` 正确——**只有初值 <243 时两种基数巧合一致**，这正是现有 e2e（g=7/ga={10,20,30}，trirv/trirv_tri_tcc_e2e.py:90-92）测不出的原因。
- 修复口径：tri_output_file 输出段前把 data image 按 9-tryte/base-243 重打包（或 init_putv 加 TriRV 分支按 base-243 写，含负数的 45-trit 编码口径），并与 tri_apply_data_relocs 统一。

### 1.7 值栈纪律

- vstack = _vstack+1（tccgen.c:49-50），VSTACK_SIZE=512（tcc.h:436），空栈 = vtop==vstack-1。check_vstack（tccgen.c:352-357）只在 gen_function 末尾（9566）与 TU 末尾（441）调用（grep 全文确认）——**唯一安全网且检测滞后**，等价于「每次函数结束才炸」；宁可崩不生成错码（tccgen.c:9558 注释）。
- 变形原语前必须 vcheck_cmp()（tccgen.c:881-903：VT_CMP 不能留在非栈顶，nocode_wanted 豁免）；vsetc（905-916，907 上溢检查）、vswap（918-926）、vpop（929-945：939-943 VT_CMP jtrue/jfalse 补链、933-938 x86 ST0 弹栈）、vpushv（997-1003）、vrotb/vrott/vrev（1011-1036，vrotb(n)=「把位置 n-1 的元素转到栈顶」）、gvtst（1094-1113，1112 跳转生成后 vtop--）。
- tccgen.c 直接 `vtop--` 恰 23 处（grep 核实）：944,1112,1632,2451,2473,2483,2517,2639,2660,3919,3981,6657,6694,7121,7225,7509,7736,7741,7852,8139,8244,8794,8904——均为「已知非 VT_CMP/非 x86-FP」场合（3981/7509/7741 自带 `NOT vpop() ... fp stack` 注释）。
- **后端守恒规则（tri-gen 已遵守，新增路径必须满足）**：二元运算净 -1（tri-gen.c:608-613、624-630 `vtop -= 2; d=get_reg(); vtop++;`）；ADDI 融合 --vtop/++vtop 平衡（585-594）；gfunc_call 净 -(n+1)（tri-gen.c:558 `vtop -= nb_args + 1;`）；ggoto vtop--（680）。直接动 vtop 前确认 vtop->r 已物化（gv/gv2 之后必然如此）。

### 1.8 gv/gv2 契约与 TriRV 单寄存器化

- gv（tccgen.c:1871-2022）把 lvalue 转值、VT_CMP 需 reload 时经 vset_VT_JMP 物化（2008-2009）；**1876 行注释 `/* NOTE: get_reg can modify vstack[] */`——不得跨 gv/get_reg 缓存 vtop 指针**（tri-gen.c:624-630 gv2 后重读 vtop[-1]/vtop[0]，正确示范）。
- gv2（2025-2050）按 rc1<=rc2 先载通用者、首寄存器被 spill 时重试。
- TriRV 单寄存器事实链：R2_RET 三分支皆不命中 → 恒返回 VT_CONST（tccgen.c:268-284）→ USING_TWO_WORDS 恒假（286）→ RC2_TYPE 恒 0（319-334）→ **VT_LLONG/VT_PTR 是单 9-tryte 寄存器值，vtop->r2 恒 VT_CONST**；gv 内双寄存器展开（1964-2006）与 save_reg_upstack r2 分支（1466-1468）全是死代码，与 type_size(VT_LLONG)=9（3619-3621）自洽。新增 64 位路径不要再引入 r2 双字假设（PTR_SIZE==4 的 lexpand/lbuild 均未编译）。

### 1.9 PTR_SIZE=9 时 tccgen.c 全部 19 处 PTR_SIZE 条件落点（经 LONG_SIZE=4，tcc.h:426-430；本会话抽查 8787/8830/8876）

| 行 | 判定 | 说明 |
|---|---|---|
| 80 | ✅ | VT_SIZE_T=VT_LLONG\|VT_UNSIGNED、VT_PTRDIFF_T=VT_LLONG（与 9-tryte 指针一致） |
| 243-250 | ⚠ | btype_size 无 TriRV 豁免：VT_INT=4/VT_LLONG=8，与 type_size 的 9/9（3612-3629 TriRV override）并存；gen_cast 非常量路径 ds/ss 与位域掩码（4739）都用 32/64 位口径 |
| 271 | ✅ | R2_RET→VT_CONST（单寄存器 VT_LLONG） |
| 567 | ✅ | PE stdcall 命名，不编译 |
| 637 | ✅ | greloc 不编译（tri-link.c:234-239 报错桩顶替） |
| 1467/1987/3972 | ✅ | USING_TWO_WORDS/rc2 死代码 |
| 2053/2076/2094/2125 | ✅ | lexpand/lbuild/gv_dup-LLONG/32 位 gen_opl 不编译；gen_opl 由 tri-gen.c:661 提供 |
| 2366 | ⚠ | value64 不把 VT_PTR 当 64 位——指针常量被按 32 位归一（VM 地址 <2^20 数值无害，模型不一致） |
| 2523 | ✅ | VT_PTR±int 走 gen_opi（=gen_opil） |
| 2852 | ✅ | is_null_pointer 的 VT_PTR 走 64 位比较 |
| 3149 | ✅ | ptr+LLONG 前置截断不编译，gen_opl 兜住 |
| 3415/3422 | ⚠ | gen_cast 常量把 VT_PTR 按 0xffffffff 掩码+符号扩展（同 2366） |
| 3486-3489 | ⚠ | `(int)p` 警告分支把指针类型改 VT_INT → 每次 (int) 指针必触发 warning |
| 3511/3530 | ⚠ | 均不编译 → gen_cast 非常量路径 ss 保持 9：`(int)p` 运行时走 bits=(9-4)*8=40 的 SHL/SAR 往返（tccgen.c:3566-3574，tri 用 MUL/DIV 模拟）——VM 地址 <2^19 时凑巧正确（probe ptr_int_roundtrip=1），常量路径走 3422 掩码无此问题 |
| 8787 | ✗ | 符号初始化检查按 `PTR_SIZE == 8 ? VT_LLONG : VT_INT` 要求 VT_INT（本会话核实原文）→ probe `long long g = (long long)&x;` 编译报错 `initializer element is not computable at load time`；8876 的 VT_LLONG greloca 分支因前置拒绝而不可达；`long g=(long)&x` ✓ |
| 8830-8837 | ✗ | compound-literal 重定位复制在 PTR_SIZE≠8 时把 addend 传 0（本会话核实 `#if PTR_SIZE == 8 / rel->r_addend / #else 0` 原文）；TriRV 未豁免，ElfW_Rel=Rela 有 addend——与 tcc.h:404-407 注释记载同类缺陷 |
| 8876-8887 | ✅/✗ | TriRV 豁免分支：VT_LLONG/VT_PTR→greloca/write64le ✅；VT_INT→write32le ✗（base-256 缺陷，§1.6） |
| 9394/9400 | ✅ | VLA 诚实报错（gen_vla_sp_save/gen_vla_alloc → tri-gen.c:673/676）。（correction ⑥：9404 在 `#if defined TCC_TARGET_PE && defined TCC_TARGET_X86_64` 内，TriRV 不编译。） |

**需修三处**：8830 补 `|| defined TCC_TARGET_TRIRV`（addend 保留）；8787 补 TriRV 豁免允许 VT_LLONG 符号初始化；btype_size 或 gen_cast 的 ds/ss 需 TriRV 口径（当前 (int)p 靠 bits=40 往返在小地址上碰巧正确）。其余落点判定为正确或无害。

---

## 2. tri-gen 后端现状

### 2.1 与上游（riscv64-gen.c）惯例对齐良好、无需改动的部分 ✅

- **load/store 宽度表**：上游「宽度→funct」单表 + 无符号 +4 位（riscv64-gen.c:265-267/445-447）；tri 等价 tri_mem_funct（tri-gen.c:266-275：1→LB/LBU、2→LH/LHU、9→LW/SW，与 tri-isa.h:139-158 编码一致），PTR/FUNC 型 lval 先强制 size=PTR_SIZE（tri-gen.c:289-290 = riscv64-gen.c:263-264）。e2e char/short 有/无符号用例全过。
- **地址综合分层**：tri_addr_base（tri-gen.c:220-241）三层等价上游 load_symofs（riscv64-gen.c:175-236）：VT_SYM→tri_lui_load 双槽占位+补丁（tri-gen.c:134-143）、VT_LOCAL、寄存器基址偏移并入。缺口：大偏移与大常量地址存储是诚实报错（231-232/239-240）。
- **帧形状**：预留 5 槽序言、ra/s0 存帧顶、参数统一洒 s0 相对槽、epilog 回填——tri-gen.c:453-455/475/513-517 与 riscv64-gen.c:817-819/865/947-959 逐条同构（反汇编 probe1.trv slot5-9 吻合）。
- **寄存器/save 纪律**：`vrotb(nb_args+1); save_regs(nb_args+1); gcall_or_jmp(1);` 两后端同款（tri-gen.c:555-557 = riscv64-gen.c:788-791）；gcall_or_jmp 三分支同构（tri-gen.c:522-536 = riscv64-gen.c:450-469）。
- **布尔取反红线**：一律 SLTIU/SLTU，未踩 XORI-1 禁区（tri-gen.c:22、115-117；禁令出处 trirv/ternary-isa-design.md:422-424）。TRI_SEQZ_NOT=SLTIU rd,rd,1 本身正确（tri-gen.c:116；VM SLTIU 同式 trirv/trirv_vm.py:470-472）。
- **gjmp_cond 分支路径**：B 型分支+取反越过 2 槽（tri-gen.c:168-197），funct 表与 riscv64-gen.c:1021 同构，全对（实测 `if(a<=3)` 分支消费正确）。

### 2.2 已实证错码缺陷（必修，修复优先级见 gaps）

| # | 缺陷 | 根因 path:line | 实测现象（核实 pass，双 VM） | 修复方向 |
|---|---|---|---|---|
| D1 | **gfunc_call 实参顺序颠倒**（ABI 级） | tri-gen.c:545-549：`int ii = nb_args - 1 - i; vrotb(ii + 1); gv(RC_R(ii));`——vrotb(ii+1) 弹出的是 arg_i（tccgen.c:1010-1020 vrotb(n) 语义），却装入 a_{n-1-i}；上游配对 `vrotb(i+1)`（riscv64-gen.c:723）配 `info[nb_args-1-i]`（:716）恰一致 | `id(5,3) return a` → 3；`id3(5,4,3)` a=3/c=5；`sub(10,4)` → 250=y−x；现有 e2e 因 add3/f8 全是加法（可交换）未暴露 | **一行修复：`vrotb(i + 1);`**；补非交换多实参 e2e |
| D2 | 比较物化 swap/notafter 表错 | tri-gen.c:381-392（本会话核实）：GT(0,1)/GE(1,1)/LE(1,0)——GT 物化成 a>=b、GE 成 a<=b、LE 成 a>b；上游归一表 riscv64-gen.c:373-385：奇数 token `inv=1;op--`、`(op&7)==6` swap+inv^=1 → 正确表 GT(1,0)/GE(0,1)/LE(1,1) | 3>3→1、3>=4→1、3<=4→0、ULE 3<=3→0、UGE 3>=5→1；p19/p15 侥幸通过（GT 错在等号、GE/LE 恰好互换） | 按 op&1 / (op&7)==6 归一写法重写 381-392；补等号边界 e2e |
| D3 | TRI_SNEZ 操作数反序 | tri-gen.c:117（本会话核实）：`TRI_ENC_R(..., 0, rd, ..., rd)` 第 3 参→rs2、第 4 参→rs1（tri-isa.h:417 绑定，宏实测编码 0x0f6815fd decode rs1=rd、rs2=0）→ 发 SLTU d,(rd<0)≡恒 0；应 rs1=0,rs2=rd（riscv64-gen.c:391-394 `sltu d, x0, d`） | NE 物化恒 0、EQ 物化恒 1：(3!=4)→0、(3==4)→1 | 交换两个宏实参为 `(rd, 0, ...)`；修好后 NE（SUB;SNEZ）与 EQ（SUB;SNEZ;SEQZ_NOT）自动转正 |
| D4 | load() VT_CMP 物化 NE/EQ 发错指令：硬编码 `(TRI_OP_OP, 0, b, a, 1, rr)` 注释写 SUB，编码真相是 SLL | tri-gen.c:398/402（本会话核实原文）；tri-isa.h:33-42：SUB=(OP,funct=0,funct2=1)、SLL=(OP,funct=1,funct2=0)（本会话核实）；gen_opil 主 switch 已在 77fe1128 修复走 TRI_SUB_* 宏（tri-gen.c:640-643 坑实录），唯独此处漏改 | 反汇编比较段 slot21=SLL/slot22=SLTU/slot23=SLTIU；`return (x==y)` → 1 | 398/402 改用 TRI_SUB_OP/TRI_SUB_FUNCT2/TRI_SUB_FUNCT（与 644 行同款）；补「物化比较值」e2e |
| D5 | load() VT_JMP 物化用未初始化 fc | tri-gen.c:280 `int fc, base, funct, ok;` 声明后仅 VT_LVAL/VT_LOCAL 分支经 tri_addr_base 赋值；VT_JMP 分支 tri-gen.c:409-416 `gsym(fc)` 的 fc 从未赋值（本会话核实）；上游 `int fc = sv->c.i;`（riscv64-gen.c:256），链头在 c.i（tccgen.c:1057-1065 vset_VT_JMP） | 逻辑运算结果当值物化（`return (a&&a<5)`）→ SIGTRAP 崩溃 rc=-5；分支消费（`if (a&&a<5)`）与 `return (a<5)` 正常；riscv64-tcc 同源 exit=0 | load() 开头补 `fc = (int)(int64_t)sv->c.i;` |
| D6 | i32-LUI 快路径对负常量三缺陷 | tri-gen.c:316-322（本会货核实）：① lo 借位只有上界 `if (lo > TRI_IMM7_MAX) lo -= 2187;`，lo<-1093 不处理；② 负 hi `(hi%1594323+1594323)%1594323u` 回绕成正数发 LUI，无 SUB 补偿（VM LUI imm13 无符号，trirv/trirv_vm.py:621 vs JALR 617 显式 sign_extend）→ 值恰好多 k×3^20；③ 窗口检查 `-TRI_IMM13_MAX..TRI_IMM13_MAX` 使 \|v\|>1743392200 报 beyond LUI window（tri-isa.h:443 IMM13_MAX=797161）。正确口径：trirv/trirv_cc.c:1297-1307 load_seg 负段取正 LUI+ADDI 再 `SUB reg,zero,reg` | `int x=-1300` → Assertion failed（tri-gen.c:104，本会话核实该断言）；`long l=3000000000; return l>0` → 1（应 0，差值精确=3^20）；`return 2000000000`/`return 2147483647`/`int x=2000000000` → 编译错误（correction ②：`return 2000000000/1743392201/2147483647` 被折叠成 0 可编译，不能作探针）；`return 1743392200` → 200 正确；3×20 分段路径（tri-gen.c:324-359）实测全对（`long long x=9223372036854775807; x%1000000007`→163） | 负 i32 常量与窗口溢出一律走「取正 LUI+ADDI+SUB」或 3×20 分段；lo 借位双向化 |
| D7 | 变量移位静默发三进制 SLL/SRL/SRA（×3^k） | tri-gen.c:651-653 R 型 switch 是唯一变量移位路径；602-604 常量门只拦 [0,45]、之外无 else 报错；598-601 注释自称「变量移位量 spike 报错」但全文无报错点（核实 pass 指令流实证：x<<2 发 op=13 MUL、x<<50 发 op=0 SLL） | `int s=10; int i=2; return s<<i` → 90（=10×3²，C 是 40）；`s>>i` → 1（=10÷9）；`return 4<<2` → 16 正确（**correction ③：双常量被前端折叠，36 只出现在变量左操作数场景**） | 方案 A 落地前先 tcc_error 诚实门控（对齐 tri-gen.c:6-9「宁可报错不可错编」）；落地按 §4.2 实现 |
| D8 | 间接调用 JALR rd==rs1 | tri-gen.c:522-536（本会话核实）：`int tr = docall ? TRI_RA : TRI_T0; ... TRI_JALR(tr, ireg(vtop->r));` 第三分支 load 进 TREG_RA 后 `TRI_JALR(tr=RA, r=RA)` 同样 rd==rs1；两个 VM 的 JALR 都是**先写 rd 再读 rs1**（trirv/trirv_vm.c:848、trirv/trirv_vm.py:615-619）→ 跳到 oldpc+4 | `int f(void){return 7;} int (*fp)(void)=f; return fp();` 全局/局部两种写法均 exit=0≠7（反汇编 slot23=JALR rd=1 rs1=1） | gcall_or_jmp 保证 rd≠rs1：目标装入 t0 再 `JALR ra,t0`（ISA 层是否改先读后写待定夺，见 §7） |

### 2.3 诚实报错 spike 清单（tri-gen.c:663-676 集中 tcc_error，对齐上游缺失项）

- 大帧两段 epilog：帧 d>1093 无处理，`TRI_ADDI` 断言崩溃（tri-gen.c:104，TRI_IMM7_MAX=1093 tri-isa.h:442）而非上游式两段远 epilog（riscv64-gen.c:938-956）；大偏移仅两处干净报错（tri-gen.c:231-232/243-247）。实测 500-int 数组触发断言。
- 栈参数 >8 报错（tri-gen.c:541-542；callee 侧 471-472）、struct 按值传参报错（467-468）、arch_transfer_ret_regs 报错（495-498）——对照 riscv64-gen.c:587-806/807-883 与 trirv/trirv-unified-memory-abi.md:89（trirv_cc 已实现 11/11+12/12 PASS）。
- **变参（correction ④ 改写）**：tri-gen.c:671 的 `gen_va_start` 报错桩**对 TriRV 不可达**——tccgen.c:6741-6752 的 `case TOK_builtin_va_start` 仅在 RISCV64/X86_64/ARM64 下编译；实测 `#include <stdarg.h>` 头文件缺失直接编译错，`__builtin_va_start` 写法**编译通过且运行时产生垃圾值（exit=128），没有任何报错**——静默错码，比报错更糟。
- struct 按值返回：gfunc_sret 只有 1 寄存器/隐藏指针两档（tri-gen.c:483-493；对照 riscv64-gen.c:885-904 reg_pass 1/2 寄存器分类）——1-word 返回 SIGSEGV（rc=-11）、2-word 报 `TriRV: symbol has no elf entry`；崩溃点未定位（§7）。
- 浮点全套/VLA/tcov/bounds 报错：gen_opf/gen_cvt_itof/ftoi/ftof/gen_increment_tcov/gen_vla_*（tri-gen.c:663-676）；func_bound_add_epilog 仅声明（tri-gen.c:65，全仓无实现）。gen_cvt_csti 报错桩（669）实际不可达（tccgen.c:3530-3564 调用点 #if 列表不含 TRIRV，窄化走 3566-3574 通用路径）。
- 乘除正确：MUL/DIV/DIVU/REM/REMU 已发（tri-gen.c:646-650；tri-isa.h op 13/14/15），e2e 除/模用例通过。

---

## 3. tri-link / ELF 与重定位

### 3.1 输出管线 bypass 与 64-bit 类

- `tcc_output_file` 对 TRIRV 的 EXE 和 OBJ 都直接 `return tri_output_file`（tccelf.c:3228-3231，本会话核实），整个 elf_output_file 管线被绕过。ELF 必要步骤中 tri 重实现 3 个（布局=tri_get_section_base tri-link.c:43-61、数据回填=tri_apply_data_relocs 93-127、符号终址=tri_sym_addr 64-80）、缺失 6 个：tcc_add_runtime（tccelf.c:3044）、resolve_common_syms（3045）、set_sec_sizes（3092）、layout_sections（3138）、relocate_syms（3152）、relocate_sections（3155）。`-run`/`-shared` 未被拦截，会落进 elf_output_file 撞 relocate() exit 桩（tri-link.c:249-255）。
- 64-bit 类强制正确且必需：tcc.h:404-416 `#if PTR_SIZE == 8 || defined TCC_TARGET_TRIRV` → ElfW=Elf64_*、ElfW_Rel=Elf64_Rela(24B)、SHT_RELX=SHT_RELA、REL_SECTION_FMT=".rela%s"（注释明言：PTR_SIZE=9 落 32-bit 类会让 greloca 的 addend 丢失）。PTR_SIZE=9 定义于 tri-gen.c:40。实测 addend 依赖成立（`char *t="abcdef"+1` exit=98='b'）。**类宽≠产物格式**——Ehdr 从不写出，改类宽/步长会破坏 tri-link 指针运算（tri-link.c:73/102/107 按 Elf64 步长）。
- greloca 负责建 symtab 条目并调 put_elf_reloca（tccgen.c:609-635）；.rela 节在 put_elf_reloca 惰性创建（tccelf.c:789-817）；greloc 仅 PTR_SIZE==4 编译（tccgen.c:637-642），TRIRV 由 tri-link.c:234-239 exit 桩顶替。TRIRV 下文本重定位完全不走 .rela（tri-gen 用 TRI_PATCHES，tri-gen.c:122-143/528），.rela 只装数据段指针。
- R_DATA_PTR 产生时机：init_putv TRIRV 豁免分支 VT_LLONG/VT_PTR 且 vtop 带 VT_SYM → `greloca(sec, vtop->sym, c, R_DATA_PTR, val)`（tccgen.c:8878-8883）；非符号值 write64le/write32le 写原始字节。

### 3.2 数据编码两种基数（同文件）✗

见 §1.6。正向闭合的只有「符号值指针」：`char *m="AB"` → rodata@100=[65,66,0]、data@108=[100,0×8]，exit=131='A'+'B' ✓；指针数组 {"xy","zw"} → 239 ✓；`char a[]="xy"`（memcpy 路径）→ 241 ✓；addend 探针 → 67='C' ✓（tri-link.c:117 `val = base + es->st_value + (addr_t)relp->r_addend;`，本会话核实）。同节非符号标量（write32le/write64le）是二进制编码——≥243 或负数静默错值。

### 3.3 符号解析与布局缺口（ELF 管线 6 缺失步骤的后果）

| 缺口 | 证据 path:line | 实测 |
|---|---|---|
| ✗ UNDEF 符号无检查（relocate_syms 未被调用；ELF 路径 weak 置 0/否则报错 tccelf.c:1127-1131） | tri_sym_addr 只有界检查 tri-link.c:73-77，无 UNDEF/weak 分支 | extern puts 代码引用 → `TriRV: symbol has no elf entry` rc=1（tri-link.c:69-72）；`extern int zzz; char *p=(char*)&zzz;` → tri_apply_data_relocs 对 SHN_UNDEF=0 取 sections[0]=NULL（tccelf.c:65）→ **SIGSEGV rc=139**（tri-link.c:108-109 解引用 sh_addr） |
| ✗ resolve_common_syms 被跳过 | 默认 nocommon=1（libtcc.c:884）使 tentative 定义进 .bss 侥幸无恙（tccgen.c:9329-9330）；-fcommon 落 common_section（9347-9348，SHN_COMMON=tccelf.c:74） | `int x; int y=3; x=4;` exit=7 正确；`-fcommon` → `TriRV: bad sym section` rc=1（tri_sym_addr 界检查命中）；**COMMON 在 tri_apply_data_relocs 路径是 OOB 读**（无界检查） |
| ✗ TRI_R_LUI_ABS 文本 +20 漏加 | tri_get_section_base 只给 rodata/data/bss/common 赋 sh_addr（tri-link.c:45-57，本会话核实），text_section->sh_addr 恒 0；数据回填 +20（tri-link.c:114）、JAL 补丁 +20（:190）各自手加（均本会话核实），LUI 分支 `tgt = tri_sym_addr(...) + p->addend`（:187）无 +20 | `int f(void){return 7;} return (int)(unsigned long)&f;` → exit=0（f 终址应 20）；`return (int)&f + 10*((int)&g)` → 144（应 108=20+10×60），错值恰为漏 +20 效应 |
| ✗ 未覆盖 ALLOC 节静默丢弃 | writer 只写 rodata/data 两节（tri-link.c:168-171 `Section *cand[2]`，本会话核实） | `__attribute__((section(".foo"))) int g = 7;` → exit=0≠7、无任何警告、TRV1 n_seg=0 |
| ✗ 多 TU / -c 工作流丢失 | 符号解析坍缩为「输出期遍历 TRI_PATCHES + .rela.data」；tri-gen.c:528-529 直接把 Sym* 存 TRI_PATCHES 不建 symtab 条目；tccelf.c:3229 把 TCC_OUTPUT_OBJ 也交给 tri_output_file | `lib.c -c`（无 main）→ `TriRV: no 'main' symbol`（tri-link.c:153-159 无条件找 main）；双 TU → `TriRV: symbol has no elf entry`（extern Sym 的 sym->c=0；ELF 路径由 greloca 建 UNDEF 条目 tccgen.c:617-631 + relocate_syms 绑定） |
| ✗ tcc_add_runtime 跳过 | 外部符号无 elf 条目（见上）；libc/libtcc1 路径需补 tcc_add_runtime 等价物或移植 trirv 仓库 trirv_crt.c 方案 | 单 TU -nostdlib 是当前唯一可用形态 |

**修复口径**：tri_get_section_base 的 layout 块里给 `text_section->sh_addr = 20`（5 槽 _start），让 tri_sym_addr 一次到位，然后删掉 tri-link.c:114 和 :190 两处散落 +20，LUI 分支自动修好；tri_apply_data_relocs 补 st_shndx 校验（对齐 tri-link.c:74-77）+ UNDEF/weak 策略（非 weak 未解析报错对齐 tccelf.c:1131 'unresolved reference'，weak 置 0）；COMMON 至少干净报错；未覆盖 ALLOC 节诚实报错。

---

## 4. 方案 A（C 位运算/移位适配层）与 ISA 覆盖

### 4.1 位运算逐 trit 直译现状（✗ 与 C 语义不符，方案 A 零实现）

- C 的 `& | ^` 全部逐 trit 直译：const 融合发 ANDI/ORI/XORI（tri-gen.c:590-591，funct 6/3/2）、寄存器版发 AND/OR/XOR（654-656）；tri-gen.c:574-578 注释自认「XORI funct=2（三进制 mod-3 加！）……与 trirv_cc 的 C 位运算方案 A 不一致……T-1 收尾时接方案 A 适配层」。实测 `int x=10; return x&6` → 0（逐 trit min；C 是 2）。
- `~` 的变量路径发 XORI(imm=-1)（逐 trit mod-3 加 −1，imm 符号安全取模 2186=全 2 trit）：tccgen.c:6379-6382 把 ~ 归约成 `^(-1)`（`vpushi(-1); gen_op('^')`）。实测 ~1 → 253（=-3；C 是 -2）；~0 → 255（=-1 巧合正确）。方案 A 定义 `~a = ((2^72-1) mod 3^45) - a`（trirv/ternary-isa-design.md §5.1.1；参考实现 trirv/trirv_cc.c:1393-1398 emit_bitnot `load_imm128(TILDE)+SUB`），~0≠-1 是定义后果。
- e2e 已把直译语义锁成期望值（trirv/trirv_tri_tcc_e2e.py:42-46「与min=3/或max=2/异或mod3=4」）——**方案 A 落地时必须翻转这些期望值**，并按 ternary-isa-design.md:507-523 补「代数律不成立」锁定用例（德摩根/结合/分配 ❌，幂等/零元/对合/交换 ✅）。
- 方案 A 五要素齐备（bits=raw/72 位视图/3^45 回绕/TILDE−a/律锁定，ternary-isa-design.md:461-506）+ trirv_cc 参照实现齐备（emit_bitop3 trirv/trirv_cc.c:1354-1389、gate_check 2807-2817）；tri-gen 零实现且 **TMIN/TMAX/TNOT 宏已在 tri-isa.h:223-242 但 gen/link 零引用**（grep 实证）。
- AND/OR/XOR 逐 trit 是 ISA 决议（非违背上游）——套件锁定该语义是过渡态。

### 4.2 移位：现状与 §5.1.1 规范

- 规范（trirv/ternary-isa-design.md:484-505）：`<<` = ×2^k mod 3^45（k≤10 ADD 链、k>10 一次 MUL by 编译期 pow2_mod(k)）；`>>` 无符号 = DIVU（k≥72→0）；`>>` 有符号 = DIV 后 REM 判余 floor 修正；变量移位 = 循环单步（需 2 callee-saved）；k<0/k>4096 编译期硬报错；有符号性只看左操作数。trirv_cc 参照：pow2_mod（trirv/trirv_cc.c:1422-1426）、emit_shl_k（1428-1437）、emit_shr_k 含 REM 修正（1439-1461）、变量循环（1468/1481）。
- tri 现状：常量移位按方案 A 发 MUL/DIVU/DIV by 2^k（tri-gen.c:598-623，正确方向）但**只覆盖 [0,45]**；有符号 `>>` 缺 REM floor 修正——实测 `-65>>3` → 248（-8，截断），C/gcc 是 247（-9）；trirv_cc 有修正而 tri 没有（TINYCC_PORT_PLAN.md:170-171 只记档未修）。变量移位与大常量移位量静默发三进制 SLL/SRL/SRA（缺陷 D7）。
- 前端折叠效应：左右皆常量的移位被 tccgen 用宿主二进制语义折叠成常量装载（实测 `return 4<<2`→16 正确、sh40/sh50 探针走常量装载路径）——**方案 A 只需处理「变量左操作数 + 常量/变量移位量」路径**。
- 守卫：方案 A 落地后产物中不得再出现 SLL/SRL/SRA（ternary-isa-design.md:497-500）。注意 SRLI/SRAI 与 C 语义不相容（三进制右移 ≠ C >>），**不要把「补齐 SRLI/SRAI 发射」当欠账**；SLTI 可用于常量比较融合。

### 4.3 寄存器供给

- trirv_cc 用 gate_check 按函数预扫描分配 BIT_R[5]+SH_R[2] callee-saved，抢不到硬报错（trirv/trirv_cc.c:1343-1346、2807-2817）。
- tri-tcc 现成资源：NB_REGS 19 中的 8 个「浮点」槽 r8-15（tri-gen.c:27，spike 不用），tccgen 已按 RC_F 分类（tri-gen.c:33-34），freg() 当前报错（:83）——**可整体征用作 BIT_R[5]/SH_R[2]**；实现成本在 gen_opil 拦截与寄存器类规划，不必改 tccgen 分配器。F 扩展落地时需重新划分 RC_FRET。

### 4.4 指令覆盖 39/57（机械核对：96 条指令宏，tri-gen/tri-link 发射 39、零发射 57）

- 已发射 39：R 型 10（ADD/SUB/SLL/SLT/SLTU/XOR/SRL/SRA/AND/OR 经命名宏）+ M 型 5（MUL/DIV/DIVU/REM/REMU）+ 裸 funct 24（ADDI/SLLI/SLTIU/XORI/ORI/ANDI、LB/LBU/LH/LHU/LW、SB/SH/SW、BEQ/BNE/BLT/BGE/BLTU/BGEU、JAL/JALR/LUI、ECALL）。
- 零发射 57：SLTI、SRLI、SRAI、AUIPC、EBREAK、FENCE、TT 9 条（TMIN/TMAX/TNOT/TAND/TOR/TNEG/TPACK/TSEXT/TUNPACK，tri-isa.h:223-254）、CSR 6 条（:259-282）、MRET/WFI（:283-290）、A 扩展 9 条（:291-326）、F 扩展 22 条（:327-414）、MULH/MULHSU/MULHU。
- （correction ⑤ 微调：SLTIU 并非全未用——TRI_SEQZ_NOT=SLTIU rd,rd,1 在用（tri-gen.c:116/404）；零发射的是 SLTI 与「常量比较融合」这一用途。）
- 即 T-1.5（MULH 3 条）+ T-1.6（F 22 条）+ T-1.7（CSR 6 条）+ trap（MRET/WFI）的完整清单；A 扩展 9 条与 TT 9 条在计划 T 系列之外需排期。TRI_OP_AUIPC 定义但 tri-gen/tri-link 零引用（寻址=绝对 LUI+ADDI+补丁表，VM 内存 1<<20 上限，tri-link.c:196-199 beyond VM memory 报错）——无 PIC/TLS/动态链接能力。

### 4.5 ISA 一致性守卫缺口（T-1.8）

- tri-gen.c:13-14 与 tri-isa.h:1-2 声称「tri-isa.h 由 trirv_isa.py 自动生成、96/96 逐位对齐验证」，但两仓库 `find -name '*gen_tri*' -o -name 'gen_*isa*'` 均空——**声称不可复核、不可持续**（trirv_isa.py 演进后漂移无人发现）。指令计数 96 属实（98 个 _OP 匹配 − TRI_W_OP − TRI_OP_OP）。

---

## 5. 数据模型与 ABI 决策点

### 5.1 struct 对齐：9-tryte vs 打包（✅ 已修——2026-11-23 探针 struct{char;int*} 指针成员偏移=9 双侧一致，ADR-13）

- ABI §6 要求 struct 成员起始对齐 9 tryte（trirv/trirv-unified-memory-abi.md:86）；tri-tcc 的 type_size 把 int/double/llong/float 对齐全设 1（tccgen.c:3619-3627 `*a = 1; return 9/4`），tri-gen.c:10-12 自认「对齐简化：全 align=1……9-对齐在 T-1 收尾时以专用 hook 重做」。实测 `struct{char c; int *p}` 的 p 落 offset 1 而非 9（全局/局部两版均 1）。
- **根因不止 type_size**：tccgen 全部对齐圆整是二进制掩码 `(c+align-1) & -align`——成员 tccgen.c:4545、struct 尺寸 4655、局部 9275、临时 1582、sret 空间 7109（本会话核实 4545/4655/9275 原文存在）。align=9 非 2 的幂 → 掩码运算清 bit3 而非圆整到 9，指针成员偏移与含指针 struct 尺寸**系统性错误**。MAX_ALIGN=9（tri-gen.c:43）还使 `__attribute__((aligned))` 裸形式直接报错（tccgen.c:4257-4268 exact_log2p1 校验；实测 aligned(9) → 'alignment must be a positive power of two'）。
- 局部变量布局小偏差：ABI §4「char 占 1，其余占 9」（trirv/trirv-unified-memory-abi.md:59）vs type_size（char=1 ✅ / short=2 ⚠ 未被 TRIRV 覆盖（3630-3632）/ int=ptr=9 ✅）；gfunc_prolog 参数槽一律 9 tryte（tri-gen.c:473 `loc -= 9;`）与 ABI §6「所有参数等宽 9 tryte」一致。
- 9-对齐 hook 不能只改 type_size 返回值，必须同时替换 tccgen 掩码圆整为对 9 有效的方法；不做则 tri-tcc 产物与 trirv_cc 的 struct/libc 布局永不互通（musl T-2.3 硬前置）。

### 5.2 char/short 截断口径（见 §1.5；需拍板）

### 5.3 long / 常量 LP32 口径（见 §1.3；建议维持 LP32 并文档化，musl 遵守）

### 5.4 short 3^10 回绕（既定偏离，非 bug）

- short=2 tryte=10 trit，窄化回绕在 3^10=59049 而非 C 的 65536：实测 `int x=70000; short s=x; return s` → 199（C 是 112&0xFF=4464 的 &0xFF 口径差）。规范表述 trirv/ternary-isa-design.md:430-431「三值 C 而非 ISO C」。e2e 现按 &0xFF 口径写期望，掩盖了 3^10 回绕差异，应在文档与测试期望中明确。

---

## 6. 回归口径（修复后必须补的 e2e）

现有 38 用例的盲区正是全部 critical 缺陷漏网的原因（trirv/trirv_tri_tcc_e2e.py:42-103：常量版被前端折叠自注释、唯一物化比较用例 `unsigned a=3000000000u; return a>5` 恰在 UGT 错码侥幸正确、add3/f8 全加法、全局初值全 <243）。修复后必须并入：

1. 静态初值 ≥243 与负数（`int g=1000` 回读 1000）；
2. 负常量/窗口边界常量装载（`int x=-1300`、`return 2000000000`、`return 2147483647`）；
3. 物化 ==/!=/GT/GE/LE（变量对变量，作为返回值与存储，真假两侧+等号边界）；
4. 非交换多实参（id(5,3)/sub(10,4)）；
5. &&/|| 取值物化（`return (a&&a<5)`）；
6. 函数指针调用（fp() 走 JALR）；
7. char 243..255 边界（(char) 口径拍板后）；
8. 变量移位负用例（门控期断言报错、方案 A 后二进制期望值）；
9. 位运算期望值翻转 + 代数律锁定用例（方案 A 落地时）；
10. 自定义节/多 TU/-c 负用例（报错路径锁定）。

---

## 7. 待验证（不编造，逐项列证据缺口）

1. `char msg[6]` 这一具体写法在 tinycc/tests 与 trirv 全仓 grep 均无命中——按「char 数组尺寸常量 + char 常量截断」同构场景处理并实测了 `char msg[6]="AB"`。若指某个特定测试/已知 bug，需给出处。
2. tccgen.c:8830 addend 丢失只做了代码级判定，未端到端复现（需 compound literal + 内部重定位被复制进静态初始化的用例）。
3. p32（`return (a&&a<5)`）SIGTRAP 崩溃未用调试器定位到具体指令——未初始化 fc 的推断来自源码分析+触发条件隔离。
4. `(int)p` 非常量路径 bits=40 移位往返在地址 ≥2^19（VM 内存上限 1<<20 高段）是否仍凑巧正确，未实测。
5. TriRV 位域（`struct { int f:3; }` 类）完全未测；btype_size 32 位口径下 tccgen.c:4739 位域掩码对 9-tryte int 是否错位未验证。
6. `long` 宽度语义（LP32 常量口径 + sizeof=9 tryte 混合态）是数据模型决策：ABI 文档未定义 long，需项目拍板后再定 tccpp/tccgen 的 LONG_SIZE 口径。
7. tri-gen.c 的 assert 在 NDEBUG 正式构建下失效：lo<-1093 的 ADDI 会静默回绕成错值（本次实测为断言崩溃，说明当前构建带 assert）；正式发布是否保留 assert 需决定。
8. gen_cast_s(VT_SIZE_T)/指针差 p-q 经 gen_opic 的 32 位 shm=63 行为未单独验证。
9. JALR「先写 rd 后读 rs1」是 ISA 规范还是两个 VM 的共同实现偏好，未对照 trirv/trirv-unified-memory-abi.md / ternary-isa-design.md / trirv_isa.py 的 JALR 章节定夺。
10. 负数/大值 C 标量在 9-tryte 内存字中的正确编码（对 3^45 取补还是平衡三进制）需 ABI 文档口径；neg 探针只证明二进制编码错，未证明正确字节序列。
11. tri-tcc 的实际负载（musl_pp/batch_musl_compile）是否用到 -fcommon、自定义 section 或 TLS——决定 COMMON/TLS 缺口优先级。
12. TRI_PATCHES 持 Sym* 跨多次 tcc_compile 是否悬挂（符号池生命周期）——只观测到 c=0 干净报错，未核查回收时机。
13. `long long` 是否应为 9 tryte（tccgen.c:3620）还是 8——影响 write64le 替换方案宽度，属 ABI 决策。
14. `-run`/`-shared` 对交叉编译 trirv-tcc 是否有实际意义——仅在静态层面确认路由（撞 relocate 桩），未实测。
15. struct 1-word 返回 SIGSEGV 崩溃点未定位（无调试符号；gfunc_sret/gfunc_call/gfunc_prolog 哪一环不明）。
16. tri-tcc 产物与 trirv_cc 产物同进程混用（struct 布局、对齐、调用约定三处不一致）可行性未测——T-2.3 musl 复用方式需先决策。
17. gen_opil const-fusion 中 `-` 对 INT_MIN 取负（tri-gen.c:570 `im=-fc`）与 45-trit 字宽的交界未测。
18. 方案 A 的 TSEXT/TPACK 类接入形态未在本次范围（当前 ANDI/ORI/XORI 逐 trit 语义被套件锁定，接入时两侧都要改）。
19. CONFIG_TCC_BCHECK 打开时 trirv 目标是否可链接（tri 无 bounds prolog/epilog 实现）——当前构建未开 bcheck，未验证。
20. tri-link.c:196-206 LUI_ABS 回填对负 addend（符号地址+负偏移）是否产生正确 hi13/lo7 借位，未数值验证。

---

## 8. 核实 corrections 应用记录（研究材料 → 本文的改写）

1. `_BitInt` 宽度**不经 expr_const64**——经 expr_const1（tccgen.c:5133）解析后由 `(int)vtop->c.i`（5143）读取；expr_const64 实际调用点=枚举 4824、case 8276/8279、choose_expr 6665、expr_const 7668。（§1.2 第 1/2 条已改写）
2. beyond-LUI-window 的可复现探针改为 `return 2000000000`/`return 2147483647`/`int x=2000000000`；`return 2000000000/1743392201/2147483647` 被折叠成 0 可编译，不作探针。（§2.2 D6 已改写）
3. `return 4<<2` 被前端折叠为 16（正确）；「4×3²=36」只在变量左操作数场景出现（`int s=10; int i=2; s<<i` → 90）。（§2.2 D7、§4.2 已改写）
4. 变参 gen_va_start 报错桩对 TriRV **不可达**；`__builtin_va_start` 编译通过、运行垃圾值 exit=128，无报错。（§2.3 已改写）
5. SLTIU 实际有使用（TRI_SEQZ_NOT）；零发射的是 SLTI 与常量比较融合用途。（§4.4 已改写）
6. tccgen.c:9404 在 PE/X86_64 条件内不编译；VLA 报错可达点为 9394 gen_vla_sp_save 与 9400 gen_vla_alloc。（§1.9 表已改写）
7. trirv 仓库存在 trirv_fuzz.py/trirv_fuzz_mem.py/trirv_fuzz_csr.py，但均为 PyVM×CVM **指令级**差分 fuzz，非 tri-tcc 编译器 fuzz——「T-3.3 编译器 fuzz 未启动」结论不变。（§0、§4.5 口径已注明）

---

## 9. 实施者速查：优先级最高的动作

1. **一行级错码速修**（§2.2 D1-D5、D8 + D7 门控）：`vrotb(i+1)`、比较归一表、SNEZ 反序、NE/EQ 用 TRI_SUB_* 宏、load() 补 `fc=(int)(int64_t)sv->c.i`、JALR rd≠rs1、变量移位先 tcc_error——每项配探针入 trirv/trirv_tri_tcc_e2e.py。
2. **常量装载**（D6）：负 i32 与窗口溢出走 load_seg 口径（取正+SUB）或 3×20 分段；lo 借位双向。
3. **数据 base-243 统一 + text 基址**（§1.6、§3.3）：init_putv/writer 统一编码、`text_section->sh_addr = 20` 删两处散落 +20、UNDEF/weak/COMMON 校验、8830/8787 两处 `|| defined TCC_TARGET_TRIRV`。
4. **方案 A 适配层**（§4.1/4.2）与 **9-tryte 对齐决策**（§5.1）按 gaps 排期。
