"""TriRV ISA —— 修正版编解码器。

关键修正：三进制字段按 3^lo 加权，不能用二进制 << 移位。
 trit 索引: t19(最高) ... t0(最低)，非平衡三进制 0/1/2。
"""
W = 20                      # 指令字长
MOD_W = 3 ** 20            # 指令模
NREG = 32                  # 物理寄存器数（D3 已撤销：4-trit 字段 0..31，对齐 RISC-V x0-x31）
XLEN = 45                  # 寄存器字长（45 trit = 9 tryte）
MOD_X = 3 ** XLEN
HALF_X = (MOD_X - 1) // 2  # 3 补码正数上限

# ---------------------------------------------------------------------------
# ISA 设计决策（ternary-isa-design.md §9 D1–D5）——2026-11-18 正式拍板并固化。
# 结论：D1/D2/D4/D5 维持现状；D3 于 2026-11-19 撤销——回到 32 寄存器 / 4-trit 字段，
# 以对齐 RISC-V x0-x31，为 LLVM 后端与 RISC-V 生态复用让路。
# 机读字段（word_trits/xlen/regs/i_imm_trits/u_imm_trits）受决策一致性测试守卫；
# 改动任一参数前必须先读 §9 理由，并同步更新本字典与文档。
# ---------------------------------------------------------------------------
ISA_DECISIONS = {
    "D1": {
        "topic": "指令字长",
        "word_trits": 20,
        "rejected": "21 trit",
        "reason": "3^21≈2^33.28 并非精确 32-bit；20 trit 恰好填满 4 tryte 容器。"
                  "改 21 需 5 tryte 容器、PC 从 4*i 变 5*i，编码/跳转/对齐/全部 golden 失效。",
    },
    "D2": {
        "topic": "符号扩展",
        "scheme": "3's-complement + explicit s(u(v))",
        "rejected": "ALU 内部平衡三进制 (-1,0,1)",
        "reason": "模 3^45 为奇数、3 补码范围对称，无 INT_MIN/-1 溢出；"
                  "s(u(v))/sign_extend_imm() 已被 ALU/访存/原子/CSR 四类差分 fuzz 验证，"
                  "改平衡三进制需重写两套 VM 算术核心且无正确性收益。",
    },
    "D3": {
        "topic": "寄存器字段",
        "regs": 32,
        "field_trits": 4,
        "applied": "2026-11-19: 27->32 寄存器, 字段 3->4 trit（撤销 11-18 的 27 寄存器方案）",
        "rejected": "27 寄存器 / 3-trit 字段：3^3=27 虽零浪费，但破坏 RISC-V x0-x31 ABI 与 LLVM 后端复用",
        "reason": "战略为'以开源 RISC-V 为规范标准、只换进制、不另建生态'，寄存器数必须与 x0-x31 对齐。"
                  "20 trit 恰好容纳 3+4+2+4+4+3：原空闲的 t6/t12/t16 并入 rd/rs1/rs2，指令字不扩长；"
                  "立即数布局完全不动（I 型仍 t13..t19，S/B 型 t3..t6+t17..t19，U/J 型 t7..t19），"
                  "故汇编器标签差值、编译器跳转逻辑零改动；别名表回到 RISC-V 标准编号。",
    },
    "D4": {
        "topic": "立即数位宽",
        "i_imm_trits": 7,
        "u_imm_trits": 13,
        "rejected": "加宽立即数字段",
        "reason": "大常数已由 LUI+ADDI（编译器 load_imm 用 3×15-trit 分段）覆盖全 45-trit 幅值，"
                  "并经全幅差分 fuzz 验证；加宽字段会重排全部编码格式。",
    },
    "D5": {
        "topic": "数据字长",
        "xlen": 45,
        "applied": "2026-11-18: 维持 45 trit（9 tryte）；曾试改 40 trit 后撤回",
        "rejected": "40 trit（为迎合 LP64 而改；三进制硬件无 64 位概念，该约束不成立）",
        "reason": "LP64 是宿主二进制 ABI 惯例，TriRV 是 3 补码三进制 ISA，硬件位宽本就不是 2 的幂；"
                  "long=pointer=45 trit 已同宽，无需追求 2 的幂字节宽度。"
                  "45 trit 方案已由 ALU/访存/原子/CSR 四类全幅差分 fuzz 与全部 e2e 验证。",
    },
}

