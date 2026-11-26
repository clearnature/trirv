#!/usr/bin/env python3
"""cpp_lower —— Layer 5 降级：Type 驱动的 C 形态决策（对齐 cpp_lower.c 规划）。

正则时代的全部特设规则在此退役，替换为符号表查证：
  REF 变量读/写      → (*n)              （Type.kind=='ref'，非散装布尔）
  调用点实参         → bind 注记（sema N8 通过后才有 bind）：
                        addr→&x / ref→原样转发 / deref|value→值形
  引用返回           → &(this_->m)（左值取址）/ 转发原样
  VALUE 语境的 REF   → *(…)（自动解引用，含 b.ref() = 30 写穿）
  const 方法 this    → const struct X *this_（const 原样传递给 C 编译器）
  跨类成员/方法      → cast 带同一 const 性：((const struct B *)this_)->bm
                        ——cast 剥 const 是 C 编译器看不见的语义洞，
                        唯一必须自证的 const 一致性（用户拍板第 2 条）

scope 约定（降级期小符号表）：形参/局部名 → Type；元键：
  __cls__ 当前类名 / __const__ 方法 const 性 / __recv_owner__ 接收者声明类
"""
from cpp_parser import (New, DeleteStmt, CtorDecl, Num, StrLit, Id, This, Member, MethodCall, FreeCall, Bin, Un,
                        Assign, VarDecl, CtorStmt, ExprStmt, Return, If,
                        While, For, BlockStmt)
from cpp_mangle import mangle_method
from cpp_types import tref, type_name, TRef, TPtr

BASE_KEY = "__base"


def has_call(e):
    """AST 侧调用检测（物化临时量两步发射判定）。"""
    if isinstance(e, New):
        return getattr(e, "sig", None) is not None
    if isinstance(e, (MethodCall, FreeCall)):
        return True
    if isinstance(e, Bin):
        return has_call(e.l) or has_call(e.r)
    if isinstance(e, Un):
        return has_call(e.e)
    if isinstance(e, Assign):
        return has_call(e.lhs) or has_call(e.rhs)
    return False


def obj_param_as_ref(ty):
    """对象 VALUE 形参按指针形降级（trirv_cc 结构体按值传参子集外）——
    scope 里改记 ref 形态，body 的成员访问/传对象自动走指针。"""
    return tref(ty) if ty.kind == "class" else ty


def flat(n):
    """限定键 → C 标识符：geo::Box → geo_Box（C 无嵌套名空间）。"""
    return n.replace("::", "_")


def type_c(ty):
    """VALUE 类型 → C 形（class 扁平 struct 前缀；TPtr 加 *；const 传递）。"""
    if isinstance(ty, TPtr):
        return type_c(ty.pointee) + " *"
    name = type_name(ty)
    s = f"struct {flat(name)}" if ty.kind == "class" else name
    return (f"const {s}" if ty.is_const else s)


def decl_class(cd, classes_meta=None):
    """class → struct（扁平名；基类嵌入 __base 首成员=upcast 通道）。"""
    cname = flat(getattr(cd, "key", cd.name))
    bkey = getattr(cd, "base_key", None) or cd.base
    parts = []
    info = classes_meta.get(getattr(cd, "key", cd.name))
    has_virt = info is not None and info.vslots
    if has_virt:
        # vtable 指针最前（单继承布局兼容：base 的 vtbl 同在位 0）
        parts.append(f"struct {cname}_v *__vtbl;")
    if bkey:
        parts.append(f"struct {flat(bkey)} {BASE_KEY};")
    for (mn, mt) in cd.members:
        parts.append(f"{type_c(mt)} {mn};")
    inner = " ".join(parts)
    return f"struct {cname} {{ {inner} }};" if inner \
        else f"struct {cname} {{}};"


