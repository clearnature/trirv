#!/usr/bin/env python3
"""audit_probe —— 0.0.x 冻结前回审：error-path 崩溃狩猎 + 极限边界探测。

诚实报错 = LexError/ParseError/SemaError/TypeError_（四种）。
其余异常 = 健壮性 bug（crash 而非诚实拒绝）。
静默成功可疑输入 = 误收（subset 泄漏）。
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "cpp"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "cpp", "src"))

from transpiler import transpile  # noqa: E402
from cpp_lexer import LexError    # noqa: E402
from cpp_parser import ParseError  # noqa: E402
from cpp_sema import SemaError    # noqa: E402
from cpp_types import TypeError_  # noqa: E402

HONEST = (LexError, ParseError, SemaError, TypeError_)

CASES = [
    # ---- 名单：(标签, 源码, 期望) 期望: honest/crash/ok ----
    # 1. 词法面
    ("unclosed-string", 'int main() { char* s = "abc; return 0; }', "honest"),
    ("bad-char", "int main() { int x = 1 @ 2; }", "honest"),
    ("empty-src", "", "honest"),
    # 2. 递归深度面
    ("deep-blocks", "int main() { " + "{ " * 3000 + "}" * 3000 + " }", "any"),
    ("deep-expr-parens", "int main() { int x = " + "(" * 2000 + "1" + ")" * 2000 + "; }", "any"),
    ("deep-expr-add", "int main() { int x = 1" + " + 1" * 3000 + "; }", "any"),
    # 3. 模板递归（instantiate 无深度限制？）
    ("tmpl-self-recursive",
     "template<typename T>\nT f(T x) {\n    return f(x);\n}\n"
     "int main() {\n    int r = f(1);\n    return r;\n}", "any"),
    ("tmpl-mutual",
     "template<typename T>\nT g(T x);\n"
     "template<typename T>\nT f(T x) {\n    return g(x);\n}\n"
     "template<typename T>\nT g(T x) {\n    return f(x);\n}\n"
     "int main() {\n    int r = f(1);\n    return r;\n}", "any"),
    # 4. 名字/平化碰撞面
    ("flat-collision",
     "namespace nsa {\n    class B {\n    public:\n        int v;\n    };\n}\n"
     "class nsa_B {\npublic:\n    int v;\n};\n"
     "int main() {\n    return 0;\n}", "any"),
    ("long-identifier",
     "int main() { int " + "a" * 300 + " = 1; return 0; }", "any"),
    ("mangle-999-name",
     "class C" + "d" * 200 + " {\npublic:\n    int f() { return 1; }\n};\n"
     "int main() {\n    return 0;\n}", "any"),
    # 5. 字面量极限
    ("huge-literal", "int main() { return 99999999999999999999; }", "any"),
    ("negative-min", "int main() { return -2147483648; }", "any"),
    ("hex-overflow", "int main() { return 0xFFFFFFFFFFFF; }", "any"),
    # 6. 类/继承极限
    ("empty-class",
     "class E {\n};\nint main() {\n    E e;\n    return 0;\n}", "any"),
    ("diamond-inherit",
     "class A {\npublic:\n    int x;\n};\n"
     "class B : public A {\npublic:\n};\n"
     "class C : public A {\npublic:\n};\n"
     "class D : public B, public C {\npublic:\n};\n"
     "int main() {\n    return 0;\n}", "any"),
    ("deep-inherit-chain",
     "class C0 {\npublic:\n    int v;\n};\n"
     + "".join(f"class C{i} : public C{i-1} {{\npublic:\n}};\n"
               for i in range(1, 60))
     + "int main() {\n    return 0;\n}", "any"),
    ("self-inherit", "class A : public A {\npublic:\n};\nint main() { return 0; }", "honest"),
    # 7. 异常面
    ("throw-in-dtor",
     "class R {\npublic:\n    R() {}\n    ~R() { throw 1; }\n};\n"
     "int main() {\n    R r;\n    return 0;\n}", "any"),
    ("eh-huge-object",
     "class Big {\npublic:\n    int a;\n    int b;\n    int c;\n    int d;\n"
     "    int e;\n    int f;\n    int g;\n    int h;\n    int i;\n    int j;\n"
     "    int k;\n    int l;\n    int m;\n    int n;\n    int o;\n    int p;\n"
     "    int q;\n    int r2;\n    int s2;\n    int t2;\n"
     "    Big() {}\n};\n"
     "int main() {\n"
     "    try { Big b2; throw b2; } catch (Big& e) { return 1; }\n"
     "    return 0;\n}", "any"),
    ("bare-throw-top", "int main() {\n    throw 1;\n    return 0;\n}", "any"),
    # 8. 引用/const 面
    ("ref-ref-chain",
     "int main() {\n    int a = 1;\n    int& r1 = a;\n    int& r2 = r1;\n"
     "    return r2;\n}", "ok"),
    ("double-ref-type", "void f(int&& x) { }\nint main() { return 0; }", "honest"),
    ("const-write",
     "int main() {\n    const int x = 1;\n    x = 2;\n    return x;\n}", "honest"),
    # 9. 虚面
    ("virtual-ctor-call",
     "class A {\npublic:\n    A() {}\n    virtual int f() { return 1; }\n"
     "    A(int k) { }\n};\nint main() { return 0; }", "any"),
    ("abstract-throw",
     "class Ab {\npublic:\n    virtual int f() = 0;\n};\n"
     "int main() {\n    try { throw 1; } catch (int e) { return e; }\n"
     "    Ab* p = 0;\n    return 0;\n}", "any"),
    # 10. 数组/指针面
    ("huge-array", "int main() { int a[100000]; a[0] = 1; return a[0]; }", "any"),
    ("neg-index-static", "int main() { int a[3]; return a[-1]; }", "any"),
    # 11. switch/控制流
    ("switch-fallthrough",
     "int main() {\n    switch (1) {\n    case 1:\n    case 2:\n        return 1;\n"
     "    default:\n        return 2;\n    }\n}", "any"),
    ("goto-stmt", "int main() {\n    goto end;\nend:\n    return 0;\n}", "honest"),
    # 12. 重载歧义面
    ("overload-ambig-defaults",
     "int f(int a, int b = 1) { return a; }\n"
     "int f(int a) { return 2; }\n"
     "int main() {\n    return f(1);\n}", "honest"),
    # 13. ns 面
    ("ns-nested-deep",
     "namespace a {\n    namespace b {\n        namespace c {\n"
     "            int f() { return 1; }\n        }\n    }\n}\n"
     "int main() {\n    return a::b::c::f();\n}", "any"),
    # 14. 模板面（显式实参形态滥用）
    ("tcall-on-nontemplate", "int f(int x) { return x; }\nint main() { return f<int>(1); }", "honest"),
    ("tspec-expr-position", "int main() { int a = 1; int b = 2; return a < b; }", "ok"),
    ("template-arg-ref", "template<typename T> class Bx { public: T v; };\nint main() { return 0; }", "any"),
    # 15. ctor 面
    ("ctor-delegating",
     "class A {\npublic:\n    int x;\n    A() : x(0) {}\n    A(int k) : A() { x = k; }\n};\n"
     "int main() {\n    A a(3);\n    return a.x;\n}", "any"),
    ("ctor-init-unknown",
     "class A {\npublic:\n    int x;\n    A() : y(1) {}\n};\nint main() { return 0; }", "honest"),
    ("dtor-args", "class A {\npublic:\n    ~A(int k) {}\n};\nint main() { return 0; }", "honest"),
    # 16. main 面
    ("two-main", "int main() { return 0; }\nint main() { return 1; }", "honest"),
    ("main-with-args", "int main(int argc, char** argv) { return 0; }", "honest"),
    ("no-main", "int f() { return 0; }", "any"),
]


def run_one(tag, src, expect):
    """单用例带 10s 信号超时——挂死=HANG 独立发现类。"""
    import signal

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
        return ("honest", type(ex).__name__, str(ex)[:90])
    except TimeoutError:
        return ("hang", "HANG>10s", "")
    except RecursionError:
        return ("crash", "RecursionError", "")
    except Exception as ex:
        return ("crash", type(ex).__name__, str(ex)[:90])


def main():
    honest_n = crash_n = ok_n = hang_n = 0
    crashes = []
    hangs = []
    suspicious_ok = []
    for (tag, src, expect) in CASES:
        kind, kname, msg = run_one(tag, src, expect)
        if kind == "ok":
            ok_n += 1
            if expect == "honest":
                suspicious_ok.append((tag, "期望拒绝却成功"))
        elif kind == "honest":
            honest_n += 1
        elif kind == "hang":
            hang_n += 1
            hangs.append(tag)
        else:
            crash_n += 1
            crashes.append((tag, kname, msg))
    print(f"诚实拒绝={honest_n}  正常通过={ok_n}  崩溃={crash_n}  挂死={hang_n}")
    if hangs:
        print("\n== 挂死清单（>10s——无限循环/二次方爆炸）==")
        for t in hangs:
            print(f"  [{t}]")
    if crashes:
        print("\n== 崩溃清单（健壮性 bug——错误路径未走过）==")
        for (t, k, m) in crashes:
            print(f"  [{t}] {k}: {m}")
    if suspicious_ok:
        print("\n== 误收清单（子集泄漏）==")
        for (t, m) in suspicious_ok:
            print(f"  [{t}] {m}")


if __name__ == "__main__":
    main()