# 字段 (lo, hi)
# D3 撤销（2026-11-19）：rd/rs1/rs2 回到 4 trit（32 寄存器），吃掉原先空闲的
# t6/t12/t16 三位，20 trit 恰好用满：3(opcode)+4(rd)+2(funct)+4(rs1)+4(rs2)+3(funct2)=20。
# 立即数布局完全不变：I 型 imm=t13..t19=rs2(4)+funct2(3)=7 trit；
# S/B 型 imm=t3..t6(复用无 rd 的位置)+t17..t19；U/J 型 imm=t7..t19。
F = {
    "opcode": (0, 2),
    "rd":     (3, 6),   # 4-trit (0..31)，原 t6 空闲位已并入
    "funct":  (7, 8),
    "rs1":    (9, 12),  # 4-trit，原 t12 空闲位已并入
    "rs2":    (13, 16), # 4-trit，原 t16 空闲位已并入
    "funct2": (17, 19),
}

OPCODE = {
    "OP":0, "OP-IMM":1, "LOAD":2, "STORE":3, "BRANCH":4,
    "JAL":5, "JALR":6, "LUI":7, "AUIPC":8, "SYSTEM":9,
    "FENCE":10, "T-LOGIC":11, "T-PACK":12,
    "ZICSR":16, "PRIV":17,          # Zicsr  CSR 读写 / 特权指令（三进制扩展）
    "M-EXT":13, "DIV-EXT":14, "REM-EXT":15,  # M 扩展（复用空闲 opcode，见 INST）
    "ATOMIC":18,                    # A 扩展：LR/SC + AMO（原子读改写）
}
# 常用 M 态 CSR 地址（沿用 RISC-V 编号，均 < 3^7=2187 可放入 imm7 字段）
CSR = {
    "mstatus":0x300, "mie":0x304, "mtvec":0x305, "mscratch":0x340,
    "mepc":0x341, "mcause":0x342, "mtval":0x343, "mip":0x344,
}
# mstatus 位布局（二进制控制位，存于 45-trit 字中）
MSTATUS_MIE  = 1 << 3      # 机器中断使能
MSTATUS_MPIE = 1 << 4      # 中断前的 MIE
MSTATUS_MPP_SHIFT = 11     # 中断前特权级 (bits 11-12)
CAUSE_ILLEGAL = 2          # 非法指令
CAUSE_ECALL_U = 11         # 来自 U 态的环境调用
CAUSE_MTIMER  = (1 << 63) | 7   # 机器定时器中断（RISC-V 风格最高位置 1）
OPCODE_NAME = {v: k for k, v in OPCODE.items()}

