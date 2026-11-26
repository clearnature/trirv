#!/usr/bin/env python3
"""cpp_sema —— Layer 4 语义：符号表/类型检查/引用绑定规则/const 规则。

验收标准（用户拍板）：Type{kind: VALUE|REF, is_const} 一次到位——
mangling 的 R/RK/NK 喂点全部由本层 Type 驱动，正则时代的布尔散装退役。

错误码（要么对、要么报错——README 章程）：
  N8  REF 形参绑定临时量（字面量/右值/非左值表达式）——真实语义要求
      调用者作用域物化具名临时量并延长生命周期，0.0.9 前明确拒绝
  K1  const 方法内写成员 / 引用返回绑自身成员（C 侧 cast 剥 const
      编译器不可见，必须在 sema 拦）
  K2  const 方法经 this 调非 const 方法
  E0  未声明/未解析标识符、重载无匹配、类不存在
  I1  声明初始化器含函数调用（trirv_cc INITLIST 限制——拆两步）
"""
from cpp_parser import (New, DeleteStmt, Num, StrLit, Id, This, Member, MethodCall,
                        FreeCall, Bin, Un, Assign, VarDecl, CtorStmt,
                        ExprStmt, Return, If, While, For, BlockStmt)
from cpp_types import (tint, tchar, tbool, tvoid, tcls, tref, tptr, type_name,
                        TRef, TClass, TPtr)
from cpp_mangle import mangle_method, mangle_function


class SemaError(Exception):
    """GADT MathError 精神：证据携带（code = N8/K1/K2/K3/E0/R1/I1）。"""

    def __init__(self, code, msg):
        self.code = code
        super().__init__(f"{code}: {msg}")


class ClassInfo:
    def __init__(self, name, base):
        self.name, self.base = name, base
        self.members = {}      # name → Type
        self.methods = []      # MethodDecl
        self.ctors = []        # CtorDecl


class Scope:
    def __init__(self, parent=None):
        self.vars = {}
        self.parent = parent

    def declare(self, name, ty):
        self.vars[name] = ty

    def lookup(self, name):
        s = self
        while s:
            if name in s.vars:
                return s.vars[name]
            s = s.parent
        return None


def tval(base, is_const=False):
    makers = {"int": tint, "char": tchar, "bool": tbool}
    if base in makers:
        return makers[base](is_const)
    return tvoid() if base == "void" else tcls(base, is_const)


def tobj(cname, is_const=False):
    return tcls(cname, is_const)


