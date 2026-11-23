# 指令覆盖矩阵（批次 1.11 快照，2026-11-23）
状态：快照（批次 1.11 补缺前；重跑 tools/cov_matrix.py 取最新）。

> 静态扫描：助记符出现在 tests/*.py、libc-musl/musl_tri/*.c、crt/firmware 源中即记命中。

> **口径**：静态零覆盖 ≠ 执行零覆盖——编译器发射型指令（ADD/LW/SW…）由 C 端到端
(crt_e2e 29 项)、musl(48 项)、wasm(112 例)、fuzz(3300 随机指令) 大面积覆盖；
> 手算黄金零覆盖由 tests/trirv_golden_e2e.py 补齐（1.12）。重跑：`python3 tools/cov_matrix.py`

## 静态零覆盖 18 条 → 1.12 手算补缺（trirv_golden_e2e 26 项）



## 全矩阵

| 指令 | 命中源数 | 主要命中 |
|---|---|---|
| ADD | 12 | strtod_tri.c,trirv_csr_diff.py,trirv_csr_e2e.py,trirv_e2e.py |
| ADDI | 21 | trirv_bitview_e2e.py,trirv_compact_e2e.py,trirv_crt.c,trirv_csr_diff.py |
| AMOADD | 1 | trirv_fuzz_mem.py |
| AMOAND | 1 | trirv_fuzz_mem.py |
| AMOMAX | 1 | trirv_fuzz_mem.py |
| AMOMIN | 1 | trirv_fuzz_mem.py |
| AMOOR | 1 | trirv_fuzz_mem.py |
| AMOSWAP | 1 | trirv_fuzz_mem.py |
| AMOXOR | 1 | trirv_fuzz_mem.py |
| AND | 6 | run_all_tests.py,trirv_cc_py_bitop_e2e.py,trirv_e2e.py,trirv_fuzz.py |
| ANDI | 1 | trirv_fuzz.py |
| AUIPC | 1 | trirv_golden_e2e.py |
| BEQ | 4 | trirv_compact_e2e.py,trirv_switch_e2e.py,trirv_tcjump_e2e.py,trirv_trv2_e2e.py |
| BGE | 1 | trirv_golden_e2e.py |
| BGEU | 1 | trirv_golden_e2e.py |
| BLT | 2 | trirv_golden_e2e.py,trirv_tri_tcc_e2e.py |
| BLTU | 1 | trirv_golden_e2e.py |
| BNE | 4 | trirv_compact_e2e.py,trirv_e2e.py,trirv_tdd_contract.py,trirv_tritref_test.py |
| CSRRC | 3 | trirv_csr_diff.py,trirv_csr_e2e.py,trirv_fuzz_csr.py |
| CSRRC.H | 1 | trirv_golden_e2e.py |
| CSRRCI | 1 | trirv_fuzz_csr.py |
| CSRRCI.H | 1 | trirv_golden_e2e.py |
| CSRRS | 3 | trirv_csr_diff.py,trirv_csr_e2e.py,trirv_fuzz_csr.py |
| CSRRS.H | 1 | trirv_golden_e2e.py |
| CSRRSI | 1 | trirv_fuzz_csr.py |
| CSRRSI.H | 1 | trirv_golden_e2e.py |
| CSRRW | 4 | trirv_csr_diff.py,trirv_csr_e2e.py,trirv_fuzz_csr.py,trirv_tdd_contract.py |
| CSRRW.H | 1 | trirv_golden_e2e.py |
| CSRRWI | 1 | trirv_fuzz_csr.py |
| CSRRWI.H | 1 | trirv_golden_e2e.py |
| DIV | 3 | div_tri.c,trirv_fuzz.py,trirv_tritref_test.py |
| DIVU | 6 | trirv_bitview_e2e.py,trirv_fmt_e2e.py,trirv_fuzz.py,trirv_mdiff.py |
| EBREAK | 1 | trirv_golden_e2e.py |
| ECALL | 18 | trirv_crt.c,trirv_csr_diff.py,trirv_csr_e2e.py,trirv_e2e.py |
| FADD | 1 | trirv_float_isa_e2e.py |
| FADD.S | 1 | trirv_float_isa_e2e.py |
| FCVT.F | 1 | trirv_golden_e2e.py |
| FCVT.W | 2 | trirv_golden_e2e.py,trirv_tri_tcc_e2e.py |
| FCVT.W.T | 1 | trirv_tri_tcc_e2e.py |
| FDIV | 1 | trirv_float_isa_e2e.py |
| FDIV.S | 1 | trirv_float_isa_e2e.py |
| FENCE | 1 | trirv_golden_e2e.py |
| FENCE.I | 1 | trirv_golden_e2e.py |
| FEQ | 2 | trirv_float_isa_e2e.py,trirv_golden_e2e.py |
| FEQ.S | 1 | trirv_golden_e2e.py |
| FLE | 2 | trirv_float_isa_e2e.py,trirv_golden_e2e.py |
| FLE.S | 1 | trirv_golden_e2e.py |
| FLT | 2 | trirv_float_isa_e2e.py,trirv_golden_e2e.py |
| FLT.S | 1 | trirv_golden_e2e.py |
| FLW | 2 | trirv_float_isa_e2e.py,trirv_golden_e2e.py |
| FLW.S | 2 | trirv_float_isa_e2e.py,trirv_golden_e2e.py |
| FMUL | 1 | trirv_float_isa_e2e.py |
| FMUL.S | 1 | trirv_float_isa_e2e.py |
| FSQRT | 1 | trirv_float_isa_e2e.py |
| FSQRT.S | 1 | trirv_float_isa_e2e.py |
| FSUB | 1 | trirv_float_isa_e2e.py |
| FSUB.S | 1 | trirv_float_isa_e2e.py |
| FSW | 1 | trirv_float_isa_e2e.py |
| FSW.S | 1 | trirv_float_isa_e2e.py |
| JAL | 5 | trirv_compact_e2e.py,trirv_crt.c,trirv_tcjump_e2e.py,trirv_tdd_contract.py |
| JALR | 4 | trirv_cc_py_fnptr_e2e.py,trirv_tcc_syscall_e2e.py,trirv_tri_tcc_e2e.py,trirv_trv2_e2e.py |
| LB | 5 | trirv_charsign_e2e.py,trirv_fuzz_mem.py,trirv_mem4_e2e.py,trirv_tri_tcc_e2e.py |
| LBU | 4 | trirv_charsign_e2e.py,trirv_fuzz_mem.py,trirv_tri_tcc_e2e.py,trirv_tritref_test.py |
| LH | 5 | trirv_charsign_e2e.py,trirv_fuzz_mem.py,trirv_mem4_e2e.py,trirv_tri_tcc_e2e.py |
| LHU | 3 | trirv_charsign_e2e.py,trirv_fuzz_mem.py,trirv_tri_tcc_e2e.py |
| LIT0 | 2 | trirv_tlit_e2e.py,trirv_tri_tcc_e2e.py |
| LIT1 | 2 | trirv_tlit_e2e.py,trirv_tri_tcc_e2e.py |
| LIT2 | 2 | trirv_tlit_e2e.py,trirv_tri_tcc_e2e.py |
| LRW | 1 | trirv_fuzz_mem.py |
| LUI | 7 | trirv_fuzz.py,trirv_fuzz_csr.py,trirv_fuzz_mem.py,trirv_golden_e2e.py |
| LW | 8 | trirv_charsign_e2e.py,trirv_compact_e2e.py,trirv_fuzz_mem.py,trirv_malloc_e2e.py |
| LW4 | 4 | run_all_tests.py,trirv_cc_e2e.py,trirv_mem4_e2e.py,trirv_tri_tcc_e2e.py |
| MRET | 3 | trirv_csr_diff.py,trirv_csr_e2e.py,trirv_fuzz_csr.py |
| MUL | 4 | strtod_tri.c,trirv_fuzz.py,trirv_tritref_test.py,trirv_wasm_e2e.py |
| MULH | 3 | trirv_fuzz.py,trirv_mdiff.py,trirv_tritref_test.py |
| MULHSU | 3 | trirv_fuzz.py,trirv_mdiff.py,trirv_tritref_test.py |
| MULHU | 3 | trirv_fuzz.py,trirv_mdiff.py,trirv_tritref_test.py |
| OR | 6 | run_all_tests.py,trirv_cc_py_bitop_e2e.py,trirv_e2e.py,trirv_fuzz.py |
| ORI | 1 | trirv_fuzz.py |
| REM | 3 | div_tri.c,trirv_fuzz.py,trirv_tritref_test.py |
| REMU | 5 | trirv_fmt_e2e.py,trirv_fuzz.py,trirv_mdiff.py,trirv_tritref_test.py |
| SB | 5 | trirv_charsign_e2e.py,trirv_fuzz_mem.py,trirv_mem4_e2e.py,trirv_tri_tcc_e2e.py |
| SCW | 1 | trirv_fuzz_mem.py |
| SH | 2 | trirv_fuzz_mem.py,trirv_mem4_e2e.py |
| SLL | 8 | trirv_bitview_e2e.py,trirv_cc_e2e.py,trirv_cc_py_bitop_e2e.py,trirv_fuzz.py |
| SLLI | 8 | trirv_bitview_e2e.py,trirv_fuzz.py,trirv_fuzz_csr.py,trirv_fuzz_mem.py |
| SLT | 3 | trirv_charsign_e2e.py,trirv_fuzz.py,trirv_tritref_test.py |
| SLTI | 2 | trirv_fuzz.py,trirv_tcjump_e2e.py |
| SLTIU | 1 | trirv_fuzz.py |
| SLTU | 4 | trirv_charsign_e2e.py,trirv_fmt_e2e.py,trirv_fuzz.py,trirv_tritref_test.py |
| SRA | 7 | trirv_bitview_e2e.py,trirv_cc_e2e.py,trirv_cc_py_bitop_e2e.py,trirv_fuzz.py |
| SRAI | 3 | trirv_bitview_e2e.py,trirv_fuzz.py,trirv_tri_tcc_e2e.py |
| SRL | 8 | trirv_bitview_e2e.py,trirv_cc_e2e.py,trirv_cc_py_bitop_e2e.py,trirv_fuzz.py |
| SRLI | 4 | trirv_bitview_e2e.py,trirv_fuzz.py,trirv_tdd_contract.py,trirv_tri_tcc_e2e.py |
| SUB | 5 | trirv_fuzz.py,trirv_golden_e2e.py,trirv_tlit_e2e.py,trirv_tritref_test.py |
| SW | 10 | trirv_compact_e2e.py,trirv_fuzz.py,trirv_fuzz_csr.py,trirv_fuzz_mem.py |
| SW4 | 4 | run_all_tests.py,trirv_cc_e2e.py,trirv_mem4_e2e.py,trirv_tri_tcc_e2e.py |
| TAND | 7 | trirv_bitview_e2e.py,trirv_cc_e2e.py,trirv_difftest_tcc.py,trirv_e2e.py |
| TMAX | 4 | trirv_bitview_e2e.py,trirv_e2e.py,trirv_fuzz.py,trirv_tritlogic_e2e.py |
| TMIN | 3 | trirv_bitview_e2e.py,trirv_fuzz.py,trirv_tritlogic_e2e.py |
| TNEG | 2 | trirv_e2e.py,trirv_fuzz.py |
| TNOT | 7 | trirv_bitview_e2e.py,trirv_cc_e2e.py,trirv_cc_py_bitop_e2e.py,trirv_fuzz.py |
| TOR | 6 | trirv_bitview_e2e.py,trirv_cc_e2e.py,trirv_cc_py_bitop_e2e.py,trirv_fuzz.py |
| TPACK | 2 | trirv_e2e.py,trirv_fuzz.py |
| TSEXT | 1 | trirv_fuzz.py |
| TUNPACK | 2 | trirv_e2e.py,trirv_fuzz.py |
| WFI | 1 | trirv_golden_e2e.py |
| XOR | 11 | run_all_tests.py,trirv_bitview_e2e.py,trirv_cc_e2e.py,trirv_cc_py_bitop_e2e.py |
| XORI | 1 | trirv_fuzz.py |
