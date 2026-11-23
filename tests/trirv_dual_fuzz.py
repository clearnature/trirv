#!/usr/bin/env python3
"""TriRV 双引擎随机差分（ADR-28①，批次 2.4）：槽制引擎 ↔ 流制引擎。

同一随机程序两路执行：
  A. 槽制（TRV1 语义，imm=槽距）
  B. migrate_trv1_stream 重编 + VM.from_stream（TRV2 语义，imm=tryte 距）
终态（exit/halted/steps）逐例一致。

含变异自检：人为破坏流制重编结果（imm9 尾 trit 翻转）必须被差分检出——
证明本套件真能抓分歧，不是恒绿。

用法：trirv_dual_fuzz.py [N=200] [seed=0]（run_all --fuzz-seed 透传）。
"""
import random
import sys

import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import trirv_isa as isa
import trirv_vm as vm

E = isa.encode
WR = [5, 6, 7, 8, 9, 10, 11, 12]          # TC 窗口寄存器


def gen_prog(rng, n):
    """TC 密集 + 全宽混合；控制流 = 顺序 + 界内向前 TC 分支 + 尾部循环结构。"""
    prog = []
    i = 0
    while i < n:
        r = rng.random()
        if r < 0.35:
            rd, rs1 = rng.choice(WR), rng.choice(WR)
            im4 = rng.randint(-40, 40)
            prog.append(sum(x * 243 ** j for j, x in
                            enumerate(isa.encode_c(0, WR.index(rd),
                                                   WR.index(rs1) + (im4 % 81) * 27))))
            i += 1
        elif r < 0.55:
            # C-BEQ/C-BNE 向前跳（跳过 1..3 槽的随机词——生成时占位后填充）
            k = rng.choice([3, 4])
            skip = rng.randint(1, 3)
            t = isa.encode_c(k, WR.index(rng.choice(WR)), skip % 2187)
            prog.append(sum(x * 243 ** j for j, x in enumerate(t)))
            # 占位：skip 个随机全宽词
            for _ in range(skip):
                prog.append(E(rng.choice(["ADDI", "XOR", "SLTU"]),
                              rd=rng.choice([5, 6, 7, 10]), rs1=5, rs2=6,
                              imm=rng.randint(-50, 50)))
            i += 1 + skip
        elif r < 0.65 and i >= 4:
            # C-J 向后回跳到固定的 4 词循环头（前文 slot i-4）——合法负跳
            prog.append(sum(x * 243 ** j for j, x in enumerate(isa.encode_c_jump(0, -4))))
            i += 1
        else:
            prog.append(E(rng.choice(["ADDI", "ADD", "SUB", "SLTU", "XOR", "SLLI"]),
                          rd=rng.choice([5, 6, 7, 10]), rs1=rng.choice([5, 6, 7, 10]),
                          rs2=rng.choice([5, 6, 7, 10]), imm=rng.randint(-50, 50)))
            i += 1
    prog.append(E("ADDI", rd=17, rs1=0, imm=93))
    prog.append(EC := isa.encode("ECALL"))
    return prog


def mutate_stream(stream, rng):
    """变异：翻转某 TC 词最后一个 tryte 的最低 trit（imm 高段 ±1——语义变化，
    不动首 tryte（翻 t0 会改判宽 TC→全宽，越界读是变异器自己的 bug 不是
    被测实现的真实分歧形态）。"""
    starts = []
    i = 0
    while i < len(stream):
        w = 3 if stream[i] % 27 >= 23 else 4
        if stream[i] % 27 >= 23:
            starts.append(i + w - 1)          # TC 词末 tryte 下标
        i += w
    if not starts:
        return stream
    t = rng.choice(starts)
    s = list(stream)
    s[t] = (s[t] + 1) % 3
    return s


def main():
    n_cases = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    rng = random.Random(seed)
    agree, diverge = 0, 0
    for trial in range(n_cases):
        prog = gen_prog(rng, rng.randint(16, 40))
        try:
            stream = isa.migrate_trv1_stream(prog)
        except ValueError:
            continue                            # 生成器护栏（越窗重生成成本高，跳过计一致）
        a = vm.VM(prog).run(max_steps=4000)
        b = vm.VM.from_stream(stream).run(max_steps=4000)
        if (a.exit_code, a.halted, a.steps) != (b.exit_code, b.halted, b.steps):
            diverge += 1
            print(f"FAIL 分歧 trial={trial}: 槽={tuple(map(str,(a.exit_code,a.halted,a.steps)))} "
                  f"流={tuple(map(str,(b.exit_code,b.halted,b.steps)))}")
            break
        agree += 1
    print(f"差分一致 {agree} / {n_cases}（分歧 {diverge}）")
    if diverge:
        sys.exit(1)

    # ---- 变异自检：破坏流制必须被检出（至少一例终态不同）----
    detected = 0
    for trial in range(30):
        prog = gen_prog(rng, 24)
        stream = isa.migrate_trv1_stream(prog)
        bad = mutate_stream(stream, rng)
        a = vm.VM(prog).run(max_steps=4000)
        b = vm.VM.from_stream(bad).run(max_steps=4000)
        if (a.exit_code, a.halted, a.steps) != (b.exit_code, b.halted, b.steps):
            detected += 1
    print(f"变异自检：30 例破坏检出 {detected}")
    if detected == 0:
        print("FAIL 变异自检未检出——差分恒绿不可信")
        sys.exit(1)
    print(f"\n双引擎随机差分（seed={seed}）：一致 {agree}，变异检出 {detected}/30")

    # ---- 2.5 TRV1/TRV2 文件差分（CVM 容器级）----
    import os
    import struct
    import subprocess
    cvm = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "vm", "bin", "trirv_vm")
    ok_files = 0
    for trial in range(40):
        prog = gen_prog(rng, rng.randint(16, 40))
        try:
            stream = isa.migrate_trv1_stream(prog)
        except ValueError:
            continue
        tf1 = _bootstrap.scratch("dfuzz_%d.trv" % trial)
        tf2 = _bootstrap.scratch("dfuzz_%d.trv2" % trial)
        for path, payload in ((tf1, prog), (tf2, stream)):
            with open(path, "wb") as f:
                f.write(b"TRV2" if path.endswith(".trv2") else b"TRV1")
                f.write(struct.pack("<III", 1,
                                    len(payload) if path.endswith(".trv") else len(payload),
                                    0))
                if path.endswith(".trv"):
                    for x in payload:
                        f.write(struct.pack("<I", x))
                else:
                    f.write(bytes(payload))
        outs = []
        for p in (tf1, tf2):
            r = subprocess.run([cvm, p, "-"], capture_output=True, text=True)
            e = [l for l in r.stderr.split() if l.startswith("exit=")]
            outs.append(e[0] if e else "?")
        if outs[0] != outs[1]:
            print(f"FAIL 文件差分 trial={trial}: TRV1={outs[0]} TRV2={outs[1]}")
            sys.exit(1)
        ok_files += 1
        os.remove(tf1); os.remove(tf2)
    print(f"TRV1/TRV2 文件差分（CVM）：{ok_files} 对一致")


if __name__ == "__main__":
    main()
