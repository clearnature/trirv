#!/usr/bin/env python3
"""trirv_link_e2e —— 多文件链接（mini linker）端到端验证。

覆盖（全部 TRV_CC_STRICT=1，C 后端）：
  1. 跨文件调用：库函数 + 主文件 main，先原型后定义
  2. 多 -l 顺序无关：库文件在主文件前后均可
  3. 重复定义检测：同一函数在两个文件定义 -> link error 退出码 1
  4. 原型声明：type name(参数表); 只签名无体，不参与代码生成
  5. 正式分层：trirv_busybox.c -l trirv_musl.c -l trirv_crt.c
     （替代 Python 文本拼接，这就是真 musl 链接路径的最小语义）
  6. 拼接边界：文件末尾无换行时自动补（避免 token 粘连）
  7. 双 VM 一致
"""
import os
import struct
import subprocess
import sys
import tempfile

import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import trirv_vm as vm

HERE = os.path.dirname(os.path.abspath(__file__))
CVM = os.environ.get("TRIRV_VM_BIN", os.path.join(HERE, "..", "vm", "bin", "trirv_vm"))
CC = os.path.join(HERE, "..", "cc", "bin", "trirv_cc")

passed = 0
failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"PASS {name}: {got!r}")
    else:
        failed += 1
        print(f"FAIL {name}: got={got!r} want={want!r}")


def link_and_load(main_src, libs=(), expect_rc=0):
    """-l 链接 -> (prog, mem)。编译失败返回 None。"""
    cf = tempfile.mktemp(suffix=".c")
    tf = tempfile.mktemp(suffix=".trv")
    with open(cf, "w") as f:
        f.write(main_src)
    args = [CC, cf, tf]
    for lib in libs:
        args += ["-l", lib]
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != expect_rc:
        return None
    if expect_rc != 0:
        return ("LINK_ERR", r.stderr)
    data = open(tf, "rb").read()
    n_inst, n_seg = struct.unpack_from("<II", data, 8)
    prog = list(struct.unpack_from(f"<{n_inst}I", data, 16))
    off = 16 + 4 * n_inst
    mem = {}
    for _ in range(n_seg):
        addr, ln = struct.unpack_from("<II", data, off)
        off += 8
        mem[addr] = data[off:off + ln]
        off += ln
    os.unlink(cf)
    os.unlink(tf)
    return prog, mem


def run_py(prog, mem, argv=None, stdin=b"", max_steps=100000):
    c = vm.VM(prog, mem_init=mem, stdin=stdin, argv=argv or []).run(max_steps=max_steps)
    return c.exit_code, bytes(c.output)


# ================ 1. 跨文件调用 ================
lib1 = tempfile.mktemp(suffix=".c")
with open(lib1, "w") as f:
    f.write('int lib_len(const char *s) { int n = 0; while (s[n] != 0) n = n + 1; return n; }\n')

main1 = tempfile.mktemp(suffix=".c")
with open(main1, "w") as f:
    f.write('int lib_len(const char *s);\n'
            'int helper(int x);\n'
            'int main() { return lib_len("abcd") + helper(10); }\n'
            'int helper(int x) { return x * 2; }\n')

r = link_and_load(open(main1).read(), [lib1])
check("链接/跨文件调用编译", r is not None and not isinstance(r[0], str), True)
got, _ = run_py(*r)
check("链接/跨文件调用执行", got, 24)

# ================ 2. 多 -l 与顺序无关 ================
lib2 = tempfile.mktemp(suffix=".c")
with open(lib2, "w") as f:
    f.write('int lib_twice(int x) { return x * 2; }\n')
main2 = tempfile.mktemp(suffix=".c")
with open(main2, "w") as f:
    f.write('int lib_len(const char *s);\n'
            'int lib_twice(int x);\n'
            'int main() { return lib_len("ab") + lib_twice(5); }\n')
r = link_and_load(open(main2).read(), [lib1, lib2])
got, _ = run_py(*r)
check("链接/双库按序", got, 2 + 10)
r = link_and_load(open(main2).read(), [lib2, lib1])
got, _ = run_py(*r)
check("链接/双库乱序", got, 12)

# 库当第一个位置参数（主文件角色互换）也按拼接顺序工作
r = link_and_load(open(lib1).read() +
                  'int main() { return lib_len("xyz"); }\n', [])
got, _ = run_py(*r)
check("链接/单文件多段", got, 3)

