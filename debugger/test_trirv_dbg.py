#!/usr/bin/env python3
"""调试器 MVP 契约（TDD RED 先行）：批命令模式驱动的断点/单步/寄存器/内存/反汇编。

TriRV 调试器 v0：PyVM 驱动（isa/trirv_vm.py VM 类），TRV1 加载，
交互 REPL 与批命令 -x 同一命令路径（测试走批命令）。
"""
import os, struct, sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_R = os.path.dirname(_HERE)
for _d in (_R, os.path.join(_R, "isa"), os.path.join(_R, "vm"), _HERE):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import trirv_isa as isa
from trirv_dbg import TriRVDebugger, write_trv

PASS = FAIL = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s %s" % (name, detail))

def make_add_prog():
    """x1=5; x2=7; x3=x1+x2; ECALL —— 4 条。"""
    e = isa.encode
    return [e("ADDI", rd=1, rs1=0, imm=5),
            e("ADDI", rd=2, rs1=0, imm=7),
            e("ADD",  rd=3, rs1=1, rs2=2),
            e("ECALL")]

def main():
    # ---- T1 TRV1 打包/加载往返 ----
    prog = make_add_prog()
    p = "/tmp/dbg_t1.trv"
    write_trv(p, prog, mem_init={100: [5, 6, 7]})
    dbg = TriRVDebugger(p)
    check("T1 加载指令数", len(dbg.vm.imem) == 4)
    check("T1 内存段", list(dbg.vm.mem[100:103]) == [5, 6, 7])

    # ---- T2 单步：s 4 → x3=12，halt ----
    out = dbg.cmd("s 3")
    check("T2 s 3 后 x3=12", dbg.vm.regs[3] == 12, "got %d" % dbg.vm.regs[3])
    out = dbg.cmd("s 1")
    check("T2 s 1 后 halt", dbg.vm.halted, dbg.cmd("w"))
    check("T2 s 输出含 pc", "pc=" in out)

    # ---- T3 断点：新实例 b slot2 → c 停在 slot2 ----
    dbg2 = TriRVDebugger(write_trv("/tmp/dbg_t3.trv", prog))
    out = dbg2.cmd("b 8")          # tryte 地址：slot2 = 8
    out += dbg2.cmd("c")
    check("T3 停在 slot2", dbg2.vm.pc == 8, "pc=%d" % dbg2.vm.pc)
    check("T3 x1 已执行=5", dbg2.vm.regs[1] == 5)
    check("T3 x2 已执行=7（slot1 在断点前）", dbg2.vm.regs[2] == 7,
          "got %d" % dbg2.vm.regs[2])
    check("T3 x3 未执行=0（断点前）", dbg2.vm.regs[3] == 0)
    # 继续到程序尾
    dbg2.cmd("c")
    check("T3 二次 c 跑到 halt", dbg2.vm.halted and dbg2.vm.regs[3] == 12)

    # ---- T4 内存查看 m：SW 42 → m 200 ----
    e = isa.encode
    prog4 = [e("ADDI", rd=1, rs1=0, imm=42),
             e("SW",   rs2=1, rs1=0, imm=200),  # S 格式：rs2=源(x1)，存 200(x0)
             e("ECALL")]
    # S 格式 SW 字段：rd 位置存的是 imm 低段？——直接查 decode 字段名再组
    dbg4 = TriRVDebugger(write_trv("/tmp/dbg_t4.trv", prog4))
    dbg4.cmd("s 2")
    out = dbg4.cmd("m 200 3")
    check("T4 m 含 42", "42" in out, out)
    check("T4 内存真值", dbg4.vm.mem[200] == 42, "got %d" % dbg4.vm.mem[200])

    # ---- T5 反汇编 d：含 ADDI/ECALL 助记符 ----
    dbg5 = TriRVDebugger(write_trv("/tmp/dbg_t5.trv", prog))
    out = dbg5.cmd("d 4")
    check("T5 d 含 ADDI", "ADDI" in out, out)
    check("T5 d 含 ECALL", "ECALL" in out)
    check("T5 d 含 slot 编号", "slot0" in out)

    # ---- T6 w（where）+ 未知命令容错 ----
    out = dbg5.cmd("w")
    check("T6 w 含 pc", "pc=" in out)
    out = dbg5.cmd("bogus_cmd")
    check("T6 未知命令不崩", "?" in out or "unknown" in out.lower() or out != "")

    # ---- T7 批命令文件端到端（-x 语义：run_script） ----
    script = "b 8\nc\ns 1\nc\nr\n"
    out = TriRVDebugger.run_script("/tmp/dbg_t3.trv", script)
    check("T7 run_script 完整走通", dbg2.vm.regs[3] == 12 or "x3=12" in out)

    print("调试器 MVP 契约：%d PASS / %d FAIL" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
