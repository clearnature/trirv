#!/usr/bin/env python3
"""audit_iter —— 0.0.x 回审迭代 2/3/4。

iter2: 语义组合面（带间交互 + 方言边界）
iter3: 类型漏洞 + 产物面
iter4: 语料变异模糊（63 个 e2e 语料程序化变异，crash=bug）

口径：诚实=LexError/ParseError/SemaError/TypeError_；其余=bug。
"""
import os
import random
import re
import signal
import sys

ROOT = os.path.dirname(os.path.abspath(__file__)) + "/.."
sys.path.insert(0, ROOT + "/cpp")
sys.path.insert(0, ROOT + "/cpp/src")

from transpiler import transpile          # noqa: E402
from cpp_lexer import LexError            # noqa: E402
from cpp_parser import ParseError         # noqa: E402
from cpp_sema import SemaError            # noqa: E402
from cpp_types import TypeError_          # noqa: E402

HONEST = (LexError, ParseError, SemaError, TypeError_)


def run_one(src):
    def handler(signum, frame):
        raise TimeoutError()
    signal.signal(signal.SIGALRM, handler)
    signal.alarm(10)
    try:
        transpile(src)
        signal.alarm(0)
        return ("ok", "", "")
    except HONEST as ex:
        signal.alarm(0)
        return ("honest", type(ex).__name__, str(ex)[:100])
    except TimeoutError:
        return ("hang", "HANG>10s", "")
    except RecursionError:
        return ("crash", "RecursionError", "")
    except Exception as ex:
        return ("crash", type(ex).__name__, str(ex)[:100])


def report(tag, cases):
    bad = []
    for (t, src, expect) in cases:
        kind, kname, msg = run_one(src)
        if kind == "crash" or kind == "hang":
            bad.append((t, kind, kname, msg))
        elif kind == "ok" and expect == "honest":
            bad.append((t, "misaccept", "期望拒绝却成功", ""))
        elif kind == "honest" and expect == "ok":
            bad.append((t, "misreject", f"期望成功却拒绝: {msg}", ""))
    print(f"  用例={len(cases)}  异常={len(bad)}")
    for (t, kind, kname, msg) in bad:
        print(f"    [{t}] {kind} {kname} {msg}")
    return bad


