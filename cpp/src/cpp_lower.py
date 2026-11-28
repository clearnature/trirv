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
from cpp_parser import (New, DeleteStmt, CtorDecl, CopyInit, NamedCast, Throw, Try, EH_FLAG, Num, StrLit, Id, This, Member, MethodCall, FreeCall, Bin, Un,
                        Assign, VarDecl, CtorStmt, ExprStmt, Return, If,
                        While, For, BlockStmt)
from cpp_mangle import mangle_method
from cpp_types import tref, type_name, TRef, TPtr, TInt, TChar

BASE_KEY = "__base"


def has_call(e):
    """AST 侧调用检测（物化临时量两步发射判定）。"""
    if isinstance(e, NamedCast):
        return has_call(e.expr)             # 内层调用 → 两步发射
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
    bkeys = getattr(cd, "bases_keys", None) or         ([bkey] if bkey else [])
    parts = []
    info = classes_meta.get(getattr(cd, "key", cd.name))
    # cfront 布局：vtbl 槽只在根类子对象位 0——派生类经 __base 共享同一
    # 槽位（派生 ctor 改指派生表），上转型零调整（0.0.26 布局修复）
    is_virt_root = info is not None and info.vslots and not cd.base
    if is_virt_root:
        parts.append(f"struct {cname}_v *__vtbl;")
    for i, bk in enumerate(bkeys):        # 0.0.43 多基类子对象顺次嵌入
        fname = BASE_KEY if i == 0 else f"__base{i}"
        parts.append(f"struct {flat(bk)} {fname};")
    for (mn, mt) in cd.members:
        parts.append(f"{type_c(mt)} {mn};")
    inner = " ".join(parts)
    return f"struct {cname} {{ {inner} }};" if inner \
        else f"struct {cname} {{}};"


CLASSES_META = [None]      # 当前 TU 类表（虚调用根指针查询用）