# ================ 3. 重复定义检测 ================
dup1 = tempfile.mktemp(suffix=".c")
with open(dup1, "w") as f:
    f.write('int dup() { return 1; }\n')
dup2 = tempfile.mktemp(suffix=".c")
with open(dup2, "w") as f:
    f.write('int dup() { return 2; }\nint main() { return dup(); }\n')
r = link_and_load(open(dup2).read(), [dup1], expect_rc=1)
ok = isinstance(r[0], str) and "duplicate function 'dup'" in r[1]
check("链接/重复定义报错", ok, True)

# 单文件内部重复也拦
r = link_and_load('int f(){ return 1; }\nint f(){ return 2; }\n'
                  'int main(){ return f(); }\n', [], expect_rc=1)
ok = isinstance(r[0], str) and "duplicate function 'f'" in r[1]
check("链接/单文件重复", ok, True)

# ================ 4. 原型声明不产码 ================
r = link_and_load('int foo(int x);\n'
                  'int main(){ return foo(4); }\n'
                  'int foo(int x){ return x+1; }\n', [])
got, _ = run_py(*r)
check("链接/原型+定义共存", got, 5)

# ================ 5. 正式分层：busybox = musl + crt ================
MUSL_P = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/cc/trirv_musl.c")
CRT_P = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/cc/trirv_crt.c")
BB_P = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/cc/trirv_busybox.c")
r = link_and_load(open(BB_P).read(), [MUSL_P, CRT_P])
check("链接/busybox 分层编译", r is not None and not isinstance(r[0], str), True)
prog, mem = r

got, out = run_py(prog, mem, argv=["bb", "echo", "linked", "layer"])
check("链接/busybox echo", (out.decode('latin1'), got), ("linked layer\n", 0))
got, out = run_py(prog, mem, argv=["bb", "basename", "/usr/bin/env"])
check("链接/busybox basename", (out.decode('latin1'), got), ("env\n", 0))
got, out = run_py(prog, mem, argv=["bb", "wc"], stdin=b"layered")
check("链接/busybox wc", out.decode('latin1'), "7\n")

# ================ 6. 拼接边界（末尾无换行） ================
nolib_nl = tempfile.mktemp(suffix=".c")
with open(nolib_nl, "w") as f:
    f.write('int t(){ return 8; }')          # 故意无结尾换行
main_nl = tempfile.mktemp(suffix=".c")
with open(main_nl, "w") as f:
    f.write('int t(void);\nint main(){ return t(); }')   # 也无换行
r = link_and_load(open(main_nl).read(), [nolib_nl])
got, _ = run_py(*r)
check("链接/无换行边界", got, 8)

# ================ 7. C VM 跑分层产物 ================
if os.path.exists(CVM):
    cf = tempfile.mktemp(suffix=".c")
    tf = tempfile.mktemp(suffix=".trv")
    with open(cf, "w") as f:
        f.write(open(BB_P).read())
    subprocess.run([CC, cf, tf, "-l", MUSL_P, "-l", CRT_P],
                   check=True, capture_output=True)
    r2 = subprocess.run([CVM, tf, "--", "echo", "cvm", "layer"],
                        capture_output=True)
    errs = [l for l in r2.stderr.decode().split() if l.startswith("exit=")]
    check("CVM/分层链接运行",
          (r2.stdout.decode('latin1'), int(errs[0].split("=")[1])),
          ("cvm layer\n", 0))
    os.unlink(cf)
    os.unlink(tf)

# ================ 8. 跨单元全局独立（§5.7 数据符号化，2026-11-22） ================
# 主程序与库各自有全局：数据地址符号化（&name）后互不踩踏。
# 坑实录：旧实现库 LINE 体烘焙独立编译期绝对地址，主文件全局槽撞库全局
# （ecvt IN[40] 覆盖 ecvt_tri 57 槽实测）；§5.7 登记为链接器专项核心。
lib_g = tempfile.mktemp(suffix=".c")
with open(lib_g, "w") as f:
    f.write("int lib_counter = 100;\n"
            "int lib_bump(void){ lib_counter = lib_counter + 5; return lib_counter; }\n")
trvo_g = tempfile.mktemp(suffix=".trvo")
subprocess.run([CC, lib_g, trvo_g], check=True, capture_output=True)
main_g = tempfile.mktemp(suffix=".c")
with open(main_g, "w") as f:
    f.write("int lib_bump(void);\n"
            "int main_arr[10];\n"
            "int main(void){\n"
            "    main_arr[0] = 1; main_arr[9] = 9;\n"
            "    int x = lib_bump();\n"
            "    int y = lib_bump();\n"
            "    return x + y*10 + main_arr[0]*100 + main_arr[9]*1000 + lib_counter*10000;\n"
            "}\n")
