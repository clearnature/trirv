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
# 3b.2 切默认配套：本套件=TRVO 目标文件链接链路（词级取证读取），
# 最终容器格式与被测对象无关——显式钉 TRV1 保持槽词口径
os.environ.setdefault("TRV_FORMAT", "1")
import sys
import tempfile

import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import _trvio
import trirv_vm as vm

HERE = os.path.dirname(os.path.abspath(__file__))
CVM = os.environ.get("TRIRV_VM_BIN", os.path.join(HERE, "..", "vm", "bin", "trirv_vm"))
CC = os.path.join(HERE, "..", "cc", "bin", "trirv_cc")
ROOT = os.path.dirname(HERE)

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
    prog, mem, _packed = _trvio.load_bytes(data)
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

# ================ X. 全量去重增量链接（A2，2026-11-22） ================
# batch_musl_compile 全量编译 string+stdlib（真 musl 源 + musl_tri 重写映射），
# 去掉同源别名单元（atof/fcvt/gcvt = strtod/ecvt 同源，同名强符号诚实报错），
# 剩余 60+ 单元一次性全部导入链接 + 跨单元行为验证——README「预编译一次、
# 多次链接」的增量构建语义从此成为被测事实。
# 数据地址已符号化（&name，§5.7 修复）：各单元全局在合并镜像里重排后仍正确。
batch_dir, _ = _bootstrap.run_musl_batch()      # 批量管线跑进本进程草稿目录
units = sorted(__import__("glob").glob(os.path.join(batch_dir, "*.trvo")))
check("全量/批量产出单元数>=60", len(units) >= 60, True)

# 符号感知去重（真链接器语义）：解析各单元 FUNC 名，贪心接受无碰撞单元，
# 跳过碰撞单元（如同源别名 atof/fcvt/gcvt↔strtod/ecvt、qsort_nr↔qsort）。
# 同名强符号 → 链接报错是诚实行为，测试按链接器规则先去重。
accepted, skipped, seen_fns = [], [], set()
for u in units:
    fns = [l.split()[1] for l in open(u).read().split("\n")
           if l.startswith("FUNC ")]
    if any(f in seen_fns for f in fns):
        skipped.append(os.path.basename(u))
        continue
    seen_fns.update(fns)
    accepted.append(u)
check("全量/去重后可链接单元数>=55", len(accepted) >= 55, True)
check("全量/碰撞单元被诚实跳过", len(skipped) >= 1, True)

driver = tempfile.mktemp(suffix=".c")
with open(driver, "w") as f:
    f.write(
        "int strlen(const char *s);\n"
        "void *memcpy(void *d, const void *s, int n);\n"
        "int memcmp(const void *a, const void *b, int n);\n"
        "int abs(int a);\n"
        "int atoi(const char *s);\n"
        "int qsort(void *b, int n, int w, int (*c)(const void*, const void*));\n"
        "int cmp_i(const int *a, const int *b){ return *a - *b; }\n"
        "int main(void){\n"
        "    char buf[8];\n"
        "    int i;\n"
        "    for (i = 0; i < 8; i++) buf[i] = 0;\n"
        "    memcpy(buf, \"trirv\", 6);\n"
        "    int n = 0;\n"
        "    while (buf[n]) n++;\n"
        "    int a[3]; a[0]=30; a[1]=10; a[2]=20;\n"
        "    qsort(a, 3, 4, cmp_i);\n"
        "    int f = 0;\n"
        "    if (n != 5) f += 1;\n"
        "    if (a[0]!=10 || a[1]!=20 || a[2]!=30) f += 2;\n"
        "    if (memcmp(buf, \"trirv\", 6) != 0) f += 4;\n"
        "    if (atoi(\"42\") != 42) f += 8;\n"
        "    if (abs(-7) != 7) f += 16;\n"
        "    return f;\n"
        "}\n")
tf = tempfile.mktemp(suffix=".trv")
cmd = [CC, driver, tf]
for u in accepted:
    cmd += ["-l", u]
r = subprocess.run(cmd, capture_output=True, text=True)
if r.returncode != 0:
    check("全量/增量链接 %d 单元" % len(accepted), "link ok", (r.stderr or "")[:120])
