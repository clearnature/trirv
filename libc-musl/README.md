# TriRV —— 三进制 RISC-V 风格软核全栈

45-trit 三进制 ISA（102 条指令）+ 双模拟器（PyVM 参考 / C VM 性能）+ 自研 C 编译器
（trirv_cc，寄存器式，~95%）+ TinyCC 移植（tri-tcc，正式工具链路线）+ musl 移植 +
wasm 前端 + 三进制浮点（TF45/TF20）。

## 目录结构（2026-11-22 重组，模块隔离）

| 目录 | 内容 |
|---|---|
| `isa/` | **ISA 真相源**：trirv_isa.py（97 条 encode/decode）、trirv_asm.py（汇编器）、gen_tri_isa_h.py（tri-isa.h 生成器 + T-1.8 守卫）、test_isa_completeness.py |
| `vm/` | **模拟器**：trirv_vm.py（PyVM 参考）、trirv_vm.c（C VM 性能实现）；`bin/` 为构建产物 |
| `cc/` | **自研 C 编译器**：trirv_cc.c（寄存器式单遍）、trirv_cc_backend.py（Python 参考后端）、trirv_crt.c/trirv_stdio.c/trirv_libc.c（内置 libc）、trirv_busybox.c/trirv_firmware.c（应用）；`bin/` 为构建产物 |
| `linker/` | 链接器（预留：trirv_cc 内置 TRVO 导入待独立化） |
| `debugger/` | 调试器（预留：PyVM trace 是内置功能，独立调试器待建） |
| `float/` | 三进制浮点：trirv_float.py（TF45/TF20 编解码+运算）、trirv_float.h（C VM 侧） |
| `wasm/` | wasm 前端（trirv_wasm.py，f32/f64 编译期穿透） |
| `libc-musl/` | musl 1.2.5 移植：musl_tri/（三进制原生重写 16 文件）、preprocess_musl.py、batch_musl_compile.py（trirv_cc 管线 65/66）、batch_musl_tri_tcc.py（tri-tcc 管线） |
| `libc-musl/musl_tri/arch_tri/` | **TriRV arch 层**（2026-11-23）：syscall_tri.h（read/write→read8/write8 路由，musl 唯一 syscall 对接面）+ atomic_tri.h（a_cas/a_swap/a_fetch_add/a_and/a_or/a_ctz_64/a_barrier 纯 C 化，单核口径）——上游 riscv64 arch 层全内联汇编，本层纯 C 重写；契约 trirv_arch_e2e 12 项 |
| `tests/` | 契约套件 47 个（e2e/差分/fuzz），run_all_tests.py 聚合（--fast 43 套） |
| `bench/` | 性能基准（bitops/div/float/wasm + perf 系列） |
| `tools/` | 工具：export_trv.py（TRV1 导出）、build.sh、trirv_encoder.py、trirv_difftest.py、trirv_adiff.py |
| `docs/` | 规范与计划：ternary-isa-design.md（ISA 规范）、ternary-abi-minimal.md、TERNARY_FLOAT_PROPOSAL.md、TERNARY_COMPAT_PLAN.md、TINYCC_PORT_PLAN.md、TCC_BEHAVIOR_NOTES.md 等 |
| `tinycc/` | TinyCC fork（独立 git 仓库，trirv 分支：tri-gen.c/tri-link.c/tri-isa.h） |
| `third_party/musl/` | musl 1.2.5 上游（移植基线） |

## 快速开始

```bash
bash tools/build.sh                      # 构建全部二进制（vm/bin、cc/bin）
python3 tests/run_all_tests.py --fast    # 全量回归（43 套）
```

## 模块完善度（2026-11-23）

- 模拟器 / 汇编器：✅（102 条指令、双 VM 差分、CSR/原子/浮点/LW4-SW4/LIT 系全）
- 自研编译器 trirv_cc（C 双后端）：🟢（musl 65/66；未声明标识符诚实报错；
  __tint/__tint2/__tbool/__builtin_tri_lit/switch/typedef/goto/do-while/
  三目/强转/const 全落地；fn-ptr 局部/全局/传参）
- tri-tcc（TinyCC 移植）：🟢（T-1 全条目；for+指针写已销案；TINT C1/C2
  收官——__tint/__tint2/__tbool 全量；__tri_syscall 对接面；typedef 数组别名）
- 链接器：🟢 主体完成（TRVO 导入、61 单元去重增量链接、数据/字符串地址
  符号化、弱符号别名、TRVO 闭包转发导出、内存映射纪律检查）
- 调试器：🟡 MVP（debugger/trirv_dbg.py 契约 18 项入册；独立 GUI 待建）
- 任务队列：docs/TASKS.md（动态台账）

详见 `docs/TINYCC_PORT_PLAN.md`（工具链路线）、`docs/TERNARY_COMPAT_PLAN.md`
（多进制兼容）与 `docs/TASKS.md`（任务台账）。
