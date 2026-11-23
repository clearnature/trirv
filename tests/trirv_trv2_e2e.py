#!/usr/bin/env python3
"""TriRV TRV2 打包格式端到端契约（B1① 正式化，2026-11-23 拍板 74cbad1）。

TRV2 = tryte 流容器（TC 3-tryte / 全宽 4-tryte，首 tryte %27>=23 判宽；
跳转 imm = tryte delta）。双引擎策略：TRV1 槽制引擎原样保留（历史生态
零改动），TRV2 流制引擎并行（VM.from_stream）；编译器按 .trv2 发射。

契约：
  1. pack/roundtrip：流打包 → 解包 word 级一致（TC/全宽混合）；
  2. 迁移等价：无烘焙地址常量的程序，槽制执行 == 流制执行（退出码/步数）；
  3. TC 分支在流制下真实生效（delta7 tryte 口径）；
  4. 密度：含 TC 程序流长 < 槽制字节长（节省兑现）。
"""
import os
import sys
import tempfile
import subprocess

import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import trirv_isa as isa
import trirv_vm as vm
import trirv_cc_backend as cc

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CC = os.path.join(ROOT, "cc", "bin", "trirv_cc")

passed = 0
failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print("PASS %s: %r" % (name, got))
    else:
        failed += 1
        print("FAIL %s: got=%r want=%r" % (name, got, want))


def load_trv(path):
    import struct
    data = open(path, "rb").read()
    n_inst, n_seg = struct.unpack_from("<II", data, 8)
    prog = list(struct.unpack_from("<%dI" % n_inst, data, 16))
    off = 16 + 4 * n_inst
    mem = {}
    for _ in range(n_seg):
        addr, ln = struct.unpack_from("<II", data, off)
        off += 8
        mem[addr] = data[off:off + ln]
        off += ln
    return prog, mem


# ================ §1 pack/roundtrip 一致性 ================
def test_roundtrip():
    words = [
        isa.encode("ADDI", rd=10, rs1=0, imm=5),
        isa.encode_c_word(0, 5, (0 << 0) + (2 * 81)),   # C-ADDI a0+=2（rd_s=5,rs1_s=0,imm4=2）
        isa.encode("LW", rd=5, rs1=8, imm=0),
        isa.encode_c_word(1, 0, 27),                     # C-LWs t0, 27(s0)
        isa.encode("BEQ", rd=5, rs1=0, imm=12),          # B 型 imm 分散域
        isa.encode_c_word(3, 0, 9),                      # C-BEQ t0, +9
        isa.encode("JAL", rd=1, imm=100),
        isa.encode("ECALL"),
    ]
    stream = isa.pack_stream(words)
    back = isa.stream_to_words(stream)
    check("roundtrip·word 级一致", back, words)
    check("roundtrip·宽度表", [isa.c_word_width(w) for w in words],
          [4, 3, 4, 3, 4, 3, 4, 4])
    check("roundtrip·偏移表", isa.stream_offsets(stream),
          [0, 4, 7, 11, 14, 18, 21, 25])
    check("roundtrip·流长节省", len(stream) < 4 * len(words), True)


# ================ §2 迁移等价（槽制 == 流制） ================
CASES = [
    ("自增循环",
     "int main(){ int x=5; int y=0; while(y<3){ x=x+2; y=y+1; } return x; }", 11),
    ("真值分支+TC 分支",
     "int f(int a){ int s=0; while(a){ s=s+a; a=a-1; } return s; } "
     "int main(){ return f(5); }", 15),
    ("调用链（JAL 重编）",
     "int inc3(int v){ v=v+3; return v; } "
     "int main(){ int a=inc3(4); a=a+10; int b=inc3(a); return b; }", 20),
    ("递归（JAL 深跳）",
     "int fib(int n){ if(n<2){ return n; } return fib(n-1)+fib(n-2); } "
     "int main(){ return fib(10); }", 55),
    ("全局数组（无烘焙地址常量）",
     "int g[8]; int main(){ int i=0; int s=0; while(i<8){ g[i]=i*3; i=i+1; } "
     "while(i>0){ i=i-1; s=s+g[i]; } return s; }", 84),
]


def _compile_tc(src):
    """以 TRV_COMPACT=1 编译（含 TC 词——迁移器才有宽度混合可处理）。"""
    os.environ["TRV_COMPACT"] = "1"
    try:
        return cc.compile_c(src)
    finally:
        os.environ.pop("TRV_COMPACT", None)


def test_migrate_equiv():
    for name, src, want in CASES:
        prog, labels, asmtext, strings = _compile_tc(src)
        try:
            stream = isa.migrate_trv1_stream(prog)
        except ValueError as e:
            check(name + "·迁移", "ERR:%s" % e, "ok")
            continue
        mem = strings or None
        c_slot = vm.VM(prog, mem_init=mem).run(max_steps=200000)
        c_stream = vm.VM.from_stream(stream, mem_init=mem).run(max_steps=200000)
        check(name + "·槽制退出码", c_slot.exit_code, want)
        check(name + "·流制退出码一致", c_stream.exit_code, want)
        check(name + "·流制停机", c_stream.halted, True)
        n_tc = sum(1 for w in prog if (w % 243) % 27 >= 23)
        check(name + "·密度兑现（含 TC 时流更短）",
              (len(stream) < 4 * len(prog)) if n_tc else True, True)


