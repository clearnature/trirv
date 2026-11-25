#!/usr/bin/env python3
"""p_trit_lab —— 三值热力学计算实验场（研究课题三值化 · 批次 ②）。

谱系：p-bit（MTJ 隧穿涨落）/ 热力学计算（Extropic）/ 振子 Ising 机的
**三值化**——自旋 = trit ∈ {0,1,2}，噪声 = 三电平量化，能量 = q=3 Potts
（图 3-着色冲突数）。

两路实现：
  Python 参考  —— 逐边冲突修复退火（与 VM 内核同语义）
  TriRV VM 内核 —— 修复循环直发 trirv 汇编（REM/ADD/LBU/SB 原生 1:1），
                   噪声从线性内存 tryte 流读取
对拍：固定噪声种子 → 双路最终自旋数组与冲突数逐一相等。

内存布局（TriRV 线性内存，tryte 编址）：
  [0, S)          噪声 trit 流（S = sweeps×E）
  [S, S+n)        自旋数组（每自旋 1 tryte，值 0..2）
  [S+n, S+n+2E)   边表（每边 2 tryte 端点对）
  [S+n+2E]        冲突计数槽（1 tryte，冲突数 < 10）
"""
import random
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "wasm"))
sys.path.insert(0, os.path.join(ROOT, "vm"))

sys.path.insert(0, ROOT)               # trirv_asm.py / trirv_vm 链接在仓库根
import trirv_asm  # noqa: F401
import trirv_vm

MOD45 = 3 ** 45


# ------------------------------------------------------------ 噪声源
def trit_noise(seed, n):
    """三电平量化噪声流：均匀随机 → trit ∈ {0,1,2}。

    硬件期对应：三值熵源（谐振噪声三阈值切割）直通 SYS_GETRANDOM 类
    ABI——接口不变，熵源升级（HW-TERNARY-NOTES §四）。"""
    rng = random.Random(seed)
    return [rng.randrange(3) for _ in range(n)]


def noise_quality(stream):
    """三桶频数均匀性（卡方，df=2，95% 阈值 5.99）。"""
    c = [0, 0, 0]
    for t in stream:
        c[t] += 1
    exp = len(stream) / 3
    chi2 = sum((ci - exp) ** 2 / exp for ci in c)
    return c, chi2


# ------------------------------------------------------------ Python 参考退火
def ref_repair(n, edges, sweeps, noise):
    """逐边冲突修复（与 VM 内核同语义）：
    it ∈ [0, sweeps×E)：e = it mod E；若两端同色 → 以噪声 trit 搬色。"""
    spins = [0] * n
    E_n = len(edges)
    for it in range(sweeps * E_n):
        e = it % E_n
        ni, nj = edges[e]
        if spins[ni] == spins[nj]:
            spins[nj] = (spins[nj] + noise[it]) % 3
    conflicts = sum(1 for ni, nj in edges if spins[ni] == spins[nj])
    return spins, conflicts


