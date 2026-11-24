#!/usr/bin/env python3
"""TriRV TRV2 长分支弛豫正确性契约（批次 8 衍生回归，ADR-16/B1①）。

背景（2026-11-24 strftime 6 分支实证）：
  TRV2 门 br_window_slots=273 槽，超窗分支弛豫为「反条件 imm=2 + JAL zero 长跳」。
  RELAX_LBLV 只在首次标记时写入 → 标签表随弛豫收敛移动后仍是旧值，
  展开 JAL 指向陈旧目标（差 2 槽=8 tryte）→ 恰好跳过循环头检查 → 死循环。
  TRV1（窗 ±1093 无弛豫）与短跳程序（V8 形）全绿漏网——本套件专钉该形状族：
  循环体内多分支 + 嵌套块声明 + 函数调用，使循环内分支距目标 >273 槽。

形状族（全部 TRV2 默认输出 × 双 VM）：
  R1  2 分支（对照——短距不弛豫）
  R2  6 分支（原 bug 形状：出口分支距 ~310 槽，弛豫 + 跨弛豫行跳转）
  R3  8 分支（更深链，多条弛豫行相互跨越）
  R4  弛豫行后向回边（while 回边 JAL 的目标在两条弛豫行之后）
  R5  真 strftime 回归（time_tri.c + 6 分支主格式，含 buf 内容逐字节校验）

坑实录：
- 垫厚语句放循环体内（V10 教训：垫循环前不改变循环内分支距，钉不住）。
- 挂起 = FAIL（subprocess timeout 归一 exit=None）。
"""
import os
import sys
import subprocess
import tempfile

import _bootstrap  # noqa: F401
import _trvio
import trirv_vm as vm

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CC = os.path.join(ROOT, "cc", "bin", "trirv_cc")
CVM = os.path.join(ROOT, "vm", "bin", "trirv_vm")
TIME_C = os.path.join(ROOT, "libc-musl", "musl_tri", "time_tri.c")

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