def lower_tu(decls, funcs, gdecls, sdefs, main, classes_meta):
    CLASSES_META[0] = classes_meta
    DC_HELPERS[0] = []
    CM_HELPERS[0] = []
    DC_SEQ[0] = 0
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
    vtbl_c = []
    for cd in order:
        info = classes_meta.get(getattr(cd, "key", cd.name))
        if not info or not info.vslots:
            continue
        cname = flat(getattr(cd, "key", cd.name))
        slots = []
        for slot in info.vslots:
            m = find_slot_impl(cd, info, slot, classes_meta)
            if m is None:
                # 纯虚槽：签名取声明（任意层含 body=None 的声明）
                m = find_slot_decl(cd, info, slot, classes_meta)
            rtxt = render_ret(m.ret)
            ps = ["void *slf"] + \
                 [decl_param(p, n) for (p, n) in m.params]
            slots.append(f"{rtxt} (*{slot})({', '.join(ps)});")
        vtbl_c.append(f"struct {cname}_v {{ const char *__ti; "
                      f"{' '.join(slots)} }};")
        vtbl_c.append(f"struct {cname}_v {cname}_vt;")
        vtbl_c.append('char ' + cname + '_ti[] = "' + getattr(info, 'key', cname) + '";')
    tiid_seq = [0]
    for cd in order:
        tiid_seq[0] += 1
        fkey = flat(getattr(cd, "key", cd.name))
        info = classes_meta.get(getattr(cd, "key", cd.name))
        if info is not None and info.vslots:
            continue                         # 有虚类已在 vtbl_c 发射（0.0.32 去重）
        structs.append('char ' + fkey + '_ti[] = "'
                       + getattr(cd, "key", cd.name) + '";')
        structs.append(f"int {fkey}_tiid = {tiid_seq[0] * 11};")
    structs = vtbl_c + structs

    gdecls_c = []
    for (gn, gty, gi) in gdecls:
        base = type_name(gty)
        if gi is not None:
            gdecls_c.append(f"{base} {gn} = {expr(gi, {'value'}, {})};")
        else:
            gdecls_c.append(f"{base} {gn};")
    eh_prologue = []
    if EH_FLAG[0]:
        gdecls_c.append("int __ex_active = 0;")
        gdecls_c.append("int __ex_obj = 0;")
        gdecls_c.append("int __ex_ti = 0;")
        gdecls_c.append("int __ex_rth = 0;")
        eh_prologue.append("void __throw(int v, int ti) { __ex_obj = v; "
                           "__ex_ti = ti; __ex_active = 1; }")
        gdecls_c.append("char __ex_buf[72];")
        eh_prologue.append(
            "void __throw_obj(void *p, int sz, int ti) { int *s = p; "
            "int *d = (int*)__ex_buf; int i = 0; "
            "while (i * 9 < sz) { d[i] = s[i]; i = i + 1; } "
            "__ex_obj = (int)__ex_buf; __ex_ti = ti; __ex_active = 1; }")
    for (cn2, nm2, ty2, i2) in sdefs:
        cf2 = flat(cn2)
        base2 = type_name(ty2)
        if i2 is not None:
            gdecls_c.append(f"{base2} {cf2}_{nm2} = "
                            f"{expr(i2, {'value'}, {})};")
        else:
            gdecls_c.append(f"{base2} {cf2}_{nm2};")
    funcs_c = eh_prologue
    for cd in decls:
        if getattr(cd, "forward", False):
            continue                         # 前向声明：无函数可发射
        # 0.0.26 修复：方法先于 ctor 发射——trirv_cc fn-ptr 赋值引用后
        # 定义函数=地址 0（p26r=0 p26s=25 实证），前序发射消除前向引用
        funcs_c.extend(lower_methods(cd))
    for cd in decls:
        if getattr(cd, "forward", False):
            continue
        funcs_c.extend(lower_ctors(cd, classes_meta))
        info = classes_meta.get(getattr(cd, "key", cd.name))
        if info and info.dtor:
            funcs_c.extend(lower_dtor(cd, info.dtor))
    for f in funcs:
        funcs_c.extend(lower_free_func(f))
    main_c = lower_main(main, getattr(main, "main_dtors", None) \
                        if main is not None else None) \
        if main is not None else None
    # 0.0.33 dc helper 发射（注册发生在 main 降级期间——须后置）
    for (hname, tis) in (DC_HELPERS[0] or []):
        ifs = " ".join(f"if (dyn == (void *)&{t}) {{ return obj; }}"
                       for t in tis)
        funcs_c.append(f"void *{hname}(void *obj, void *dyn) {{ "
                       f"if (obj == 0) {{ return 0; }} {ifs} return 0; }}")
    for (hname, tis) in (CM_HELPERS[0] or []):
        ifs = " ".join(f"if (dyn == {t}) {{ return 1; }}" for t in tis)
        funcs_c.append(f"int {hname}(int dyn) {{ {ifs} return 0; }}")
    return {"structs": structs, "globals": gdecls_c, "funcs": funcs_c,
            "main": main_c}


# ============ 构造器（this 非 const，基类 cast 无 const 侧） ============
def _alloc_expr(cf, classes_meta):
    """new 分配形态：类有 operator new → X_new(sizeof)；否则 malloc。"""
    info = classes_meta.get(cf) if classes_meta else None
    if info is not None and getattr(info, "has_op_new", False):
        return f"{cf}_new(sizeof(struct {cf}))"
    return f"malloc(sizeof(struct {cf}))"


def _eh_ret(scope):
    """函数级上浮返回面：main=terminate 134 / 非 main 按签名。
    0.0.37 RAII：return 前插已声明 dtor 对象逆序（so_far 展开）。"""
    dt = "".join(f"{flat(ck)}_dtor(&{v}); "
                 for (v, ck) in reversed(scope.get("__dtors_so_far__", [])))
    if scope.get("__terminate__"):
        return f"{dt}return 134;"
    return f"{dt}return {';' if scope.get('__ret_void__') else '0;'}"