# ------------------------------------------------------------ TriRV 内核装配
def build_kernel_asm(n, edges, sweeps, noise_len):
    """修复循环 → trirv 汇编。寄存器：s1=it s2=E s3=地址 t0-t4 值临时。"""
    E_n = len(edges)
    S = sweeps * E_n
    NOISE = 0
    SPIN = S
    EDGE = S + n
    CNT = EDGE + 2 * E_n
    A = []
    add = lambda t: A.append(t)
    add("    ADDI s1, zero, 0")                # it = 0
    add("    ADDI s2, zero, %d" % E_n)
    add("    ADDI s8, zero, %d" % SPIN)        # 自旋数组基址（s8 常驻）
    add("__loop:")
    add("    ADDI t2, zero, %d" % S)
    add("    SLT t2, s1, t2")
    add("    BEQ t2, zero, __done")
    add("    REM t0, s1, s2")                  # e = it mod E
    add("    ADD t0, t0, t0")                  # 2e
    A.extend(li_lines("t1", EDGE))
    add("    ADD s3, t1, t0")                  # &edges[2e]
    add("    LBU t1, 0(s3)")                   # ni
    add("    LBU t2, 1(s3)")                   # nj
    add("    ADD s3, s8, t1")
    add("    LBU t3, 0(s3)")                   # spin[ni]
    add("    ADD s3, s8, t2")
    add("    LBU t4, 0(s3)")                   # spin[nj]
    add("    SUB s3, t3, t4")
    add("    BNE s3, zero, __next")            # 不同色 → 跳过
    # 同色冲突：spin[nj] = (spin[nj] + noise[it]) mod 3
    add("    ADDI t1, zero, %d" % NOISE)
    add("    ADD s3, t1, s1")
    add("    LBU t1, 0(s3)")                   # noise[it]
    add("    ADD t4, t4, t1")
    add("    ADDI s3, zero, 3")
    add("    REM t4, t4, s3")
    add("    ADD s3, s8, t2")
    add("    SB t4, 0(s3)")
    add("__next:")
    add("    ADDI s1, s1, 1")
    add("    JAL zero, __loop")
    add("__done:")
    # 冲突计数 → mem[CNT]
    A.extend(li_lines("t1", CNT))
    add("    SB zero, 0(t1)")
    add("    ADDI s1, zero, 0")
    add("__cnt:")
    add("    ADDI t2, zero, %d" % E_n)
    add("    SLT t2, s1, t2")
    add("    BEQ t2, zero, __cntdone")
    add("    ADD t0, s1, s1")
    A.extend(li_lines("t1", EDGE))
    add("    ADD s3, t1, t0")
    add("    LBU t1, 0(s3)")
    add("    LBU t2, 1(s3)")
    add("    ADD s3, s8, t1")
    add("    LBU t3, 0(s3)")
    add("    ADD s3, s8, t2")
    add("    LBU t4, 0(s3)")
    add("    SUB s3, t3, t4")
    add("    BNE s3, zero, __ce")
    A.extend(li_lines("t1", CNT))
    add("    LBU t3, 0(t1)")
    add("    ADDI t3, t3, 1")
    add("    SB t3, 0(t1)")
    add("__ce:")
    add("    ADDI s1, s1, 1")
    add("    JAL zero, __cnt")
    add("__cntdone:")
    A.extend(li_lines("t1", CNT))
    add("    LBU a0, 0(t1)")
    return "\n".join(A) + "\n", CNT


def li_lines(rd, v):
    """大常数分段装载（trirv_cc load_imm128 同款）：|v|≤1093 一条 ADDI；
    否则按 2187 分段、每段三补码归位（±1093）的 LUI/ADDI 链。
    术语注意：这是「三补码字段编码」（v mod 2187，≥1094 解码为负），
    不是平衡三进制——平衡三进制（数字集 {−1,0,+1}）在本项目非法。"""
    if -1093 <= v <= 1093:
        return [f"    ADDI {rd}, zero, {v}"]
    digits, x = [], v
    while x:
        d = x % 2187
        if d > 1093:
            d -= 2187
        digits.append(d)
        x = (x - d) // 2187
    out = [f"    ADDI {rd}, zero, {digits[-1]}"]
    for d in reversed(digits[:-1]):
        out.append(f"    SLLI {rd}, {rd}, 7")
        if d:
            out.append(f"    ADDI {rd}, {rd}, {d}")
    return out