def lower_tu(decls, funcs, gdecls, sdefs, main, classes_meta):
    for cd in decls:
        for meth in cd.methods:
            if not hasattr(meth, "mname"):
                meth.mname = mangle_method(getattr(cd, "key", cd.name), meth)
    for f in funcs:
        if not hasattr(f, "mname"):
            f.mname = mangle_function(f)
    order, seen = [], set()

    def visit(cd):
        k = getattr(cd, "key", cd.name)
        if k in seen:
            return
        seen.add(k)
        if cd.base:
            for d in decls:
                if getattr(d, "key", d.name) == getattr(cd, "base_key", cd.base):
                    visit(d)
        order.append(cd)

    for cd in decls:
        if not getattr(cd, "forward", False):
            visit(cd)
    structs = [decl_class(cd, classes_meta) for cd in order]

    gdecls_c = []
    for (gn, gty, gi) in gdecls:
        base = type_name(gty)
        if gi is not None:
            gdecls_c.append(f"{base} {gn} = {expr(gi, {'value'}, {})};")
        else:
            gdecls_c.append(f"{base} {gn};")
    for (cn2, nm2, ty2, i2) in sdefs:
        cf2 = flat(cn2)
        base2 = type_name(ty2)
        if i2 is not None:
            gdecls_c.append(f"{base2} {cf2}_{nm2} = "
                            f"{expr(i2, {'value'}, {})};")
        else:
            gdecls_c.append(f"{base2} {cf2}_{nm2};")
    funcs_c = []
    for cd in decls:
        if getattr(cd, "forward", False):
            continue                         # 前向声明：无函数可发射
        funcs_c.extend(lower_ctors(cd, classes_meta))
        funcs_c.extend(lower_methods(cd))
        info = classes_meta.get(getattr(cd, "key", cd.name))
        if info and info.dtor:
            funcs_c.extend(lower_dtor(cd, info.dtor))
    for f in funcs:
        funcs_c.extend(lower_free_func(f))
    main_c = lower_main(main) if main is not None else None
    return {"structs": structs, "globals": gdecls_c, "funcs": funcs_c,
            "main": main_c}


# ============ 构造器（this 非 const，基类 cast 无 const 侧） ============
def lower_ctors(cd, classes_meta=None):
    out = []
    cname = flat(getattr(cd, "key", cd.name))
    bkey = getattr(cd, "base_key", None) or cd.base
    for ctor in cd.ctors:
        params = [f"struct {cname} *this_"]
        for (pty, pn) in ctor.params:
            params.append(decl_param(pty, pn))
        scope = {pn: obj_param_as_ref(pty) for (pty, pn) in ctor.params}
        scope["__cls__"] = getattr(cd, "key", cd.name)
        scope["__const__"] = False
        body = []
        for (tgt, args) in ctor.inits:
            rargs = ", ".join(expr(a, {"value"}, scope) for a in args)
            if bkey and tgt == bkey:
                body.append(f"{flat(bkey)}_ctor((struct {flat(bkey)} *)this_, "
                            f"{rargs});")
            else:
                body.append(f"this_->{tgt} = {rargs};")
        body.extend(stmts(ctor.body, scope, False))
        out.append(f"void {cname}_ctor({', '.join(params)}) "
                   f"{{ {' '.join(body)} }}" if body else
                   f"void {cname}_ctor({', '.join(params)}) {{ }}")
    return out


def lower_free_func(f):
    params = [decl_param(p, n) for (p, n) in f.params]
    sig = f"{render_ret(f.ret)} {f.mname}({', '.join(params)})"
    if f.body is None:
        return [sig + ";"]                # extern "C" 原型直出
    scope = {n: obj_param_as_ref(p) for (p, n) in f.params}
    scope["__cls__"], scope["__const__"] = None, False
    scope["__method__"] = f
    body = stmts(f.body, scope, False)
    return [f"{sig} {{ {' '.join(body)} }}" if body else f"{sig} {{ }}"]