def aug_try_body(body):
    """try 体循环条件增补：cond && !__ex_active（中段 throw 退出循环）。
    不下探嵌套 Try（其体由自身机制守卫）。AST 级原位修改——sema 已完成。"""
    for s in body:
        _aug_stmt(s)


def _aug_stmt(s):
    if isinstance(s, While):
        s.cond = Bin("&&", s.cond, Un("!", Id("__ex_active")))
        _aug_stmt(s.body)
    elif isinstance(s, For):
        if s.cond is not None:
            s.cond = Bin("&&", s.cond, Un("!", Id("__ex_active")))
        if s.body is not None:
            _aug_stmt(s.body)
    elif isinstance(s, If):
        _aug_stmt(s.then)
        if s.els is not None:
            _aug_stmt(s.els)
    elif isinstance(s, BlockStmt):
        for x in s.stmts:
            _aug_stmt(x)


def _ctor_is_copy(ctor, cname):
    """拷贝 ctor 判定（lower 侧镜像 sema._is_copy_ctor）。"""
    if len(ctor.params) != 1:
        return False
    p = ctor.params[0][0]
    return (p.kind == "ref" and p.pointee.kind == "class"
            and p.pointee.name == cname and p.pointee.is_const)


def lower_ctors(cd, classes_meta=None):
    out = []
    cname = flat(getattr(cd, "key", cd.name))
    bkey = getattr(cd, "base_key", None) or cd.base
    for ctor in cd.ctors:
        if ctor.body is None:
            continue                         # 声明（体=外联定义并入实例）
        cfname = (f"{cname}_ctor_copy" if _ctor_is_copy(ctor, cname)
                  else f"{cname}_ctor")
        params = [f"struct {cname} *this_"]
        for (pty, pn) in ctor.params:
            params.append(decl_param(pty, pn))
        scope = {pn: obj_param_as_ref(pty) for (pty, pn) in ctor.params}
        scope["__cls__"] = getattr(cd, "key", cd.name)
        scope["__const__"] = False
        body = []
        vset = []
        if classes_meta.get(cd.key) and classes_meta[cd.key].vslots:
            # 派生类：cast 到根类后单箭头（trirv_cc 不支持 ->x.__y 混合链）
            rkey = _root_of(cd.key, classes_meta)
            rf = flat(rkey) if rkey else cname
            vset.append(f"((struct {rf} *)this_)->__vtbl = &{cname}_vt;")
            vset.append(f"{cname}_vt.__ti = {cname}_ti;")
            for slot in classes_meta[cd.key].vslots:
                m = find_slot_impl(cd, classes_meta[cd.key], slot,
                                   classes_meta)
                if m is None:
                    continue                 # 纯虚槽=NULL（抽象类不可实例化保证不可达）
                vset.append(f"{cname}_vt.{slot} = {m.mname};")
        bkeys = getattr(cd, "bases_keys", None) or             ([bkey] if bkey else [])
        for (tgt, args) in ctor.inits:
            rargs = ", ".join(expr(a, {"value"}, scope) for a in args)
            if tgt in bkeys:
                i = bkeys.index(tgt)      # 0.0.43 多基类 init
                bk = bkeys[i]
                recv = f"(struct {flat(bk)} *)this_" if i == 0                     else f"&this_->__base{i}"
                body.append(f"{flat(bk)}_ctor({recv}, {rargs});")
            else:
                body.append(f"this_->{tgt} = {rargs};")
        ustmts = stmts(ctor.body, scope, False)
        body = body + vset + ustmts  # 序：base_ctor/成员初始化 → vtbl 设定 → 用户体
        out.append(f"void {cfname}({', '.join(params)}) "
                   f"{{ {' '.join(body)} }}" if body else
                   f"void {cfname}({', '.join(params)}) {{ }}")
    return out