# ============ 迭代 2：语义组合 / 方言边界 ============
I2 = [
    # 带间组合
    ("tmpl-method-throw",
     "template<typename T>\nclass Bx {\npublic:\n    T v;\n"
     "    Bx(T x) : v(x) {}\n    T get() { return v; }\n"
     "    int boom(int k) { if (k == 1) { throw 7; } return 0; }\n};\n"
     "int main() {\n"
     "    try { Bx<int> b(3); b.boom(1); } catch (int e) { return e; }\n"
     "    return 0;\n}", "ok"),
    ("tmpl-inherit",
     "template<typename T>\nclass Bx {\npublic:\n    T v;\n};\n"
     "class D : public Bx<int> {\npublic:\n    D() {}\n};\n"
     "int main() {\n    return 0;\n}", "any"),
    ("tmpl-catch-ref",
     "template<typename T>\nclass Bx {\npublic:\n    T v;\n    Bx() {}\n};\n"
     "int main() {\n"
     "    try { Bx<int> b; throw b; } catch (Bx<int>& e) { return 1; }\n"
     "    return 0;\n}", "any"),
    ("tmpl-typeid", "template<typename T>\nclass Bx { public: Bx() {} };\n"
     "int main() { Bx<int> b; return typeid(b) == typeid(Bx<int>); }", "any"),
    ("tmpl-and-ns",
     "namespace n {\n    template<typename T>\n    T f(T x) { return x; }\n}\n"
     "int main() {\n    return n::f<int>(3);\n}", "any"),
    ("overload-vs-template",
     "int f(int x) { return 1; }\n"
     "template<typename T>\nT f(T x) { return 2; }\n"
     "int main() {\n    return f(1);\n}", "any"),
    # 方言边界
    ("do-while",
     "int main() {\n    int i = 0;\n    do { i = i + 1; } while (i < 3);\n"
     "    return i;\n}", "any"),
    ("ternary",
     "int main() {\n    int a = 1;\n    int b = a > 0 ? 2 : 3;\n"
     "    return b;\n}", "any"),
    ("strlit-to-charptr",
     'int main() {\n    char* s = "abc";\n    return 0;\n}', "any"),
    ("strlit-to-int",
     'int main() {\n    int x = "abc";\n    return x;\n}', "any"),
    ("new-array",
     "int main() {\n    int* p = new int[5];\n    delete p;\n    return 0;\n}",
     "any"),
    ("ptr-arith",
     "int main() {\n    int a[3];\n    int* p = a;\n    int* q = p + 1;\n"
     "    return q == p;\n}", "any"),
    ("include-directive", "#include <stdio.h>\nint main() { return 0; }",
     "honest"),
    ("define-directive", "#define X 1\nint main() { return X; }", "honest"),
    ("preincr",
     "int main() {\n    int i = 0;\n    ++i;\n    return i;\n}", "any"),
    ("sizeof-expr",
     "class A {\npublic:\n    int x;\n};\n"
     "int main() {\n    return sizeof(A);\n}", "any"),
    ("compound-assign",
     "int main() {\n    int i = 1;\n    i += 2;\n    i *= 3;\n    return i;\n}",
     "ok"),
    ("array-of-class",
     "class A {\npublic:\n    int x;\n    A() : x(1) {}\n};\n"
     "int main() {\n    A arr[3];\n    return arr[2].x;\n}", "any"),
    ("char-array-member",
     "class S {\npublic:\n    char name[4];\n};\n"
     "int main() {\n    return 0;\n}", "any"),
    ("goto", "int main() {\n    goto end;\nend:\n    return 0;\n}", "honest"),
    ("crlf-src", "int main() {\r\n    return 0;\r\n}", "any"),
    ("eof-no-newline", "int main() { return 0; }", "any"),
    ("tab-indent", "int main() {\n\treturn 0;\n}", "any"),
    ("nested-comment", "/* a /* b */ c */\nint main() { return 0; }", "any"),
    ("block-comment-eof", "int main() { return 0; } /* unterminated", "honest"),
    # 变异类
    ("try-no-catch", "int main() {\n    try { return 1; }\n}", "honest"),
    ("catch-no-try", "int main() {\n    catch (int e) { }\n    return 0;\n}",
     "honest"),
    ("throw-bare-top", "int main() {\n    throw;\n    return 0;\n}", "any"),
    ("catch-const-ref",
     "int main() {\n"
     "    try { throw 5; } catch (const int& e) { return e; }\n"
     "}", "any"),
    ("nested-try",
     "int main() {\n"
     "    try { try { throw 1; } catch (int e) { throw e; } }\n"
     "    catch (int e) { return e; }\n}", "any"),
    ("switch-in-try",
     "int main() {\n"
     "    try { switch (1) { case 1: throw 3; } }\n"
     "    catch (int e) { return e; }\n}", "any"),
    ("loop-throw-continue",
     "int main() {\n    int s = 0;\n"
     "    for (int i = 0; i < 3; i = i + 1) {\n"
     "        try { if (i == 1) { continue; } s = s + i; }\n"
     "        catch (int e) { }\n    }\n    return s;\n}", "any"),
    ("dtor-virtual",
     "class A {\npublic:\n    A() {}\n    virtual ~A() { }\n};\n"
     "int main() {\n    return 0;\n}", "any"),
    ("static-mem-tmpl",
     "template<typename T>\nclass Bx {\npublic:\n    static int cnt;\n};\n"
     "int main() {\n    return 0;\n}", "any"),
    ("private-ctor",
     "class A {\nprivate:\n    A() {}\n};\nint main() {\n    A a;\n"
     "    return 0;\n}", "honest"),
    ("friend-parse",
     "class A {\npublic:\n    friend class B;\n};\nint main() { return 0; }",
     "any"),
]