class Sema:
    def __init__(self):
        self.classes = {}
        self.cur_method = None
        self.cur_cls = None
        self.cur_ns = None

    # ============ 类表 ============
    def _cur_key(self):
        """当前类规范键（ClassDecl.key，回退 name 兼容）。"""
        return getattr(self.cur_cls, "key", None) or self.cur_cls.name

    def resolve_class_name(self, name, ns):
        """短名/限定名 → 规范键；找不到返回 None。"""
        if name in self.classes:
            return name
        if ns and f"{ns}::{name}" in self.classes:
            return f"{ns}::{name}"
        return None

    def canon_ty(self, ty, ns):
        """TClass 短名 → 限定键（仅当该 ns 内确有此名——外部裸名保持）。"""
        if isinstance(ty, TRef):
            self.canon_ty(ty.pointee, ns)
        elif isinstance(ty, TClass) and "::" not in ty.name:
            r = self.resolve_class_name(ty.name, ns)
            if r:
                ty.name = r

    def canon_stmt(self, s, ns):
        """body 内 VarDecl 类型 / CtorStmt 类名规范化。"""
        if isinstance(s, VarDecl):
            self.canon_ty(s.ty, ns)
            if s.init is not None:
                self.canon_expr(s.init, ns)
        elif isinstance(s, CtorStmt):
            s.cls = self.resolve_class_name(s.cls, ns) or s.cls
            for a in s.args:
                self.canon_expr(a, ns)
        elif isinstance(s, DeleteStmt):
            self.canon_expr(s.expr, ns)
        elif isinstance(s, ExprStmt):
            self.canon_expr(s.expr, ns)
        elif isinstance(s, Return):
            if s.expr is not None:
                self.canon_expr(s.expr, ns)
        elif isinstance(s, If):
            self.canon_expr(s.cond, ns)
            self.canon_stmt(s.then, ns)
            if s.els is not None:
                self.canon_stmt(s.els, ns)
        elif isinstance(s, While):
            self.canon_expr(s.cond, ns)
            self.canon_stmt(s.body, ns)
        elif isinstance(s, For):
            if s.init is not None:
                self.canon_stmt(s.init, ns)
            if s.cond is not None:
                self.canon_expr(s.cond, ns)
            if s.step is not None:
                self.canon_expr(s.step, ns)
            self.canon_stmt(s.body, ns)
        elif isinstance(s, BlockStmt):
            for x in s.stmts:
                self.canon_stmt(x, ns)

    def canon_expr(self, e, ns):
        if isinstance(e, New):
            e.cls = self.resolve_class_name(e.cls, ns) or e.cls
            for a in e.args:
                self.canon_expr(a, ns)
        elif isinstance(e, (MethodCall, FreeCall)):
            if isinstance(e, MethodCall):
                self.canon_expr(e.obj, ns)
            for a in e.args:
                self.canon_expr(a, ns)
        elif isinstance(e, Bin):
            self.canon_expr(e.l, ns)
            self.canon_expr(e.r, ns)
        elif isinstance(e, Un):
            self.canon_expr(e.e, ns)
        elif isinstance(e, Assign):
            self.canon_expr(e.lhs, ns)
            self.canon_expr(e.rhs, ns)
        elif isinstance(e, Member):
            self.canon_expr(e.obj, ns)
        # Num/StrLit/Id/This：叶子

    def build_classes(self, decls):
        # Phase A：键注册（ns 类 → ns::name 全局唯一键）
        for cd in decls:
            key = f"{cd.ns}::{cd.name}" if cd.ns else cd.name
            if key in self.classes:
                raise SemaError("E0", f"类 {key} 重复定义")
            cd.key = key
            self.classes[key] = ClassInfo(cd.name, None)
        # Phase B：类型/主体规范化（ns 内短名 → 键；基类解析）
        for cd in decls:
            ns = cd.ns
            for meth in cd.methods:
                self.canon_ty(meth.ret, ns)
                for (pty, _pn) in meth.params:
                    self.canon_ty(pty, ns)
                for ss in meth.body:
                    self.canon_stmt(ss, ns)
            for ctor in cd.ctors:
                for (pty, _pn) in ctor.params:
                    self.canon_ty(pty, ns)
                for ss in ctor.body:
                    self.canon_stmt(ss, ns)
            if cd.base:
                bkey = self.resolve_class_name(cd.base, ns)
                if bkey is None:
                    raise SemaError("E0", f"基类 {cd.base} 未定义")
                cd.base_key = bkey
        # Phase C：完整构建 + mangling 守卫
        for cd in decls:
            info = self.classes[cd.key]
            if cd.base:
                info.base = getattr(cd, "base_key", None)
            for (mn, mty) in cd.members:
                info.members[mn] = mty
            info.methods.extend(cd.methods)
            info.ctors.extend(cd.ctors)
            seen = {}
            for meth in cd.methods:
                self.check_defaults(meth)
                mn2 = mangle_method(cd.key, meth)
                if mn2 in seen:
                    raise SemaError("E0",
                                    f"{cd.key}.{meth.name} 重定义：mangled "
                                    f"{mn2} 撞 {seen[mn2]}（默认实参不进签名）")
                seen[mn2] = meth.name
                meth.mname = mn2
            for ctor in cd.ctors:
                self.check_defaults(ctor)

    def find_member(self, cname, name):
        r = self.find_member_owner(cname, name)
        return r[0] if r else None

    def find_member_owner(self, cname, name):
        """(Type, 声明该成员的类名)——cast 一致性的归属喂点。"""
        c = cname
        while c:
            info = self.classes[c]
            if name in info.members:
                return (info.members[name], c)
            c = info.base
        return None

    def find_method(self, cname, name, nargs):
        r = self.find_method_owner(cname, name, nargs)
        return r[0] if r else None

    def find_method_owner(self, cname, name, nargs):
        """(MethodDecl, 声明类名)——跨类调用的 cast 喂点。
        0.0.11：缺省实参可补全（nargs < 参数数且缺位全有默认）；歧义报错。"""
        hits = []
        c = cname
        while c:
            for m in self.classes[c].methods:
                if m.name != name:
                    continue
                n = len(m.params)
                if n == nargs:
                    hits.append((m, c))
                elif n > nargs and all(
                        m.defaults[i] is not None for i in range(nargs, n)):
                    hits.append((m, c))
            c = self.classes[c].base
        if len(hits) > 1:
            cands = ", ".join(f"{k[1]}::{name}/{len(k[0].params)}参"
                              for k in hits)
            raise SemaError("E0", f"{name}({nargs} 参) 重载歧义：{cands}")
        return hits[0] if hits else None

    def build_funcs(self, funcs):
        """自由函数符号表：ns 函数以 ns::name 为键（限定调用 + ns 内裸调）。"""
        self.funcs = {}            # call-name → [MethodDecl…]
        self.funcs_list = funcs
        for f in funcs:
            clink = getattr(f, "c_link", False)
            if clink:
                # C 链接面：引用/对象/默认形参拒绝（ABI=指针，子集先守基本型）
                if f.ret.kind not in ("int", "char", "bool", "void"):
                    raise SemaError("E0",
                                    f'extern "C" {f.name} 返回类型限基本型')
                for (pty, _pn) in f.params:
                    if pty.kind not in ("int", "char", "bool"):
                        raise SemaError("E0",
                                        f'extern "C" {f.name} 形参限基本型'
                                        f"（引用/对象=指针 ABI，后续版本）")
            else:
                for (pty, _pn) in f.params:
                    self.canon_ty(pty, f.ns)
                self.canon_ty(f.ret, f.ns)
                for ss in f.body or []:
                    self.canon_stmt(ss, f.ns)
                self.check_defaults(f)
            fm = mangle_function(f)
            if any(x.mname == fm for lst in self.funcs.values() for x in lst):
                raise SemaError("E0", f"自由函数重定义：mangled {fm}")
            f.mname = fm
            key = f"{f.ns}::{f.name}" if f.ns else f.name
            f.call_key = key
            lst = self.funcs.setdefault(key, [])
            if clink and lst:
                raise SemaError("E0",
                                f'extern "C" {f.name} 重定义（C 链接无重载）')
            lst.append(f)

    def visible_method_names(self, cname):
        """本类链全部方法名（隐式 this 改写的判定集）。"""
        out, c = set(), cname
        while c:
            out.update(m.name for m in self.classes[c].methods)
            c = self.classes[c].base
        return out

    def find_free_func(self, name, nargs):
        """自由函数解析：限定名直查；裸名 → cur_ns::name → 全局。"""
        tried = [name]
        if self.cur_ns and "::" not in name:
            tried.append(f"{self.cur_ns}::{name}")
        cands = []
        for key in tried:
            for m in self.funcs.get(key, []):
                n = len(m.params)
                if n == nargs or (n > nargs and all(
                        m.defaults[i] is not None
                        for i in range(nargs, n))):
                    cands.append(m)
            if cands:
                break                     # 内层 ns 优先（C++ 查找序）
        if len(cands) > 1:
            raise SemaError("E0",
                            f"自由函数 {name}({nargs} 参) 重载歧义")
        return cands[0] if cands else None

    # ============ 主体 ============
    def check(self, decls, funcs, main):
        self.build_classes(decls)
        self.build_funcs(funcs)
        for cd in decls:
            self.cur_cls, self.cur_method = cd, None
            self.cur_ns = cd.ns
            visible = self.visible_method_names(cd.key)
            for ctor in cd.ctors:
                rewrite_implicit_this(ctor.body, visible)
            for meth in cd.methods:
                rewrite_implicit_this(meth.body, visible)
            for ctor in cd.ctors:
                sc = Scope()
                for (pty, pn) in ctor.params:
                    sc.declare(pn, pty)
                for (tgt, args) in ctor.inits:
                    btgt = self.resolve_class_name(tgt, cd.ns) or tgt
                    if btgt == getattr(cd, "base_key", None):
                        if cd.base is None:
                            raise SemaError("E0", f"{tgt} 不是 {cd.name} 基类")
                        tgt = btgt           # 规范化为限定键（lower 用）
                    elif self.find_member(cd.key, tgt) is None:
                        raise SemaError(
                            f"E0: 构造初始化目标 {tgt} 不是 {cd.name} 成员")
                    for a in args:
                        self.check_stmts(ctor.body, sc, False)
            for meth in cd.methods:
                self.cur_method = meth
                sc = Scope()
                for (pty, pn) in meth.params:
                    if pty.kind == "ptr":
                        raise SemaError("E0",
                                        "指针形参=后续版本（引用形参同语义）")
                    sc.declare(pn, pty)
                self.check_stmts(meth.body, sc, meth.is_const)
        for f in self.funcs_list:
            self.cur_cls, self.cur_method = None, f
            self.cur_ns = None            # extern "C" 仅全局（子集）
            if f.body is None:
                continue                  # 原型声明：无体可查
            sc = Scope()
            for (pty, pn) in f.params:
                sc.declare(pn, pty)
            self.check_stmts(f.body, sc, False)
        self.cur_cls, self.cur_method = None, None
        self.cur_ns = None
        if main is not None:
            self.check_stmts(main, Scope(), False)
        # 0.0.10 生命周期扩展：const T& 绑临时量 → 调用者作用域具名物化
        self._tmpn = 0
        for cd in decls:
            for ctor in cd.ctors:
                self._mat_body(ctor.body)
            for meth in cd.methods:
                self._mat_body(meth.body)
        for f in self.funcs_list:
            if f.body is not None:
                self._mat_body(f.body)
        if main is not None:
            self._mat_body(main)

    def check_stmts(self, ss, sc, is_const):
        for s in ss:
            self.check_stmt(s, sc, is_const)

    def check_stmt(self, s, sc, is_const):
        if isinstance(s, VarDecl):
            if s.ty.kind == "ref":
                # 引用局部声明：const T& r = 42 物化延寿；T& r = 42 拒绝（N8）
                if s.init is None:
                    raise SemaError(
                        f"E0: 引用声明 {s.name} 需初始化器")
                self.check_expr(s.init, sc, is_const)
                if not s.ty.pointee.is_const \
                        and not getattr(s.init, "lval", False) \
                        and s.init.ty.kind != "ref":
                    raise SemaError(
                        f"N8: 非 const 引用 {s.name} 绑临时量（{_fmt(s.init)}）"
                        f"——C++ 法：保持拒绝")
                # 物化改写在 check 末尾统一跑
            elif s.init is not None:
                self.check_expr(s.init, sc, is_const)
                # 调用初始化不再报错——lower 两步发射
            sc.declare(s.name, s.ty)
        elif isinstance(s, CtorStmt):
            ckey = self.resolve_class_name(s.cls, self.cur_ns)
            if ckey is None:
                raise SemaError("E0", f"类 {s.cls} 未定义")
            s.cls = ckey
            hits = []
            for c in self.classes[s.cls].ctors:
                n = len(c.params)
                if n == len(s.args) or (n > len(s.args) and all(
                        c.defaults[i] is not None
                        for i in range(len(s.args), n))):
                    hits.append(c)
            if len(hits) > 1:
                raise SemaError("E0",
                                f"{s.cls} 构造歧义（{len(s.args)} 实参）")
            if not hits:
                raise SemaError(
                    f"E0: {s.cls} 无 {len(s.args)} 参构造")
            s.sig = hits[0]
            while len(s.args) < len(s.sig.params):      # 默认补全
                s.args.append(s.sig.defaults[len(s.args)])
            for a in s.args:
                self.check_expr(a, sc, is_const)
            sc.declare(s.var, tobj(s.cls))
        elif isinstance(s, DeleteStmt):
            et = self.check_expr(s.expr, sc, is_const)
            if et.kind != "ptr" or et.pointee.kind != "class":
                raise SemaError("E0",
                                "delete 操作数须为对象指针（Box*）——子集内"
                                "delete 仅回收对象存储（无 dtor 语法）")
            s.ty = et
        elif isinstance(s, ExprStmt):
            self.check_expr(s.expr, sc, is_const)
        elif isinstance(s, Return):
            if s.expr is not None:
                self.check_expr(s.expr, sc, is_const)
            self.check_return(s.expr)
        elif isinstance(s, (If, While)):
            self.check_expr(s.cond, sc, is_const)
            self.check_stmt(s.then if isinstance(s, If) else s.body, sc,
                            is_const)
            if isinstance(s, If) and s.els is not None:
                self.check_stmt(s.els, sc, is_const)
        elif isinstance(s, For):
            self.check_stmt(s.init, sc, is_const)
            if s.cond is not None:
                self.check_expr(s.cond, sc, is_const)
            if s.step is not None:
                self.check_expr(s.step, sc, is_const)
            self.check_stmt(s.body, sc, is_const)
        elif isinstance(s, BlockStmt):
            self.check_stmts(s.stmts, Scope(sc), is_const)
        else:
            raise SemaError("E0", f"未知语句 {type(s).__name__}")

    def check_return(self, expr):
        meth = self.cur_method
        if meth is None or expr is None:
            return                              # main / 空返回
        if meth.ret.kind == "ref":
            if expr.ty.kind == "ref":
                return                          # 引用转发 ✓
            if not getattr(expr, "lval", False):
                raise SemaError(
                    "R1: 引用返回绑非左值——临时量物化=0.0.9 生命周期扩展")
            if meth.is_const:
                raise SemaError(
                    "K1: const 方法引用返回绑自身成员——写通道穿透 const")
        # VALUE 返回 ← REF 表达式：自动解引用（C++ 语义），lower 落 *(…)

    # ============ 表达式 ============
    def check_expr(self, e, sc, is_const):
        if isinstance(e, Num):
            e.ty, e.lval = tval("int"), False
        elif isinstance(e, StrLit):
            e.ty, e.lval = tval("int"), False   # 子集内字符串不逐字节类型化
        elif isinstance(e, This):
            if self.cur_cls is None:
                raise SemaError("E0", "this 在类外")
            e.ty, e.lval = tobj(self._cur_key(), is_const=is_const), True
        elif isinstance(e, Id):
            vt = sc_find(sc, e.name)
            if vt is not None:
                e.ref_kind, e.ty, e.lval = "var", vt, True
            elif self.cur_cls is not None:
                hit = self.find_member_owner(self._cur_key(), e.name)
                if hit is None:
                    raise SemaError("E0", f"未声明标识符 {e.name!r}")
                mt, owner = hit
                e.ref_kind, e.ty, e.lval, e.owner = "member", mt, True, owner
            else:
                raise SemaError("E0", f"未声明标识符 {e.name!r}")
        elif isinstance(e, Member):
            ot = self.check_expr(e.obj, sc, is_const)
            if ot.kind == "ref":
                ot = ot.pointee                  # 引用穿透（C++ 语义）
            if ot.kind == "ptr":
                if not e.arrow:
                    raise SemaError("E0", "指针成员访问用 ->（C++ 法）")
                ot = ot.pointee                  # 指针穿透
            if ot.kind != "class":
                raise SemaError("E0", f"非对象上取成员 {e.name!r}")
            hit = self.find_member_owner(ot.name, e.name)
            if hit is None:
                raise SemaError("E0", f"{ot.name} 无成员 {e.name!r}")
            mt, e.owner = hit
            e.ty, e.lval = mt, True
        elif isinstance(e, MethodCall):
            ot = self.check_expr(e.obj, sc, is_const)
            if ot.kind == "ref":
                ot = ot.pointee                  # 引用穿透（C++ 语义）
            if ot.kind == "ptr":
                ot = ot.pointee                  # 指针穿透（-> 已由 parser 分支）
            if ot.kind != "class":
                raise SemaError("E0", f"非对象上调用 {e.name!r}()")
            hit = self.find_method_owner(ot.name, e.name, len(e.args))
            if hit is None:
                raise SemaError("E0", f"{ot.name}.{e.name} 无 {len(e.args)} 参重载")
            meth, e.sig_owner = hit
            if is_const and not meth.is_const and is_this_receiver(e.obj):
                raise SemaError("K2", f"const 方法经 this 调非 const 方法 {e.name}()")
            while len(e.args) < len(meth.params):        # 0.0.11 默认补全
                e.args.append(meth.defaults[len(e.args)])
            self.check_args(e.args, meth, sc, is_const)
            e.sig, e.ty, e.lval = meth, meth.ret, False
        elif isinstance(e, New):
            ckey = self.resolve_class_name(e.cls, self.cur_ns)
            if ckey is None:
                raise SemaError("E0", f"类 {e.cls} 未定义")
            e.cls = ckey
            hits = []
            for c in self.classes[ckey].ctors:
                n = len(c.params)
                if n == len(e.args) or (n > len(e.args) and all(
                        c.defaults[i] is not None for i in range(len(e.args), n))):
                    hits.append(c)
            if len(hits) > 1:
                raise SemaError("E0", f"new {ckey} 构造歧义")
            if not hits and e.args:
                raise SemaError("E0", f"{ckey} 无 {len(e.args)} 参构造")
            if hits:
                e.sig = hits[0]
                while len(e.args) < len(e.sig.params):
                    e.args.append(e.sig.defaults[len(e.args)])
                for a in e.args:
                    self.check_expr(a, sc, is_const)
                    a.bind = "value"
            e.ty, e.lval = tptr(tobj(ckey)), False
        elif isinstance(e, FreeCall):
            f = self.find_free_func(e.name, len(e.args))
            if f is None:
                raise SemaError("E0",
                                f"自由函数 {e.name} 无 {len(e.args)} 参重载")
            while len(e.args) < len(f.params):        # 默认补全
                e.args.append(f.defaults[len(e.args)])
            self.check_args(e.args, f, sc, is_const)
            e.sig, e.ty, e.lval = f, f.ret, False
        elif isinstance(e, Assign):
            lt = self.check_expr(e.lhs, sc, is_const)
            rt = self.check_expr(e.rhs, sc, is_const)
            self.const_guard(e.lhs, is_const)
            if not getattr(e.lhs, "lval", False) and lt.kind != "ref":
                raise SemaError("E0", "赋值目标非左值且非引用")
            e.ty, e.lval = lt, False
        elif isinstance(e, Bin):
            self.check_expr(e.l, sc, is_const)
            self.check_expr(e.r, sc, is_const)
            for side in (e.l, e.r):
                if side.ty.kind == "class":
                    raise SemaError("E0", f"对象 {type_name(side.ty)} 参与算术")
            e.ty, e.lval = tval("int"), False
        elif isinstance(e, Un):
            self.check_expr(e.e, sc, is_const)
            e.ty, e.lval = tval("int"), False
        else:
            raise SemaError("E0", f"未知表达式 {type(e).__name__}")
        return e.ty

    def check_args(self, args, meth, sc, is_const):
        """引用绑定规则（N8 落点）——注记 arg.bind 供 lower：
        ref  = REF 实参转发（不插 &）
        addr = VALUE 左值取址（插 &）
        deref= REF 实参传给值形参（自动解引用）
        value= 值实参原样"""
        for i, a in enumerate(args):
            at = self.check_expr(a, sc, is_const)
            if i >= len(meth.params):
                break
            (pty, _pn) = meth.params[i]
            if pty.kind == "ref":
                if at.kind == "ref":
                    a.bind = "ref"
                elif getattr(a, "lval", False):
                    a.bind = "addr"
                elif pty.pointee.is_const:
                    a.bind = "mat"       # 0.0.10：const T& 绑临时量→物化
                else:
                    raise SemaError(
                        f"N8: 非 const 引用形参绑定临时量（{_fmt(a)}）"
                        f"——C++ 法：非常量左值引用不延寿，保持拒绝")
            else:
                a.bind = "deref" if at.kind == "ref" else "value"

    def const_guard(self, lhs, is_const):
        if not is_const:
            return
        if isinstance(lhs, Id) and getattr(lhs, "ref_kind", None) == "member":
            raise SemaError("K1", f"const 方法内写成员 {lhs.name}")
        if isinstance(lhs, Member):
            raise SemaError("K1", f"const 方法内写成员 {lhs.name}")