def lower_free_func(f):
    params = [decl_param(p, n) for (p, n) in f.params]
    sig = f"{render_ret(f.ret)} {f.mname}({', '.join(params)})"
    if f.body is None:
        return [sig + ";"]                # extern "C" 原型直出
    scope = {n: obj_param_as_ref(p) for (p, n) in f.params}
    scope["__cls__"], scope["__const__"] = None, False
    scope["__method__"] = f
    scope["__ret_void__"] = f.ret.kind == "void"
    scope["__fn_dtors__"] = getattr(f, "fn_dtors", [])   # 0.0.37
    body = stmts(f.body, scope, False)
    body = body + [f"{flat(ck)}_dtor(&{v});"
                   for (v, ck) in reversed(getattr(f, "fn_dtors", []))]
    return [f"{sig} {{ {' '.join(body)} }}" if body else f"{sig} {{ }}"]


TRYD = [0]                 # try 嵌套深度（0.0.36 函数级上浮守卫）
CM_HELPERS = [None]        # [(hname, [ti_global…])]（0.0.38 类 catch 匹配）
DC_HELPERS = [None]        # [(hname, target_flat, [acceptable_flat…])]
DC_SEQ = [0]


def _lower_is_ancestor(anc, cur, meta):
    c = cur
    while c:
        if c == anc:
            return True
        c = meta[c].base if c in meta else None
    return False


def _root_of(cname, classes_meta):
    """基类链上溯到根（vtbl 槽所在子对象）。"""
    c = cname
    while c and c in classes_meta:
        info = classes_meta[c]
        if not info.base:
            return c
        c = info.base
    return c


def find_slot_decl(cd, info, slot, classes_meta):
    """槽声明（含 body=None 纯虚）——vtbl 签名发射用。"""
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


def find_slot_impl(cd, info, slot, classes_meta):
    """槽 impl：本类 override → 本类方法；否则沿基类链找。
    body None（纯虚声明）不算 impl（0.0.27）。"""
    for m in cd.methods:
        if m.name == slot and m.body is not None:
            return m
    c = info.base
    while c:
        bi = classes_meta.get(c)
        if bi:
            for m in bi.methods:
                if m.name == slot and m.body is not None:
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
        if meth.body is None:
            continue                         # 纯虚声明：无 C 函数可发射
        cname = flat(getattr(cd, "key", cd.name))
        sp = getattr(meth, "is_special", None)
        if sp:
            # 0.0.30 类专属分配器：X_new/X_delete（无 this，约定名）
            params = [decl_param(p, n) for (p, n) in meth.params]
            scope = {n: p for (p, n) in meth.params}
            scope["__cls__"], scope["__const__"] = cd.key, False
            scope["__method__"] = meth
            body = stmts(meth.body, scope, False)
            fname = f"{cname}_{'new' if sp == 'new' else 'delete'}"
            out.append(f"{render_ret(meth.ret)} {fname}({', '.join(params)}) "
                       f"{{ {' '.join(body)} }}")
            continue
        if getattr(meth, "is_static", False):
            params = [decl_param(p, n) for (p, n) in meth.params]
        else:
            this_t = (f"const struct {cname} *this_" if meth.is_const
                      else f"struct {cname} *this_")
            params = [this_t] + [decl_param(p, n) for (p, n) in meth.params]
        scope = {n: obj_param_as_ref(p) for (p, n) in meth.params}
        scope["__cls__"] = getattr(cd, "key", cd.name)
        scope["__const__"] = meth.is_const
        scope["__ret_void__"] = meth.ret.kind == "void"   # 0.0.35 EH 上浮
        scope["__fn_dtors__"] = getattr(meth, "fn_dtors", [])   # 0.0.37
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
            # 0.0.15：new X(args) → 分配 + ctor（0.0.30：类专属分配器接管）
            cf = flat(e.cls)
            args = ", ".join(expr(a, {"value"}, scope) for a in e.args)
            ctor = f" {cf}_ctor({tn}{', ' + args if args else ''});" \
                if getattr(e, "sig", None) is not None else ""
            parts.append(f"struct {cf} *{tn} = "
                         f"{_alloc_expr(cf, CLASSES_META[0])};{ctor}")
        elif getattr(e, "ty", None) is not None and e.ty.kind == "ptr":
            # 0.0.26 vtbl 指针提升（虚分派拆链）——正确 v 表指针类型
            rf = flat(type_name(e.ty.pointee))
            c = expr(e, {"value"}, scope)
            parts.append(f"struct {rf}_v *{tn} = {c};")
        else:
            c = expr(e, {"value"}, scope)
            if has_call(e):
                parts.append(f"int {tn}; {tn} = {c};")
            else:
                parts.append(f"const int {tn} = {c};")
    return " ".join(parts) + " "


