#!/usr/bin/env python3
"""gen_tri_isa_h —— 从 trirv_isa.py（单一真相源）生成 tinycc/tri-isa.h。

T-1.8 ISA 一致性守卫（TINYCC_PORT_PLAN.md D-T4）：
    python3 gen_tri_isa_h.py            # 重写 ../tinycc/tri-isa.h
    python3 gen_tri_isa_h.py --check    # 只比对：不一致 exit 1（回归门控）

背景：tri-isa.h 头注释自称"自动生成"，但生成脚本此前并不存在（笔记 §4.5：
声称不可复核、trirv_isa.py 演进后漂移无人发现）。本脚本补上生成/校验闭环。
文件分两区：指令宏区（INST 驱动，本脚本生成）与固定区（ENC 组装宏/寄存器/
补丁表，verbatim 内嵌——手改这两区请同步本脚本）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import trirv_isa as isa  # noqa: E402

OUT = os.path.normpath(os.path.join(HERE, "..", "tinycc", "tri-isa.h"))

# 字段权重与边界常量（20-trit 指令字，权重 = 3^lo）
WEIGHTS = [
    ("TRI_W_OP", 1), ("TRI_W_RD", 27), ("TRI_W_FUNCT", 2187),
    ("TRI_W_RS1", 19683), ("TRI_W_RS2", 1594323),
    ("TRI_W_FUNCT2", 129140163), ("TRI_W_IMM13", 2187), ("TRI_W_IMM7", 1594323),
]

# 固定区（与生成无关，逐字保持）
FIXED_ENC = """\
/* 组装宏（r = 原始值 mod 3^20，u32 直发） */
#define TRI_ENC_R(op,f2,rs2,rs1,f,rd)  ((op) + (rd)*TRI_W_RD + (f)*TRI_W_FUNCT + (rs1)*TRI_W_RS1 + (rs2)*TRI_W_RS2 + (f2)*TRI_W_FUNCT2)
#define TRI_ENC_I(op,imm7,rs1,f,rd)    ((op) + (rd)*TRI_W_RD + (f)*TRI_W_FUNCT + (rs1)*TRI_W_RS1 + (((int)((imm7)%2187)+2187)%2187)*TRI_W_IMM7)
#define TRI_ENC_S(op,imm7,rs2,rs1,f)   ((op) + (f)*TRI_W_FUNCT + (rs1)*TRI_W_RS1 + (rs2)*TRI_W_RS2 + ((((int)((imm7)%2187)+2187)%2187)/81u)*TRI_W_FUNCT2 + ((((int)((imm7)%81)+81)%81))*TRI_W_RD)
#define TRI_ENC_U(op,imm13,rd)         ((op) + (rd)*TRI_W_RD + (((int)((imm13)%1594323)+1594323)%1594323)*TRI_W_IMM13)
"""

FIXED_TAIL = """\
#define TRI_IMM7_MAX 1093       /* (3^7-1)/2 */
#define TRI_IMM13_MAX 797161    /* (3^13-1)/2 */

/* ---- 符号地址补丁表（tri-gen 定义 / tri-link 消费） ---- */
enum { TRI_R_LUI_ABS = 1, TRI_R_JAL_SLOT = 2 };
struct tri_patch { int text_off; int kind; Sym *sym; int addend; unsigned rd; };
/* 声明：ST_DATA 宏按 ONE_SOURCE 自动切 static/extern；定义在 tri-gen.c
   （C tentative definition 规则下 `ST_DATA int NTP;` 即定义） */
ST_DATA struct tri_patch *TRI_PATCHES;
ST_DATA int NTP;

#endif /* TRI_ISA_H */
"""

REGS = ["ZERO", "RA", "SP", "GP", "TP", "T0", "T1", "T2", "S0", "S1",
        "A0", "A1", "A2", "A3", "A4", "A5", "A6", "A7"]


def opcode_c(name):
    return name.replace("-", "")


def inst_c(name):
    return name.replace(".", "_")


def generate():
    L = []
    A = L.append
    A("/* tri-isa.h —— 由 trirv_isa.py 自动生成（单一真相源，勿手改）。")
    A(" * 生成脚本: gen_tri_isa_h.py（trirv 仓库）--check 模式作 T-1.8 一致性守卫；字段权重按 3^lo 三进制加权。 */")
    A("#ifndef TRI_ISA_H")
    A("#define TRI_ISA_H")
    A("")
    A("#define TRI_MOD_W %du      /* 3^20 */" % isa.MOD_W)
    # 核心 opcode（值 0..9，前端/后端直接引用）导出 TRI_OP_*；其余 opcode 经
    # 各指令的 TRI_<助记符>_OP 引用
    for name, val in isa.OPCODE.items():
        if val < 10:
            A("#define TRI_OP_%s %du" % (opcode_c(name), val))
    A("")
    A("/* 助记符 -> (opcode, funct, funct2, 格式)  格式: 0=R 1=I 2=S 3=B 4=U 5=J */")
    A("enum { TRI_FMT_R=0, TRI_FMT_I, TRI_FMT_S, TRI_FMT_B, TRI_FMT_U, TRI_FMT_J };")
    A("")
    A("/* 字段权重（3 的幂，u32 常量） */")
    for nm, w in WEIGHTS:
        if nm == "TRI_W_IMM7":
            A("#define TRI_W_IMM7  1594323u")   # 原文件对齐：名后双空格
        else:
            A("#define %s %du" % (nm, w))
    A("")
    for mn, (op, funct, funct2, fmt) in isa.INST.items():
        c = inst_c(mn)
        A("#define TRI_%s_OP %du" % (c, op))
        A("#define TRI_%s_FUNCT %du" % (c, funct))
        A("#define TRI_%s_FUNCT2 %du" % (c, funct2))
        A("#define TRI_%s_FMT TRI_FMT_%s" % (c, fmt))
    A("")
    L.extend(FIXED_ENC.rstrip("\n").split("\n"))
    A("")
    A("/* 寄存器号（与 trirv_asm.py 别名表一致，x0-x31） */")
    for r in REGS:
        num = isa.NREG and {"ZERO": 0, "RA": 1, "SP": 2, "GP": 3, "TP": 4,
                            "T0": 5, "T1": 6, "T2": 7, "S0": 8, "S1": 9,
                            "A0": 10, "A1": 11, "A2": 12, "A3": 13, "A4": 14,
                            "A5": 15, "A6": 16, "A7": 17}[r]
        A("#define TRI_%s %du" % (r, num))
    A("")
    L.extend(FIXED_TAIL.rstrip("\n").split("\n"))
    return "\n".join(L) + "\n"


def main():
    check = "--check" in sys.argv
    content = generate()
    old = open(OUT).read() if os.path.exists(OUT) else ""
    if check:
        if content == old:
            print("T-1.8 ISA 守卫：tri-isa.h 与 trirv_isa.py 逐字一致（%d 条指令）" % len(isa.INST))
            return
        import difflib
        diff = list(difflib.unified_diff(old.splitlines(), content.splitlines(),
                                         "tri-isa.h(现存)", "trirv_isa.py(生成)", lineterm=""))
        print("T-1.8 ISA 守卫失败：tri-isa.h 与 trirv_isa.py 漂移（%d 行差异）" %
              sum(1 for d in diff if d[:1] in "+-" and d[:3] not in ("+++", "---")))
        print("\n".join(diff[:60]))
        sys.exit(1)
    with open(OUT, "w") as f:
        f.write(content)
    print("已生成 %s（%d 条指令）" % (OUT, len(isa.INST)))


if __name__ == "__main__":
    main()
