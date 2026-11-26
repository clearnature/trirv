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
from cpp_parser import (Num, StrLit, Id, This, Member, MethodCall,
                        FreeCall, Bin, Un, Assign, VarDecl, CtorStmt,
                        ExprStmt, Return, If, While, For, BlockStmt)
from cpp_types import tint, tchar, tbool, tvoid, tcls, tref, type_name


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

    # ============ 类表 ============
    def build_classes(self, decls):
        for cd in decls:
            if cd.name in self.classes:
                raise SemaError("E0", f"类 {cd.name} 重复定义")
            self.classes[cd.name] = ClassInfo(cd.name, cd.base)
        for cd in decls:
            info = self.classes[cd.name]
            if cd.base and cd.base not in self.classes:
                raise SemaError("E0", f"基类 {cd.base} 未定义")
            for (mn, mty) in cd.members:
                info.members[mn] = mty
            info.methods.extend(cd.methods)
            info.ctors.extend(cd.ctors)

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
        """(MethodDecl, 声明类名)——跨类调用的 cast 喂点。"""
        c = cname
        while c:
            for m in self.classes[c].methods:
                if m.name == name and len(m.params) == nargs:
                    return (m, c)
            c = self.classes[c].base
        return None

    # ============ 主体 ============
    def check(self, decls, main):
        self.build_classes(decls)
        for cd in decls:
            self.cur_cls, self.cur_method = cd, None
            for ctor in cd.ctors:
                rewrite_implicit_this(ctor.body)
            for meth in cd.methods:
                rewrite_implicit_this(meth.body)
            for ctor in cd.ctors:
                sc = Scope()
                for (pty, pn) in ctor.params:
                    sc.declare(pn, pty)
                for (tgt, args) in ctor.inits:
                    if tgt == cd.base:
                        if cd.base is None:
                            raise SemaError("E0", f"{tgt} 不是 {cd.name} 基类")
                    elif self.find_member(cd.name, tgt) is None:
                        raise SemaError(
                            f"E0: 构造初始化目标 {tgt} 不是 {cd.name} 成员")
                    for a in args:
                        self.check_stmts(ctor.body, sc, False)
            for meth in cd.methods:
                self.cur_method = meth
                sc = Scope()
                for (pty, pn) in meth.params:
                    sc.declare(pn, pty)
                self.check_stmts(meth.body, sc, meth.is_const)
        self.cur_cls, self.cur_method = None, None
        if main is not None:
            self.check_stmts(main, Scope(), False)
        # 0.0.10 生命周期扩展：const T& 绑临时量 → 调用者作用域具名物化
        self._tmpn = 0
        for cd in decls:
            for ctor in cd.ctors:
                self._mat_body(ctor.body)
            for meth in cd.methods:
                self._mat_body(meth.body)
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
            if s.cls not in self.classes:
                raise SemaError("E0", f"类 {s.cls} 未定义")
            hits = [c for c in self.classes[s.cls].ctors
                    if len(c.params) == len(s.args)]
            if not hits:
                raise SemaError(
                    f"E0: {s.cls} 无 {len(s.args)} 参构造")
            s.sig = hits[0]
            for a in s.args:
                self.check_expr(a, sc, is_const)
            sc.declare(s.var, tobj(s.cls))
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
            e.ty, e.lval = tobj(self.cur_cls.name, is_const=is_const), True
        elif isinstance(e, Id):
            vt = sc_find(sc, e.name)
            if vt is not None:
                e.ref_kind, e.ty, e.lval = "var", vt, True
            elif self.cur_cls is not None:
                hit = self.find_member_owner(self.cur_cls.name, e.name)
                if hit is None:
                    raise SemaError("E0", f"未声明标识符 {e.name!r}")
                mt, owner = hit
                e.ref_kind, e.ty, e.lval, e.owner = "member", mt, True, owner
            else:
                raise SemaError("E0", f"未声明标识符 {e.name!r}")
        elif isinstance(e, Member):
            ot = self.check_expr(e.obj, sc, is_const)
            if ot.kind != "class":
                raise SemaError("E0", f"非对象上取成员 {e.name!r}")
            hit = self.find_member_owner(ot.name, e.name)
            if hit is None:
                raise SemaError("E0", f"{ot.name} 无成员 {e.name!r}")
            mt, e.owner = hit
            e.ty, e.lval = mt, True
        elif isinstance(e, MethodCall):
            ot = self.check_expr(e.obj, sc, is_const)
            if ot.kind != "class":
                raise SemaError("E0", f"非对象上调用 {e.name!r}()")
            hit = self.find_method_owner(ot.name, e.name, len(e.args))
            if hit is None:
                raise SemaError("E0", f"{ot.name}.{e.name} 无 {len(e.args)} 参重载")
            meth, e.sig_owner = hit
            if is_const and not meth.is_const and is_this_receiver(e.obj):
                raise SemaError("K2", f"const 方法经 this 调非 const 方法 {e.name}()")
            self.check_args(e.args, meth, sc, is_const)
            e.sig, e.ty, e.lval = meth, meth.ret, False
        elif isinstance(e, FreeCall):
            raise SemaError("E0", f"自由函数 {e.name}() 暂不支持（透传=后续版本）")
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