def sc_find(sc, name):
    s = sc
    while s:
        if name in s.vars:
            return s.vars[name]
        s = s.parent
    return None


def is_this_receiver(e):
    return isinstance(e, This) or (isinstance(e, Id) and
                                   getattr(e, "ref_kind", None) == "member")


def has_call(e):
    if isinstance(e, (MethodCall, FreeCall)):
        return True
    if isinstance(e, Bin):
        return has_call(e.l) or has_call(e.r)
    if isinstance(e, Un):
        return has_call(e.e)
    if isinstance(e, Assign):
        return has_call(e.lhs) or has_call(e.rhs)
    return False


def _fmt(a):
    if isinstance(a, Num):
        return f"字面量 {a.v}"
    if isinstance(a, Bin):
        return "算术表达式"
    if isinstance(a, MethodCall):
        return f"{a.name}() 返回值"
    return type(a).__name__


def rewrite_implicit_this(body, method_names=None):
    """裸 name(args) 在方法内 = this 调用（C++ 隐式 this）——AST 预改写。
    0.0.12：名字不在本类链方法集 → 保留 FreeCall（自由函数）。"""
    for s in body:
        _rw(s, method_names)


def _rw(s, method_names=None):
    if isinstance(s, VarDecl):
        if s.init is not None:
            _rw_expr(s.init, method_names)
    elif isinstance(s, CtorStmt):
        for a in s.args:
            _rw_expr(a, method_names)
    elif isinstance(s, ExprStmt):
        _rw_expr(s.expr, method_names)
    elif isinstance(s, Return):
        if s.expr is not None:
            _rw_expr(s.expr, method_names)
    elif isinstance(s, Assign):
        if isinstance(s.lhs, FreeCall) and method_names and \
                s.lhs.name in method_names:
            s.lhs = MethodCall(This(), s.lhs.name, s.lhs.args)
        else:
            _rw_expr(s.lhs, method_names)
        _rw_expr(s.rhs, method_names)
    elif isinstance(s, If):
        _rw_expr(s.cond, method_names)
        _rw(s.then, method_names)
        if s.els is not None:
            _rw(s.els, method_names)
    elif isinstance(s, While):
        _rw_expr(s.cond, method_names)
        _rw(s.body, method_names)
    elif isinstance(s, For):
        if s.init is not None:
            _rw(s.init, method_names)
        if s.cond is not None:
            _rw_expr(s.cond, method_names)
        if s.step is not None:
            _rw_expr(s.step, method_names)
        _rw(s.body, method_names)
    elif isinstance(s, BlockStmt):
        for x in s.stmts:
            _rw(x, method_names)