def find_slot_impl(cd, info, slot, classes_meta):
    """槽 impl：本类 override → 本类方法；否则沿基类链找。"""
    for m in cd.methods:
        if m.name == slot:
            return m
    c = info.base
    while c:
        bi = classes_meta.get(c)
        if bi:
            for m in bi.methods:
                if m.name == slot:
                    return m
            c = bi.base
        else:
            break
    return None


def lower_dtor(cd, dt):
    cname = flat(getattr(cd, "key", cd.name))
    scope = {"__cls__": getattr(cd, "key", cd.name), "__const__": False,
             "__method__": None}
    body = stmts(dt.body, scope, False)
    return [f"void {cname}_dtor(struct {cname} *this_) "
            f"{{ {' '.join(body)} }}" if body else
            f"void {cname}_dtor(struct {cname} *this_) {{ }}"]


# ============ 方法 ============
def lower_methods(cd):
    out = []
    for meth in cd.methods:
        cname = flat(getattr(cd, "key", cd.name))
        if getattr(meth, "is_static", False):
            params = [decl_param(p, n) for (p, n) in meth.params]
        else:
            this_t = (f"const struct {cname} *this_" if meth.is_const
                      else f"struct {cname} *this_")
            params = [this_t] + [decl_param(p, n) for (p, n) in meth.params]
        scope = {n: obj_param_as_ref(p) for (p, n) in meth.params}
        scope["__cls__"] = getattr(cd, "key", cd.name)
        scope["__const__"] = meth.is_const
        scope["__method__"] = meth
        body = stmts(meth.body, scope, meth.is_const)
        out.append(f"{render_ret(meth.ret)} {meth.mname}({', '.join(params)}) "
                   f"{{ {' '.join(body)} }}" if body else
                   f"{render_ret(meth.ret)} {meth.mname}({', '.join(params)}) "
                   f"{{ }}")
    return out


def render_ret(ty):
    if ty.kind == "ref":
        return f"{type_c(ty.pointee)} *"
    return type_c(ty)


def decl_param(pty, pn):
    if pty.kind == "ref":
        return f"{type_c(pty.pointee)} *{pn}"
    return f"{type_c(pty)} {pn}"


# ============ 语句 ============
def stmts(ss, scope, is_const):
    return [stmt(s, scope, is_const) for s in ss]


def stmt(s, scope, is_const):
    pre = hoist_pre(s, scope)
    return pre + _stmt(s, scope, is_const)


def hoist_pre(s, scope):
    """0.0.10 物化临时量：const T& 绑临时量的具名落盘（调用者作用域）。
    含调用的初始化拆两步（trirv_cc INITLIST 限制）——临时量非 const。"""
    hs = getattr(s, "hoists", None)
    if not hs:
        return ""
    parts = []
    for (tn, e) in hs:
        if getattr(e, "ty", None) is not None and e.ty.kind == "class":
            if isinstance(e, FreeCall) and isinstance(e.sig, CtorDecl):
                # 0.0.17 显式构造临时：栈物化+ctor（无结构体赋值依赖）
                cf = flat(e.name)
                args = ", ".join(expr(a, {"value"}, scope) for a in e.args)
                parts.append(f"struct {cf} {tn}; {cf}_ctor(&{tn}, {args});")
            else:
                # 0.0.18 调用结果对象：栈临时+结构体赋值（p18c=12 证可用）
                cf = flat(type_name(e.ty))
                c = expr(e, {"value"}, scope)
                parts.append(f"struct {cf} {tn}; {tn} = {c};")
        elif isinstance(e, New):
            # 0.0.15：new X(args) → malloc + ctor（§四 规划行落地）
            cf = flat(e.cls)
            args = ", ".join(expr(a, {"value"}, scope) for a in e.args)
            ctor = f" {cf}_ctor({tn}{', ' + args if args else ''});" \
                if getattr(e, "sig", None) is not None else ""
            parts.append(f"struct {cf} *{tn} = malloc(sizeof(struct {cf}));"
                         f"{ctor}")
        else:
            c = expr(e, {"value"}, scope)
            if has_call(e):
                parts.append(f"int {tn}; {tn} = {c};")
            else:
                parts.append(f"const int {tn} = {c};")
    return " ".join(parts) + " "