def build_heatbath_asm(n, edges, sweeps, noise_len):
    """顶点热浴（Glauber 三值版）→ trirv 汇编。4 层循环：sweep/vertex/
    neighbor/argmin。内存布局（tryte 编址）：
      [0, NL)        噪声流
      [NL, NL+n)     自旋数组
      [NL+n, NL+2n)  CSR 行偏移（n+1 tryte，值 ≤ 242）
      CSR+…          CSR 平铺邻居（2E tryte）
      EDGE=FLAT+2E   边表（2E tryte，冲突计数用）
      CNT=EDGE+2E    三色计数器（3 tryte）
      CCNT=CNT+3     冲突计数槽
    语义与 heatbath_repair 逐条对应（噪声消费序/平票破缺/收敛早退）。"""
    E_n = len(edges)
    NL = sweeps * n + 16
    NOISE = 0
    SPINS = NL
    CSR = SPINS + n
    FLAT = CSR + n + 1
    EDGE = FLAT + 2 * E_n
    CNT = EDGE + 2 * E_n
    CCNT = CNT + 3
    A = []
    add = lambda t: A.append(t)
    add("    ADDI s1, zero, 0")                    # sweep
    add("    ADDI s6, zero, %d" % n)               # n 常驻
    add("    ADDI s7, zero, %d" % sweeps)
    add("__sweep:")
    add("    SLT t2, s1, s7")
    add("    BEQ t2, zero, __ccinit")
    add("    ADDI s2, zero, 0")                    # vertex
    add("__vloop:")
    add("    SLT t2, s2, s6")
    add("    BEQ t2, zero, __vend")
    # cnt[0..2] 清零（CNT/CNT+1/CNT+2）
    A.extend(li_lines("t1", CNT))
    add("    SB zero, 0(t1)")
    add("    SB zero, 1(t1)")
    add("    SB zero, 2(t1)")
    # 邻居迭代：k = CSR[v] .. CSR[v+1]
    add("    ADDI t1, zero, %d" % CSR)
    add("    ADD s3, t1, s2")
    add("    LBU s4, 0(s3)")                       # k = CSR[v]
    add("    ADDI s3, s3, 1")
    add("    LBU s5, 0(s3)")                       # kend = CSR[v+1]
    add("__nb:")
    add("    SLT t2, s4, s5")
    add("    BEQ t2, zero, __nbdone")
    add("    ADDI t1, zero, %d" % FLAT)
    add("    ADD s3, t1, s4")
    add("    LBU t3, 0(s3)")                       # nb
    add("    ADDI t1, zero, %d" % SPINS)
    add("    ADD s3, t1, t3")
    add("    LBU t3, 0(s3)")                       # spin[nb]
    # cnt[spin[nb]] += 1：地址 = CNT + c
    A.extend(li_lines("t1", CNT))
    add("    ADD s3, t1, t3")
    add("    LBU t4, 0(s3)")
    add("    ADDI t4, t4, 1")
    add("    SB t4, 0(s3)")
    add("    ADDI s4, s4, 1")
    add("    JAL zero, __nb")
    add("__nbdone:")
    # cur = cnt[spin[v]]
    add("    ADDI t1, zero, %d" % SPINS)
    add("    ADD s3, t1, s2")
    add("    LBU t3, 0(s3)")                       # spin[v]
    A.extend(li_lines("t1", CNT))
    add("    ADD s3, t1, t3")
    add("    LBU t4, 0(s3)")                       # cur
    # argmin（平票取小色）：best=0; if cnt[1]<cnt[0]: best=1; if cnt[2]<cnt[best]: best=2
    add("    ADDI s3, zero, %d" % CNT)
    add("    LBU t0, 0(s3)")                       # cnt[0]
    add("    LBU t1, 1(s3)")                       # cnt[1]
    add("    LBU t2, 2(s3)")                       # cnt[2]
    add("    ADDI t5, zero, 0")                    # best
    add("    SLT t6, t1, t0")
    add("    BNE t6, zero, __a1")
    add("    JAL zero, __a2")
    add("__a1:")
    add("    ADDI t5, zero, 1")
    add("__a2:")
    add("    ADD s3, t1, zero")                    # 占位防误删（t1 复用保护无实义）
    add("    LBU t1, 2(s3)")
    # cnt[2] < cnt[best]：读 cnt[best]（CNT+best）
    A.extend(li_lines("t1", CNT))
    add("    ADD s3, t1, t5")
    add("    LBU t1, 0(s3)")
    add("    SLT t1, t2, t1")
    add("    BEQ t1, zero, __a3")
    add("    ADDI t5, zero, 2")
    add("__a3:")
    # 更新判定：cnt[best] < cur → 更新；== cur 且 best≠spin[v] 且噪声=0 → 更新
    A.extend(li_lines("t1", CNT))
    add("    ADD s3, t1, t5")
    add("    LBU t1, 0(s3)")                       # cnt[best]
    add("    SLT t2, t1, t4")                      # cnt[best] < cur
    add("    BNE t2, zero, __upd")
    add("    SUB t2, t1, t4")                      # == cur？
    add("    BNE t2, zero, __nou")
    add("    SUB t2, t5, t3")                      # best == spin[v]？
    add("    BNE t2, zero, __nou")
    # 噪声 trit：nit = sweep*n + vertex；addr = NOISE + (nit mod NL)
    add("    MUL t2, s1, s6")
    add("    ADD t2, t2, s2")
    add("    ADDI t1, zero, %d" % NL)
    add("    REM t2, t2, t1")
    add("    ADDI t1, zero, %d" % NOISE)
    add("    ADD s3, t1, t2")
    add("    LBU t2, 0(s3)")
    add("    BNE t2, zero, __nou")                 # 噪声≠0 不迁移
    add("__upd:")
    add("    ADDI t1, zero, %d" % SPINS)
    add("    ADD s3, t1, s2")
    add("    SB t5, 0(s3)")
    add("__nou:")
    add("    ADDI s2, s2, 1")
    add("    JAL zero, __vloop")
    add("__vend:")
    # 冲突计数：若 0 → 收敛早退（记 CCNT=0）
    A.extend(li_lines("t1", CCNT))
    add("    SB zero, 0(t1)")
    add("    ADDI s2, zero, 0")                    # edge idx
    add("__cloop:")
    add("    ADDI t2, zero, %d" % (2 * E_n))
    add("    SLT t2, s2, t2")
    add("    BEQ t2, zero, __cdone")
    A.extend(li_lines("t1", EDGE))
    add("    ADD s3, t1, s2")
    add("    LBU t1, 0(s3)")                       # edges[s2]   = ni
    add("    ADDI s3, s2, 1")
    add("    ADDI t2, zero, %d" % EDGE)
    add("    ADD s3, t2, s3")
    add("    LBU t5, 0(s3)")                       # edges[s2+1] = nj（t5——基址寄存器不复用）
    add("    ADDI t2, zero, %d" % SPINS)
    add("    ADD s3, t2, t1")
    add("    LBU t3, 0(s3)")                       # spin[ni]
    add("    ADDI t2, zero, %d" % SPINS)
    add("    ADD s3, t2, t5")
    add("    LBU t4, 0(s3)")                       # spin[nj]
    add("    SUB s3, t3, t4")
    add("    BNE s3, zero, __cne")
    A.extend(li_lines("t1", CCNT))
    add("    LBU t3, 0(t1)")
    add("    ADDI t3, t3, 1")
    add("    SB t3, 0(t1)")
    add("__cne:")
    add("    ADDI s2, s2, 2")
    add("    JAL zero, __cloop")
    add("__cdone:")
    A.extend(li_lines("t1", CCNT))
    add("    LBU t2, 0(t1)")
    add("    BNE t2, zero, __next_sweep")
    add("    JAL zero, __finish")                  # 收敛早退
    add("__next_sweep:")
    add("    ADDI s1, s1, 1")
    add("    JAL zero, __sweep")
    add("__ccinit:")
    add("__finish:")
    # 返回：a0 = CCNT（最终冲突数）
    A.extend(li_lines("t1", CCNT))
    add("    LBU a0, 0(t1)")
    return "\n".join(A) + "\n", dict(NL=NL, SPINS=SPINS, CSR=CSR,
                                      FLAT=FLAT, EDGE=EDGE, CNT=CNT, CCNT=CCNT)