# 指令 -> (opcode, funct, funct2, 格式)
INST = {
    # R-type: OP
    "ADD":(0,0,0,"R"), "SUB":(0,0,1,"R"), "SLL":(0,1,0,"R"),
    "SLT":(0,1,1,"R"), "SLTU":(0,1,2,"R"), "XOR":(0,2,0,"R"),
    "SRL":(0,2,1,"R"), "SRA":(0,2,2,"R"), "OR":(0,3,0,"R"), "AND":(0,3,1,"R"),
    # M 扩展：高低位乘法 + 无符号除法（复用 opcode 13/14/15 的 funct 变体）
    # funct 为 2-trit(0..8)，funct2 为 3-trit(0..26)，组合唯一即可
    "MUL":(13,0,0,"R"), "MULH":(13,1,0,"R"), "MULHSU":(13,2,0,"R"), "MULHU":(13,3,0,"R"),
    "DIV":(14,0,0,"R"), "DIVU":(14,1,0,"R"),
    "REM":(15,0,0,"R"), "REMU":(15,1,0,"R"),
    # I-type: OP-IMM
    "ADDI":(1,0,0,"I"), "SLTI":(1,1,0,"I"), "SLTIU":(1,5,0,"I"),
    "XORI":(1,2,0,"I"), "ORI":(1,3,0,"I"), "ANDI":(1,6,0,"I"),
    "SLLI":(1,4,0,"I"), "SRLI":(1,7,0,"I"), "SRAI":(1,8,0,"I"),
    # I-type: LOAD
    "LB":(2,0,0,"I"), "LH":(2,1,0,"I"), "LW":(2,2,0,"I"),
    "LBU":(2,3,0,"I"), "LHU":(2,4,0,"I"),
    # S-type: STORE
    "SB":(3,0,0,"S"), "SH":(3,1,0,"S"), "SW":(3,2,0,"S"),
    # B-type: BRANCH
    "BEQ":(4,0,0,"B"), "BNE":(4,1,0,"B"), "BLT":(4,2,0,"B"),
    "BGE":(4,3,0,"B"), "BLTU":(4,4,0,"B"), "BGEU":(4,5,0,"B"),
    # J/U/I/SYSTEM
    "JAL":(5,0,0,"J"), "JALR":(6,0,0,"I"), "LUI":(7,0,0,"U"),
    # SYSTEM: ECALL/EBREAK 用 funct 区分（funct2 区 t17..t19 与 I-type imm 高位重叠，
    # 不能用来区分 I-type 指令，否则 EBREAK 永远解成 ECALL）。funct 字段 t7..t8 空闲可用。
    "AUIPC":(8,0,0,"U"), "ECALL":(9,0,0,"I"), "EBREAK":(9,1,0,"I"),
    "FENCE":(10,0,0,"I"),
    # T-LOGIC (R-type)
    "TMIN":(11,0,0,"R"), "TMAX":(11,0,1,"R"), "TNOT":(11,0,2,"R"),
    "TAND":(11,1,0,"R"), "TOR":(11,1,1,"R"), "TNEG":(11,1,2,"R"),
    # TSEXT: 单操作数（rs1），funct=2；把 rs1 低 5 trit(1 tryte) 按 3 补码符号扩展到 45 trit
    "TSEXT":(11,2,0,"R"),
    "TPACK":(12,0,0,"R"), "TUNPACK":(12,0,1,"R"),
    # ZICSR: I-type，csr 号编码在 imm[6:0] 字段，rs1 字段为源寄存器（读/写值）或立即数
    "CSRRW":(16,0,0,"I"), "CSRRS":(16,1,0,"I"), "CSRRC":(16,2,0,"I"),
    "CSRRWI":(16,3,0,"I"), "CSRRSI":(16,4,0,"I"), "CSRRCI":(16,5,0,"I"),
    # PRIV: I-type，无有效操作数（编码占位），trap 返回与等待中断
    "MRET":(17,0,0,"I"), "WFI":(17,1,0,"I"),
    # A 扩展 (R-type)：rs1=地址，rs2=源值，rd=旧值（LR 的 rs2 忽略，SC 的 rd=成功标志）
    # funct=0: 保留集指令；funct=1: AMO 读改写
    "LRW":(18,0,0,"R"), "SCW":(18,0,1,"R"),
    "AMOADD":(18,1,0,"R"), "AMOSWAP":(18,1,1,"R"), "AMOAND":(18,1,2,"R"),
    "AMOOR":(18,1,3,"R"), "AMOXOR":(18,1,4,"R"), "AMOMIN":(18,1,5,"R"), "AMOMAX":(18,1,6,"R"),
}