def _stmt(s, scope, is_const):
    if isinstance(s, VarDecl):
        scope[s.name] = s.ty             # 局部类型入 scope（ptr/ref 通道）
        if isinstance(s.ty, TRef):
            # 引用局部 = 指针绑定；右值初始化已被物化（init 是 Id(temp)）
            pt = type_name(s.ty.pointee)
            c = "const " if s.ty.pointee.is_const else ""
            ie = s.init
            if ie is not None and ie.ty.kind == "ref":
                rhs = expr(ie, {"ref"}, scope)          # 引用转发指针本体
                return f"{c}{pt} *{s.name} = {rhs};"
            return f"{c}{pt} *{s.name} = &({expr(ie, {'value'}, scope)});"
        base = type_name(s.ty)
        cpre = "const " if s.ty.is_const else ""
        if isinstance(s.ty, TPtr) and isinstance(s.init, New):
            # Box* p = new Box(args);（§四 规划行：malloc 单声明 + ctor 后句）
            cf = flat(s.init.cls)
            args = ", ".join(expr(a, {"value"}, scope) for a in s.init.args)
            ctor = f" {cf}_ctor({s.name}{', ' + args if args else ''});" \
                if getattr(s.init, "sig", None) is not None else ""
            return (f"struct {cf} *{s.name} = "
                    f"malloc(sizeof(struct {cf}));{ctor}")
        if s.init is not None:
            c = expr(s.init, {"value"}, scope)
            if has_call(s.init):
                return f"{cpre}{base} {s.name}; {s.name} = {c};"
            return f"{cpre}{base} {s.name} = {c};"
        return f"{cpre}{base} {s.name};"
    if isinstance(s, CtorStmt):
        cf = flat(s.cls)
        args = ", ".join(expr(a, {"value"}, scope) for a in s.args)
        call = f"{cf}_ctor(&{s.var}{', ' + args if args else ''});"
        return f"struct {cf} {s.var}; {call}"
    if isinstance(s, DeleteStmt):
        e = f"free({expr(s.expr, {'value'}, scope)});"
        dk = getattr(s, "dtor_key", None)
        if dk:
            e = f"{flat(dk)}_dtor({expr(s.expr, {'value'}, scope)}); " + e
        return e
    if isinstance(s, ExprStmt):
        return f"{expr(s.expr, {'value'}, scope)};"
    if isinstance(s, Return):
        return ret_stmt(s, scope)
    if isinstance(s, If):
        els = f" else {stmt(s.els, scope, is_const)}" if s.els else ""
        return (f"if ({expr(s.cond, {'value'}, scope)}) "
                f"{stmt(s.then, scope, is_const)}{els}")
    if isinstance(s, While):
        return (f"while ({expr(s.cond, {'value'}, scope)}) "
                f"{stmt(s.body, scope, is_const)}")
    if isinstance(s, For):
        init = stmt(s.init, scope, is_const) if s.init else ";"
        cond = expr(s.cond, {"value"}, scope) if s.cond else "1"
        step = expr(s.step, {"value"}, scope) if s.step else ""
        init_c = init[:-1] if init.endswith(";") else init
        return f"for ({init_c}; {cond}; {step}) " \
               f"{stmt(s.body, scope, is_const)}"
    if isinstance(s, BlockStmt):
        inner = " ".join(stmt(x, scope, is_const) for x in s.stmts)
        return "{ " + inner + " }"
    raise AssertionError(f"lower: 未知语句 {type(s).__name__}")


def ret_stmt(s, scope):
    meth = scope.get("__method__")
    e = s.expr
    if e is None:
        return "return;"
    if meth is not None and meth.ret.kind == "ref":
        if e.ty.kind == "ref":
            return f"return {expr(e, {'ref'}, scope)};"      # 转发指针本体
        return f"return &({expr(e, {'value'}, scope)});"     # 左值取址
    return f"return {expr(e, {'value'}, scope)};"


