#!/usr/bin/env python3
"""cpp/transpiler.py —— C++ -> C 自动降级器 v0.1（cfront 式）。"""
import re
import sys


def transpile(src):
    classes = {}
    out = []

    # Pass 1: 提取 class 定义
    class_pat = re.compile(
        r'class\s+(\w+)\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}\s*;', re.DOTALL)
    for m in class_pat.finditer(src):
        cname = m.group(1)
        body = m.group(2)
        cls = {"name": cname, "members": [], "methods": [], "ctors": []}
        for mem in re.finditer(r'(?:int|char|void)\s+(\w+)\s*;', body):
            cls["members"].append(mem.group(1))
        ctor_pat = re.compile(
            rf'{cname}\s*\(\s*\)\s*:\s*([\w(,)\s]+?)\s*\{{([^}}]*)\}}')
        for cm in ctor_pat.finditer(body):
            inits = []
            for init in re.finditer(r'(\w+)\s*\(\s*([^)]*)\s*\)', cm.group(1)):
                inits.append((init.group(1), init.group(2)))
            cls["ctors"].append({"inits": inits, "body": cm.group(2).strip()})
        meth_pat = re.compile(
            r'(int|void)\s+(\w+)\s*\(\s*\)\s*\{([^{}]*)\}')
        for mm in meth_pat.finditer(body):
            cls["methods"].append({"ret": mm.group(1), "name": mm.group(2),
                                   "body": mm.group(3).strip()})
        classes[cname] = cls

    # Pass 2: 生成 C
    for cname, cls in classes.items():
        if cls["members"]:
            members = ", ".join(f"int {m}" for m in cls["members"])
            out.append(f"struct {cname} {{ {members}; }};")
        else:
            out.append(f"struct {cname} {{}};")
        for ctor in cls["ctors"]:
            inits = "; ".join(f"this_->{m} = {v};" for (m, v) in ctor["inits"])
            if ctor["body"]:
                out.append(f"void {cname}_ctor(struct {cname} *this_) {{ {inits} {ctor['body']} }}")
            else:
                out.append(f"void {cname}_ctor(struct {cname} *this_) {{ {inits} }}")
        for meth in cls["methods"]:
            body = re.sub(r'\b(\w+)\b',
                          lambda m: f"this_->{m.group(1)}"
                          if m.group(1) in cls["members"] else m.group(0),
                          meth["body"])
            out.append(f"{meth['ret']} {cname}_{meth['name']}(struct {cname} *this_) {{ {body} }}")

    # Pass 3: main 降级
    main_pat = re.compile(r'int\s+main\s*\(\s*\)\s*\{(.*)\}', re.DOTALL)
    mm = main_pat.search(src)
    if mm:
        main_body = mm.group(1)
        for cname, cls in classes.items():
            ctor_call = f"    {cname}_ctor(&{cname.lower()});\n"
            decl_pat = re.compile(rf'{cname}\s+(\w+)\s*;')
            for dm in decl_pat.finditer(main_body):
                var = dm.group(1)
                main_body = main_body.replace(dm.group(0),
                                              f"struct {cname} {var};\n    {cname}_ctor(&{var});")
            for meth in cls["methods"]:
                call_pat = re.compile(rf'(\w+)\.{meth["name"]}\(\)')
                for call in call_pat.finditer(main_body):
                    main_body = main_body.replace(call.group(0),
                                                  f"{cname}_{meth['name']}(&{call.group(1)})")
        out.append(f"int main(void) {{{main_body}}}")

    return "\n\n".join(out)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: transpiler.py input.cpp output.c")
        sys.exit(1)
    src = open(sys.argv[1]).read()
    c_src = transpile(src)
    open(sys.argv[2], "w").write(c_src)
    print(f"transpiled {sys.argv[1]} -> {sys.argv[2]}")
