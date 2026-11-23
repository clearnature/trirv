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

# 32 个物理寄存器（x0..x31，4-trit 字段），别名**严格对齐 RISC-V 标准 ABI**，
# 以便直接复用 RISC-V 的汇编语法、ABI 文档与 LLVM 后端。
REG = {f"x{i}": i for i in range(32)}
REG.update({"zero":0,"ra":1,"sp":2,"gp":3,"tp":4,
            **{f"t{i}":5+i for i in range(3)},      # t0-t2  = 5,6,7
            **{f"s{i}":8+i for i in range(2)},      # s0/s1  = 8,9
            **{f"a{i}":10+i for i in range(8)},     # a0-a7  = 10..17
            **{f"s{i+2}":18+i for i in range(10)},  # s2-s11 = 18..27
            **{f"t{i+3}":28+i for i in range(4)}})  # t3-t6  = 28..31
REG["fp"] = 8
# F 扩展（P2/P3）：f0..f31 是独立 freg 堆，字段编号与整数堆复用 0..31，
# 靠指令 opcode（19/20/21）区分语义——所以汇编层映射同一个整数即可。
REG.update({f"f{i}": i for i in range(32)})

def parse_int(tok, ntrits=7):
    """解析立即数为 ntrits-trit 的 3 补码位模式。
    I/S/B 型与 CSR 地址为 7-trit（默认）；U/J 型立即数为 13-trit，需显式传 ntrits=13。

    取值范围（**越界直接报错，绝不取模**）：
      正数：0 .. 3^ntrits-1   —— 就是原始位模式，写多少就是多少
      负数：-lim .. -1        —— lim=(3^ntrits-1)//2，按 3 补码回绕

    为什么要卡这条：B/J 型曾用 `d % 3^7` / `d % 3^13` 静默取模，跳到一个"看起来
    合法"的错误位置；I/S/U 型字面量有同一个洞——`ADDI x1,x0,5000` 会静默变成 626，
    `LUI x1,1594323` 变成 0。程序照样跑出数，只是数是错的，比崩溃危险得多。
    """
    tok = tok.strip()
    mod = 3 ** ntrits
    lim = (mod - 1) // 2
    if tok in isa.CSR:               # CSR 名（mstatus/mtvec/...）-> 地址
        return isa.CSR[tok] % mod
    if tok.startswith("-"):
        n = int(tok[1:], 0)
        if n > lim:
            raise ValueError(
                f"立即数 -{n} 超出 {ntrits} trit 的 3 补码范围（-{lim}..-1），"
                f"取模会静默改变语义")
        return (mod - n) % mod       # 负立即数 -> ntrits-trit 3 补码
    v = int(tok, 0)
    if not 0 <= v <= mod - 1:
        raise ValueError(
            f"立即数 {v} 超出 {ntrits} trit 的位模式范围（0..{mod - 1}），"
            f"取模会静默改变语义")
    return v

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

def _expand_sym_lines(lines, labels):
    """&sym 操作数展开（T7b py fn-ptr）：ADDI rd, rs1, &label →
    单 ADDI（地址<2187）或 LUI+ADDI（<3^13）。值 = 标签槽位 ×4（tryte 地址）。
    与 C 后端 sym_emit_seg 语义对齐；依赖标签值 → 定点迭代由 assemble 驱动。"""
    out = []
    for ln in lines:
        m = re.search(r"&([A-Za-z_]\w*)", ln)
        if not m or not ln.strip().upper().startswith("ADDI"):
            out.append(ln); continue
        sym = m.group(1)
        if sym not in labels:
            out.append(ln); continue            # 未命中（首轮）：原样保留
        ops = [o.strip() for o in ln.split(",")]
        if len(ops) != 3:
            out.append(ln); continue
        rd, rs1 = ops[0].split()[1], ops[1]
        v = labels[sym] * 4
        if v < 0:
            raise ValueError(f"&{sym} 地址为负：不支持")
        h, lo = divmod(v, 2187)
        if lo > 1093: lo -= 2187; h += 1
        if h == 0:
            out.append(f"ADDI {rd}, {rs1}, {lo}")
        else:
            out.append(f"LUI {rd}, {h}")
            if lo: out.append(f"ADDI {rd}, {rd}, {lo}")
    return out


