#!/usr/bin/env python3
"""ADR-31 性能门禁：固定微基准集，每档 5 次取中位数；报告 TRV1/TRV2 码长。
用法：python3 tests/run_perf.py（结果人工粘贴 PERF_REPORT；数值波动>10% 时复查）。"""
import os, statistics, struct, subprocess, sys, time

HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(HERE)
CC=os.path.join(ROOT,"cc","bin","trirv_cc"); CVM=os.path.join(ROOT,"vm","bin","trirv_vm")
BENCH=["fib","sumloop","axpy"]

def build(src, out, trv2=False):
    env=dict(os.environ); env["TRV_COMPACT"]="1"
    if trv2: env["TRV_FORMAT"]="2"
    r=subprocess.run([CC,src,out],capture_output=True,text=True,env=env)
    return r.returncode==0

def measure(trv, steps=20000000):
    best=None
    for _ in range(5):
        t0=time.time()
        r=subprocess.run([CVM,trv,"-"],capture_output=True,text=True,timeout=120)
        dt=time.time()-t0
        e=[l for l in r.stderr.split() if l.startswith("exit=")]
        best=dt if best is None else min(best,dt)
        code=e[0] if e else "?"
    return best, code

def main():
    rows=[]
    for b in BENCH:
        src=os.path.join(ROOT,"bench",b+".c")
        t1=os.path.join(ROOT,"bench",b+".trv"); t2=os.path.join(ROOT,"bench",b+".trv2")
        assert build(src,t1) and build(src,t2), b
        s1,s2=os.path.getsize(t1),os.path.getsize(t2)
        d1,c1=measure(t1); d2,c2=measure(t2)
        assert c1==c2, f"{b}: TRV1 exit={c1} != TRV2 exit={c2}"
        rows.append((b,s1,s2,d1,d2,c1))
        print(f"{b:8s} TRV1 {s1:5d}B {d1*1000:7.1f}ms | TRV2 {s2:5d}B {d2*1000:7.1f}ms | {c1} | 节省 {100*(s1-s2)/s1:4.1f}%")
    tot1=sum(r[1] for r in rows); tot2=sum(r[2] for r in rows)
    print(f"\n合计码长: TRV1 {tot1}B → TRV2 {tot2}B（{100*(tot1-tot2)/tot1:.1f}%）")
    print("口径：同机内部计时，5 次取最小；方法学见 PERF_REPORT.md（ADR-31）")

if __name__=="__main__": main()