else:
    check("全量/增量链接 %d 单元" % len(accepted), 0, 0)
    prog_data = open(tf, "rb").read()
    nn = struct.unpack_from("<I", prog_data, 8)[0]
    pp = list(struct.unpack_from("<%dI" % nn, prog_data, 16))
    off2 = 16 + 4 * nn
    mem2 = {}
    for _ in range(struct.unpack_from("<I", prog_data, 12)[0]):
        a2, l2 = struct.unpack_from("<II", prog_data, off2); off2 += 8
        mem2[a2] = list(prog_data[off2:off2 + l2]); off2 += l2
    c2 = vm.VM(pp, mem_init=mem2).run(max_steps=2000000)
    check("全量/PyVM 行为位图", c2.exit_code & 0xFF, 0)
    if CVM:
        rr = subprocess.run([CVM, tf], capture_output=True)
        errs = [l for l in rr.stderr.decode().split() if l.startswith("exit=")]
        cv = int(errs[0].split("=")[1]) if errs else -1
        check("全量/CVM 行为位图", cv, 0)

# ================ Y. 跨单元字符串（A1s，2026-11-22） ================
# 库单元字符串 + 主程序字符串/全局垫片共存：字符串地址符号化前，库的
# 字符串引用烘焙独立编译期 STRBASE，合并重排后错位（且与主字符串重叠）。
lib_s = tempfile.mktemp(suffix=".c")
with open(lib_s, "w") as f:
    f.write("char *lib_hi(void){ return \"hi\"; }\n")
trvo_s = tempfile.mktemp(suffix=".trvo")
subprocess.run([CC, lib_s, trvo_s], check=True, capture_output=True)
driver_s = tempfile.mktemp(suffix=".c")
with open(driver_s, "w") as f:
    f.write(
        "char *lib_hi(void);\n"
        "int pad[40];\n"
        "int main(void){\n"
        "    char *m = \"MAIN\";\n"
        "    char *h = lib_hi();\n"
        "    int f = 0;\n"
        "    if (m[0]!='M' || m[1]!='A' || m[2]!='I' || m[3]!='N' || m[4]!=0) f += 1;\n"
        "    if (h[0]!='h' || h[1]!='i' || h[2]!=0) f += 2;\n"
        "    if (pad[39] != 0) f += 4;\n"
        "    return f;\n"
        "}\n")
tf_s = tempfile.mktemp(suffix=".trv")
cmd = [CC, driver_s, tf_s, "-l", trvo_s]
r = subprocess.run(cmd, capture_output=True, text=True)
if r.returncode != 0:
    check("跨单元/字符串链接", "link ok", (r.stderr or "")[:120])
else:
    check("跨单元/字符串链接", 0, 0)
    prog_data = open(tf_s, "rb").read()
    nn = struct.unpack_from("<I", prog_data, 8)[0]
    pp = list(struct.unpack_from("<%dI" % nn, prog_data, 16))
    off2 = 16 + 4 * nn
    mem2 = {}
    for _ in range(struct.unpack_from("<I", prog_data, 12)[0]):
        a3, l3 = struct.unpack_from("<II", prog_data, off2); off2 += 8
        mem2[a3] = list(prog_data[off2:off2 + l3]); off2 += l3
    c3 = vm.VM(pp, mem_init=mem2).run(max_steps=2000000)
    check("跨单元/字符串行为位图", c3.exit_code & 0xFF, 0)

# ================ Z. 两级 .trvo 闭包链（A5a，2026-11-22） ================
# lib1 → lib1.trvo；lib2 导入 lib1 编出 lib2_closure.trvo（闭包转发导出）；
# main 只 -l lib2_closure.trvo——增量构建的传递闭包形态（下游一次 -l）。
# 坑实录：旧导出跳过导入函数（fbody==NULL），闭包只有一半——下游必须再
# -l 全部上游单元；且 GLOB/STR 本来就转发（半闭包自相矛盾）。
lib1 = tempfile.mktemp(suffix=".c")
with open(lib1, "w") as f:
    f.write("int lib1_fn(void){ return 11; }\n")
lib1_trvo = tempfile.mktemp(suffix=".trvo")
subprocess.run([CC, lib1, lib1_trvo], check=True, capture_output=True)
lib2 = tempfile.mktemp(suffix=".c")
with open(lib2, "w") as f:
    f.write("int lib1_fn(void);\n"
            "int lib2_fn(void){ return lib1_fn() * 2; }\n")
