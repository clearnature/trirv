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
    add("    ADDI t1, zero, %d" % EDGE)
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
    add("    ADDI t1, zero, %d" % CNT)
    add("    SB zero, 0(t1)")
    add("    ADDI s1, zero, 0")
    add("__cnt:")
    add("    ADDI t2, zero, %d" % E_n)
    add("    SLT t2, s1, t2")
    add("    BEQ t2, zero, __cntdone")
    add("    ADD t0, s1, s1")
    add("    ADDI t1, zero, %d" % EDGE)
    add("    ADD s3, t1, t0")
    add("    LBU t1, 0(s3)")
    add("    LBU t2, 1(s3)")
    add("    ADD s3, s8, t1")
    add("    LBU t3, 0(s3)")
    add("    ADD s3, s8, t2")
    add("    LBU t4, 0(s3)")
    add("    SUB s3, t3, t4")
    add("    BNE s3, zero, __ce")
    add("    ADDI t1, zero, %d" % CNT)
    add("    LBU t3, 0(t1)")
    add("    ADDI t3, t3, 1")
    add("    SB t3, 0(t1)")
    add("__ce:")
    add("    ADDI s1, s1, 1")
    add("    JAL zero, __cnt")
    add("__cntdone:")
    add("    ADDI t1, zero, %d" % CNT)
    add("    LBU a0, 0(t1)")
    return "\n".join(A) + "\n", CNT


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
    return 0 if same and conf == 0 else 1


if __name__ == "__main__":
    sys.exit(main())


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