# ============ 表达式（ctx：value=读值 / ref=要指针本体） ============
def expr(e, ctx, scope):
    if isinstance(e, Num):
        return str(e.v)
    if isinstance(e, StrLit):
        return e.v
    if isinstance(e, Id):
        if getattr(e, "ref_kind", None) == "static":
            return flat(getattr(e, "owner")) + "_" + e.name
        if getattr(e, "ref_kind", None) == "member":
            return this_member(e.name, getattr(e, "owner", None), scope)
        ty = scope.get(e.name)
        if ty is not None and ty.kind == "ref":
            return e.name if "ref" in ctx else f"(*{e.name})"
        return e.name
    if isinstance(e, This):
        return this_recv(getattr(e, "owner", None), scope)
    if isinstance(e, Member):
        form = member_obj(e.obj, scope)
        owner = getattr(e, "owner", None)
        if form == "this_" and owner not in (None, scope.get("__cls__")):
            c = "const " if scope.get("__const__") else ""
            form = f"({c}struct {owner} *)this_"   # this->跨类成员 cast 一致
        if getattr(e, "ref_kind", None) == "static":
            return flat(getattr(e, "owner")) + "_" + e.name   # 静态成员=全局
        sep = "->" if form_is_ptr(form, e.obj, scope) else "."
        return f"{form}{sep}{e.name}"
    if isinstance(e, New):
        raise AssertionError("lower: 裸 New 未经物化改写（sema/_mat 通道缺失）")
    if isinstance(e, (MethodCall, FreeCall)):
        return call(e, ctx, scope)
    if isinstance(e, Assign):
        lhs = expr(e.lhs, {"value"}, scope)     # REF 左值自然出 (*…)
        rhs = expr(e.rhs, {"value"}, scope)
        return f"{lhs} = {rhs}"
    if isinstance(e, Bin):
        c = getattr(e, "call", None)
        if c is not None:
            return expr(c, ctx, scope)       # 算子调用（sema 已 bind 注记）
        return (f"({expr(e.l, {'value'}, scope)} {e.op} "
                f"{expr(e.r, {'value'}, scope)})")
    if isinstance(e, Un):
        if e.op == "&":
            return f"&({expr(e.e, {'value'}, scope)})"
        if e.op == "*":
            return f"*({expr(e.e, {'value'}, scope)})"
        return f"({e.op}{expr(e.e, {'value'}, scope)})"
    raise AssertionError(f"lower: 未知表达式 {type(e).__name__}")


def this_member(name, owner, scope):
    """this 裸成员访问：本类直达；跨类 cast 带方法同款 const（拍板第 2 条）。"""
    if owner is None or scope.get("__cls__") in (None, owner):
        return f"this_->{name}"
    c = "const " if scope.get("__const__") else ""
    return f"(({c}struct {flat(owner)} *)this_)->{name}"


def this_recv(owner, scope):
    """this 作接收者：跨类（方法声明在祖先类）→ 基类指针 cast。"""
    if owner is None or scope.get("__cls__") in (None, owner):
        return "this_"
    c = "const " if scope.get("__const__") else ""
    return f"({c}struct {flat(owner)} *)this_"


def member_obj(o, scope):
    """数据成员接收者形态：局部对象=名字本体；REF/TPtr=指针；this=cast。"""
    if isinstance(o, This):
        return this_recv(getattr(o, "owner", None), scope)
    if isinstance(o, Id):
        if getattr(o, "ref_kind", None) == "member":
            ot = getattr(o, "ty", None)
            if isinstance(ot, TPtr):
                # 指针成员：this_->p 本体即指针（0.0.21）
                return this_member(o.name, getattr(o, "owner", None), scope)
            return this_recv(getattr(o, "owner", None), scope)
        ty = scope.get(o.name)
        if ty is not None and ty.kind in ("ref", "ptr"):
            return o.name
        return o.name
    if isinstance(o, New):
        raise AssertionError("lower: New 作接收者未经物化")
    return expr(o, {"ref" if o.ty.kind in ("ref", "ptr") else "value"}, scope)


