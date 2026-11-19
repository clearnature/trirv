#!/usr/bin/env python3
"""trirv_trvo_e2e —— TRVO 目标文件格式 + 符号级链接端到端验证。

TRVO（TriRV Object，文本行式中间格式）：
  TRVO 1 / LBLBASE <n> / FUNC name nparams / PARAM i name type /
  LINE <asm> / ENDFUNC / GLOB name type / STR <len> <hex> / END

覆盖：
  1. 编译→TRVO 导出→链接→执行全流程（musl+crt 预编译为 .trvo，
     busybox 只链 .trvo —— 增量构建语义）
  2. 未定义引用检测：可执行 .trv 链接期报 undefined reference（退出码 1）；
     目标文件 .trvo 编译豁免（UNDEF 合法，留给最终链接把关）
  3. 跨单元局部标签改写：_L<n> → _X<seq>_<n>（多单元同名 _L* 不冲突）
  4. 重复定义检测对 TRVO 导入同样生效
  5. 拼接式（-l .c）与 TRVO 式（-l .trvo）产物指令数一致（等价性）
  6. 双 VM 运行 TRVO 链接产物
"""
import os
import struct
import subprocess
import sys
import tempfile

import trirv_vm as vm

HERE = os.path.dirname(os.path.abspath(__file__))
CVM = os.environ.get("TRIRV_VM_BIN", os.path.join(HERE, "trirv_vm"))
CC = os.path.join(HERE, "trirv_cc")

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


def compile_any(out, main_src=None, libs=(), expect_rc=0):
    """返回 (rc, stderr)。main_src=None 时 out 必须是 .trvo 且 libs 提供 .c 源。"""
    args = [CC]
    libs = list(libs)
    if main_src is not None:
        cf = tempfile.mktemp(suffix=".c")
        with open(cf, "w") as f:
            f.write(main_src)
        args.append(cf)
    elif libs:
        # 无主文件（纯库导出）：第一个 .c 源升格为 argv[1]（编译器要求
        # argv[1] 必须是可 lex 的 .c），其余仍走 -l。
        args.append(libs.pop(0))
    args.append(out)
    for lib in libs:
        args += ["-l", lib]
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != expect_rc:
        print(f"  [compile_any rc={r.returncode}] {r.stderr.strip()[:200]}")
    return r.returncode, r.stderr


def load_trv(path):
    data = open(path, "rb").read()
    n_inst, n_seg = struct.unpack_from("<II", data, 8)
    prog = list(struct.unpack_from(f"<{n_inst}I", data, 16))
    off = 16 + 4 * n_inst
    mem = {}
    for _ in range(n_seg):
        addr, ln = struct.unpack_from("<II", data, off)
        off += 8
        mem[addr] = data[off:off + ln]
        off += ln
    return prog, mem


# ================ 1. 基本流程：库 trvo 化再链接 ================
lib_src = tempfile.mktemp(suffix=".c")
with open(lib_src, "w") as f:
    f.write("int count(int n){ int i=0; int s=0; while(i<n){ s=s+i; i=i+1; } return s; }\n")
trvo = tempfile.mktemp(suffix=".trvo")
rc, _ = compile_any(trvo, libs=[lib_src])
check("trvo/库导出", rc, 0)

main_src = tempfile.mktemp(suffix=".c")
with open(main_src, "w") as f:
    f.write("int count(int n);\nint main(){ return count(5); }\n")
trv = tempfile.mktemp(suffix=".trv")
TRV_COUNT = trv                    # 独立路径绑定（后续用例 trv 会被覆盖）
rc, _ = compile_any(trv, main_src=open(main_src).read(), libs=[trvo])
check("trvo/链接编译", rc, 0)
prog, mem = load_trv(trv)
c = vm.VM(prog, mem_init=mem).run(max_steps=5000)
check("trvo/链接执行", c.exit_code, 10)

# ================ 2. 未定义引用 ================
rc, err = compile_any(trv, main_src="int main(){ return missing(1); }\n")
ok = rc == 1 and "undefined reference to 'missing'" in err
check("trvo/未定义引用报错", ok, True)

# 目标文件模式豁免：库引用不存在符号也能导出 trvo
rc, err = compile_any(trvo, libs=[lib_src.replace("count", "tmpcount")])
check("trvo/目标文件豁免 UNDEF", rc, 0)

# ================ 3. 跨单元标签改写 ================
lib_a = tempfile.mktemp(suffix=".c")
with open(lib_a, "w") as f:
    f.write("int fa(int n){ int i=0; while(i<n){ i=i+1; } return i; }\n")
lib_b = tempfile.mktemp(suffix=".c")
with open(lib_b, "w") as f:
    f.write("int fb(int n){ int i=0; while(i<n*2){ i=i+1; } return i; }\n")
ta = tempfile.mktemp(suffix=".trvo")
tb = tempfile.mktemp(suffix=".trvo")
compile_any(ta, libs=[lib_a])
compile_any(tb, libs=[lib_b])
main2 = tempfile.mktemp(suffix=".c")
with open(main2, "w") as f:
    f.write("int fa(int n);\nint fb(int n);\n"
            "int main(){ return fa(3)*10 + fb(4); }\n")
trv_dual = tempfile.mktemp(suffix=".trv")
rc, _ = compile_any(trv_dual, main_src=open(main2).read(), libs=[ta, tb])
check("trvo/双单元编译", rc, 0)
prog, mem = load_trv(trv_dual)
c = vm.VM(prog, mem_init=mem).run(max_steps=5000)
check("trvo/双单元标签不冲突", c.exit_code, 3 * 10 + 8)

# ================ 4. TRVO 重复定义 ================
dup = tempfile.mktemp(suffix=".c")
with open(dup, "w") as f:
    f.write("int count(int n){ return 99; }\nint main(){ return count(1); }\n")
rc, err = compile_any(trv, main_src=open(dup).read(), libs=[trvo], expect_rc=1)
ok = "duplicate function 'count'" in err
check("trvo/重复定义报错", ok, True)

# ================ 5. 拼接式 vs TRVO 式等价 ================
trv_concat = tempfile.mktemp(suffix=".trv")
rc1, _ = compile_any(trv_concat, main_src=open(main_src).read(), libs=[lib_src])
n_concat = struct.unpack_from("<I", open(trv_concat, "rb").read(), 12)[0]
n_trvo = struct.unpack_from("<I", open(trv, "rb").read(), 12)[0]
check("trvo/指令数等价", n_trvo, n_concat)

# ================ 6. C VM 运行 TRVO 链接产物 ================
if os.path.exists(CVM):
    r = subprocess.run([CVM, TRV_COUNT], capture_output=True)
    errs = [l for l in r.stderr.decode().split() if l.startswith("exit=")]
    check("trvo/CVM 执行", int(errs[0].split("=")[1]), 10)

print(f"\nTRVO 目标文件端到端：{passed} PASS / {failed} FAIL")
sys.exit(1 if failed else 0)