def rewrite_implicit_this(body):
    """裸 name(args) 在方法内 = this 调用（C++ 隐式 this）——AST 预改写。"""
    for s in body:
        _rw(s)


def _rw(s):
    if isinstance(s, VarDecl):
        if s.init is not None:
            _rw_expr(s.init)
    elif isinstance(s, CtorStmt):
        for a in s.args:
            _rw_expr(a)
    elif isinstance(s, ExprStmt):
        _rw_expr(s.expr)
    elif isinstance(s, Return):
        if s.expr is not None:
            _rw_expr(s.expr)
    elif isinstance(s, Assign):
        _rw_expr(s.lhs)
        _rw_expr(s.rhs)
    elif isinstance(s, If):
        _rw_expr(s.cond)
        _rw(s.then)
        if s.els is not None:
            _rw(s.els)
    elif isinstance(s, While):
        _rw_expr(s.cond)
        _rw(s.body)
    elif isinstance(s, For):
        if s.init is not None:
            _rw(s.init)
        if s.cond is not None:
            _rw_expr(s.cond)
        if s.step is not None:
            _rw_expr(s.step)
        _rw(s.body)
    elif isinstance(s, BlockStmt):
        for x in s.stmts:
            _rw(x)


def _rw_expr(e):
    """原地改写：FreeCall → MethodCall(This(), …)。子表达式递归。"""
    if isinstance(e, Bin):
        _rw_expr(e.l)
        _rw_expr(e.r)
    elif isinstance(e, Un):
        _rw_expr(e.e)
    elif isinstance(e, Assign):
        # 左值是 FreeCall（隐式 this 引用返回）也改写
        if isinstance(e.lhs, FreeCall):
            e.lhs = MethodCall(This(), e.lhs.name, e.lhs.args)
        else:
            _rw_expr(e.lhs)
        _rw_expr(e.rhs)
    elif isinstance(e, Member):
        _rw_expr(e.obj)
    elif isinstance(e, MethodCall):
        _rw_expr(e.obj)
        for a in e.args:
            _rw_expr(a)
    elif isinstance(e, FreeCall):
        e.__class__ = MethodCall          # 原地变身：隐式 this
        e.obj = This()
    # Num/StrLit/Id/This：叶子


# ============ 0.0.10 物化改写（Sema 方法，文件尾追加便于审阅） ============
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
        elif isinstance(s, ExprStmt):
            self._mat_expr(s.expr, h)
        elif isinstance(s, Return):
            if s.expr is not None:
                self._mat_expr(s.expr, h)
        if h:
            s.hoists = h

    def _mat_expr(self, e, h):
        """后序：内层调用的物化先入 hoists（声明序=依赖序，C 合法）。"""
        if isinstance(e, MethodCall):
            self._mat_expr(e.obj, h)
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