# ============ 迭代 3：类型漏洞 / 产物面 ============
I3 = [
    ("int-to-ptr-assign",
     "int main() {\n    int* p = 5;\n    return 0;\n}", "honest"),
    ("ptr-to-int-assign",
     "int main() {\n    int a;\n    int* p = &a;\n    int x = p;\n"
     "    return 0;\n}", "honest"),
    ("unrel-ptr-cmp",
     "class A { public: int x; };\nclass B { public: int y; };\n"
     "int main() {\n    A a;\n    B b;\n    A* pa = &a;\n    B* pb = &b;\n"
     "    return pa == pb;\n}", "any"),
    ("void-ptr-roundtrip",
     "int main() {\n    int a = 7;\n    void* v = &a;\n    int* p = (int*)v;\n"
     "    return *p;\n}", "any"),
    ("ptr-delete-value",
     "int main() {\n    int a = 1;\n    delete &a;\n    return 0;\n}", "any"),
    ("double-delete",
     "int main() {\n    int* p = new int(3);\n    delete p;\n    delete p;\n"
     "    return 0;\n}", "any"),
    ("new-noctor-args",
     "class A {\npublic:\n    A() {}\n};\n"
     "int main() {\n    A* p = new A(1);\n    return 0;\n}", "honest"),
    ("ref-return-local",
     "int& f() {\n    int x = 1;\n    return x;\n}\n"
     "int main() {\n    return f();\n}", "any"),
    ("array-oob-literal",
     "int main() {\n    int a[2];\n    a[5] = 1;\n    return a[5];\n}", "any"),
    ("zero-array", "int main() {\n    int a[0];\n    return 0;\n}", "any"),
    ("neg-array", "int main() {\n    int a[-1];\n    return 0;\n}", "honest"),
    ("div-zero-const", "int main() {\n    int x = 1 / 0;\n    return x;\n}",
     "any"),
    ("uninit-read",
     "int main() {\n    int x;\n    return x;\n}", "any"),
    ("assign-self-class",
     "class A {\npublic:\n    int x;\n};\n"
     "int main() {\n    A a;\n    a = a;\n    return 0;\n}", "any"),
    ("char-overflow",
     "int main() {\n    char c = 300;\n    return c;\n}", "any"),
    ("int-overflow-wrap",
     "int main() {\n    int x = 2147483647;\n    x = x + 1;\n"
     "    return x == 0;\n}", "any"),
    ("eh-throw-then-more",
     "int main() {\n"
     "    try { throw 1; int dead = 9; }\n"
     "    catch (int e) { return e; }\n}", "any"),
    ("this-outside",
     "int main() {\n    return this;\n}", "honest"),
    ("member-on-int", "int main() {\n    int x = 1;\n    return x.f();\n}",
     "honest"),
    ("ctor-chain-loop",
     "class A {\npublic:\n    int x;\n    A() : A(1) {}\n"
     "    A(int k) : A() {}\n};\nint main() {\n    A a;\n    return 0;\n}",
     "any"),
    ("default-arg-nonconst",
     "class A {\npublic:\n    A() {}\n"
     "    int f(int k = 1) { return k; }\n};\n"
     "int main() {\n    A a;\n    return a.f();\n}", "ok"),
]

# ============ 迭代 4：语料变异模糊 ============


def mutate(src, rng):
    """固定语料程序化变异：删行/复制行/删分号/花括号换方括号/数字翻转。"""
    lines = src.splitlines()
    op = rng.randrange(5)
    if not lines:
        return src
    i = rng.randrange(len(lines))
    if op == 0:                                  # 删一行
        lines.pop(i)
    elif op == 1:                                # 复制一行
        lines.insert(i, lines[i])
    elif op == 2:                                # 删分号
        lines[i] = lines[i].replace(";", "", 1)
    elif op == 3:                                # } → ]
        lines[i] = lines[i].replace("}", "]", 1)
    else:                                        # 数字翻转
        m = re.search(r"\d+", lines[i])
        if m:
            lines[i] = lines[i][:m.start()] + "999" + lines[i][m.end():]
    return "\n".join(lines) + "\n"


def iter4():
    bad = []
    rng = random.Random(42)
    tdir = ROOT + "/cpp/tests"
    corpus = sorted(f for f in os.listdir(tdir) if f.endswith(".cpp"))
    total = 0
    for fn in corpus:
        src = open(tdir + "/" + fn).read()
        for k in range(6):                       # 每语料 6 次变异
            mut = mutate(src, rng)
            total += 1
            kind, kname, msg = run_one(mut)
            if kind in ("crash", "hang"):
                bad.append((f"{fn}#{k}", kind, kname, msg))
    print(f"  变异={total}  异常={len(bad)}")
    for (t, kind, kname, msg) in bad[:12]:
        print(f"    [{t}] {kind} {kname} {msg}")
    return bad


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("2", "all"):
        print("== 迭代 2：语义组合/方言边界 ==")
        report("i2", I2)
    if which in ("3", "all"):
        print("== 迭代 3：类型漏洞/产物面 ==")
        report("i3", I3)
    if which in ("4", "all"):
        print("== 迭代 4：语料变异模糊 ==")
        iter4()


if __name__ == "__main__":
    main()