def _stmt(s, scope, is_const):
    if isinstance(s, CopyInit):
        cf = flat(s.cls)
        if isinstance(s.init, FreeCall) and isinstance(getattr(s.init, "sig", None), CtorDecl):
            # 零拷贝 elision：直接构造（C++ 法）
            args = ", ".join(expr(a, {"value"}, scope) for a in s.init.args)
            scope[s.var] = s.ty
            return f"struct {cf} {s.var}; {cf}_ctor(&{s.var}, {args});"
        rhs = expr(s.init, {"value"}, scope)
        scope[s.var] = s.ty              # 局部类型入 scope
        if getattr(s, "conv", None) is not None:
            # 0.0.30 隐式转换（单参非 explicit ctor）
            return f"struct {cf} {s.var}; {cf}_ctor(&{s.var}, {rhs});"
        if s.user_copy:
            return f"struct {cf} {s.var}; {cf}_ctor_copy(&{s.var}, &({rhs}));"
        return f"struct {cf} {s.var}; {s.var} = {rhs};"   # 隐式=两步结构体赋值
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
        if isinstance(s.ty, TPtr):
            base = type_c(s.ty)             # 0.0.31：指针形完整渲染（const int *)
            cpre = ""
        else:
            base = type_name(s.ty)
            cpre = "const " if s.ty.is_const else ""
        if isinstance(s.ty, TPtr) and isinstance(s.init, New):
            # Box* p = new Box(args);（§四 规划行：malloc 单声明 + ctor 后句）
            cf = flat(s.init.cls)
            args = ", ".join(expr(a, {"value"}, scope) for a in s.init.args)
            ctor = f" {cf}_ctor({s.name}{', ' + args if args else ''});" \
                if getattr(s.init, "sig", None) is not None else ""
            return (f"struct {cf} *{s.name} = "
                    f"{_alloc_expr(cf, CLASSES_META[0])};{ctor}")
        if s.init is not None:
            c = expr(s.init, {"value"}, scope)
            if has_call(s.init):
                cp = f" if (__ex_active) {{ {_eh_ret(scope)} }}" \
                    if EH_FLAG[0] and not scope.get("__eh_suppress__") else ""
                return f"{cpre}{base} {s.name}; {s.name} = {c};{cp}"
            return f"{cpre}{base} {s.name} = {c};"
        return f"{cpre}{base} {s.name};"
    if isinstance(s, CtorStmt):
        cf = flat(s.cls)
        args = ", ".join(expr(a, {"value"}, scope) for a in s.args)
        call = f"{cf}_ctor(&{s.var}{', ' + args if args else ''});"
        info = CLASSES_META[0].get(s.cls) if CLASSES_META[0] else None
        if info is not None and info.dtor:
            scope.setdefault("__dtors_so_far__", []).append((s.var, s.cls))
        eh_cp = (f" if (__ex_active) {{ {_eh_ret(scope)} }}"
                 if TRYD[0] == 0 and EH_FLAG[0]
                 and not scope.get("__eh_suppress__") else "")
        return f"struct {cf} {s.var}; {call}{eh_cp}"
    if isinstance(s, Throw):
        if s.expr is None:
            # 0.0.36 bare rethrow：rth 标记——arm 清位跳过，active 保持，
            # 同帧外层 catch 接续（无 goto 的结构化等价）
            return "__ex_rth = 1;"
        if s.expr.ty.kind == "class":
            # 0.0.38 类对象抛出：拷入 __ex_buf + tiid 值（零指针转换）
            cf = flat(s.throw_cls)
            ti_expr = f"{cf}_tiid"
            if TRYD[0] > 0:
                return (f"__throw_obj(&({expr(s.expr, {'value'}, scope)}), "
                        f"sizeof(struct {cf}), {ti_expr});")
            retz = "return;" if scope.get("__ret_void__") else "return 0;"
            return (f"__throw_obj(&({expr(s.expr, {'value'}, scope)}), "
                    f"sizeof(struct {cf}), {ti_expr}); {retz}"
                    f"  /* throw 即返——本帧展开起点 */")
        ti = 2 if s.expr.ty.kind == "char" else 1
        if TRYD[0] > 0:
            # 同帧 try 体内：设异常即 fall-through——守卫跳过后续语句，
            # try 尾 catch 检查接住（0.0.37：无 goto 的结构化等价）
            return f"__throw({expr(s.expr, {'value'}, scope)}, {ti});"
        retz = "return;" if scope.get("__ret_void__") else "return 0;"
        return (f"__throw({expr(s.expr, {'value'}, scope)}, {ti}); {retz}"
                f"  /* throw 即返——本帧展开起点 */")
    if isinstance(s, Try):
        # 方案 C 检查点展开（docs/EXCEPTION-DESIGN.md）
        aug_try_body(s.try_body)               # 循环条件增补
        TRYD[0] += 1                           # 体期间：函数级上浮停用
        parts = []
        for x in s.try_body:
            if isinstance(x, VarDecl) and x.init is not None \
                    and has_call(x.init):
                # 声明提升（不守卫）+ 赋值守卫——decl/uses 作用域割裂防御
                base = type_name(x.ty)
                cpre = "const " if x.ty.is_const else ""
                c = expr(x.init, {"value"}, scope)
                parts.append(f"{cpre}{base} {x.name};")
                parts.append(f"if (!__ex_active) {{ {x.name} = {c}; }}")
                continue
            if isinstance(x, VarDecl):
                parts.append(stmt(x, scope, is_const))   # 纯声明不守卫
                continue
            parts.append(f"if (!__ex_active) {{ {stmt(x, scope, is_const)} }}")
        TRYD[0] -= 1                           # arms 属 try 语句层（else 深度判定）
        arms = []
        cm_needed = []
        for (cty, cvar, cbody) in s.catches:
            if cty is None:
                # catch(...)：无条件匹配（if(1) 化——bare 块不可接 else）
                body_c = " ".join(stmt(x, scope, is_const) for x in cbody)
                arms.append(f"if (1) {{ {body_c} if (!__ex_rth) "
                           f"{{ __ex_active = 0; }} __ex_rth = 0; }}")
                continue
            if cty.kind == "ref":
                # 0.0.38 类引用捕获：__cm_N 匹配（target∪descendants）+
                # 形参 = (struct X *)__ex_obj（引用→指针，共享槽零调整）
                cf = flat(cty.pointee.name)
                DC_SEQ[0] += 1
                hname = f"__cm_{DC_SEQ[0]}"
                acc = [flat(cf) + "_tiid"]
                meta = CLASSES_META[0]
                for k in meta:
                    if k != cf and _lower_is_ancestor(cf, k, meta):
                        acc.append(flat(k) + "_tiid")
                cm_needed.append((hname, acc))
                csc = dict(scope)
                csc["__eh_suppress__"] = True   # catch 体：上浮检查点停用
                csc[cvar] = tref(cty.pointee)
                cbody_c = " ".join(stmt(x, csc, is_const) for x in cbody)
                arms.append(f"if ({hname}(__ex_ti)) {{ "
                            f"struct {cf} *{cvar} = (struct {cf} *)__ex_obj; "
                            f"{cbody_c} if (!__ex_rth) {{ __ex_active = 0; }} "
                            f"__ex_rth = 0; }}")
                continue
            ti = 2 if cty.kind == "char" else 1
            csc = dict(scope)
            csc["__eh_suppress__"] = True       # catch 体：上浮检查点停用
            csc[cvar] = TInt() if cty.kind == "int" else TChar()
            cbody_c = " ".join(stmt(x, csc, is_const) for x in cbody)
            arms.append(f"if (__ex_ti == {ti}) {{ int {cvar} = __ex_obj; "
                        f"{cbody_c} if (!__ex_rth) {{ __ex_active = 0; }} "
                        f"__ex_rth = 0; }}")
        chain = " else ".join(arms)
        if scope.get("__terminate__") and TRYD[0] == 0:
            chain += " else { return 134; }"   # main 顶层未匹配 = terminate
            #（TRYD>0=有外层 try：fall-through——外层 catch 接续）
        if cm_needed:
            CM_HELPERS[0] = (CM_HELPERS[0] or []) + cm_needed   # 0.0.38 flush
        return " ".join(parts) + f"if (__ex_active) {{ {chain} }}"
    if isinstance(s, DeleteStmt):
        c = expr(s.expr, {"value"}, scope)
        parts = []
        dk = getattr(s, "dtor_key", None)
        if dk:
            parts.append(f"{flat(dk)}_dtor({c});")
        od = getattr(s, "op_delete", None)
        if od:
            parts.append(f"{flat(od)}_delete({c});")   # 0.030 类专属回收
        else:
            parts.append(f"free({c});")
        return " ".join(parts)
    if isinstance(s, ExprStmt):
        c = f"{expr(s.expr, {'value'}, scope)};"
        if has_call(s.expr) and TRYD[0] == 0 and EH_FLAG[0] \
                and not scope.get("__eh_suppress__"):
            c += f" if (__ex_active) {{ {_eh_ret(scope)} }}"   # 0.0.36 函数级上浮（EH 门控）
        return c
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
    dt = "".join(f"{flat(ck)}_dtor(&{v}); "
                 for (v, ck) in reversed(scope.get("__dtors_so_far__", [])))
    if e is None:
        return f"{dt}return;"
    if meth is not None and meth.ret.kind == "ref":
        if e.ty.kind == "ref":
            return f"{dt}return {expr(e, {'ref'}, scope)};"      # 转发指针本体
        return f"{dt}return &({expr(e, {'value'}, scope)});"     # 左值取址
    return f"{dt}return {expr(e, {'value'}, scope)};"


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
    if isinstance(e, NamedCast):
        if getattr(e, "dc_kind", None):
            # 0.0.33 dynamic_cast：上行/同类直达；下行运行期 ti 检查
            cf = flat(e.ty.pointee.name)
            src = expr(e.expr, {"value"}, scope)
            if e.dc_kind in ("same", "up"):
                return f"({type_c(e.ty)})({src})"
            src_cls = e.expr.ty.pointee.name
            root = _root_of(src_cls, CLASSES_META[0])
            rf = flat(root)
            meta = CLASSES_META[0]
            acc = [e.ty.pointee.name]
            for k in meta:
                if k != e.ty.pointee.name and \
                        _lower_is_ancestor(e.ty.pointee.name, k, meta):
                    acc.append(k)           # target ∪ descendants
            DC_SEQ[0] += 1
            hname = f"__dc_{DC_SEQ[0]}"
            DC_HELPERS[0] = (DC_HELPERS[0] or []) + \
                [(hname, [flat(t) + "_ti" for t in acc])]
            dyn = f"((struct {rf} *){src})->__vtbl->__ti"
            return f"((struct {cf} *){hname}({src}, {dyn}))"
        # 0.0.31 命名 cast → C cast 直发（sema 已定边界）
        return f"({type_c(e.ty)})({expr(e.expr, {'value'}, scope)})"
    if isinstance(e, New):
        raise AssertionError("lower: 裸 New 未经物化改写（sema/_mat 通道缺失）")
    if isinstance(e, FreeCall) and getattr(e, "ti_kind", None):
        # 0.0.32 RTTI·typeid（sig=None——先于 call() 的 meth 解引用）
        if e.ti_kind == "static":
            return flat(e.ti_class) + "_ti"
        recv = recv_expr(e.args[0], scope)
        rf = flat(_root_of(e.ti_class, CLASSES_META[0]))
        return f"((struct {rf} *){recv})->__vtbl->__ti"
    if isinstance(e, (MethodCall, FreeCall)):
        return call(e, ctx, scope)
    if isinstance(e, Assign):
        c = getattr(e, "call", None)
        if c is not None:
            return expr(c, ctx, scope)          # 用户 operator=（0.0.29）
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
            if getattr(e, "up_base", None) is not None:
                recv = expr(e.e, {"value"}, scope)
                return f"&({recv}.__base{e.up_base})"
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
    """this 作接收者：跨类（方法声明在祖先类）→ 基类指针 cast。
    0.0.43 多继承：owner=位 i>0 基类 → &this_->__base{i} 子对象取址。"""
    if owner is None or scope.get("__cls__") in (None, owner):
        return "this_"
    c = "const " if scope.get("__const__") else ""
    info = (CLASSES_META[0] or {}).get(scope.get("__cls__"))
    if info is not None:
        bkeys = getattr(info, "bases", None) \
            or ([info.base] if info.base else [])
        for i, b in enumerate(bkeys):
            if b == owner and i > 0:
                return f"({c}struct {flat(owner)} *)&this_->__base{i}"
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
    if getattr(meth, "is_virtual", False) and hasattr(e, "obj"):
        # 0.0.25 虚分派：recv->__vtbl->slot(recv, args…)
        recv_owner = scope.get("__recv_owner__")
        scope["__recv_owner__"] = getattr(e, "sig_owner", None)
        recv = recv_expr(e.obj, scope)
        parts = [recv]
        for a in e.args:
            bind = getattr(a, "bind", "value")
            if bind == "addr":
                parts.append(f"&({expr(a, {'value'}, scope)})")
            elif bind == "ref":
                parts.append(expr(a, {"ref"}, scope))
            else:
                parts.append(expr(a, {"value"}, scope))
        scope["__recv_owner__"] = recv_owner
        # 0.0.26 类型套用拆链：(*(struct Root_v **)recv)->slot(…)
        # （trirv_cc 链式箭头 fn-ptr codegen 不工作——p26=42 探针实证）
        root = _root_of(getattr(e, "sig_owner", None), CLASSES_META[0])
        if not root:
            root = _root_of(scope.get("__cls__"), CLASSES_META[0])
        rootf = flat(root) if root else "Object"
        s = f"(*(struct {rootf}_v **){recv})->{meth.name}({', '.join(parts)})"
        if "ref" in ctx or e.ty.kind != "ref":
            return s
        return f"*({s})"
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
    rb = getattr(e, "recv_base", None)
    if rb is not None:
        # 0.0.43 多继承：接收者=派生对象，方法声明在位 i>0 基类
        # → &recv.__base{i}（子对象取址）
        parts = [f"&({expr(e.obj, {'value'}, scope)}.__base{rb})"]
    else:
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
def lower_main(main, main_dtors=None):
    scope = {"__cls__": None, "__const__": False, "__method__": None,
             "__ret_void__": False, "__terminate__": True,
             "__fn_dtors__": main_dtors or []}
    body = " ".join(stmt(s, scope, False) for s in main)
    body = body + "".join(f"{flat(ck)}_dtor(&{v}); "
                          for (v, ck) in reversed(main_dtors or []))
    return f"int main(void) {{ {body} }}"
