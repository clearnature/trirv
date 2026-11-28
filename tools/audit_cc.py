#!/usr/bin/env python3
"""audit_cc —— C 端双编译器回审（trirv_cc + tri-tcc），同 cpp 回审方法。

分类：honest（exit≠0 且有报错信息）/ crash（信号退出/超时）/ silent
（exit=0——误收，需 VM 复验语义）/ ok。
语义差分：同一 C 源 → cc × tcc × 双 VM，exit 恒等=一致。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__)) + "/.."
sys.path.insert(0, ROOT)                    # trirv_isa（tests/_trvio 依赖）
CC_A = ROOT + "/cc/bin/trirv_cc"
CC_B = ROOT + "/tinycc/trirv-tcc"
sys.path.insert(0, ROOT + "/tests")
sys.path.insert(0, ROOT + "/vm")

TMP = ROOT + "/tmp"


def run_cc(cc, src, tag):
    cfile = f"{TMP}/audit_cc_{tag}.c"
    tfile = f"{TMP}/audit_cc_{tag}.trv"
    open(cfile, "w").write(src)
    if os.path.exists(tfile):
        os.remove(tfile)
    cmd = [cc, cfile, tfile]
    if "tcc" in os.path.basename(cc):
        cmd = [cc, "-nostdlib", cfile, "-o", tfile]
    try:
        r = subprocess.run(cmd, capture_output=True,
                           text=True, timeout=15,
                           errors="replace")
    except subprocess.TimeoutExpired:
        return ("hang", "", "")
    except Exception as ex:
        return ("crash", type(ex).__name__, "")
    if r.returncode < 0:
        return ("crash", f"signal{-r.returncode}", r.stderr[:80])
    if r.returncode != 0:
        return ("honest", f"exit{r.returncode}", r.stderr[:80])
    if not os.path.exists(tfile):
        return ("silent", "no-output", "")
    return ("silent-ok", "", "")


def run_vm(trv):
    import _trvio
    import trirv_vm as vm
    prog, mem, _ = _trvio.load_bytes(open(trv, "rb").read())
    c = vm.VM(prog, mem_init=mem).run(max_steps=50000)
    return c.exit_code


# ---- 1. 错误路径/边界用例（畸形 C）----
ROBUST = [
    ("empty-file", ""),
    ("no-main", "int f(void) { return 0; }"),
    ("unclosed-string", 'int main(void) { char* s = "abc; return 0; }'),
    ("unclosed-comment", "int main(void) { return 0; } /* oops"),
    ("unclosed-brace", "int main(void) { return 0;"),
    ("bad-token", "int main(void) { int x = 1 @ 2; return 0; }"),
    ("empty-expr", "int main(void) { int x = ; return 0; }"),
    ("type-missing", "main(void) { return 0; }"),
    ("double-main", "int main(void) { return 0; } int main(void) { return 1; }"),
    ("deep-blocks-1500", "int main(void) { " + "{ " * 1500 + "}" * 1500 + " }"),
    ("deep-parens-2000", "int main(void) { return " + "(" * 2000 + "1" + ")" * 2000 + "; }"),
    ("long-chain-5000", "int main(void) { return 1" + " + 1" * 5000 + "; }"),
    ("huge-literal-1e25", "int main(void) { return 9999999999999999999999999; }"),
    ("huge-literal-1e20", "int main(void) { return 99999999999999999999; }"),
    ("neg-huge", "int main(void) { return -9999999999999999999999999; }"),
    ("char-overflow", "int main(void) { char c = 300; return c; }"),
    ("char-243", "int main(void) { char c = 243; return c; }"),
    ("char-242", "int main(void) { char c = 242; return c; }"),
    ("div-zero-const", "int main(void) { return 1 / 0; }"),
    ("mod-zero-const", "int main(void) { return 1 % 0; }"),
    ("shift-neg", "int main(void) { return 4 >> -1; }"),
    ("shift-big", "int main(void) { return 4 << 100; }"),
    ("goto-undefined-label", "int main(void) { goto nowhere; return 0; }"),
    ("undefined-var", "int main(void) { return xyz; }"),
    ("redecl-var", "int main(void) { int x = 1; int x = 2; return x; }"),
    ("break-outside", "int main(void) { break; return 0; }"),
    ("continue-outside", "int main(void) { continue; return 0; }"),
    ("case-outside", "int main(void) { case 1: return 0; }"),
    ("array-neg", "int main(void) { int a[-2]; return 0; }"),
    ("array-zero", "int main(void) { int a[0]; return 0; }"),
    ("array-huge", "int main(void) { static int a[100000000]; return 0; }"),
    ("strlit-in-expr", "int main(void) { return \"abc\" == 0; }"),
    ("null-deref-const-fold", "int main(void) { int* p = 0; return *p; }"),
    ("cast-garbage", "int main(void) { return (struct zzz*)0 == 0; }"),
    ("typedef-loop", "typedef int A; typedef A A; int main(void) { A x = 1; return x; }"),
    ("enum-neg", "enum E { A = -3, B }; int main(void) { return A; }"),
    ("sizeof-empty", "struct E { }; int main(void) { return sizeof(struct E); }"),
    ("nested-comment", "/* a /* b */ int main(void) { return 0; }"),
    ("line-continuation", "int main(vo\\\nid) { return 0; }"),
    ("unicode-ident", "int mäin(void) { return 0; }"),
    ("null-bytes", "int m\x00ain(void) { return 0; }"),
    ("crlf", "int main(void)\r\n{\r\n    return 42;\r\n}\r\n"),
    ("eof-no-newline", "int main(void) { return 7; }"),
]

# ---- 2. 语义差分四路（cc × tcc × 双 VM）----
DIFF = [
    ("neg-div", "int main(void) { return -7 / 2 + 200; }"),
    ("neg-mod", "int main(void) { return -7 % 2 + 200; }"),
    ("int-wrap", "int main(void) { int x = 2147483647; x = x + 1; return x == 2147483648; }"),
    ("const-write", "int main(void) { const int x = 1; x = 2; return x; }"),
    ("bitwise-and", "int main(void) { return (6 & 5) + 200; }"),
    ("bitwise-xor", "int main(void) { return (6 ^ 5) + 200; }"),
    ("bitwise-or", "int main(void) { return (6 | 5) + 200; }"),
    ("shift-l", "int main(void) { return (6 << 2) + 200; }"),
    ("shift-r", "int main(void) { return (13 >> 1) + 200; }"),
    ("unsigned-int", "int main(void) { unsigned int x = 5; return x + 200; }"),
    ("char-neg", "int main(void) { char c = -3; return c < 0; }"),
    ("ternary-op", "int main(void) { return (1 > 0 ? 7 : 9) + 200; }"),
    ("compound-add", "int main(void) { int i = 1; i += 2; return i; }"),
    ("postincr", "int main(void) { int i = 1; int j = i++; return j * 10 + i; }"),
    ("ptr-arith", "int main(void) { int a[3]; int* p = a; int* q = p + 1; return q == p; }"),
    ("funcptr", "int f(int x) { return x + 1; } int main(void) { return (*f)(2); }"),
    ("recursion", "int f(int n) { return n <= 0 ? 0 : n + f(n - 1); } int main(void) { return f(10); }"),
    ("struct-sizeof", "struct S { int x; int y; }; int main(void) { return sizeof(struct S); }"),
    ("empty-struct", "struct E { }; int main(void) { return sizeof(struct E) + 200; }"),
    ("global-init", "int g = 42; int main(void) { return g; }"),
    ("static-local", "int f(void) { static int s = 5; s = s + 1; return s; } int main(void) { f(); return f(); }"),
    ("strlit-addr", "int main(void) { char* s = \"hi\"; return s[0] == 'h'; }"),
    ("div-trunc", "int main(void) { return 7 / 2 + 200; }"),
    ("mod-pos", "int main(void) { return 7 % 2 + 200; }"),
]


def fourway(tag, src):
    """同一 C 源 → cc × tcc × 双 VM（PyVM/CVM）→ exit 一致性。"""
    import _trvio
    import trirv_vm as vm
    exits = {}
    for name, cc in (("cc", CC_A), ("tcc", CC_B)):
        if not os.path.exists(cc):
            exits[name] = "NO-BIN"
            continue
        kind, kname, _ = run_cc(cc, src, f"diff_{tag}_{name}")
        if kind in ("crash", "hang", "honest"):
            exits[name] = f"{kind}:{kname}"
            continue
        try:
            prog, mem, _ = _trvio.load_bytes(
                open(f"{TMP}/audit_cc_diff_{tag}_{name}.trv", "rb").read())
            c = vm.VM(prog, mem_init=mem).run(max_steps=100000)
            exits[name] = c.exit_code
        except Exception as ex:
            exits[name] = f"vmerr:{type(ex).__name__}"
    return exits


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("robust", "all"):
        print("== 错误路径/边界（两编译器）==")
        crashes = []
        for (tag, src) in ROBUST:
            row = {}
            for name, cc in (("cc", CC_A), ("tcc", CC_B)):
                if not os.path.exists(cc):
                    row[name] = "NO-BIN"
                    continue
                kind, kname, msg = run_cc(cc, src, f"rb_{tag}_{name}")
                row[name] = kind if kind in ("crash", "hang") \
                    else f"{kind}" if kind == "honest" else "silent"
                if kind == "crash":
                    crashes.append((tag, name, kname, msg))
                if kind == "hang":
                    crashes.append((tag, name, "HANG", ""))
            mark = "" if all(v in ("honest", "silent") for v in row.values()
                             ) or "NO-BIN" in row.values() else "  <-- 异常"
            print(f"  [{tag}] cc={row.get('cc')} tcc={row.get('tcc')}{mark}")
        if crashes:
            print("\n== crash/hang 清单 ==")
            for (t, n, k, m) in crashes:
                print(f"  [{t}/{n}] {k} {m}")
    if which in ("diff", "all"):
        print("\n== 语义差分四路（cc × tcc）==")
        diverge = []
        for (tag, src) in DIFF:
            ex = fourway(tag, src)
            vals = set(str(v) for v in ex.values())
            ok = len(vals) == 1 and not any(
                str(v).startswith(("crash", "hang", "honest", "vmerr",
                                   "NO-BIN")) for v in ex.values())
            mark = "" if ok else "  <-- 分歧/异常"
            print(f"  [{tag}] {ex}{mark}")
            if not ok:
                diverge.append((tag, ex))
        if diverge:
            print(f"\n分歧/异常={len(diverge)}")


if __name__ == "__main__":
    main()
