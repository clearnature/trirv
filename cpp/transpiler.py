#!/usr/bin/env python3
"""transpiler v0.0.2 —— C++ -> C 降级器（单继承版）。
用法：python3 cpp/transpiler.py input.cpp output.c
"""
import re, sys

VERSION = "0.0.2"


def transpile(src):
    # ===== Pass 1: 提取 class =====
    cls_pat = re.compile(
        r'class\s+(\w+)\s*(?::\s*public\s+(\w+))?\s*\{((?:[^{}]|\{[^{}]*\})*)\}\s*;',
        re.DOTALL)
    classes = {}
    order = []
    for cm in cls_pat.finditer(src):
        name = cm.group(1)
        base = cm.group(2)
        body = cm.group(3)
        info = {"name": name, "base": base, "members": [],
                "ctor_params": [], "ctor_inits": [], "ctor_body": "", "methods": []}
        for mm in re.finditer(r'int\s+(\w+)\s*;', body):
            info["members"].append(mm.group(1))
        ctor = re.search(
            rf'{name}\s*\(([^)]*)\)\s*:\s*([^{{]+?)\s*\{{([^}}]*)\}}', body)
        if ctor:
            info["ctor_params"] = [p.strip() for p in ctor.group(1).split(",") if p.strip()]
            for im in re.finditer(r'(\w+)\s*\(\s*(\w+)\s*\)', ctor.group(2)):
                info["ctor_inits"].append((im.group(1), im.group(2)))
            info["ctor_body"] = ctor.group(3).strip()
        for mm in re.finditer(r'(int|void)\s+(\w+)\s*\(\s*\)\s*\{([^{}]*)\}', body):
            info["methods"].append({"ret": mm.group(1), "name": mm.group(2),
                                    "body": mm.group(3).strip()})
        classes[name] = info
        order.append(name)

    # ===== Pass 2: 生成 C =====
    out = []
    for name in order:
        info = classes[name]
        base = info["base"]
        members = info["members"]

        # struct（基类嵌入 __base 首成员）
        parts = []
        if base:
            parts.append(f"struct {base} __base;")
        if members:
            parts.append("; ".join(f"int {m}" for m in members) + ";")
        out.append(f"struct {name} {{ {' '.join(parts)} }};")

        # ctor
        if info["ctor_params"]:
            params = ", ".join(info["ctor_params"])
            base_calls = []
            mem_inits = []
            for (m, v) in info["ctor_inits"]:
                if base and m == base:
                    base_calls.append(f"{base}_ctor((struct {base} *)this_, {v});")
                else:
                    mem_inits.append(f"this_->{m} = {v};")
            init_str = " ".join(base_calls) + " " + "; ".join(mem_inits)
            if info["ctor_body"]:
                init_str += " " + info["ctor_body"]
            out.append(f"void {name}_ctor(struct {name} *this_, {params}) {{ {init_str} }}")
        elif info["ctor_inits"]:
            base_calls = []
            mem_inits = []
            for (m, v) in info["ctor_inits"]:
                if base and m == base:
                    base_calls.append(f"{base}_ctor((struct {base} *)this_, {v});")
                else:
                    mem_inits.append(f"this_->{m} = {v};")
            init_str = " ".join(base_calls) + " " + "; ".join(mem_inits)
            if info["ctor_body"]:
                init_str += " " + info["ctor_body"]
            out.append(f"void {name}_ctor(struct {name} *this_) {{ {init_str} }}")

        # 方法
        for meth in info["methods"]:
            ret = meth["ret"]
            mname = meth["name"]
            body = meth["body"]
            for m in members:
                body = re.sub(rf'(?<![.\w]){m}\b', f"this_->{m}", body)
            if base and base in classes:
                for bm in classes[base]["members"]:
                    body = re.sub(rf'(?<![.\w]){bm}\b',
                                  f"((struct {base} *)this_)->{bm}", body)
            out.append(f"{ret} {name}_{mname}(struct {name} *this_) {{ {body} }}")

    # ===== Pass 3: main 降级 =====
    main_pat = re.compile(r'int\s+main\s*\(\s*\)\s*\{(.*)\}', re.DOTALL)
    mm = main_pat.search(src)
    if mm:
        main_body = mm.group(1)
        for cname, info in classes.items():
            # ctor 声明替换
            if info["ctor_params"]:
                # 有参构造：X var(args); → struct X var; X_ctor(&var, args);
                ctor_pat = re.compile(rf'{cname}\s+(\w+)\s*\(([^)]+)\)')
                for dm in ctor_pat.finditer(main_body):
                    var = dm.group(1)
                    main_body = main_body.replace(dm.group(0),
                        f"struct {cname} {var}; {cname}_ctor(&{var}, {dm.group(2)});")
            elif info["ctor_inits"] or info["ctor_body"]:
                # 无参构造：X var; → struct X var; X_ctor(&var);
                ctor_inits_pat = re.compile(rf'{cname}\s+(\w+)\s*;')
                for dm in ctor_inits_pat.finditer(main_body):
                    var = dm.group(1)
                    main_body = main_body.replace(dm.group(0),
                        f"struct {cname} {var}; {cname}_ctor(&{var});")
            # 方法调用替换
            for meth in info["methods"]:
                call_pat = re.compile(rf'(\w+)\.{meth["name"]}\(\)')
                main_body = call_pat.sub(
                    lambda m: f"{cname}_{meth['name']}(&{m.group(1)})", main_body)
        out.append(f"int main(void) {{{main_body}}}")

    return "\n\n".join(out)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"usage: transpiler.py input.cpp output.c  (v{VERSION})")
        sys.exit(1)
    src = open(sys.argv[1]).read()
    c_src = transpile(src)
    open(sys.argv[2], "w").write(c_src)
    print(f"v{VERSION}: {sys.argv[1]} -> {sys.argv[2]}")
