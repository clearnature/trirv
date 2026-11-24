#!/usr/bin/env python3
"""spec_run —— WebAssembly 官方 spec testsuite 基线运行器（ADR-21/批次 9.1）。

架构（生态标准做法）：
    .wast --wast2json--> .json 命令流 + .wat/.wasm 模块
    模块 --wat2wasm--> .wasm --trirv_wasm--> .trv --PyVM--> 结果

i32 结果通道：入口返回值由 _start SW 到 RESULT_ADDR=180（全宽 9 tryte），
运行后从 VM 内存读回 mod 2^32 与期望值比对（exit 通道 8 bit 装不下 i32）。

诚实计数口径：
  pass    = assert_return 比对一致 / assert_trap 得到 134 陷阱退出
  fail    = 翻译成功但结果不符 / 该 trap 没触发
  tskip   = 模块翻译不支持（Unsupported，后续断言随之跳过）——批次 9.2+ 补
  vskip   = 断言类型本 runner 无法裁决（assert_invalid/malformed/unlinkable
            = 需 wasm 验证器；assert_exhaustion = 无燃料计量；register/get/
            非 i32 参返 = 类型面未实现）——计数单列，不混进 pass/fail

wabt 位置：tmp/wabt/wabt-1.0.42/bin（本地开发依赖，不入库；
CI 用平台包管理器安装——ci.yml 后续批次补）。

用法：
    python3 wasm/spec_run.py                     # 全量 258 文件
    python3 wasm/spec_run.py if.wast local.wast  # 指定文件（名字不含 .wast 亦可）
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import _bootstrap  # noqa: F401
import _trvio
import trirv_vm
import trirv_wasm
from trirv_wasm import compile_wasm, Unsupported, RESULT_ADDR

TS = os.path.join(ROOT, "third_party", "wasm-testsuite")
WABT = os.path.join(ROOT, "tmp", "wabt", "wabt-1.0.42", "bin")
WAST2JSON = os.path.join(WABT, "wast2json")
WAT2WASM = os.path.join(WABT, "wat2wasm")

M32 = 2 ** 32


def s45(word):
    """45-trit 模值 → 带符号。"""
    word %= 3 ** 45
    return word - 3 ** 45 if word >= 3 ** 44 // 2 else word


def run_module(wasm_bytes, field, args):
    """编译+PyVM 执行一次调用 → (结果 u32 或 None, 'ok'|'trap'|'err')。"""
    try:
        asm, segs, _ = compile_wasm(wasm_bytes, entry=field, args=args)
    except Unsupported:
        return None, "unsup"
    except Exception:
        return None, "cerr"
    import trirv_asm
    prog, _ = trirv_asm.assemble(asm)
    mem = {a: bytes(b) for a, b in segs.items()}
    v = trirv_vm.VM(prog, mem_init=mem)
    v.run(max_steps=3000000)
    if v.exit_code == 134:
        return None, "trap"
    if not getattr(v, "halted", True):
        return None, "err"     # 未停机=死循环/步数耗尽
    # exit 通道 = 返回值 &0xFF（历史契约），非零是正常结果不是错误；
    # 全宽结果走 RESULT_ADDR 内存通道
    word = 0
    for k in range(9):
        word += v.mem[RESULT_ADDR + k] * 3 ** k
    return s45(word) % M32, "ok"


def main():
    names = sys.argv[1:]
    files = sorted(f for f in os.listdir(TS) if f.endswith(".wast"))
    if names:
        files = [f if f.endswith(".wast") else f + ".wast" for f in names
                 if (f if f.endswith(".wast") else f + ".wast") in
                 set(os.listdir(TS))]
    tot = {"pass": 0, "fail": 0, "tskip": 0, "vskip": 0}
    rows = []
    work = tempfile.mkdtemp(prefix="spec_", dir=os.path.join(ROOT, "tmp"))
    for fn in files:
        outd = os.path.join(work, fn[:-5])
        os.makedirs(outd, exist_ok=True)
        jsonp = os.path.join(outd, "out.json")
        try:
            r = subprocess.run([WAST2JSON, "--enable-all", os.path.join(TS, fn),
                                "-o", jsonp], capture_output=True, timeout=60)
            if r.returncode != 0:
                rows.append((fn, "wast2json 失败", 0, 0, 0, 0))
                continue
        except subprocess.TimeoutExpired:
            rows.append((fn, "wast2json 超时", 0, 0, 0, 0))
            continue
        cmds = json.load(open(jsonp))["commands"]
        cur = {"wasm": None, "ok": False}
        p = f = t = v = 0
        fails_shown = [0]
        for c in cmds:
            ct = c["type"]
            if ct == "module":
                src = os.path.join(outd, c["filename"])
                cur["wasm"] = src if os.path.exists(src) else None
                cur["ok"] = cur["wasm"] is not None
            elif ct in ("assert_return", "action", "assert_trap"):
                act = c.get("action", {})
                if act.get("type") != "invoke" or not cur["ok"]:
                    v += 1
                    continue
                field = act.get("field")
                aargs = act.get("args", [])
                exps = c.get("expected", [])
                if ct == "assert_return" and exps and \
                   exps[0].get("type") != "i32":
                    v += 1
                    continue
                if any(a.get("type") != "i32" for a in aargs):
                    v += 1
                    continue
                argv = [int(a["value"]) % M32 for a in aargs]
                data = open(cur["wasm"], "rb").read()
                res, st = run_module(data, field, argv)
                if st == "unsup":
                    t += 1
                    cur["ok"] = False          # 同模块后续断言一并 tskip
                    continue
                if st == "cerr":
                    f += 1
                    continue
                if ct == "assert_trap":
                    if st == "trap":
                        p += 1
                    else:
                        f += 1
                elif ct == "assert_return":
                    if not exps:
                        if st == "ok":
                            p += 1
                        else:
                            f += 1
                    else:
                        exp = int(exps[0]["value"]) % M32
                        if st == "ok" and res == exp:
                            p += 1
                        else:
                            f += 1
                            if os.environ.get("SPEC_DEBUG") and fails_shown[0] < 5:
                                fails_shown[0] += 1
                                print("  FAIL %s %s(%s) got=%s want=%d st=%s"
                                      % (fn, field, argv, res, exp, st))
                else:                           # action：只要求正常执行
                    if st == "ok":
                        p += 1
                    else:
                        f += 1
            elif ct in ("assert_invalid", "assert_malformed", "assert_uninstantiable",
                        "assert_unlinkable", "assert_exhaustion", "register"):
                v += 1
            # 其他（np_/script 等）忽略
        tot["pass"] += p
        tot["fail"] += f
        tot["tskip"] += t
        tot["vskip"] += v
        rows.append((fn, "", p, f, t, v))
    print("%-28s %6s %6s %6s %6s" % ("文件", "pass", "fail", "tskip", "vskip"))
    for fn, note, p, f, t, v in rows:
        print("%-28s %6d %6d %6d %6d %s" % (fn, p, f, t, v, note))
    print("-" * 60)
    print("合计 pass=%d fail=%d tskip=%d vskip=%d" %
          (tot["pass"], tot["fail"], tot["tskip"], tot["vskip"]))


if __name__ == "__main__":
    main()
