#!/usr/bin/env python3
"""cpp C 移植 lexer 差分套件（0.1.3）。
验收=L1 差分恒等：63 语料 + 手工探针（PUNCTS 最长前缀/字符串转义/
块注释跨行/错误行号）——Python cpp_lexer.py 与 C cpp_lexer.c 的
token 流逐行恒等（TSV：KIND\\x1fVAL\\x1fLINE；错误=LEXERROR\\x1fline）。
"""
import glob
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSRC = os.path.join(ROOT, "cpp", "c")
sys.path.insert(0, os.path.join(ROOT, "cpp", "src"))
from cpp_lexer import lex, LexError  # noqa: E402


def build_tricpp():
    out = os.path.join(tempfile.gettempdir(), f"tricpp_{os.getpid()}")
    r = subprocess.run(
        ["gcc", "-std=gnu11", "-O2", "-I", CSRC,
         "-o", out, os.path.join(CSRC, "tricpp.c"),
         os.path.join(CSRC, "cpp_types.c"), os.path.join(CSRC, "cpp_lexer.c")],
        capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        print(f"tricpp 构建失败: {r.stderr[:300]}")
        sys.exit(1)
    return out


def py_dump(src):
    """Python lex → TSV 形（与 C 侧 dump 格式同一）。"""
    lines = []
    try:
        for (k, v, ln) in lex(src):
            lines.append(f"{k}\x1f{v}\x1f{ln}")
    except LexError as ex:
        lines.append(f"LEXERROR\x1f{str(ex).split(':')[0].lstrip('L')}")
    return "\n".join(lines)


PROBES = [
    ("punct-prefix", "int main(void) { int x = 1 <<<= 2; }"),   # << 后回落 < <=
    ("punct-arrow-chain", "struct S { int x; }; int f(struct S* s) { return s->x; } int main(void) { S s2; return 0; }"),
    ("plus-chain", "int main(void) { int a = 1; return a+++1; }"),   # ++ 后 +
    ("shift-assign", "int main(void) { int x = 1; x >>= 1; return x; }"),
    ("colon-vs-ns", "namespace n { int f(void) { return 0; } } int main(void) { return n::f(); }"),
    ("str-escape", 'int main(void) { char* s = "a\\"b\\tc"; return 0; }'),
    ("block-comment-lines", "int main(void) {\n  /* a\n b\n c */\n  return 1;\n}"),
    ("line-comment", "// lead\nint main(void) { return 2; } // tail"),
    ("num-forms", "int main(void) { int x = 0x1F; int y = 1.5; return x; }"),
    ("ident-digits", "int main(void) { int _a1b = 7; return _a1b; }"),
    ("illegal-char-line2", "int main(void) {\n  int x = 1 @ 2;\n  return 0;\n}"),
    ("unclosed-str-line3", "int main(void) {\n  int a = 1;\n  char* s = \"abc;\n  return 0;\n}"),
    ("unclosed-comment", "int main(void) { return 0; } /* oops"),
    ("nul-byte", "int main(void) { int x = 1; } int\x00main(void) { return 0; }"),
]


def main():
    binp = build_tricpp()
    corpus = sorted(glob.glob(os.path.join(ROOT, "cpp", "tests", "*.cpp")))
    cases = [(f"corpus:{os.path.basename(f)}", open(f).read()) for f in corpus]
    cases += PROBES
    fails = []
    for (tag, src) in cases:
        # C 侧
        cfile = os.path.join(tempfile.gettempdir(), "clex_case.cpp")
        open(cfile, "w").write(src)
        r = subprocess.run([binp, "selftest-lex", cfile],
                           capture_output=True, text=True, timeout=60,
                           errors="replace")
        import re
        if r.returncode == 2:
            m = re.search(r"L(\d+):", r.stderr)
            c_dump = f"LEXERROR\x1f{m.group(1) if m else 0}"
        else:
            c_dump = r.stdout.rstrip("\n")
        p_dump = py_dump(src)
        if r.returncode not in (0, 2):
            fails.append((tag, f"tricpp exit={r.returncode} {r.stderr[:80]}"))
        elif c_dump != p_dump:
            fails.append((tag, f"diverge:\n  py={p_dump[:200]}\n  c ={c_dump[:200]}"))
    print(f"lexer 差分：{len(cases)} 语料，恒等 {len(cases) - len(fails)}，"
          f"分歧 {len(fails)}")
    for (tag, msg) in fails[:10]:
        print(f"  FAIL [{tag}] {msg}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