# ------------------------------------------------------------ TriRV 执行

def run_on_trirv(n, edges, sweeps, noise):
    """装配内核 → PyVM 执行 → (最终自旋数组, 冲突数, halted)。"""
    asm, cnt_off = build_kernel_asm(n, edges, sweeps, len(noise))
    E_n = len(edges)
    S = sweeps * E_n
    segs = {0: bytes(noise)}
    eb = bytearray()
    for ni, nj in edges:
        eb += bytes([ni, nj])
    segs[S + n] = bytes(eb)
    prog, _ = trirv_asm.assemble(asm)
    v = trirv_vm.VM(prog, mem_init={a: bytes(b) for a, b in segs.items()})
    v.run(max_steps=2000000)
    spins = [v.mem[S + k] for k in range(n)]
    return spins, v.mem[cnt_off], v.halted


# ------------------------------------------------------------ 主实验
DEMO_EDGES = [(0, 1), (0, 2), (1, 3), (2, 3), (1, 2),
              (3, 4), (2, 4), (0, 4), (4, 5), (3, 5), (0, 5)]


def main():
    n = 6
    print("== p-trit 三值热力学修复退火（图 3-着色，q=3 Potts）==")
    noise = trit_noise(seed=42, n=400)
    c, chi2 = noise_quality(noise)
    print(f"噪声三桶频数 {c}  χ²={chi2:.2f}（df=2 95% 阈值 5.99）→ "
          + ("均匀 ✓" if chi2 < 5.99 else "偏斜 ✗"))
    spins, conf = ref_repair(n, DEMO_EDGES, sweeps=40, noise=noise)
    print(f"Python 参考：最终自旋 {spins}  冲突 {conf}")
    tspins, tcnt, halted = run_on_trirv(n, DEMO_EDGES, sweeps=40, noise=noise)
    same = (tspins == spins and tcnt == conf)
    print(f"TriRV 内核：最终自旋 {tspins}  冲突 {tcnt}  halted={halted}")
    print("差分：", "逐位一致 ✓" if same else "不一致 ✗")
    hb_ok = heatbath_demo()
    return 0 if (same and conf == 0 and hb_ok) else 1