def write_trv2(path, stream, mem):
    """TRV2 文件：magic TRV2 + version + n_trytes + n_seg + 流 + 段表。"""
    import struct
    with open(path, "wb") as f:
        f.write(b"TRV2")
        f.write(struct.pack("<III", 1, len(stream), len(mem)))
        f.write(bytes(stream))
        for addr, data in mem.items():
            f.write(struct.pack("<II", addr, len(data)))
            f.write(bytes(data))


CVM = os.path.join(ROOT, "vm", "bin", "trirv_vm")


def run_cvm(path):
    r = subprocess.run([CVM, path, "-"], capture_output=True, text=True)
    errs = [l for l in r.stderr.split() if l.startswith("exit=")]
    return int(errs[0].split("=")[1]) if errs else None


# ================ §3 流制非法目标诚实停机 ================
def test_misaligned():
    words = [
        isa.encode("ADDI", rd=10, rs1=0, imm=1),
        isa.encode("JALR", rd=0, rs1=10, imm=0),   # x10=1：非指令起点
    ]
    stream = isa.pack_stream(words)
    c = vm.VM.from_stream(stream).run(max_steps=100)
    check("misaligned·JALR 未命中指令起点停机", c.halted, True)


# ================ §3b 编译器 .trv2 直发（sym 重写 + TC 分支 tryte delta） ================
CC2_CASES = [
    ("TRV2·自增+TC 分支",
     "int main(){ int x=5; int y=0; while(y<3){ x=x+2; y=y+1; } return x; }", 11),
    ("TRV2·调用链（JAL 重编）",
     "int inc3(int v){ v=v+3; return v; } "
     "int main(){ int a=inc3(4); a=a+10; int b=inc3(a); return b; }", 20),
    ("TRV2·fn-ptr（sym 地址重写）",
     "int inc(int x){ return x+1; } "
     "int main(){ int (*fp)(int); fp = inc; return (*fp)(41); }", 42),
    ("TRV2·递归（JAL 深跳+sym）",
     "int fact(int n){ if(n<=1){ return 1; } return n*fact(n-1); } "
     "int main(){ return fact(5); }", 120),
]


def compile_trv2(src, compact=True):
    cf = tempfile.mktemp(suffix=".c")
    tf = tempfile.mktemp(suffix=".trv2")
    with open(cf, "w") as f:
        f.write(src)
    env = dict(os.environ)
    if compact:
        env["TRV_COMPACT"] = "1"
    else:
        env.pop("TRV_COMPACT", None)
    r = subprocess.run([CC, cf, tf], capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise RuntimeError("编译失败: %s" % (r.stderr or r.stdout)[:200])
    return tf


def load_trv2(path):
    import struct
    data = open(path, "rb").read()
    assert data[:4] == b"TRV2", "magic"
    ver, n_trytes, n_seg = struct.unpack_from("<III", data, 4)
    stream = list(data[16:16 + n_trytes])
    off = 16 + n_trytes
    mem = {}
    for _ in range(n_seg):
        addr, ln = struct.unpack_from("<II", data, off)
        off += 8
        mem[addr] = data[off:off + ln]
        off += ln
    return stream, mem


def test_cc_trv2():
    for name, src, want in CC2_CASES:
        tf2 = compile_trv2(src, compact=True)
        stream, mem = load_trv2(tf2)
        i = 0
        cnt_tc = 0
        while i < len(stream):
            w = 3 if stream[i] % 27 >= 23 else 4
            if w == 3:
                cnt_tc += 1
            i += w
        check(name + "·含 TC 槽", cnt_tc > 0, True)
        c_py = vm.VM.from_stream(stream, mem_init=mem).run(max_steps=200000)
        check(name + "·PyVM 流制退出码", c_py.exit_code, want)
        check(name + "·PyVM 停机", c_py.halted, True)
        check(name + "·CVM 流制退出码", run_cvm(tf2), want)


# ================ §4 CVM 双引擎（TRV1 槽制 / TRV2 流制） ================
def test_cvm():
    for name, src, want in CASES[:3]:
        prog, labels, asmtext, strings = _compile_tc(src)
        try:
            stream = isa.migrate_trv1_stream(prog)
        except ValueError:
            continue
        tf1 = tempfile.mktemp(suffix=".trv")
        tf2 = tempfile.mktemp(suffix=".trv2")
        _write_trv1(tf1, prog, strings or {})
        write_trv2(tf2, stream, strings or {})
        check("CVM·%s·TRV1 槽制" % name, run_cvm(tf1), want)
        check("CVM·%s·TRV2 流制" % name, run_cvm(tf2), want)
        check("CVM·%s·TRV2 更小" % name,
              os.path.getsize(tf2) < os.path.getsize(tf1), True)


def _write_trv1(path, prog, mem):
    import struct
    with open(path, "wb") as f:
        f.write(b"TRV1")
        f.write(struct.pack("<III", 1, len(prog), len(mem)))
        for w in prog:
            f.write(struct.pack("<I", w))
        for addr, data in mem.items():
            f.write(struct.pack("<II", addr, len(data)))
            f.write(bytes(data))


def main():
    test_roundtrip()
    test_migrate_equiv()
    test_misaligned()
    test_cc_trv2()
    test_cvm()
    print("\nTRV2 正式化契约：%d PASS / %d FAIL" % (passed, failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