def form_is_ptr(form, o, scope):
    if isinstance(o, This):
        return True                              # this_ / cast 后均指针
    if isinstance(o, Id):
        ty = scope.get(o.name)
        return (getattr(o, "ref_kind", None) == "member"
                or (ty is not None and ty.kind in ("ref", "ptr"))
                or isinstance(getattr(o, "ty", None), TPtr))
    if isinstance(o, MethodCall):
        return o.ty.kind == "ref"                # 引用返回→指针本体
    return False


def call(e, ctx, scope):
    meth = e.sig
    if getattr(meth, "is_static", False):
        # 静态方法：无 this 形参（实例调用形态亦省 receiver）
        parts = []
        for a in e.args:
            bind = getattr(a, "bind", "value")
            if bind == "addr":
                parts.append(f"&({expr(a, {'value'}, scope)})")
            elif bind == "ref":
                parts.append(expr(a, {"ref"}, scope))
            else:
                parts.append(expr(a, {"value"}, scope))
        s = f"{meth.mname}({', '.join(parts)})"
        if "ref" in ctx or e.ty.kind != "ref":
            return s
        return f"*({s})"
    if not hasattr(e, "obj"):                    # 自由函数（0.0.12）
        parts = []
        for a in e.args:
            bind = getattr(a, "bind", "value")
            if bind == "addr":
                parts.append(f"&({expr(a, {'value'}, scope)})")
            elif bind == "ref":
                parts.append(expr(a, {"ref"}, scope))
            else:
                parts.append(expr(a, {"value"}, scope))
        s = f"{meth.mname}({', '.join(parts)})"
        if "ref" in ctx or e.ty.kind != "ref":
            return s
        return f"*({s})"
    recv_owner = scope.get("__recv_owner__")
    scope["__recv_owner__"] = getattr(e, "sig_owner", None)
    parts = [recv_expr(e.obj, scope)]
    for a in e.args:
        bind = getattr(a, "bind", "value")
        if bind == "addr":
            parts.append(f"&({expr(a, {'value'}, scope)})")
        elif bind == "ref":
            parts.append(expr(a, {"ref"}, scope))
        else:
            parts.append(expr(a, {"value"}, scope))
    scope["__recv_owner__"] = recv_owner
    s = f"{meth.mname}({', '.join(parts)})"
    if "ref" in ctx or e.ty.kind != "ref":
        return s
    return f"*({s})"                             # VALUE 语境自动解引用


def recv_expr(o, scope):
    """方法接收者：对象左值取 &；引用/指针本体直传；this 走跨类 cast。"""
    if isinstance(o, This):
        return this_recv(getattr(o, "sig_owner", None)
                         if hasattr(o, "sig_owner") else
                         scope.get("__recv_owner__"), scope)
    if isinstance(o, Id):
        if getattr(o, "ref_kind", None) == "member":
            ot = getattr(o, "ty", None)
            if isinstance(ot, TPtr):
                # 指针成员：this_->p 本体即指针（0.0.21）
                return this_member(o.name, getattr(o, "owner", None), scope)
            return this_recv(getattr(o, "owner", None), scope)
        ty = scope.get(o.name)
        if ty is not None and ty.kind in ("ref", "ptr"):
            return o.name                        # 引用/指针对象→指针本体
        return f"&{o.name}"
    if isinstance(o, MethodCall):
        return expr(o, {"ref" if o.ty.kind == "ref" else "value"}, scope)
    return f"&({expr(o, {'value'}, scope)})"


# ============ main ============
def lower_main(main):
    scope = {"__cls__": None, "__const__": False, "__method__": None}
    body = " ".join(stmt(s, scope, False) for s in main)
    return f"int main(void) {{ {body} }}"
