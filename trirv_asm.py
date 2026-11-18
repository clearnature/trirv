"""TriRV 汇编器 —— 两遍扫描：标签定义 + 助记符编码。

语法:
  label:              # 标签定义
  ADD  rd, rs1, rs2   # R-type
  ADDI rd, rs1, imm   # I-type (立即数可为负，或 -1 表示 3 补码)
  LW   rd, imm(rs1)   # load
  SW   rs2, imm(rs1)  # store
  BEQ  rs1, rs2, label# 分支 (PC 相对)
  JAL  rd, label      # 跳转
  LUI  rd, imm        # U-type
  ECALL               # 系统
"""
import re
import trirv_isa as isa

# D3: 27 个物理寄存器 (x0..x26, 3-trit 字段)。别名重排保留 t0-t6/a0-a7/s0-s6。
REG = {f"x{i}": i for i in range(27)}
REG.update({"zero":0,"ra":1,"sp":2,"gp":3,"tp":4,
            **{f"t{i}":5+i for i in range(3)},      # t0-t2 = 5,6,7
            **{f"s{i}":8+i for i in range(2)},      # s0/s1 = 8,9 (fp=s0)
            **{f"a{i}":10+i for i in range(8)},     # a0-a7 = 10..17
            **{f"t{i+3}":18+i for i in range(4)},   # t3-t6 = 18,19,20,21
            **{f"s{i+2}":22+i for i in range(5)}})  # s2-s6 = 22..26

def parse_int(tok, ntrits=7):
    """解析立即数为 ntrits-trit 的 3 补码位模式。
    I/S/B 型与 CSR 地址为 7-trit（默认）；U/J 型立即数为 13-trit，需显式传 ntrits=13。"""
    tok = tok.strip()
    mod = 3 ** ntrits
    if tok in isa.CSR:               # CSR 名（mstatus/mtvec/...）-> 地址
        return isa.CSR[tok] % mod
    if tok.startswith("-"):
        n = int(tok[1:], 0)
        return (mod - n) % mod       # 负立即数 -> ntrits-trit 3 补码
    return int(tok, 0) % mod

def strip_comment(line):
    return line.split("#", 1)[0].strip()

def first_pass(lines):
    labels, insts = {}, []
    for ln in lines:
        s = strip_comment(ln)
        if not s: continue
        m = re.match(r"^([A-Za-z_]\w*):\s*(.*)$", s)
        if m:
            labels[m.group(1)] = len(insts)
            s = m.group(2).strip()
            if not s: continue
        insts.append(s)
    return labels, insts

def assemble(src):
    labels, insts = first_pass(src.splitlines())
    out = []
    for idx, s in enumerate(insts):
        parts = re.split(r"\s+", s, maxsplit=1)
        mn = parts[0].upper()
        ops = [o.strip() for o in parts[1].split(",")] if len(parts) > 1 else []
        if mn not in isa.INST:
            raise ValueError(f"第 {idx} 行未知指令: {mn}")
        fmt = isa.INST[mn][3]

        def reg(t):
            t = t.strip()
            if t not in REG: raise ValueError(f"未知寄存器: {t}")
            return REG[t]

        if fmt == "R":
            if mn in ("TNEG","TNOT","TPACK","TUNPACK"):
                rd, rs1 = reg(ops[0]), reg(ops[1])
                rs2 = 0
            else:
                rd, rs1, rs2 = map(reg, ops)
            v = isa.encode(mn, rd=rd, rs1=rs1, rs2=rs2)
        elif fmt == "I":
            if mn in ("ECALL","EBREAK","FENCE","MRET","WFI"):
                v = isa.encode(mn)
            elif mn in ("CSRRWI","CSRRSI","CSRRCI"):
                # CSRRWI rd, uimm, csr  ->  uimm 放 rs1 字段, csr 放 imm 字段
                rd = reg(ops[0]); uimm = parse_int(ops[1]); csr = parse_int(ops[2])
                v = isa.encode(mn, rd=rd, rs1=uimm, imm=csr)
            elif mn in ("CSRRW","CSRRS","CSRRC"):
                rd = reg(ops[0]); rs1v = reg(ops[1]); csr = parse_int(ops[2])
                v = isa.encode(mn, rd=rd, rs1=rs1v, imm=csr)
            elif mn in ("LW","LH","LB","LBU","LHU","JALR"):
                rd = reg(ops[0])                 # load/jalr 的目标寄存器不能漏
                m = re.match(r"(-?\w+)\((\w+)\)", ops[1])
                imm = parse_int(m.group(1)); rs1 = reg(m.group(2))
                v = isa.encode(mn, rd=rd, rs1=rs1, imm=imm)
            else:
                rd = reg(ops[0]); rs1 = reg(ops[1]); imm = parse_int(ops[2])
                v = isa.encode(mn, rd=rd, rs1=rs1, imm=imm)
        elif fmt in ("S", "B"):
            if fmt == "S":
                rs2 = reg(ops[0]); m = re.match(r"(-?\w+)\((\w+)\)", ops[1])
                imm = parse_int(m.group(1)); rs1 = reg(m.group(2))
            else:  # B
                rs1 = reg(ops[0]); rs2 = reg(ops[1])
                tgt = ops[2].strip()
                imm = (labels[tgt] - idx) % (3**7) if tgt in labels else parse_int(tgt)
            v = isa.encode(mn, rs1=rs1, rs2=rs2, imm=imm)
        elif fmt in ("U", "J"):
            rd = reg(ops[0]); tgt = ops[1].strip()
            # U/J 立即数字段为 13 trit：字面量必须用 13-trit 解析，否则大常数被截到 7 trit。
            imm = (labels[tgt] - idx) % (3**13) if (fmt=="J" and tgt in labels) else parse_int(tgt, 13)
            v = isa.encode(mn, rd=rd, imm=imm)
        out.append(v)
    return out, labels
