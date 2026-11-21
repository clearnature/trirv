"""统一编译后端 —— 默认走 C 编译器，Python 编译器仅作参考实现保留。

背景：trirv_cc.py 是栈式代码生成的教学实现，方便但慢（解释执行 Python +
逐条字符串拼接汇编）。trirv_cc.c 是寄存器式代码生成的原生实现，编译期开销
是进程启动级别的，与源程序规模基本脱钩。

后端选择：
  默认（不设变量）  -> C 编译器 trirv_cc（源码在同目录，缺失时自动构建）
  TRV_CC=py         -> 回退到 trirv_cc.py（差分测试 / 对照基准用）

对外契约与 trirv_cc.compile_c 完全一致：
    compile_c(src) -> (prog, labels, asm_text, strings)
      prog    : list[int]  20-trit 指令编码
      labels  : dict[str,int]  标签 -> 指令下标
      asm_text: str        汇编文本（可读，供调试/统计）
      strings : dict[int, bytes]  字符串常量段 {地址: 数据}
"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
WORD_TRYTES = 9          # 与 ISA 一致：一个字 = 9 tryte（int/指针同宽）
SRC = os.path.join(HERE, "trirv_cc.c")
BIN = os.path.join(HERE, "trirv_cc")


def _cc_compilers():
    """可用的宿主 C 编译器候选（按优先级）。"""
    for name in (os.environ.get("CC"), "clang", "gcc", "cc"):
        if name and shutil.which(name):
            yield name


def _ensure_binary():
    """确保 C 编译器二进制存在且不旧于源码。"""
    if os.path.exists(BIN) and os.path.getmtime(BIN) >= os.path.getmtime(SRC):
        return BIN
    last = None
    for cc in _cc_compilers():
        cmd = [cc, "-std=gnu11", "-O2", "-o", BIN, SRC, "-lm"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            return BIN
        last = r.stderr
    raise RuntimeError(f"无法构建 C 编译器 {BIN}：{last}")


def _parse_trv(path):
    """解析 TRV1：返回 (prog, strings)。"""
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"TRV1":
        raise ValueError("不是 TRV1 文件")
    _ver, n_inst, n_seg = struct.unpack_from("<III", data, 4)
    off = 16
    prog = list(struct.unpack_from(f"<{n_inst}I", data, off))
    off += 4 * n_inst
    strings = {}
    for _ in range(n_seg):
        addr, ln = struct.unpack_from("<II", data, off)
        off += 8
        strings[addr] = data[off:off + ln]
        off += ln
    return prog, strings


def _parse_asm(text):
    """汇编文本 -> (labels, 指令条数)。标签行形如 `name:`。"""
    labels, count = {}, 0
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.endswith(":"):
            labels[s[:-1]] = count
        else:
            count += 1
    return labels, count


def _log(backend, src, reason=None):
    """TRV_CC_LOG=<path> 时记录每次编译走的是哪个后端（覆盖率统计用）。"""
    path = os.environ.get("TRV_CC_LOG")
    if not path:
        return
    head = " ".join(src.split())[:120]
    with open(path, "a") as f:
        f.write(f"{backend}\t{(reason or [''])[0]}\t{head}\n")


def compile_c(src):
    """把 C 源码编译为 TriRV 程序。

    默认路径：C 编译器（trirv_cc）。若 C 编译器对该源不支持（语法/特性缺失），
    按以下策略处理：
      TRV_CC_STRICT=1 -> 直接抛错（用于验证 C 后端覆盖率时抓缺口）
      否则            -> 回退到 Python 参考实现，并在 stderr 提示一次原因
    回退不是长期方案：C 后端每补齐一项特性，回退次数就减一（见 CC_覆盖率.md）。
    """
    global LAST_BACKEND, FALLBACK_REASONS
    mode = os.environ.get("TRV_CC", "c").lower()
    if mode == "py":
        import trirv_cc as pycc
        LAST_BACKEND = "py"
        return pycc.compile_c(src)

    try:
        result = _compile_c(src)
        LAST_BACKEND = "c"
        _log("c", src)
        return result
    except Exception as e:                       # noqa: BLE001 - 回退需捕获全部编译失败
        if os.environ.get("TRV_CC_STRICT") or mode == "strict":
            _log("py", src, reason=str(e).strip().splitlines()[-1:])
            raise
        reason = str(e).strip().splitlines()
        reason = reason[-1] if reason else "未知错误"
        if reason not in FALLBACK_REASONS:       # 每类原因只提示一次
            FALLBACK_REASONS.append(reason)
            print(f"[trirv_cc_backend] C 后端不支持，回退 Python：{reason}",
                  file=sys.stderr)
        import trirv_cc as pycc
        LAST_BACKEND = "py"
        _log("py", src, reason=[reason])
        return pycc.compile_c(src)


LAST_BACKEND = "?"
FALLBACK_REASONS = []


def _compile_c(src):
    binary = _ensure_binary()
    tmpdir = tempfile.mkdtemp(prefix="trvcc_")
    cpath = os.path.join(tmpdir, "in.c")
    tpath = os.path.join(tmpdir, "out.trv")
    apath = os.path.join(tmpdir, "out.s")
    try:
        with open(cpath, "w") as f:
            f.write(src)
        r = subprocess.run([binary, cpath, tpath, apath],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise ValueError(f"trirv_cc 编译失败: {r.stderr.strip() or r.returncode}")
        prog, strings = _parse_trv(tpath)
        asm_text = ""
        if os.path.exists(apath):
            with open(apath) as f:
                asm_text = f.read()
        labels, n = _parse_asm(asm_text)
        if n != len(prog):
            raise RuntimeError(f"汇编行数 {n} 与机器码条数 {len(prog)} 不一致")
        return prog, labels, asm_text, strings
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        print("用法: python3 trirv_cc_backend.py <src.c> <out.trv>")
        sys.exit(1)
    text = open(sys.argv[1]).read()
    prog, _labels, _asm, strings = compile_c(text)
    buf = bytearray(b"TRV1") + struct.pack("<III", 1, len(prog), len(strings))
    for w in prog:
        buf += struct.pack("<I", w & 0xFFFFFFFF)
    for addr, data in strings.items():
        buf += struct.pack("<II", addr, len(data)) + bytes(data)
    open(sys.argv[2], "wb").write(bytes(buf))
    print(f"{len(prog)} instructions, {len(strings)} strings -> {sys.argv[2]}")
