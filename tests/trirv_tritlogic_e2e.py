#!/usr/bin/env python3
"""trirv_tritlogic_e2e —— AND / OR / XOR 的「逐 trit」语义锁定测试。

2026-11-19 决议 B：这三条指令不再是二进制按位，而是和 TMIN/TMAX 同族的
**逐 trit 三值逻辑**：

    AND = min(a_i, b_i)      （≡ TAND / TMIN）
    OR  = max(a_i, b_i)      （≡ TOR  / TMAX）
    XOR = (a_i + b_i) mod 3

为什么必须锁死：早先两套 VM 都直接调宿主的 `& | ^`，那是二进制 ALU 的语义，
在三进制硬件上不存在；而 SLL/SRL 又是三进制移位，同一个 ISA 里两个进制。
一旦有人改回 `& | ^`，这里会立刻红。

做法：把每组操作数的运算结果低字节写进缓冲区，`write` 到 stdout，
Python VM 与 C VM 各跑一遍，再与 Python 侧逐 trit 算出的期望值三方差分。
"""
import subprocess

import os
import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import trirv_asm
import trirv_vm
import trirv_wasm
from trirv_isa import to_signed_word

PAIRS = [(12, 10), (5, 8), (26, 26), (0, 7), (121, 2), (242, 1),
         (40, 13), (81, 80), (1, 1), (0, 0)]
IMMS = [(12, 10), (242, 1), (40, 13), (81, 2)]
OPS = ("AND", "OR", "XOR")


def trit(a, b, f, n=45):
    r, p = 0, 1
    for _ in range(n):
        a, da = divmod(a, 3)
        b, db = divmod(b, 3)
        r += f(da, db) * p
        p *= 3
    return r


FN = {"AND": min, "OR": max, "XOR": lambda x, y: (x + y) % 3}


def build():
    """生成汇编：每个结果写 1 字节到缓冲区，最后 write 出去。"""
    lines, out, expect = ["    ADDI x5, zero, 0"], [], []
    # 二元寄存器形式
    for a, b in PAIRS:
        lines.append(f"    ADDI x1, zero, {a}")
        lines.append(f"    ADDI x2, zero, {b}")
        for op in OPS:
            v = trit(a % 3**45, b % 3**45, FN[op])
            lines.append(f"    {op} x3, x1, x2")
            lines.append(f"    SB x3, {len(out)}(x0)")
            out.append(v % 243)
            expect.append(v % 243)
    # 立即数形式（imm 走 7-trit 符号扩展，这里全用正数）
    for a, im in IMMS:
        lines.append(f"    ADDI x1, zero, {a}")
        for op in OPS:
            v = trit(a % 3**45, im % 3**45, FN[op])
            lines.append(f"    {op}I x3, x1, {im}")
            lines.append(f"    SB x3, {len(out)}(x0)")
            out.append(v % 243)
            expect.append(v % 243)
    lines += [f"    ADDI a0, zero, 1",
              f"    ADDI a1, zero, 0",
              f"    ADDI a2, zero, {len(out)}",
              f"    ADDI a7, zero, 64",
              "    ECALL 0",
              "    ADDI a0, zero, 0",
              "    ADDI a7, zero, 93",
              "    ECALL 0"]
    return "\n".join(lines) + "\n", bytes(expect)


def main():
    src, expect = build()
    prog, _ = trirv_asm.assemble(src)
    v = trirv_vm.VM(prog).run(max_steps=500000)
    py = bytes(v.output)

    trv = _bootstrap.scratch("tritlogic.trv")
    trirv_wasm.build_trv(src, {}, trv)
    r = subprocess.run([os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vm", "bin", "trirv_vm"), trv], capture_output=True)
    c = r.stdout

    print(f"用例 {len(expect)} 组（{len(PAIRS)}×3 寄存器 + {len(IMMS)}×3 立即数）")
    ok = True
    for i, (e, p, cc) in enumerate(zip(expect, py, c)):
        if not (e == p == cc):
            print(f"  FAIL #{i}: 期望 {e}  PyVM {p}  C VM {cc}")
            ok = False
    if len(py) != len(expect) or len(c) != len(expect):
        print(f"  长度不符: 期望 {len(expect)} PyVM {len(py)} CVM {len(c)}")
        ok = False
    print(f"\n{'全部一致 ✓' if ok else '存在差异 ✗'}   "
          f"PyVM={len(py)} CVM={len(c)} 字节")
    print("语义：AND=min, OR=max, XOR=(a+b) mod 3（逐 trit，非二进制按位）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