lib2_closure = tempfile.mktemp(suffix=".trvo")
r = subprocess.run([CC, lib2, lib2_closure, "-l", lib1_trvo], capture_output=True, text=True)
check("闭包/二级导出编译", r.returncode, 0)
main_z = tempfile.mktemp(suffix=".c")
with open(main_z, "w") as f:
    f.write("int lib2_fn(void);\n"
            "int main(void){ return lib2_fn(); }\n")
r = subprocess.run([CC, main_z, tfz := tempfile.mktemp(suffix=".trv"), "-l", lib2_closure],
                   capture_output=True, text=True)
if r.returncode != 0:
    check("闭包/主程序单 -l 链接", "link ok", (r.stderr or "")[:120])
else:
    check("闭包/主程序单 -l 链接", 0, 0)
    prog_data = open(tfz, "rb").read()
    nn = struct.unpack_from("<I", prog_data, 8)[0]
    pp = list(struct.unpack_from("<%dI" % nn, prog_data, 16))
    off2 = 16 + 4 * nn
    mem2 = {}
    for _ in range(struct.unpack_from("<I", prog_data, 12)[0]):
        a3, l3 = struct.unpack_from("<II", prog_data, off2); off2 += 8
        mem2[a3] = list(prog_data[off2:off2 + l3]); off2 += l3
    c3 = vm.VM(pp, mem_init=mem2).run(max_steps=2000000)
    check("闭包/行为位图(11*2=22)", c3.exit_code & 0xFF, 22)

# ================ W. 弱符号：#pragma weak 别名（A6 最小模型，2026-11-22） ================
# 语义：#pragma weak 别名 = 名称映射（引用绑目标的体，汇编期 find_label 链式
# 跟随）；若合并符号空间另有同名的强定义，强定义自然覆盖（add_label 先到
# 先得于本名，别名仅在无强定义时命中）。musl weak_alias 形态的最小落地。
# TRVO 格式扩展：ALIAS <别名> <本体> 记录（导出/导入对），传递保持。
lib_w = tempfile.mktemp(suffix=".c")
with open(lib_w, "w") as f:
    f.write("int my_cleanup(void){ return 7; }\n"
            "#pragma weak cleanup = my_cleanup\n")
trvo_w = tempfile.mktemp(suffix=".trvo")
subprocess.run([CC, lib_w, trvo_w], check=True, capture_output=True)
# 纯弱：无强定义，引用绑别名体
main_w1 = tempfile.mktemp(suffix=".c")
with open(main_w1, "w") as f:
    f.write("int main(void){ return cleanup(); }\n")
tf_w1 = tempfile.mktemp(suffix=".trv")
r = subprocess.run([CC, main_w1, tf_w1, "-l", trvo_w], capture_output=True, text=True)
if r.returncode != 0:
    check("弱符号/纯弱链接", "link ok", (r.stderr or "")[:120])
else:
    check("弱符号/纯弱链接", 0, 0)
    prog_w = open(tf_w1, "rb").read()
    nn = struct.unpack_from("<I", prog_w, 8)[0]
    pp = list(struct.unpack_from("<%dI" % nn, prog_w, 16))
    off3 = 16 + 4 * nn
    mem3 = {}
    for _ in range(struct.unpack_from("<I", prog_w, 12)[0]):
        a4, l4 = struct.unpack_from("<II", prog_w, off3); off3 += 8
        mem3[a4] = list(prog_w[off3:off3 + l4]); off3 += l4
    c4 = vm.VM(pp, mem_init=mem3).run(max_steps=2000000)
    check("弱符号/纯弱行为(7)", c4.exit_code & 0xFF, 7)
# 强覆盖：下游自带强定义，强体胜出（99）
main_w2 = tempfile.mktemp(suffix=".c")
with open(main_w2, "w") as f:
    f.write("int cleanup(void){ return 99; }\n"
            "int main(void){ return cleanup(); }\n")
tf_w2 = tempfile.mktemp(suffix=".trv")
r = subprocess.run([CC, main_w2, tf_w2, "-l", trvo_w], capture_output=True, text=True)
if r.returncode != 0:
    check("弱符号/强覆盖链接", "link ok", (r.stderr or "")[:120])
