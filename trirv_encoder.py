"""TriRV 指令编码器雏形 —— 验证 20-trit 字段布局与编码自洽性。

 trit 索引: t19(最高) ... t0(最低)。用 0/1/2 的非平衡三进制。
"""
W = 20  # 指令字长 trit

# 字段位置 (low, high)，含端点
FIELDS = {
    "opcode": (0, 2),
    "rd":     (3, 5),   # D3: 3-trit (0..26)，t6 空闲保持 0
    "funct":  (7, 8),
    "rs1":    (9, 11),  # D3: 3-trit，t12 空闲保持 0
    "rs2":    (13, 15), # D3: 3-trit，t16 空闲保持 0
    "funct2": (17, 19),
}

def check_layout():
    print("=== 字段布局检查 ===")
    covered = set()
    ok = True
    for name, (lo, hi) in FIELDS.items():
        bits = set(range(lo, hi + 1))
        overlap = bits & covered
        print(f"  {name:7s} [{hi}:{lo}]  宽度={hi-lo+1} trit  容量=3^{hi-lo+1}={3**(hi-lo+1)}"
              + (f"  ⚠️ 重叠 {overlap}" if overlap else ""))
        if overlap: ok = False
        covered |= bits
    missing = set(range(W)) - covered
    print(f"  覆盖 trit 数={len(covered)}/{W}" + (f"  未覆盖={missing}" if missing else "  ✓ 无重叠无遗漏"))
    return ok and not missing

def trit_string(n, width):
    """把整数 n 转成 width 位非平衡三进制字符串（低位在右）。"""
    digs = []
    for _ in range(width):
        digs.append(str(n % 3))
        n //= 3
    assert n == 0, f"值溢出 {width} trit"
    return "".join(reversed(digs))

def encode_R(opcode, funct2, rs2, rs1, funct, rd):
    v = 0
    v |= opcode << FIELDS["opcode"][0]
    v |= rd      << FIELDS["rd"][0]
    v |= funct   << FIELDS["funct"][0]
    v |= rs1     << FIELDS["rs1"][0]
    v |= rs2     << FIELDS["rs2"][0]
    v |= funct2  << FIELDS["funct2"][0]
    return v

def encode_I(opcode, imm7, rs1, funct, rd):
    # I-type: imm 占 t13..t19 (7 trit)，但字段表里 t13..16 是 rs2 区、t17..19 是 funct2 区
    # 按布局 I-type 把 imm[6:0] 放在 t13..t19，需重新解释这两个字段
    v = 0
    v |= opcode << 0
    v |= rd      << FIELDS["rd"][0]
    v |= funct   << FIELDS["funct"][0]
    v |= rs1     << FIELDS["rs1"][0]
    v |= imm7    << 13   # t13..t19
    return v

OPCODES = {
    "OP":0, "OP-IMM":1, "LOAD":2, "STORE":3, "BRANCH":4,
    "JAL":5, "JALR":6, "LUI":7, "AUIPC":8, "SYSTEM":9,
    "FENCE":10, "T-LOGIC":11, "T-PACK":12,
}

def show(name, value):
    ts = trit_string(value, W)
    print(f"  {name:24s} 值={value:<12d} trit[{ts}]  (t19..t0)")

if __name__ == "__main__":
    ok = check_layout()
    print()
    print("=== 示例指令机器码 ===")
    # ADD r5, r3, r2  → R-type, opcode=0(OP), funct2=0, funct=0(ADD), rd=5, rs1=3, rs2=2
    show("ADD x5, x3, x2", encode_R(OPCODES["OP"], 0, 2, 3, 0, 5))
    # TMAX x6, x1, x4 → R-type, opcode=11(T-LOGIC), funct=1(TMAX)
    show("TMAX x6, x1, x4", encode_R(OPCODES["T-LOGIC"], 0, 4, 1, 1, 6))
    # ADDI x10, x0, 5 → I-type, opcode=1, imm=5, rs1=0, funct=0, rd=10
    show("ADDI x10, x0, 5", encode_I(OPCODES["OP-IMM"], 5, 0, 0, 10))
    # 负立即数: ADDI x1, x0, -1 → 3 补码 7-trit: 3^7-1 = 2186
    show("ADDI x1, x0, -1", encode_I(OPCODES["OP-IMM"], 3**7 - 1, 0, 0, 1))
    print()
    print("布局自洽:", "✓ 通过" if ok else "✗ 失败")