def _rw_expr(e, method_names=None):
    """原地改写：FreeCall → MethodCall(This(), …)（仅类方法名）。
    子表达式递归；Num/StrLit/Id/This 叶子。"""
    if isinstance(e, Bin):
        _rw_expr(e.l, method_names)
        _rw_expr(e.r, method_names)
    elif isinstance(e, Un):
        _rw_expr(e.e, method_names)
    elif isinstance(e, Assign):
        if isinstance(e.lhs, FreeCall) and method_names and \
                e.lhs.name in method_names:
            e.lhs = MethodCall(This(), e.lhs.name, e.lhs.args)
        else:
            _rw_expr(e.lhs, method_names)
        _rw_expr(e.rhs, method_names)
    elif isinstance(e, Member):
        _rw_expr(e.obj, method_names)
    elif isinstance(e, MethodCall):
        _rw_expr(e.obj, method_names)
        for a in e.args:
            _rw_expr(a, method_names)
    elif isinstance(e, FreeCall):
        if method_names and e.name in method_names:
            e.__class__ = MethodCall          # 原地变身：隐式 this
            e.obj = This()
        # 否则保留 FreeCall——自由函数（sema 解析）

def _install_mat(cls):
    def _fresh(self):
        n = f"__tmp{self._tmpn}"
        self._tmpn += 1
        return n

    def _mat_body(self, body):
        for s in body:
            self._mat_stmt(s)

    def _mat_stmt(self, s):
        if isinstance(s, BlockStmt):
            for x in s.stmts:
                self._mat_stmt(x)
            return
        if isinstance(s, If):
            h = []
            self._mat_expr(s.cond, h)
            if h:
                s.hoists = h
            self._mat_stmt(s.then)
            if s.els is not None:
                self._mat_stmt(s.els)
            return
        if isinstance(s, While):
            h = []
            self._mat_expr(s.cond, h)
            if h:
                s.hoists = h
            self._mat_stmt(s.body)
            return
        if isinstance(s, For):
            self._mat_stmt(s.init)
            h = []
            if s.cond is not None:
                self._mat_expr(s.cond, h)
            if s.step is not None:
                self._mat_expr(s.step, h)
            if h:
                s.hoists = h   # 偏差记录：循环条件物化=单次求值（子集文档化）
            self._mat_stmt(s.body)
            return
        h = []
        if isinstance(s, VarDecl):
            if s.init is not None:
                self._mat_expr(s.init, h)
            # 引用局部 + 右值初始化 → 物化为临时量，init 换 Id(temp)
            if s.ty.kind == "ref" and s.init is not None \
                    and s.init.ty.kind != "ref" \
                    and not getattr(s.init, "lval", False):
                tn = self._fresh()
                h.append((tn, s.init))
                rid = Id(tn)
                rid.ty, rid.lval = tval("int"), True
                s.init = rid
        elif isinstance(s, DeleteStmt):
            pass                                # delete p 无物化需求
        elif isinstance(s, ExprStmt):
            self._mat_expr(s.expr, h)
        elif isinstance(s, Return):
            if s.expr is not None:
                self._mat_expr(s.expr, h)
        if h:
            s.hoists = h

    def _mat_expr(self, e, h):
        """后序：内层调用的物化先入 hoists（声明序=依赖序，C 合法）。"""
        if isinstance(e, New):
            for a in e.args:
                self._mat_expr(a, h)
            if getattr(e, "sig", None) is not None:
                for i, a in enumerate(e.args):
                    if getattr(a, "bind", None) == "mat":
                        tn = self._fresh()
                        h.append((tn, a))
                        rid = Id(tn)
                        rid.ty, rid.lval = tval("int"), True
                        e.args[i] = rid
                        e.args[i].bind = "value"   # ctor 值形参
        elif isinstance(e, (MethodCall, FreeCall)):
            if isinstance(e, MethodCall):
                self._mat_expr(e.obj, h)     # FreeCall 无接收者
            for a in e.args:
                self._mat_expr(a, h)
            meth = e.sig
            for i, a in enumerate(e.args):
                if getattr(a, "bind", None) == "mat":
                    tn = self._fresh()
                    h.append((tn, a))
                    rid = Id(tn)
                    rid.ty, rid.lval = tval("int"), True
                    e.args[i] = rid
                    e.args[i].bind = "addr"
        elif isinstance(e, Bin):
            self._mat_expr(e.l, h)
            self._mat_expr(e.r, h)
        elif isinstance(e, Un):
            self._mat_expr(e.e, h)
        elif isinstance(e, Assign):
            self._mat_expr(e.lhs, h)
            self._mat_expr(e.rhs, h)
        elif isinstance(e, Member):
            self._mat_expr(e.obj, h)
        # Num/StrLit/Id/This：叶子

    cls._fresh = _fresh
    cls._mat_body = _mat_body
    cls._mat_stmt = _mat_stmt
    cls._mat_expr = _mat_expr


