#!/usr/bin/env python3
"""research p-trit 三值热力学计算契约（研究课题三值化 · 批次 ②）。

钉三件事（WASM_ROUTE_A §三谱系的三值化实证）：
  1. 三电平量化噪声均匀性（χ² < 5.99，df=2）
  2. Python 参考修复退火收敛（图 3-着色冲突 = 0）
  3. TriRV VM 内核与 Python 参考逐位一致（自旋数组 + 冲突数）
     ——三值热力学计算内核跑在三值虚拟硬件上
"""
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "research"))

import p_trit_lab as lab

passed = 0
failed = 0


def check(name, good):
    global passed, failed
    if good:
        passed += 1
        print(f"PASS {name}")
    else:
        failed += 1
        print(f"FAIL {name}")


def main():
    n, sweeps = 6, 40
    noise = lab.trit_noise(seed=42, n=400)
    c, chi2 = lab.noise_quality(noise)
    check(f"噪声三桶均匀 χ²={chi2:.2f}<5.99", chi2 < 5.99)
    spins, conf = lab.ref_repair(n, lab.DEMO_EDGES, sweeps, noise)
    check(f"Python 参考收敛 冲突={conf}==0", conf == 0)
    tspins, tcnt, halted = lab.run_on_trirv(n, lab.DEMO_EDGES, sweeps, noise)
    check("TriRV 内核 halted", bool(halted))
    check(f"差分逐位一致（自旋 {tspins} 冲突 {tcnt}）",
          tspins == spins and tcnt == conf)
    # 热浴动力学收敛（Glauber 三值版）：n=24 三部分图 10 种子全收敛
    ok10, seeds10 = lab.heatbath_stats(groups=(8, 8, 8), p=0.3, seeds=10, sweeps=40)
    check(f"热浴动力学收敛 {ok10}/{seeds10}（n=24, sweeps=40）", ok10 == seeds10)
    # 热浴内核 VM 差分（Glauber 三值版跑在三值虚拟硬件上，逐位对拍）
    vsp, vcnt, halted = lab.run_heatbath_on_trirv(n, lab.DEMO_EDGES, sweeps=40, seed=0)
    noise = lab.trit_noise(0, 40 * n + 16)
    rsp, rconf, _ = lab.heatbath_repair(n, lab.DEMO_EDGES, 40, noise)
    vconf = sum(1 for ni, nj in lab.DEMO_EDGES if vsp[ni] == vsp[nj])
    check(f"热浴内核 VM 差分（自旋 {vsp} 冲突 {vconf}=={rconf}）",
          halted and vsp == rsp and vconf == rconf)
    # 热浴收敛率曲线钉死（种子确定性，数据=README 热浴收敛率曲线节）
    r96 = lab.heatbath_curve(sweep_grad=(1,), size_grad=(96,), seeds=20)
    check(f"热浴 3部图 n=96 1 sweep 全收敛（{int(r96[(96, 1)] * 100)}%）",
          r96[(96, 1)] == 1.0)
    rp6 = lab.heatbath_planted_curve(n=48, p_in_grad=(0.6,),
                                     sweep_grad=(5,), seeds=20)
    ok6, med6 = rp6[(0.6, 5)]
    check(f"热浴 planted p=0.6 sw=5 全收敛 中位 {med6} sweep",
          ok6 == 1.0 and med6 is not None and med6 <= 2)
    rp3 = lab.heatbath_planted_curve(n=48, p_in_grad=(0.3,),
                                     sweep_grad=(40,), seeds=20)
    ok3, med3 = rp3[(0.3, 40)]
    check(f"热浴 planted p=0.3 sw=40 饱和 {int(ok3 * 100)}%（局部极小）",
          ok3 == 0.55)
    print(f"\np-trit 契约：{passed} PASS / {failed} FAIL")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