def get_field(v, lo, hi):
    return (v // (3 ** lo)) % (3 ** (hi - lo + 1))

def put_field(v, lo, hi, x):
    """把字段 x 放入 [lo,hi]；该区间须为 0。按 3^lo 加法叠加。"""
    w = hi - lo + 1
    assert 0 <= x < 3 ** w, f"字段 x={x} 超出 {w} trit (max {3**w-1})"
    assert get_field(v, lo, hi) == 0, f"目标字段 [{hi}:{lo}] 区间非零"
    return v + x * (3 ** lo)

def trit_str(v, width=W):
    ds = []
    for _ in range(width):
        ds.append(str(v % 3)); v //= 3
    assert v == 0, "值溢出 width trit"
    return "".join(reversed(ds))

def to_signed_word(v):
    v %= MOD_X
    return v if v <= HALF_X else v - MOD_X

def from_signed_word(sv):
    return sv % MOD_X

def sign_extend_imm(imm, ntrits=7):
    """n-trit 立即数符号扩展到 45-trit 字（3 补码）。"""
    mod = 3 ** ntrits
    half = (mod - 1) // 2
    sv = imm if imm <= half else imm - mod
    return from_signed_word(sv)

# ---------- 编码 ----------
def enc_R(op, funct2, rs2, rs1, funct, rd):
    v = 0
    for key, val in [("opcode",op),("rd",rd),("funct",funct),
                     ("rs1",rs1),("rs2",rs2),("funct2",funct2)]:
        lo, hi = F[key]; v = put_field(v, lo, hi, val)
    return v

def enc_I(op, imm7, rs1, funct, rd):
    v = 0
    for key, val in [("opcode",op),("rd",rd),("funct",funct),("rs1",rs1)]:
        lo, hi = F[key]; v = put_field(v, lo, hi, val)
    # I-type imm[6:0] 占 t13..t19 (7 trit)
    v = put_field(v, 13, 19, imm7 % (3**7))
    return v

def enc_S(op, imm7, rs2, rs1, funct):
    v = 0
    for key, val in [("opcode",op),("funct",funct),("rs1",rs1),("rs2",rs2)]:
        lo, hi = F[key]; v = put_field(v, lo, hi, val)
    lo_i = imm7 % (3**4)          # imm[3:0] -> t3..t6 (rd 位置)
    hi_i = imm7 // (3**4)         # imm[6:4] -> t17..t19
    v = put_field(v, 3, 6, lo_i)
    v = put_field(v, 17, 19, hi_i)
    return v

def enc_U(op, imm13, rd):
    v = 0
    for key, val in [("opcode",op),("rd",rd)]:
        lo, hi = F[key]; v = put_field(v, lo, hi, val)
    v = put_field(v, 7, 19, imm13 % (3**13))
    return v

def encode(name, rd=0, rs1=0, rs2=0, imm=0):
    op, funct, funct2, fmt = INST[name]
    if fmt == "R":
        return enc_R(op, funct2, rs2, rs1, funct, rd)
    if fmt == "I":
        return enc_I(op, imm % (3**7), rs1, funct, rd)
    if fmt in ("S", "B"):
        return enc_S(op, imm % (3**7), rs2, rs1, funct)
    if fmt in ("U", "J"):
        return enc_U(op, imm % (3**13), rd)
    raise ValueError(fmt)

def decode(inst):
    op = get_field(inst, 0, 2)
    name = None
    for k, (o, f, f2, fmt) in INST.items():
        if o != op: continue
        # U/J 格式没有 funct 字段（t7..t19 全是立即数），直接匹配
        if fmt in ("U", "J"):
            name = k; break
        fc = get_field(inst, 7, 8)
        if f != fc: continue
        if fmt == "R" and f2 != get_field(inst, 17, 19):
            continue
        name = k; break
    def _f(key):
        lo, hi = F[key]
        return get_field(inst, lo, hi)
    d = {
        "raw": inst, "trits": trit_str(inst), "opcode": op,
        "opname": OPCODE_NAME.get(op, "?"), "inst": name,
        "rd": _f("rd"), "funct": _f("funct"), "rs1": _f("rs1"),
        "rs2": _f("rs2"), "funct2": _f("funct2"),
    }
    if name and INST[name][3] in ("U", "J"):
        d["imm"] = get_field(inst, 7, 19)          # U/J: imm 在 t7..t19
    elif name and INST[name][3] == "I":
        d["imm"] = get_field(inst, 13, 19)         # I: imm 在 t13..t19
    elif name and INST[name][3] in ("S", "B"):
        lo = get_field(inst, 3, 6)                  # S/B: imm 分散在 t3..t6 与 t17..t19
        hi = get_field(inst, 17, 19)
        d["imm"] = hi * (3**4) + lo
    return d
