#!/usr/bin/env python3
"""TriRV 调试器 v0（debugger 模块 MVP）。

PyVM 驱动：TRV1 加载 → 断点/单步/寄存器/内存查看/当前 pc 反汇编。
交互 REPL（人）与批命令 run_script/-x（测试/自动化）走同一 cmd() 路径。

命令：
  b <tryte地址>   设断点（执行前停在该 pc；slot N = tryte 地址 4N）
  c               继续跑到下一断点或停机
  s [n]           单步 n 步（默认 1）
  r               寄存器 dump（x0-x31 有符号解读 + freg raw）
  m <addr> [n]    内存 tryte dump（默认 18 tryte，9/行）
  d [n]           当前 pc 起反汇编 n 条（默认 6）
  w               where：pc/slot/sp/s8/ra/steps/halted
  q               退出

用法：python3 trirv_dbg.py <prog.trv> [-x <命令文件>]
"""
import struct
import sys
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_R = os.path.dirname(_HERE)
for _d in (_R, os.path.join(_R, "isa"), os.path.join(_R, "vm")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import trirv_isa as isa
from trirv_vm import VM, s as _signed


def read_trv(path):
    """TRV1/TRV2 -> (prog list, mem dict, packed)。TRV2 tryte 流解包为流序词；
    调用方据 packed 设置 VM.packed（流制 PC=tryte 偏移，ADR-17 准入②）。"""
    data = open(path, "rb").read()
    magic = data[:4]
    n_seg = struct.unpack_from("<I", data, 12)[0]
    mem = {}
    if magic == b"TRV2":
        n_trytes = struct.unpack_from("<I", data, 8)[0]
        stream = list(data[16:16 + n_trytes])
        off = 16 + n_trytes
        prog = isa.stream_to_words(stream)
        packed = True
    elif magic == b"TRV1":
        n_inst = struct.unpack_from("<I", data, 8)[0]
        prog = list(struct.unpack_from("<%dI" % n_inst, data, 16))
        off = 16 + 4 * n_inst
        packed = False
    else:
        raise ValueError("%s: 非 TRV1/TRV2 镜像" % path)
    for _ in range(n_seg):
        addr, ln = struct.unpack_from("<II", data, off)
        off += 8
        mem[addr] = list(data[off:off + ln])
        off += ln
    return prog, mem, packed


def write_trv(path, prog, mem_init=None):
    """prog(+内存段) -> TRV1。mem_init: {addr: [tryte,...]}。"""
    with open(path, "wb") as f:
        f.write(b"TRV1")
        f.write(struct.pack("<III", 1, len(prog), len(mem_init or {})))
        for w in prog:
            f.write(struct.pack("<I", w % isa.MOD_X))
        for addr in sorted(mem_init or {}):
            d = bytes(t % 243 for t in mem_init[addr])
            f.write(struct.pack("<II", addr, len(d)))
            f.write(d)
    return path


def disasm_one(w):
    """单条指令 -> 'MNEM 字段串'（与 isa.decode 字段对齐）。"""
    d = isa.decode(w)
    name = d.get("inst") or "???"
    parts = [name]
    fmt = isa.INST.get(name, (0, 0, 0, "R"))[3]
    if fmt in ("R", "I"):
        if d.get("rd"):
            parts.append("rd=x%d" % d["rd"])
        if fmt == "R" and d.get("rs1"):
            parts.append("rs1=x%d" % d["rs1"])
        if fmt == "R" and d.get("rs2"):
            parts.append("rs2=x%d" % d["rs2"])
        if fmt == "I" and d.get("rs1"):
            parts.append("rs1=x%d" % d["rs1"])
        if fmt == "I":
            parts.append("imm=%d" % isa.sign_extend_imm(d.get("imm", 0), 7))
    elif fmt == "S":
        parts.append("mem=x%d+%d" % (d.get("rs1", 0), isa.sign_extend_imm(d.get("imm", 0), 7)))
        if d.get("rs2"):
            parts.append("src=x%d" % d["rs2"])
    elif fmt == "B":
        parts.append("rs1=x%d rs2=x%d -> +4*%d" % (d.get("rs1", 0), d.get("rs2", 0),
                                                   isa.sign_extend_imm(d.get("imm", 0), 7)))
    elif fmt in ("U", "J"):
        parts.append("rd=x%d imm=%d" % (d.get("rd", 0),
                                        isa.sign_extend_imm(d.get("imm", 0), 13)))
    return " ".join(parts)


class TriRVDebugger:
    """TRV1 程序的交互式调试器（PyVM 语义）。"""

    def __init__(self, trv_path, stdin=b""):
        prog, mem, packed = read_trv(trv_path)
        self.vm = VM(prog, mem_init=mem, stdin=stdin)
        if packed:
            self.vm.packed = True
            self.vm.imem_offs = isa.stream_offsets(isa.pack_stream(prog))
        self.fmt_trv2 = packed
        self.breakpoints = set()

    def _slot_of(self, pc):
        """流制：PC→流序指令号（未命中 -1）；槽制：pc//4。"""
        if self.vm.packed:
            from bisect import bisect_right
            offs = self.vm.imem_offs
            i = bisect_right(offs, pc) - 1
            return i if (i >= 0 and offs[i] == pc) else -1
        return self.vm.pc // 4

    # ---- 执行控制 ----
    def _step(self):
        self.vm.step()

    def _continue(self):
        start_pc = self.vm.pc          # c 语义：从当前位置出发，本 pc 不立即再停
        while not self.vm.halted:
            if self.vm.pc in self.breakpoints and self.vm.pc != start_pc:
                return "breakpoint @ pc=%d (%s)" % (
                    self.vm.pc, ("off%d" % self._slot_of(self.vm.pc))
                    if self.vm.packed else ("slot%d" % (self.vm.pc // 4)))
            self._step()
        return "halted (exit=%d)" % self.vm.exit_code

    # ---- 视图 ----
    def _dump_regs(self):
        out = []
        for base in range(0, 32, 4):
            row = []
            for r in range(base, base + 4):
                v = self.vm.regs[r]
                row.append("x%-2d=%-6d" % (r, _signed(v)))
            out.append(" ".join(row))
        out.append(" ".join("f%-2d=%-4d" % (r, self.vm.freg[r]) for r in range(0, 6)))
        # fa0-fa7 = freg[0..7]（tri-gen freg(r)=r-8：tcc 浮点槽 r8-15 直落 freg 0-7）
        out.append("fa0-fa3 = " + " ".join("%d" % self.vm.freg[k] for k in range(4)))
        out.append("fa4-fa7 = " + " ".join("%d" % self.vm.freg[k] for k in range(4, 8)))
        return "\n".join(out)

    def _dump_mem(self, addr, n=18):
        out = []
        for off in range(0, n, 9):
            row = []
            for k in range(off, min(off + 9, n)):
                a = addr + k
                if a < len(self.vm.mem):
                    row.append("%3d" % self.vm.mem[a])
                else:
                    row.append("  .")
            out.append("%6d: %s" % (addr + off, " ".join(row)))
        return "\n".join(out)

    def _disasm(self, n=6):
        out = []
        pc = self.vm.pc
        for k in range(n):
            if self.vm.packed:
                i0 = self._slot_of(pc)
                if i0 < 0: break
                idx = i0 + k
                if idx >= len(self.vm.imem) or idx < 0: break
                mark = " ->" if k == 0 else "   "
                out.append("%s off%-4d pc=%-5d %s" % (mark, idx,
                            self.vm.imem_offs[idx], disasm_one(self.vm.imem[idx])))
            else:
                idx = pc // 4 + k
                if idx >= len(self.vm.imem):
                    break
                mark = " ->" if k == 0 else "   "
                out.append("%s slot%-3d pc=%-5d %s" % (mark, idx, pc + 4 * k,
                                                       disasm_one(self.vm.imem[idx])))
        return "\n".join(out)

    def _where(self):
        pos = ("off%d" % self._slot_of(self.vm.pc)) if self.vm.packed else ("slot%d" % (self.vm.pc // 4))
        return ("pc=%d %s sp=x2=%d s8=x8=%d ra=x1=%d steps=%d halted=%s exit=%d"
                % (self.vm.pc, pos, _signed(self.vm.regs[2]),
                   _signed(self.vm.regs[8]), _signed(self.vm.regs[1]),
                   self.vm.steps, self.vm.halted, self.vm.exit_code))

    # ---- 命令入口 ----
    def cmd(self, line):
        """执行一条调试命令，返回输出文本。"""
        line = line.strip()
        if not line:
            return ""
        parts = line.split()
        op, args = parts[0].lower(), parts[1:]
        try:
            if op == "b":
                addr = int(args[0], 0)
                self.breakpoints.add(addr)
                return "breakpoint set @ pc=%d (%s)" % (
                    addr, ("off%d" % self._slot_of(addr)) if self.vm.packed
                    else "slot%d" % (addr // 4))
            if op == "c":
                return self._continue()
            if op == "s":
                n = int(args[0]) if args else 1
                for _ in range(n):
                    if self.vm.halted:
                        break
                    self._step()
                return self._where()
            if op == "r":
                return self._dump_regs()
            if op == "m":
                addr = int(args[0], 0)
                n = int(args[1]) if len(args) > 1 else 18
                return self._dump_mem(addr, n)
            if op == "d":
                n = int(args[0]) if args else 6
                return self._disasm(n)
            if op == "w":
                return self._where()
            if op == "q":
                return "quit"
            return "? 未知命令: %s（可用：b/c/s/r/m/d/w/q）" % op
        except Exception as ex:                      # 调试器自身不许把 VM 搞挂
            return "? %s" % ex

    @staticmethod
    def run_script(trv_path, script):
        """批命令模式（-x 语义）：逐行执行，拼接输出。q 提前终止。"""
        dbg = TriRVDebugger(trv_path)
        chunks = []
        for line in script.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            out = dbg.cmd(line)
            chunks.append("> %s\n%s" % (line, out))
            if out == "quit":
                break
        return "\n".join(chunks)


def main():
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        sys.exit(2)
    trv = argv[0]
    script = None
    if "-x" in argv:
        script = open(argv[argv.index("-x") + 1]).read()
    if script is not None:
        print(TriRVDebugger.run_script(trv, script))
        return
    # 交互 REPL
    dbg = TriRVDebugger(trv)
    print("TriRV debugger v0 —— %s（h=帮助见模块头注释）" % trv)
    while True:
        try:
            line = input("trirv-dbg> ")
        except EOFError:
            break
        out = dbg.cmd(line)
        if out:
            print(out)
        if out == "quit":
            break


if __name__ == "__main__":
    main()
