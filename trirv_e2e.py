"""TriRV 端到端验证：汇编程式 -> 机器码 -> VM 执行。"""
import trirv_asm as asm
import trirv_vm as vm
import trirv_isa as isa

SRC = r"""
# 真正的 1+2+3+4+5 = 15
    ADDI  x1, zero, 0      # x1 = 累加器 = 0
    ADDI  x2, zero, 1      # x2 = 当前数 = 1
    ADDI  x3, zero, 6      # x3 = 终止哨兵 = 6
loop:
    ADD   x1, x1, x2       # sum += cur
    ADDI  x2, x2, 1        # cur += 1
    BNE   x2, x3, loop     # cur != 6 则继续循环
    ADDI  x10, x1, 0       # x10 = sum (期望 15)
    TMAX  x11, x10, x2     # x11 = 逐 trit max
    TNEG  x12, x10         # x12 = -sum
    TPACK x13, x10         # x13 = 把 sum 低 5 trit 打包成 byte
    TUNPACK x14, x13       # x14 = 解包回 5 trit（应等于 sum 低 5 trit）
    ECALL
"""

def main():
    prog, labels = asm.assemble(SRC)
    print("=== 标签表 ===", labels)
    print(f"=== 汇编得到 {len(prog)} 条指令 ===")
    print("=== 执行轨迹 ===")
    cpu = vm.VM(prog).run(trace=True)

    def s(v): return isa.to_signed_word(v)
    print("=== 结果校验 ===")
    print(f"  x10 累加和      = {s(cpu.regs[10])}   (期望 15)")
    print(f"  x11 TMAX        = {isa.trit_str(cpu.regs[11],45)}")
    print(f"  x12 TNEG        = {s(cpu.regs[12])}   (期望 -15)")
    print(f"  x13 TPACK byte  = {cpu.regs[13]}  (0..255)")
    print(f"  x14 TUNPACK     = {cpu.regs[14]}  (应等于 x10 低5trit={cpu.regs[10] % 243})")

    assert s(cpu.regs[10]) == 15, "累加和错误，应为 15"
    assert s(cpu.regs[12]) == -15, "TNEG 错误"
    assert cpu.regs[14] == cpu.regs[10] % 243, "TPACK/TUNPACK 往返不一致"
    print("  ✓ 全部断言通过")

if __name__ == "__main__":
    main()