# ------------------------------------------------------------ 热浴内核差分（§九 100% 收敛动力学的 VM 移植）
def run_heatbath_on_trirv(n, edges, sweeps, seed=0):
    """热浴内核执行。布局一致性定版（段重叠破案 2026-11-25）：
    噪声长度必须 = 内核 NL（sweeps×n+16）——旧 runner 生成超长噪声罩住
    自旋段 [NL,+n)，自旋写入腐蚀噪声读出（差分分歧实证）。
    返回 (最终自旋, 冲突数, halted)。"""
    noise = trit_noise(seed, sweeps * n + 16)
    asm, lay = build_heatbath_asm(n, edges, sweeps, len(noise))
    adj = [[] for _ in range(n)]
    for ni, nj in edges:
        adj[ni].append(nj); adj[nj].append(ni)
    csr, flat = bytearray(), bytearray()
    for v in range(n):
        csr.append(len(flat))
        flat += bytes(adj[v])
    csr.append(len(flat))
    segs = {0: bytes(noise), lay["SPINS"]: bytes(n),
            lay["CSR"]: bytes(csr), lay["FLAT"]: bytes(flat)}
    prog, _ = trirv_asm.assemble(asm)
    v = trirv_vm.VM(prog, mem_init={a: bytes(b) for a, b in segs.items()})
    v.run(max_steps=2000000)
    spins = [v.mem[lay["SPINS"] + k] for k in range(n)]
    return spins, v.mem[lay["CCNT"]], v.halted





# ------------------------------------------------------------ 收敛率统计
def gen_3partite(groups, p, seed):
    """三部分图生成器：组内无边、组间以概率 p 连边——构造性 3-可着色。"""
    rng = random.Random(seed)
    ids = []
    start = 0
    for g in range(3):
        ids.append(list(range(start, start + groups[g])))
        start += groups[g]
    edges = []
    for g1 in range(3):
        for g2 in range(g1 + 1, 3):
            for i in ids[g1]:
                for j in ids[g2]:
                    if rng.random() < p:
                        edges.append((i, j))
    return ids, edges