else:
    check("弱符号/强覆盖链接", 0, 0)
    prog_w2 = open(tf_w2, "rb").read()
    nn2 = struct.unpack_from("<I", prog_w2, 8)[0]
    pp2 = list(struct.unpack_from("<%dI" % nn2, prog_w2, 16))
    off3 = 16 + 4 * nn2
    mem4 = {}
    for _ in range(struct.unpack_from("<I", prog_w2, 12)[0]):
        a5, l5 = struct.unpack_from("<II", prog_w2, off3); off3 += 8
        mem4[a5] = list(prog_w2[off3:off3 + l5]); off3 += l5
    c5 = vm.VM(pp2, mem_init=mem4).run(max_steps=2000000)
    check("弱符号/强覆盖行为(99)", c5.exit_code & 0xFF, 99)

# ================ T9b 多单元直链回归（2026-11-23 悬垂 ty 修复锁死） ================
# 根因：global_add 的 g->ty 裸别名 TRVO 导入的栈上行缓冲 → 后续行复用即悬垂
# → 类型变垃圾字节（int*）→ ty_bytes 塌成 9 → 数组全局互相重叠 →
# ec_core 的 SD_E 常量被 g_ec_buf 初始化循环踩踏 → 直链自旋（拼接式无导入
# 路径故幸存）。修复后契约：直链 == 拼接式（双 VM 轨迹级一致）。
EC_CORE = os.path.join(ROOT, "libc-musl", "musl_tri", "ec_core_tri.c")
ECVT = os.path.join(ROOT, "libc-musl", "musl_tri", "ecvt_tri.c")
if os.path.exists(EC_CORE) and os.path.exists(ECVT):
    u1 = tempfile.mktemp(suffix=".trvo")
    u2 = tempfile.mktemp(suffix=".trvo")
    rc_u1, _ = compile_any(u1, main_src=open(EC_CORE).read())
    rc_u2, _ = compile_any(u2, main_src=open(ECVT).read())
    if rc_u1 == 0 and rc_u2 == 0:
        main_src = (
            "extern char *ecvt(double value, int n, int *decpt, int *sign);\n"
            "int main(){\n"
            "  char buf[64]; int decpt; int sign; char *s;\n"
            "  s = ecvt(492400275253316431169, 4, &decpt, &sign);\n"
            "  return decpt;\n"
            "}\n"
        )
        trv_direct = tempfile.mktemp(suffix=".trv")
        rc_d, _ = compile_any(trv_direct, main_src=main_src, libs=[u1, u2])
        cat_src = open(EC_CORE).read() + "\n" + open(ECVT).read() + "\n" + main_src
        trv_cat = tempfile.mktemp(suffix=".trv")
        rc_c, _ = compile_any(trv_cat, main_src=cat_src)
        if rc_d == 0 and rc_c == 0:
            pD, mD = load_trv(trv_direct)
            pC, mC = load_trv(trv_cat)
            check("T9b/直链指令数==拼接", len(pD), len(pC))
            cD = vm.VM(pD, mem_init=mD).run(max_steps=2000000)
            cC = vm.VM(pC, mem_init=mC).run(max_steps=2000000)
            check("T9b/PyVM 直链 ecvt(3.5)→decpt=1", cD.exit_code, 1)
            check("T9b/PyVM 直链==拼接轨迹", (cD.exit_code, cD.halted, cD.pc, cD.steps),
                  (cC.exit_code, cC.halted, cC.pc, cC.steps))
            if os.path.exists(CVM):
                rD = subprocess.run([CVM, trv_direct], capture_output=True, text=True)
                rC = subprocess.run([CVM, trv_cat], capture_output=True, text=True)
                eD = next((l for l in rD.stderr.split() if l.startswith("exit=")), "")
                eC = next((l for l in rC.stderr.split() if l.startswith("exit=")), "")
                check("T9b/CVM 直链 decpt=1", eD, "exit=1")
                check("T9b/CVM 直链==拼接", eD, eC)

print(f"\nTRVO 目标文件端到端：{passed} PASS / {failed} FAIL")
sys.exit(1 if failed else 0)