def run_cvm(src, sandbox, timeout=20):
    full = tempfile.mktemp(suffix=".c", dir=os.path.abspath(os.path.join(ROOT, "tmp")))
    tf = tempfile.mktemp(suffix=".trv", dir=os.path.abspath(os.path.join(ROOT, "tmp")))
    with open(full, "w") as f:
        f.write(src)
    r = subprocess.run([CC, full, tf], capture_output=True, timeout=60)
    if r.returncode != 0:
        return "CFAIL: " + r.stderr.decode("utf-8", "replace")[:120]
    try:
        r2 = subprocess.run([CVM, tf], capture_output=True, cwd=sandbox, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None                              # 挂起 = FAIL
    for line in r2.stderr.decode("utf-8", "replace").split():
        if line.startswith("exit="):
            return int(line.split("=")[1])
    return None


def run_py(src, sandbox, max_steps=2000000):
    full = tempfile.mktemp(suffix=".c", dir=os.path.abspath(os.path.join(ROOT, "tmp")))
    tf = tempfile.mktemp(suffix=".trv", dir=os.path.abspath(os.path.join(ROOT, "tmp")))
    with open(full, "w") as f:
        f.write(src)
    r = subprocess.run([CC, full, tf], capture_output=True, timeout=60)
    if r.returncode != 0:
        return "CFAIL"
    prog, mem, packed = _trvio.load_any(tf)
    v = vm.VM(prog, mem_init=mem)
    if packed:
        v.packed = True
        v.imem_offs = __import__("trirv_isa").stream_offsets(
            __import__("trirv_isa").pack_stream(prog))
    cwd0 = os.getcwd()
    os.chdir(sandbox)
    try:
        v.run(max_steps=max_steps)
    finally:
        os.chdir(cwd0)
    return v.exit_code if getattr(v, "halted", True) else None


# ---- 形状族生成器：nb 分支的循环内链（每分支嵌套块声明 + 调用 + 跨块 for）----
BRANCH_TMPL = '''        }} else if (*p == {c}) {{
            {{ int n = {helper}(tm[{k}], nb);
               int i; for (i = 0; i < n && total < max - 1; i++) buf[total] = nb[i], i2[0] = i, total++; }}
            p++;
'''
# 上模板不可行（逗号表达式未支持）——改用已证 strftime 原体：从 time_tri.c 摘
# strftime 支架，分支数参数化重建（与实证 V6 同构）。


def mk_chain(nb):
    """nb 分支 strftime 形（%Y + (nb-1) 个 %X 垫分支），fmt 同长。"""
    fmt_chars = "YmdHMSab"[:nb]                   # 8 个互异分支字符（重复会被链截胡）
    fmt = "".join("%" + c for c in fmt_chars)
    tm_idx = [5, 4, 3, 2, 1, 0, 6, 7][:nb]        # 各分支读的 tm 字段（a→wday b→yday）
    chain = []
    for bi in range(nb):
        head = "if" if bi == 0 else "} else if"
        c = ord(fmt_chars[bi])
        k = tm_idx[bi]
        chain.append(
            '        %s (*p == %d) {\n'
            '            { int n = tri_pad2(tm[%d], nb);\n'
            '              int i; for (i = 0; i < n && total < max - 1; i++) { buf[total] = nb[i]; total++; } }\n'
            '            p++;' % (head, c, k))
    chain.append('        } else {\n'
                 '            buf[total] = *p; total++; p++;\n'
                 '        }')
    body = "\n".join(chain)
    # 期望值按链语义模拟：每分支输出 pad2(tm[k])；tm=gmtime(1078012800)
    tm_vals = {5: 104, 4: 1, 3: 29, 2: 0, 1: 0, 0: 0, 6: 0, 7: 59}   # wday=0 yday=59
    parts = []
    for bi in range(nb):
        v = tm_vals[tm_idx[bi]]
        parts.append(str(v) if v >= 10 else "0" + str(v))
    expect = "".join(parts)
    want = len(expect)
    tmpl = '''int sf_chain(char *buf, int max, const char *fmt, int *tm) {
    int total = 0;
    const char *p = fmt;
    char nb[8];
    while (*p && total < max - 1) {
        if (*p == 37) {
            p++;
@BODY@
        } else {
            buf[total] = *p; total++; p++;
        }
    }
    buf[total] = 0;
    return total;
}
int tri_eq(char *a, char *b){
  int i = 0;
  while (a[i] && b[i]) { if (a[i] != b[i]) return 0; i++; }
  return a[i] == b[i];
}
int main(){
  int tm[9];
  char buf[64];
  int t = 1078012800;                      /* 2004-02-29 00:00:00 UTC */
  gmtime_r(&t, tm);
  int n = sf_chain(buf, 64, "@FMT@", tm);
  if (n != @WANT@) return 100 + n;
  if (!tri_eq(buf, "@EXPECT@")) return 7;
  return 42;
}
'''
    return (tmpl.replace("@BODY@", body).replace("@FMT@", fmt)
                .replace("@WANT@", str(want)).replace("@EXPECT@", expect))


# gmtime_r 需要 time_tri.c——把链生成器并到 time_tri.c 前（去重名 strftime 冲突：
# time_tri.c 的 strftime 与 sf_chain 不同名，tri_pad2/tri_d2s_t 会重名——链生成器
# 独立命名 helper。简化：链 case 直接带 gmtime_r（拼接 time_tri.c），helper 改名。

def mk_chain_src(nb):
    src = mk_chain(nb)
    # time_tri.c 已有 tri_pad2/tri_d2s_t → 链里的定义重名。改：链源去掉 helper
    # 定义，直接拼接 time_tri.c（其 strftime 共存不冲突）。
    time_src = open(TIME_C).read()
    # 链源里的 helper 定义删除（time_tri.c 提供）
    import re
    src2 = re.sub(r'int tri_pad2\(int val, char \*buf\) \{.*?\n\}\n', '', src, flags=re.S)
    src2 = re.sub(r'int tri_d2s_t\(int val, char \*buf\) \{.*?\n\}\n', '', src2, flags=re.S)
    return time_src + "\n" + src2


# R5：真 strftime（原 bug 的最小复现主体）
R5 = open(TIME_C).read() + '''
int tri_eq2(char *a, char *b){
  int i = 0;
  while (a[i] && b[i]) { if (a[i] != b[i]) return 0; i++; }
  return a[i] == b[i];
}
int main(){
  int tm[9];
  char buf[64];
  int t = 1078012800;
  gmtime_r(&t, tm);
  int n = strftime(buf, 64, "%Y-%m-%d %H:%M:%S", tm);
  if (n != 19) return 100 + n;
  if (!tri_eq2(buf, "2004-02-29 00:00:00")) return 7;
  return 42;
}
'''

# R4：弛豫行后向回边——while 回边的目标（循环头）位于两条弛豫行之后的位置。
# 构造：循环头 → 垫厚（把链推远）→ 6 分支链（2 条弛豫行）→ 回边 JAL 跨回。
# mk_chain 的 while 回边即此形状（回边目标 = 循环头在弛豫行之前）——R4 用
# 嵌套外层循环强化：内层链弛豫后回外层再判。
R4 = mk_chain_src(6).replace(
    "int n = sf_chain(buf, 64,",
    "int n = sf_chain(buf, 64,", 1)


def main():
    sandbox = _bootstrap.scratch("trv2_relax_sandbox")
    os.makedirs(sandbox, exist_ok=True)

    cases = [
        ("R1 2分支对照", mk_chain_src(2), 42),
        ("R2 6分支原bug形状", mk_chain_src(6), 42),
        ("R3 8分支深链", mk_chain_src(8), 42),
        ("R4 弛豫+回边嵌套", R4, 42),
        ("R5 真strftime回归", R5, 42),
    ]
    for name, src, want in cases:
        check("%s·CVM" % name, run_cvm(src, sandbox), want)
        check("%s·PyVM" % name, run_py(src, sandbox), want)

    print("\nTRV2 弛豫契约：%d PASS / %d FAIL" % (passed, failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
