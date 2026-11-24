#!/usr/bin/env python3
"""TriRV 固件定时器契约（批次 5，ADR-24）：CLINT 模型 mtime/mtimecmp + ISR 向量表。

双 VM 同构（PyVM/CVM 逐语义镜像，全部期望值手算，ADR-28②）：
  - mtime（0xB01 高区）= 指令步进钟，只读；读出值 = 已执行指令数（steps 在
    指令提交后自增——slot n 执行期间读 = n，确定性等值，非不等式）
  - mtimecmp（0xB02 高区）= deadline；mtime ≥ mtimecmp 且 MIE=1 → 步末挂起
    MTIP → 既有 mtimer 陷入路径；触发后 mtimecmp 解除武装（诚实重装语义）
  - 向量模式：mtvec 低 2 tryte 位 = 模式位（0=direct 1=vectored，旧实现被
    4 对齐抹掉、批次 5 起保留）；中断按 cause 低位散转 base+4*cause
    （MTIMER=7→base+28、MSI=3→base+12，4 tryte/槽 = RISC-V 4 字节向量步距
    的三值对应）；异常（EBREAK/ECALL/非法）恒落 base

坑实录：mtime/mtimecmp/mhartid 均 > 2186（imm7 上限），词级程序必须走
CSR 高区 .H 形式（imm = 地址-2187）；大立即数（a7=5014 等）必须两段装载。
"""
import os
import sys
import struct
import subprocess
import tempfile

import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path；草稿经 scratch() 落仓库 tmp/）
import trirv_isa as isa
import trirv_vm as vm

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CVM = os.path.join(ROOT, "vm", "bin", "trirv_vm")

passed = 0
failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print("PASS %s: %r" % (name, got))
    else:
        failed += 1
        print("FAIL %s: got=%r want=%r" % (name, got, want))


E = isa.encode
PAD = E("ADDI", rd=0, rs1=0, imm=0)
MTVEC, MSTATUS, MTIMECMP = 773, 768, 2818
MTIME_H = 2817 - 2187                     # 高区 imm：630
MTIMECMP_H = 2818 - 2187                  # 高区 imm：631（低区写 2818 会静默回绕——坑）


def load_const(rd, val):
    """LUI 高段 + ADDI 低段（imm7 窗口 ±1093；LUI=imm×3^7 两 VM 已锁 §11）。"""
    hi = (val + 1093) // 2187
    lo = val - hi * 2187
    if hi == 0:
        return [E("ADDI", rd=rd, rs1=0, imm=lo)]
    return [E("LUI", rd=rd, imm=hi), E("ADDI", rd=rd, rs1=rd, imm=lo)]


def run_py(prog, steps=20000):
    return vm.VM(prog).run(max_steps=steps)


def run_cvm(prog, steps=20000, amp=0):
    path = _bootstrap.scratch("fwtick_%d.trv" % abs(hash(tuple(prog))))
    with open(path, "wb") as f:
        f.write(b"TRV1")
        f.write(struct.pack("<III", 1, len(prog), 0))
        for x in prog:
            f.write(struct.pack("<I", x))
    args = [CVM] + (["-a", str(amp)] if amp else []) + [path, "-"]
    r = subprocess.run(args, capture_output=True, text=True)
    errs = [l for l in r.stderr.split() if l.startswith("exit=")]
    return int(errs[0].split("=")[1]) if errs else None


# ================ §1 mtime 步进钟：读出值 = 已执行指令数（确定性等值） ================
# slot0-3 四条 PAD（各计 1 步），slot4 读 mtime → 此时 steps=4 → exit 4
prog = [PAD, PAD, PAD, PAD,
        E("CSRRS.H", rd=10, rs1=0, imm=MTIME_H),
        E("ADDI", rd=17, rs1=0, imm=93), E("ECALL")]
c = run_py(prog)
check("mtime·PyVM 读出=已执行步数（确定性等值 4）", (c.exit_code, c.regs[10]), (4, 4))
check("mtime·CVM 等值一致", run_cvm(prog), 4)

# ================ §2 mtime 写忽略 ================
# slot0 写 mtime←12345（必须被忽略），slot1 读回 → =1（不是 12345）
prog = [E("CSRRW.H", rd=0, rs1=0, imm=12345 - 2187),
        E("CSRRS.H", rd=10, rs1=0, imm=MTIME_H),
        E("ADDI", rd=17, rs1=0, imm=93), E("ECALL")]
c = run_py(prog)
check("mtime·PyVM 写忽略（读回=1 非 12345）", (c.exit_code, c.regs[10]), (1, 1))
check("mtime·CVM 写忽略一致", run_cvm(prog), 1)