r = link_and_load(open(main_g).read(), [trvo_g])
got, _ = run_py(*r)
check("跨单元/全局独立(主数组+库计数器)", got, (105 + 1100 + 100 + 9000 + 1100000) % 256)

# ================ 9. 导入函数作值（A4，2026-11-22） ================
# 库单元函数名作实参传 fn-ptr 形参：符号 &name 汇编期绑定到导入体标签。
# 坑实录：fast 实参路径原对 fn 名静默发 ADDI 0；gen 期烘焙地址对导入体
# 不可得（TVO_OUT 尚未拼入 OUT）——符号化天然免疫。
lib_g2 = tempfile.mktemp(suffix=".c")
with open(lib_g2, "w") as f:
    f.write("int lib_bump(void){ lib_counter_dummy_none(); return 105; }\n"
            .replace("lib_counter_dummy_none(); ", ""))   # 纯返回 105
trvo_g2 = tempfile.mktemp(suffix=".trvo")
subprocess.run([CC, lib_g2, trvo_g2], check=True, capture_output=True)
main_g2 = tempfile.mktemp(suffix=".c")
with open(main_g2, "w") as f:
    f.write("int lib_bump(void);\n"
            "int call0(int (*f)(void)){ return (*f)(); }\n"
            "int main(void){ return call0(lib_bump) - 105; }\n")
r = link_and_load(open(main_g2).read(), [trvo_g2])
got, _ = run_py(*r)
check("跨单元/导入函数作值(fn-ptr 形参)", got, 0)

# ================ 10. 跨单元全局同名合并（A3，2026-11-22） ================
# C file-scope 语义：tentative（无 init）跨单元合并共享存储；有 init 者为准；
# 双 init = 重复定义诚实报错；extern 声明 = 纯引用。
# 坑实录：旧实现同名全局各得一块独立存储且无任何报错——跨单元读写静默不同步。
# --- R1 tentative 共享：库写 shared[3]=7，主写 shared[0]=3 读 shared[3] ---
lib_r1 = tempfile.mktemp(suffix=".c")
with open(lib_r1, "w") as f:
    f.write("int shared[4];\n"
            "int lib_set3(void){ shared[3] = 7; return 0; }\n")
trvo_r1 = tempfile.mktemp(suffix=".trvo")
subprocess.run([CC, lib_r1, trvo_r1], check=True, capture_output=True)
main_r1 = tempfile.mktemp(suffix=".c")
with open(main_r1, "w") as f:
    f.write("int lib_set3(void);\n"
            "int shared[4];\n"
            "int main(void){ shared[0] = 3; lib_set3(); return shared[3]*10 + shared[0]; }\n")
r = link_and_load(open(main_r1).read(), [trvo_r1])
got, _ = run_py(*r)
check("A3/tentative 共享(库写7主写3)", got, 73)

# --- R2 有 init 者为准：库 GLOB 带 hex 初值 42，主 tentative 读取 ---
lib_r2 = tempfile.mktemp(suffix=".c")
with open(lib_r2, "w") as f:
    f.write("int cfg = 42;\n"
            "int cfg_get(void){ return cfg; }\n")
trvo_r2 = tempfile.mktemp(suffix=".trvo")
subprocess.run([CC, lib_r2, trvo_r2], check=True, capture_output=True)
main_r2 = tempfile.mktemp(suffix=".c")
with open(main_r2, "w") as f:
    f.write("int cfg_get(void);\n"
            "int cfg;\n"
            "int main(void){ return cfg_get()*10 + cfg; }\n")
r = link_and_load(open(main_r2).read(), [trvo_r2])
got, _ = run_py(*r)
check("A3/有init者为准(两路读都42)", got, (42*10 + 42) & 0xFF)

# --- R3 双初始化 = 重复定义诚实报错 ---
lib_r3 = tempfile.mktemp(suffix=".c")
with open(lib_r3, "w") as f:
    f.write("int dup = 5;\n")
trvo_r3 = tempfile.mktemp(suffix=".trvo")
subprocess.run([CC, lib_r3, trvo_r3], check=True, capture_output=True)
main_r3 = tempfile.mktemp(suffix=".c")
with open(main_r3, "w") as f:
    f.write("int dup = 7;\n"
            "int main(void){ return dup; }\n")