def convergence_stats(groups=(8, 8, 8), p=0.3, seeds=50, sweep_grid=(20, 40, 80, 160)):
    """多种子收敛率：{sweeps: 成功数/seeds}——「能 achieving 到吗」的定量答案。"""
    rows = []
    for sweeps in sweep_grid:
        ok = 0
        for sd in range(seeds):
            _, edges = gen_3partite(groups, p, seed=1000 + sd)
            noise = trit_noise(seed=sd, n=sweeps * len(edges))
            _, conf = ref_repair(sum(groups), edges, sweeps, noise)
            ok += (conf == 0)
        rows.append((sweeps, ok, seeds))
        print(f"  sweeps={sweeps:4d}: 收敛 {ok}/{seeds} = {100 * ok / seeds:.0f}%")
    return rows


def stats_main():
    print("== 收敛率统计（三部分图 n=24, p=0.3, 50 种子）==")
    rows = convergence_stats()
    return rows


def heatbath_demo():
    n = 6
    print("== 热浴内核差分（Glauber 三值版 VM 移植）==")
    vspins, vcnt, halted = run_heatbath_on_trirv(n, DEMO_EDGES, sweeps=40, seed=0)
    vconf = sum(1 for ni, nj in DEMO_EDGES if vspins[ni] == vspins[nj])
    # 参考侧：同 seed 噪声 + 同语义热浴
    noise = trit_noise(0, 40 * n + 16)
    rspins, rconf, _ = heatbath_repair(n, DEMO_EDGES, 40, noise)
    same = (vspins == rspins and vconf == rconf)
    print(f"TriRV 内核：自旋 {vspins} 冲突 {vconf} halted={halted}")
    print(f"Python 参考：自旋 {rspins} 冲突 {rconf}")
    print("差分：", "逐位一致 ✓" if same else "不一致 ✗")
    return same


if __name__ == "__main__":
    pass


def conflicts_of(spins, edges):
    return sum(1 for ni, nj in edges if spins[ni] == spins[nj])


# ------------------------------------------------------------ 顶点热浴动力学（Glauber）
def heatbath_repair(n, edges, sweeps, noise):
    """顶点热浴：每顶点取冲突最小色（平票噪声破缺）——p-bit/Ising 机的
    Glauber 动力学三值版。返回 (最终自旋, 冲突数, 收敛所用 sweep 数)。"""
    adj = [[] for _ in range(n)]
    for ni, nj in edges:
        adj[ni].append(nj)
        adj[nj].append(ni)
    spins = [0] * n
    nit = 0
    done_at = None
    for sweep in range(sweeps):
        for v in range(n):
            cnt = [0, 0, 0]
            for nb in adj[v]:
                cnt[spins[nb] % 3] += 1
            cur = cnt[spins[v]]
            best = min(range(3), key=lambda c: (cnt[c], c))
            t = noise[nit % len(noise)]
            nit += 1
            if cnt[best] < cur:
                spins[v] = best
            elif cnt[best] == cur and best != spins[v] and t == 0:
                spins[v] = best                 # 平票噪声游走（避免死锁）
        if done_at is None and conflicts_of(spins, edges) == 0:
            done_at = sweep + 1
            break
    return spins, conflicts_of(spins, edges), done_at


def heatbath_stats(groups=(8, 8, 8), p=0.3, seeds=50, sweeps=40):
    ok = 0
    for sd in range(seeds):
        _, edges = gen_3partite(groups, p, seed=1000 + sd)
        noise = trit_noise(seed=sd, n=sweeps * (sum(groups) + len(edges)) + 16)
        _, conf, _ = heatbath_repair(sum(groups), edges, sweeps, noise)
        ok += (conf == 0)
    print(f"  热浴 sweeps={sweeps}: 收敛 {ok}/{seeds} = {100 * ok / seeds:.0f}%")
    return ok, seeds


# ------------------------------------------------------------ 热浴 VM 内核（CSR 布局）