# ================ §3 deadline 触发 → handler 直接 exit ================
# 布局：main 0..13（武装后 10 条 PAD 自旋），handler 在 20
H = 20
main_ = []
main_ += load_const(5, H * 4)
main_.append(E("CSRRW", rd=0, rs1=5, imm=MTVEC))          # mtvec ← handler
main_.append(E("CSRRSI", rd=0, rs1=8, imm=MSTATUS))       # MIE=1
main_.append(E("CSRRS.H", rd=9, rs1=0, imm=MTIME_H))      # x9 = mtime（=3）
main_.append(E("ADDI", rd=11, rs1=9, imm=10))             # deadline = +10
main_.append(E("CSRRW.H", rd=0, rs1=11, imm=MTIMECMP_H))  # 武装（.H：2818 超低区窗）
main_ += [PAD] * 10                                       # 自旋（第 10 步末触发）
main_.append(E("ADDI", rd=10, rs1=0, imm=1))              # 未触发则 exit 1
main_ += [E("ADDI", rd=17, rs1=0, imm=93), E("ECALL")]
prog = main_ + [PAD] * (H - len(main_)) + [
    E("ADDI", rd=10, rs1=0, imm=77),                      # handler：exit 77
    E("ADDI", rd=17, rs1=0, imm=93), E("ECALL"),
]
check("deadline·PyVM 触发陷入 handler", run_py(prog).exit_code, 77)
check("deadline·CVM 一致", run_cvm(prog), 77)

# ================ §4 触发后解除武装 + 重装（两次 tick） ================
# 布局纪律（初版 H2 与主段 pad 区碰撞、退出 ECALL 被覆盖 → 诚实停机 exit 0 实证）：
# handler 必须落在退出路径之后、仅由陷阱可达；主循环用「x9<2 自旋」等第二次 tick。
# prefix(8, mtvec→H2) + pads + 检查块 E0（x9>=2 才退出，否则 J 回环自旋）+ gap + handler
pre = []
pre += load_const(5, 0)                                   # 占位回填
pre.append(E("CSRRW", rd=0, rs1=5, imm=MTVEC))
pre.append(E("CSRRSI", rd=0, rs1=8, imm=MSTATUS))
pre.append(E("CSRRS.H", rd=11, rs1=0, imm=MTIME_H))       # x11 = mtime（=3）
pre.append(E("ADDI", rd=12, rs1=11, imm=10))
pre.append(E("CSRRW.H", rd=0, rs1=12, imm=MTIMECMP_H))    # 第一次武装（+10）
pre.append(E("ADDI", rd=9, rs1=0, imm=0))                 # tick 计数
# 布局坑：load_const(rd,0) 只发 1 条（hi=0 短路）——检查块槽号必须从 len(pre)
# 推导，硬编码会错一位（handler 首条被跳过、x9 恒 0、1111 次重触发实证）
E0 = len(pre) + 24                                        # 检查块槽号（24 拍自旋垫）
hd = [E("ADDI", rd=9, rs1=9, imm=1),                      # x9 += 1
      E("SLTI", rd=14, rs1=9, imm=2),
      E("BNE", rs1=14, rs2=0, imm=2),                     # x9<2 → 越过 MRET 去重装
      E("MRET"),                                           # x9>=2：不再武装
      E("CSRRS.H", rd=13, rs1=0, imm=MTIME_H),
      E("ADDI", rd=15, rs1=13, imm=15),
      E("CSRRW.H", rd=0, rs1=15, imm=MTIMECMP_H),         # 重装（+15）
      E("MRET")]
H2 = E0 + 8                                               # handler 槽（检查块后隔 2 拍）
pre[0] = E("ADDI", rd=5, rs1=0, imm=H2 * 4)               # 回填 mtvec
prog2 = (pre + [PAD] * (E0 - len(pre))
         + [E("SLTI", rd=14, rs1=9, imm=2),
            E("BEQ", rs1=14, rs2=0, imm=2),
            E("JAL", rd=0, imm=-2),                        # x9<2：自旋等第二次 tick
            E("ADDI", rd=10, rs1=9, imm=0),
            E("ADDI", rd=17, rs1=0, imm=93), E("ECALL")]   # x9>=2：exit x9 = 2
         + [PAD, PAD] + hd)
check("rearm·PyVM 两次 tick 后解除（exit=2）", run_py(prog2).exit_code, 2)
check("rearm·CVM 一致", run_cvm(prog2), 2)

# ================ §5 MIE=0 挂起待使能 ================
# MIE=0 时武装（deadline 早过）→ 自旋 10 拍不触发 → 开 MIE → 步末兑现 → exit 89
H3 = 30
main3 = []
main3 += load_const(5, H3 * 4)
main3.append(E("CSRRW", rd=0, rs1=5, imm=MTVEC))
main3.append(E("CSRRS.H", rd=11, rs1=0, imm=MTIME_H))     # x11 = mtime（=2）
main3.append(E("ADDI", rd=12, rs1=11, imm=1))             # deadline = +1（立即过）
main3.append(E("CSRRW.H", rd=0, rs1=12, imm=MTIMECMP_H))  # 武装（MIE=0 → 不触发）
main3 += [PAD] * 10                                       # 全程 MIE=0，不得触发
main3.append(E("CSRRSI", rd=0, rs1=8, imm=MSTATUS))       # 开 MIE → 下一步末兑现
main3.append(E("ADDI", rd=10, rs1=0, imm=1))              # 未触发 exit 1
main3 += [E("ADDI", rd=17, rs1=0, imm=93), E("ECALL")]
prog3 = main3 + [PAD] * (H3 - len(main3)) + [
    E("ADDI", rd=10, rs1=0, imm=89),
    E("ADDI", rd=17, rs1=0, imm=93), E("ECALL"),
]
check("pending·PyVM MIE=0 挂起待使能（exit=89 非 1）", run_py(prog3).exit_code, 89)
check("pending·CVM 一致", run_cvm(prog3), 89)