cf3 = tempfile.mktemp(suffix=".c")
with open(cf3, "w") as f:
    f.write(open(main_r3).read())
r = subprocess.run([CC, cf3, tempfile.mktemp(suffix=".trv"), "-l", trvo_r3],
                   capture_output=True, text=True)
good = r.returncode != 0 and "duplicate global" in r.stderr
if good:
    passed += 1
    print("PASS A3/双初始化诚实报错")
else:
    failed += 1
    print(f"FAIL A3/双初始化诚实报错: rc={r.returncode} stderr={r.stderr.strip()[:100]}")

# --- R4 extern 声明 = 纯引用（lib_g.trvo: lib_bump/lib_counter） ---
lib_g4 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tmp_symtest", "lib_g.trvo")
if not os.path.exists(lib_g4):
    lib_g4_src = tempfile.mktemp(suffix=".c")
    with open(lib_g4_src, "w") as f:
        f.write("int lib_counter = 100;\n"
                "int lib_bump(void){ lib_counter = lib_counter + 5; return lib_counter; }\n")
    lib_g4 = tempfile.mktemp(suffix=".trvo")
    subprocess.run([CC, lib_g4_src, lib_g4], check=True, capture_output=True)
main_r4 = tempfile.mktemp(suffix=".c")
with open(main_r4, "w") as f:
    f.write("extern int lib_counter;\n"
            "extern int lib_bump(void);\n"
            "int main(void){ lib_bump(); return lib_counter - 105; }\n")
try:
    r = link_and_load(open(main_r4).read(), [lib_g4])
    got, _ = run_py(*r)
    good = got == 0
    if good:
        passed += 1
        print("PASS A3/extern 声明纯引用")
    else:
        failed += 1
        print(f"FAIL A3/extern 声明纯引用: got={got}")
except Exception as e:
    failed += 1
    print(f"FAIL A3/extern 声明纯引用: 编译失败 {str(e)[:80]}")

# ================ 11. 弱全局变量（A6 顺延，2026-11-22） ================
# #pragma weak <名>（无目标）= 弱全局定义：合并时同名单方有强初始化 → 强胜出。
# TRVO 格式扩 GLOBW 记录（弱全局+hex）。musl 场景：weak 定义的默认值让位平台强定义。
# W1 强胜弱：库弱定义 cfg=42，主强定义 cfg=99 → 两路读 99（今天 duplicate 报错 → RED）
lib_w1 = tempfile.mktemp(suffix=".c")
with open(lib_w1, "w") as f:
    f.write("#pragma weak cfg\n"
            "int cfg = 42;\n"
            "int cfg_get(void){ return cfg; }\n")
trvo_w1 = tempfile.mktemp(suffix=".trvo")
subprocess.run([CC, lib_w1, trvo_w1], check=True, capture_output=True)
main_w1 = tempfile.mktemp(suffix=".c")
with open(main_w1, "w") as f:
    f.write("int cfg_get(void);\n"
            "int cfg = 99;\n"
            "int main(void){ return cfg_get()*10 + cfg; }\n")
cf_w1 = tempfile.mktemp(suffix=".c")
with open(cf_w1, "w") as f:
    f.write(open(main_w1).read())
tf_w1 = tempfile.mktemp(suffix=".trv")
r = subprocess.run([CC, cf_w1, tf_w1, "-l", trvo_w1], capture_output=True, text=True)
if r.returncode != 0:
    # RED 期形态：A3 双初始化报错——弱标记落地后此路径消失
    check("A3s/强胜弱(两路读99)", "compile error", 0)
else:
    got, _ = run_py(*link_and_load(open(main_w1).read(), [trvo_w1]))
    check("A3s/强胜弱(两路读99)", got, (99*10 + 99) & 0xFF)

# W2 弱默认：主无强定义（tentative 无 init）→ 库弱默认 42 生效
main_w2 = tempfile.mktemp(suffix=".c")
with open(main_w2, "w") as f:
    f.write("int cfg_get(void);\n"
            "int cfg;\n"
            "int main(void){ return cfg_get()*10 + cfg; }\n")
r = link_and_load(open(main_w2).read(), [trvo_w1])
got, _ = run_py(*r)
check("A3s/弱默认生效(两路读42)", got, (42*10 + 42) & 0xFF)

print(f"\n多文件链接端到端：{passed} PASS / {failed} FAIL")
sys.exit(1 if failed else 0)
sys.exit(1 if failed else 0)
sys.exit(1 if failed else 0)
sys.exit(1 if failed else 0)