def assemble(src):
    lines = src.splitlines()
    labels, insts = first_pass(lines)
    # &sym 展开定点迭代：展开改变行数 → 标签重排 → 值可能变 → 至稳定（实测 2 轮）
    for _ in range(5):
        new_lines = _expand_sym_lines(lines, labels)
        if new_lines == lines:
            break
        lines = new_lines
        labels, insts = first_pass(lines)
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

        H_MAP = {"CSRRW":"CSRRW.H","CSRRS":"CSRRS.H","CSRRC":"CSRRC.H",
                 "CSRRWI":"CSRRWI.H","CSRRSI":"CSRRSI.H","CSRRCI":"CSRRCI.H"}
        # CSR 地址自动选区（ADR-06）：<2187 低区 opcode16；2187–4373 高区 opcode9
        # （imm=地址-2187）；≥4374 报错。imm 操作数可能是 ops[-1]（CSR 号在末位）。
        if mn in H_MAP:
            csr = parse_int(ops[-1], 13)   # CSR 地址跨两区（上限 4373 > 7-trit 位模式），按 13-trit 解析
            if csr >= 4374:
                raise ValueError(f"{idx}: CSR 地址 {csr} 超出高区上限 4373（ADR-06）")
            if csr >= 2187 and not mn.endswith(".H"):
                mn = H_MAP[mn]
                ops[-1] = str(csr - 2187)

        if fmt == "R":
            if mn in ("TNEG","TNOT","TPACK","TUNPACK","FSQRT","FSQRT.S",
                      "FCVT.W","FCVT.F","FCVT.W.T"):
                rd, rs1 = reg(ops[0]), reg(ops[1])
                rs2 = 0
            else:
                rd, rs1, rs2 = map(reg, ops)
            v = isa.encode(mn, rd=rd, rs1=rs1, rs2=rs2)
        elif fmt == "I":
            if mn in ("ECALL","EBREAK","FENCE","FENCE.I","MRET","WFI"):
                v = isa.encode(mn)
            elif mn in ("CSRRWI","CSRRSI","CSRRCI","CSRRWI.H","CSRRSI.H","CSRRCI.H"):
                # CSRRWI rd, uimm, csr  ->  uimm 放 rs1 字段, csr 放 imm 字段
                rd = reg(ops[0]); uimm = parse_int(ops[1]); csr = parse_int(ops[2])
                v = isa.encode(mn, rd=rd, rs1=uimm, imm=csr)
            elif mn in ("CSRRW","CSRRS","CSRRC","CSRRW.H","CSRRS.H","CSRRC.H"):
                rd = reg(ops[0]); rs1v = reg(ops[1]); csr = parse_int(ops[2])
                v = isa.encode(mn, rd=rd, rs1=rs1v, imm=csr)
            elif mn in ("LW","LH","LB","LBU","LHU","LW4","JALR","FLW","FLW.S"):
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
                if tgt in labels:
                    d = labels[tgt] - idx
                    # 7 trit 的 B 型偏移只有 ±1093 条指令。超范围必须报错：
                    # 静默 % 2187 会跳到一个"看起来合法"的错误位置，比崩溃危险得多。
                    if not -1093 <= d <= 1093:
                        raise ValueError(
                            f"{idx}: 条件跳转 {mn} -> {tgt} 超出 B 型范围 {d}（限 ±1093 条指令）")
                    imm = d % (3**7)
                else:
                    imm = parse_int(tgt)
            v = isa.encode(mn, rs1=rs1, rs2=rs2, imm=imm)
        elif fmt in ("U", "J"):
            rd = reg(ops[0]); tgt = ops[1].strip()
            # U/J 立即数字段为 13 trit：字面量必须用 13-trit 解析，否则大常数被截到 7 trit。
            if fmt == "J" and tgt in labels:
                d = labels[tgt] - idx
                lim = (3**13 - 1) // 2
                if not (-lim <= d <= lim):
                    raise ValueError(
                        f"{idx}: 跳转 {mn} -> {tgt} 超出 J 型范围 {d}（限 ±{lim} 条指令）")
                imm = d
            else:
                imm = parse_int(tgt, 13)
            v = isa.encode(mn, rd=rd, imm=imm)
        out.append(v)
    return out, labels