# ================ §6 向量模式：中断按 cause 散转，异常落 base ================
# mtvec = base|1（vectored）；MSI handler@base+12（slot B+3）、
# MTIMER handler@base+28（slot B+7）、base（异常）直接 exit 66。
# 流程：self-mipi → MSI handler（x9+=1, mret）→ 武装 deadline → MTIMER handler
#（x10+=1, mret）→ exit(x9+x10*2)=3 → EBREAK → 异常落 base → exit 66。
mainv = []
mainv += load_const(5, 0)                                 # mtvec 占位（B 依赖 len(mainv)，两段回填）
mainv.append(E("CSRRW", rd=0, rs1=5, imm=MTVEC))
mainv.append(E("CSRRSI", rd=0, rs1=8, imm=MSTATUS))       # MIE=1
mainv += [E("ADDI", rd=9, rs1=0, imm=0), E("ADDI", rd=10, rs1=0, imm=0)]
mainv += load_const(17, 5014)
mainv += [E("ADDI", rd=8, rs1=0, imm=0), E("ECALL")]      # a0=x8=0（self-IPI；a0 独立于计数器）
mainv += [E("CSRRS.H", rd=11, rs1=0, imm=MTIME_H),
          E("ADDI", rd=12, rs1=11, imm=3),
          E("CSRRW.H", rd=0, rs1=12, imm=MTIMECMP_H)]    # 武装 deadline（+3）
mainv += [PAD, PAD, PAD, PAD]                             # 第 3 拍末 MTIMER 触发
mainv += [E("ADD", rd=13, rs1=10, rs2=10),
          E("ADD", rd=13, rs1=13, rs2=9)]                 # x13 = x10*2 + x9 = 3
mainv += [E("ADDI", rd=10, rs1=13, imm=0),
          E("ADDI", rd=17, rs1=0, imm=93), E("ECALL")]    # exit 3（两中断均发生）
mainv += [E("EBREAK")]                                    # mret 回来后 EBREAK → 异常落 base
# 布局坑（同 §4）：mainv 长度依赖 load_const 短路（81→1 条、5014→2 条），
# B 必须从 len(mainv) 推导——硬编码 20 时 handler 块与主段尾部重叠实证
B = len(mainv) + 6                                        # 6 拍隔离垫：主段与 handler 区绝不重叠
mainv[0] = E("ADDI", rd=5, rs1=0, imm=(B * 4) | 1)        # 回填 mtvec = base|1 向量模式
handlers = [PAD] * (B - len(mainv))
# base（slot B..B+2，异常入口）：exit 66
handlers += [E("ADDI", rd=10, rs1=0, imm=66),
             E("ADDI", rd=17, rs1=0, imm=93), E("ECALL")]
# base+12 = slot B+3（MSI，cause 低位 3）：x9+=1; mret
handlers += [E("ADDI", rd=9, rs1=9, imm=1), E("MRET")]
# B+5,B+6 补位
handlers += [PAD, PAD]
# base+28 = slot B+7（MTIMER，低位 7）：x10+=1; mret
handlers += [E("ADDI", rd=10, rs1=10, imm=1), E("MRET")]
progv = mainv + handlers
c_v = vm.run_amp(progv, 1, max_steps=20000)[0][0]
check("vectored·PyVM MSI+MTIMER 散转正确（exit=3）", c_v.exit_code, 3)
check("vectored·CVM 一致", run_cvm(progv, amp=1), 3)

# EBREAK 落 base 单证（同布局，直接 EBREAK → base exit 66）
mainw = []
mainw += load_const(5, (B * 4) | 1)
mainw.append(E("CSRRW", rd=0, rs1=5, imm=MTVEC))
mainw.append(E("EBREAK"))
mainw += [PAD] * (B - len(mainw))
progw = mainw + [E("ADDI", rd=10, rs1=0, imm=66),
                 E("ADDI", rd=17, rs1=0, imm=93), E("ECALL")]
check("vectored·PyVM 异常落 base（EBREAK→exit 66）", run_py(progw, steps=5000).exit_code, 66)
check("vectored·CVM 异常一致", run_cvm(progw), 66)

print("\n固件定时器契约：%d PASS / %d FAIL" % (passed, failed))
sys.exit(1 if failed else 0)