_install_mat(Sema)




def is_const_expr(e):
    if isinstance(e, Num):
        return True
    if isinstance(e, Bin):
        return is_const_expr(e.l) and is_const_expr(e.r)
    if isinstance(e, Un):
        return is_const_expr(e.e)
    return False


def _install_defaults(cls):
    def check_defaults(self, d):
        """后缀规则 + 常量表达式限制 + 形参类别限制（0.0.11）。"""
        defaults = getattr(d, "defaults", None)
        params = d.params
        if defaults is None:
            defaults = [None] * len(params)
            d.defaults = defaults
        seen_default = False
        for i, df in enumerate(defaults):
            if df is None:
                if seen_default:
                    raise SemaError("E0",
                                    f"{d.name} 形参 {i} 无默认：后缀规则要求"
                                    f"一旦有默认，后续必须全有")
                continue
            seen_default = True
            if not is_const_expr(df):
                raise SemaError("E0",
                                f"{d.name} 形参 {i} 默认实参限常量表达式"
                                f"（字面量/算术）——名字引用需声明作用域查找，"
                                f"子集外")
            (pty, _pn) = params[i]
            if pty.kind == "ref" and not pty.pointee.is_const:
                raise SemaError("E0",
                                f"{d.name} 形参 {i}：非 const 引用形参"
                                f"默认实参=左值需求，子集外拒绝")
            if pty.kind == "class":
                raise SemaError("E0",
                                f"{d.name} 形参 {i}：对象形参默认实参"
                                f"（拷贝构造语义）= 后续版本")

    cls.check_defaults = check_defaults


_install_defaults(Sema)
