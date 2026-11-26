#!/usr/bin/env python3
"""cpp/transpiler.py —— C++ -> C 自动降级器 v0.2。

v0.2 新增：
  多成员变量 / 构造参数 / 方法参数（含对象参数）/ 成员互操作

降级规则（cfront-3 同构）：
  class X { int a; int b; };    → struct X { int a; int b; };
  X(int pa, int pb) : a(pa), b(pb) {}
                              → void X_ctor(struct X *this_, int pa, int pb)
                                 { this_->a = pa; this_->b = pb; }
  ret X::method(T p) { ... }  → ret X_method(struct X *this_, T p) { ... }
  obj.method(arg)              → X_method(&obj, arg)
  X obj(pa, pb);               → struct X obj; X_ctor(&obj, pa, pb);
"""
import re
import sys


def transpile(src):
    classes = {}   # cname → class info
    out = []       # C 输出行

    # ---- Pass 1: 提取 class ----
    # 匹配 class Name { ... };（允许嵌套 {}）
    class_pat = re.compile(
        r'class\s+(\w+)\s*\{((?:[^{}]|\{[^{}]*\})*)\}\s*;', re.DOTALL)
    for cm in class_pat.finditer(src):
        cname = cm.group(1)
        body = cm.group(2)
        cls = {"name": cname, "members": [], "ctor_params": [],
               "ctor_inits": [], "ctor_body": "", "methods": []}

        # 成员变量（int/char 类型声明）
        for mm in re.finditer(r'int\s+(\w+)\s*;', body):
            cls["members"].append(mm.group(1))

        # 构造函数（ClassName(params) : init_list { body }）
        ctor_pat = re.compile(
            rf'{cname}\s*\(([^)]*)\)\s*:\s*([^{{]+?)\s*\{{([^}}]*)\}}')
        ctor_m = ctor_pat.search(body)
        if ctor_m:
            params_str = ctor_m.group(1).strip()
            cls["ctor_params"] = [p.strip() for p in params_str.split(",") if p.strip()]
            init_str = ctor_m.group(2).strip()
            cls["ctor_inits"] = [(m.group(1), m.group(2))
                                 for m in re.finditer(r'(\w+)\s*\(\s*(\w+)\s*\)', init_str)]
            cls["ctor_body"] = ctor_m.group(3).strip()

        # 方法（ret name(params) { body }）
        meth_pat = re.compile(
            r'(int|void)\s+(\w+)\s*\(([^)]*)\)\s*\{((?:[^{}]|\{[^{}]*\})*)\}')
        for mm in meth_pat.finditer(body):
            ret = mm.group(1)
            mname = mm.group(2)
            params_str = mm.group(3).strip()
            mbody = mm.group(4).strip()
            # 解析参数列表 → [(type, name)]
            params = []
            if params_str:
                for p in params_str.split(","):
                    p = p.strip()
                    parts = p.split()
                    if len(parts) >= 2:
                        params.append((parts[0], parts[-1]))
                    elif len(parts) == 1:
                        params.append((parts[0], ""))
            cls["methods"].append({"ret": ret, "name": mname,
                                   "params": params, "body": mbody})

        classes[cname] = cls

    # ---- Pass 2: 生成 C 输出 ----
    for cname, cls in classes.items():
        members = cls["members"]

        # struct 定义
        if members:
            mem_str = "; ".join(f"int {m}" for m in members)
            out.append(f"struct {cname} {{ {mem_str}; }};")
        else:
            out.append(f"struct {cname} {{}};")

        # 构造函数 → X_ctor（带参数）
        if cls["ctor_params"]:
            ctor_params = ", ".join(cls["ctor_params"])
            # this_->a = pa; 这种初始化
            init_body = "; ".join(f"this_->{m} = {v}" for (m, v) in cls["ctor_inits"])
            if cls["ctor_body"]:
                init_body += f"; {cls['ctor_body']}" if init_body else cls["ctor_body"]
            out.append(f"void {cname}_ctor(struct {cname} *this_, {ctor_params}) {{ {init_body}; }}")
        elif cls["ctor_inits"]:
            init_body = "; ".join(f"this_->{m} = {v}" for (m, v) in cls["ctor_inits"])
            out.append(f"void {cname}_ctor(struct {cname} *this_) {{ {init_body}; }}")

        # 方法 → X_method（this + 方法参数）
        for meth in cls["methods"]:
            mret = meth["ret"]
            mname = meth["name"]
            mparams = meth["params"]
            mbody = meth["body"]

            # 参数：this_ + 方法参数
            all_params = ["struct " + cname + " *this_"]
            for (ptype, pname) in mparams:
                if ptype in classes:
                    all_params.append(f"struct {ptype} *{pname}")
                else:
                    all_params.append(f"{ptype} {pname}")
            param_str = ", ".join(all_params)

            # body 改写：
            #   this 隐式成员 val → this_->val
            #   对象参数 other.x → other->x（对象参数是指针）
            obj_params = [pname for (ptype, pname) in mparams if ptype in classes]
            body = mbody
            for m in members:
                body = re.sub(rf'(?<![.\w]){m}\b', f"this_->{m}", body)
            for op in obj_params:
                body = re.sub(rf'\b{op}\.', f"{op}->", body)

            out.append(f"{mret} {cname}_{mname}({param_str}) {{ {body} }}")

    # ---- Pass 3: main 降级 ----
    main_pat = re.compile(r'int\s+main\s*\(\s*\)\s*\{(.*)\}', re.DOTALL)
    mm = main_pat.search(src)
    if mm:
        main_body = mm.group(1)

        for cname, cls in classes.items():
            members = cls["members"]

            # X var(params); → struct X var; X_ctor(&var, params);
            if cls["ctor_params"]:
                ctor_call_pat = re.compile(rf'{cname}\s+(\w+)\s*\(([^)]+)\)')
                for dm in ctor_call_pat.finditer(main_body):
                    var = dm.group(1)
                    args = dm.group(2)
                    main_body = main_body.replace(
                        dm.group(0),
                        f"struct {cname} {var};\n    {cname}_ctor(&{var}, {args})"
                    )
            else:
                decl_pat = re.compile(rf'{cname}\s+(\w+)\s*;')
                for dm in decl_pat.finditer(main_body):
                    var = dm.group(1)
                    ctor = f"{cname}_ctor(&{var});" if cls["ctor_inits"] or cls["ctor_params"] else ""
                    main_body = main_body.replace(dm.group(0),
                                                  f"struct {cname} {var};\n    {ctor}")

            # var.method(args) → X_method(&var, args)
            for meth in cls["methods"]:
                mname = meth["name"]
                # 方法参数中哪些是对象类型 → 实参需加 &
                obj_arg_idx = [i for i, (pt, pn) in enumerate(meth["params"]) if pt in classes]
                call_pat = re.compile(rf'(\w+)\.{mname}\(([^)]*)\)')
                for call in call_pat.finditer(main_body):
                    var = call.group(1)
                    args_str = call.group(2)
                    if args_str:
                        args = [a.strip() for a in args_str.split(",")]
                        for oi in obj_arg_idx:
                            if oi < len(args) and not args[oi].startswith("&"):
                                args[oi] = "&" + args[oi]
                        arg_str = ", ".join(args)
                        main_body = main_body.replace(
                            call.group(0),
                            f"{cname}_{mname}(&{var}, {arg_str})"
                        )
                    else:
                        main_body = main_body.replace(
                            call.group(0),
                            f"{cname}_{mname}(&{var})"
                        )

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
