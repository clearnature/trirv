#!/usr/bin/env python3
"""cpp_parser —— Layer 2 语法：递归下降 → AST（对齐 cpp_parser.c 规划）。

子集边界（章程：超出子集必须报错，静默错编比不支持更致命）：
  支持：class/struct、public:、成员 int 声明、ctor(: init-list)、方法
        [const]、参数 [const] T [&]、语句（vardecl/ctor声明/赋值/调用/
        return/if/else/while/for）、表达式（算术/比较/逻辑/成员/方法调用/
        this）、引用赋值（f() = v）。
  拒绝：自由函数、new/delete、模板、命名空间、虚函数、浮点字面量、
        一元 &/*（地址/解引用——引用语义由降级器插，用户手写易与 TAND 撞）。
"""
from cpp_lexer import lex
from cpp_types import (CType, TInt, TChar, TBool, TVoid, TClass, TRef, TPtr, tptr,
                       tint, tchar, tbool, tvoid, tcls, tref)


class ParseError(Exception):
    pass


# ---- AST 节点（轻量：__slotsys 无需，dict-free 明确字段） ----
Type = CType            # 兼容旧引用名（ADT 基类）


class ClassDecl:
    def __init__(self, name, base):
        self.name, self.base = name, base
        self.bases = []            # 0.0.43 多基类全列表（base=位 0 别名）
        self.ns = None             # 所属命名空间（0.0.13）
        self.nested_in = None      # 外层类键（0.0.22 嵌套类）
        self.member_access = {}    # name → public/private/protected（0.0.28）
        self.static_access = {}    # static 成员访问级（0.0.28）
        self.members = []          # [(name, Type)]
        self.ctors = []            # [CtorDecl]
        self.dtors = []            # [DtorDecl]（0.0.19，至多一个）
        self.statics = []          # [(name, Type)]（0.0.20 static 数据成员）
        self.methods = []          # [MethodDecl]


class CtorDecl:
    def __init__(self, params, inits, body):
        self.params, self.inits, self.body = params, inits, body


class DtorDecl:
    """~X() { body }——子集无参析构（0.0.19）。"""

    def __init__(self, body):
        self.body = body


class MethodDecl:
    def __init__(self, name, ret, params, is_const, body):
        self.name, self.ret = name, ret
        self.params, self.is_const, self.body = params, is_const, body


class Num:
    def __init__(self, v):
        self.v = v


class StrLit:
    def __init__(self, v):
        self.v = v


class Id:
    def __init__(self, name):
        self.name = name


class This:
    pass


class Member:
    def __init__(self, obj, name, arrow):
        self.obj, self.name, self.arrow = obj, name, arrow


class MethodCall:
    def __init__(self, obj, name, args):
        self.obj, self.name, self.args = obj, name, args


class FreeCall:
    def __init__(self, name, args):
        self.name, self.args = name, args
        self.targs = None                # 0.0.40 显式模板实参（list|None）


class New:
    """new X(args) → 0.0.15（cls=限定键，sema 填 sig）。"""

    def __init__(self, cls, args):
        self.cls, self.args = cls, args


class DeleteStmt:
    """delete p; → 0.0.15（子集无 dtor 语法：仅回收存储）。"""

    def __init__(self, expr):
        self.expr = expr


class CopyInit:
    """X var = expr; —— 拷贝初始化（0.0.29）。"""

    def __init__(self, cls, var, init):
        self.cls, self.var, self.init = cls, var, init


class NamedCast:
    """static_cast<T>(e) 等（0.0.31）——C cast 直发，sema 定边界。"""

    def __init__(self, kind, ty, expr):
        self.kind, self.ty, self.expr = kind, ty, expr


class Throw:
    """throw expr; / throw;（0.0.35；bare rethrow=0.0.36）。"""

    def __init__(self, expr):
        self.expr = expr


class Try:
    """try { } catch (T v) { } …（0.0.35：单 int catch）。"""

    def __init__(self, try_body, catches):
        self.try_body, self.catches = try_body, catches


class FuncTemplate:
    """template<typename T[, typename U…]> ret name(...) { body }
    （0.0.39 按需实例化；0.0.40 多类型参数 tvars 列表）。"""

    def __init__(self, tvars, decl):
        self.tvars, self.decl = tvars, decl


class ClassTemplate:
    """template<typename T> class X { ... }（0.0.41 按需实例化）。
    ext_methods/ext_ctors：外联定义（Box<T>::member 形态，tvars 恒等
    映射才归模板；具体实参外联=E0）。"""

    def __init__(self, tvars, decl):
        self.tvars, self.decl = tvars, decl
        self.tvar_dfls = [(v, None) for v in tvars]   # 0.0.42 默认模板参数
        self.specializations = {}        # 0.0.42 全特化 flat→ClassDecl
        self.ext_methods = []            # [(cls_flat_name, MethodDecl)]
        self.ext_ctors = []              # [(cls_flat_name, CtorDecl)]


class Bin:
    def __init__(self, op, l, r):
        self.op, self.l, self.r = op, l, r


class Un:
    def __init__(self, op, e):
        self.op, self.e = op, e


class Assign:
    def __init__(self, lhs, rhs):
        self.lhs, self.rhs = lhs, rhs


class VarDecl:
    def __init__(self, name, ty, init):
        self.name, self.ty, self.init = name, ty, init


class CtorStmt:
    """X var(args); —— 无参构造 args=[]（sema 期查类存在）。"""

    def __init__(self, cls, var, args):
        self.cls, self.var, self.args = cls, var, args


class ExprStmt:
    def __init__(self, expr):
        self.expr = expr


class BlockStmt:
    def __init__(self, stmts):
        self.stmts = stmts


class Return:
    def __init__(self, expr):
        self.expr = expr


class If:
    def __init__(self, cond, then, els):
        self.cond, self.then, self.els = cond, then, els


class While:
    def __init__(self, cond, body):
        self.cond, self.body = cond, body


class For:
    def __init__(self, init, cond, step, body):
        self.init, self.cond, self.step, self.body = init, cond, step, body


BASIC = {"int": "int", "char": "char", "void": "void", "bool": "bool"}

EH_FLAG = [False]           # TU 含 throw/try（0.0.35 序言门控，sema 置位 lower 读）
CUR_CATCH = [0]             # catch 嵌套深度（0.0.36 bare rethrow 域判定）


class Parser:
    def __init__(self, toks):
        self.toks = toks
        self.i = 0
        self.collected_inner = []      # 嵌套类冒泡（0.0.22）
        self.class_templs = {}         # 类模板名 → ClassTemplate（0.0.41 外联归属）

    # ---- 基元 ----
    def peek(self, k=0):
        return self.toks[min(self.i + k, len(self.toks) - 1)]

    def next(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def accept(self, kind, val=None):
        t = self.peek()
        if t[0] == kind and (val is None or t[1] == val):
            return self.next()
        return None

    def expect(self, kind, val=None):
        t = self.next()
        if t[0] != kind or (val is not None and t[1] != val):
            raise ParseError(f"L{t[2]}: 期望 {val or kind}，得 {t[1]!r}")
        return t

    # ---- 翻译单元 ----
    def parse_tu(self):
        """0.0.12：classes + 自由函数 + main 三类顶层声明。"""
        classes, funcs, main = [], [], None
        globals_, sdefs, templates = [], [], []
        ty_of = lambda kw: tchar() if kw == "char" else tint()
        while self.peek()[0] != "EOF":
            t = self.peek()
            if t[0] == "KW" and t[1] in ("int", "char") and \
                    self.peek(1)[0] == "ID" and self.peek(2)[1] == "::":
                self.next()
                cn = self.expect("ID")[1]
                self.expect("PUNCT", "::")
                nm = self.expect("ID")[1]
                init = self.expr() if self.accept("PUNCT", "=") else None
                self.expect("PUNCT", ";")
                sdefs.append((cn, nm, ty_of(t[1]), init))
            elif t[0] == "KW" and t[1] in ("int", "char") and \
                    self.peek(1)[0] == "ID" and \
                    self.peek(2)[1] in (";", "="):
                ty = tchar() if t[1] == "char" else tint()
                self.next()
                nm = self.expect("ID")[1]
                init = self.expr() if self.accept("PUNCT", "=") else None
                self.expect("PUNCT", ";")
                globals_.append((nm, ty, init))
            elif t[0] == "KW" and t[1] == "extern":
                self.extern_c(funcs)
            elif t[0] == "KW" and t[1] == "template":
                templates.append(self.templatedef())
            elif t[0] == "KW" and t[1] == "namespace":
                ns_name = self.nsdef(classes, funcs)
            elif t[0] == "KW" and t[1] in ("class", "struct"):
                cd2 = self.classdef()
                if cd2 is not None:
                    classes.append(cd2)
            elif t[0] == "KW" and t[1] == "int" and \
                    self.peek(1)[1] == "main":
                if main is not None:
                    raise ParseError(f"L{t[2]}: main 重定义")
                main = self.maindef()
            elif t[0] == "KW" and t[1] in ("void", "bool", "const", "int", "char", "operator") or \
                    t[0] == "ID":
                funcs.append(self.funcdef())
            else:
                raise ParseError(f"L{t[2]}: 不支持的顶层声明 {t[1]!r}")
        classes.extend(self.collected_inner)
        self.collected_inner = []
        return classes, funcs, globals_, sdefs, templates, main

    def templatedef(self):
        """template<typename T> T name(...) { body }——T 复用类通道。"""
        self.expect("KW", "template")
        self.expect("PUNCT", "<")
        tvars = []                        # 0.0.40 多类型参数
        tvar_dfls = []                    # 0.0.42 默认模板参数 [(名, Type|None)]
        while True:
            if self.peek()[0] == "PUNCT" and self.peek()[1] == ">":
                break                     # template<> = 全特化头（0.0.42）
            self.expect("KW", "typename")
            vn = self.expect("ID")[1]
            dfl = self.parse_type() if self.accept("PUNCT", "=") else None
            tvars.append(vn)
            tvar_dfls.append((vn, dfl))
            if not self.accept("PUNCT", ","):
                break
        self.expect("PUNCT", ">")
        t = self.peek()
        if t[0] == "KW" and t[1] in ("class", "struct"):
            # 0.0.41 类模板：template<typename T> class X {...}
            kind = self.next()[1]
            cname = self.expect("ID")[1]
            if self.accept("PUNCT", ":"):
                raise ParseError("类模板继承=0.0.43 多继承面，暂不支持")
            if tvars == [] and self.peek()[0] == "PUNCT" and \
                    self.peek()[1] == "<":
                # 0.0.42 全特化：template<> class Desc<int> {...}
                self.next()
                args = [self.parse_type()]
                while self.accept("PUNCT", ","):
                    args.append(self.parse_type())
                self.expect("PUNCT", ">")
                flat = cname + "__" + "__".join(
                    a.name.replace("::", "_") if a.kind == "class"
                    else a.kind for a in args)
                ct = self.class_templs.get(cname)
                if ct is None:
                    raise ParseError(
                        f"全特化 {flat} 须在主模板 {cname} 之后")
                if self.peek()[0] == "PUNCT" and self.peek()[1] == ";":
                    self.next()
                    return ct            # 特化声明（子集：须完整定义）
                cd = self.classdef(kind=kind, name=cname)
                ct.specializations[flat] = cd
                return ct
            cd = self.classdef(kind=kind, name=cname)
            node = ClassTemplate(tvars, cd)
            node.tvar_dfls = tvar_dfls
            self.class_templs[cname] = node       # 外联定义归模板（0.0.41）
            return node
        ty = self.parse_type()
        t = self.peek()
        if t[0] == "PUNCT" and t[1] == "::":
            # 0.0.41 ctor 外联：Box<T>::Box(...)（ty=parse_type 吃的 Box<T>）
            self.next()
            cls_short = ty.name.split("__")[0]
            t2 = self.peek()
            if t2[0] != "ID" or t2[1] != cls_short:
                raise ParseError(
                    f"外联 ctor 形态限 类名::类名(...)：得 {t2[1]!r}")
            ct = self.ctordef(t2[1])      # ctordef 自行消费 ID+参数+体
            return self._ext_class_member(tvars, ty.name,
                                          ("ext_ctors", ct))
        if t[0] == "ID" and self.peek(1)[0] == "PUNCT" and \
                self.peek(1)[1] == "<":
            # 0.0.41 方法外联：T Box<T>::get(...)（ty=ret）
            cls_ty = self.parse_type()
            self.expect("PUNCT", "::")
            nm = self.expect("ID")[1]
            self.expect("PUNCT", "(")
            params, defaults = self.parse_params()
            self.expect("PUNCT", ")")
            is_const = bool(self.accept("KW", "const"))
            md = MethodDecl(nm, ty, params, is_const, self.block())
            md.defaults = defaults
            return self._ext_class_member(tvars, cls_ty.name,
                                          ("ext_methods", md))
        nm = self.expect("ID")[1]
        self.expect("PUNCT", "(")
        params, defaults = self.parse_params()
        self.expect("PUNCT", ")")
        if self.accept("KW", "const"):
            raise ParseError("模板函数无 const（无 this 可限定）")
        body = self.block()
        fd = MethodDecl(nm, ty, params, False, body)
        fd.defaults = defaults
        fd.tvars = tvars
        ft = FuncTemplate(tvars, fd)
        ft.tvar_dfls = tvar_dfls
        return ft

    def _ext_class_member(self, tvars, cls_flat, pair):
        """外联成员归模板：cls_flat 须= Name__T（tvars 恒等映射）；
        具体实参外联=E0；类模板未定义=ParseError。"""
        tag, member = pair
        ct = self.class_templs.get(cls_flat.split("__")[0])
        if ct is None:
            raise ParseError(f"类模板 {cls_flat.split('__')[0]} 未定义"
                             f"（外联定义须在类模板之后）")
        flat_tvars = ct.decl.name + "__" + "__".join(ct.tvars)
        if cls_flat != flat_tvars:
            raise ParseError(
                f"外联定义限模板参数形态 {flat_tvars}（具体实参外联=E0）")
        getattr(ct, tag).append((cls_flat, member))
        return ct

    def extern_c(self, funcs):
        """extern "C" { fdecl… } / extern "C" int f(params);——裸名链接面。
        内限函数（类/namespace=拒绝）；无体声明=原型直出。"""
        self.expect("KW", "extern")
        s = self.expect("STR")[1]
        if s.replace('"', "") != "C":
            raise ParseError(f'extern 语言链接仅支持 "C"（得 {s}）')
        if self.accept("PUNCT", "{"):
            while not self.accept("PUNCT", "}"):
                funcs.append(self.funcdef(c_link=True))
        else:
            funcs.append(self.funcdef(c_link=True))

    def nsdef(self, classes, funcs):
        """namespace ID { class/func }——嵌套 ns/using=拒绝（扁平子集）。"""
        self.expect("KW", "namespace")
        ns = self.expect("ID")[1]
        self.expect("PUNCT", "{")
        while not self.accept("PUNCT", "}"):
            t = self.peek()
            if t[0] == "KW" and t[1] == "namespace":
                raise ParseError(f"L{t[2]}: 嵌套命名空间=子集外（扁平化）")
            if t[0] == "KW" and t[1] in ("class", "struct"):
                cd = self.classdef()
                cd.ns = ns
                classes.append(cd)
            elif t[0] == "KW" and t[1] in ("void", "bool", "const", "int", "char") or \
                    t[0] == "ID":
                f = self.funcdef()
                f.ns = ns
                funcs.append(f)
            else:
                raise ParseError(f"L{t[2]}: 命名空间内不支持 {t[1]!r}")
        return ns

    def qual_name(self, first):
        """ID (:: ID)* → 'a::b' 限定名拼接。"""
        nm = first
        while self.peek()[1] == "::" and self.peek()[0] == "PUNCT":
            self.next()
            nm += "::" + self.expect("ID")[1]
        return nm

    def funcdef(self, c_link=False):
        """自由函数：Type name(params) [{ body }]——extern "C" 时允许无体
        （原型直出）；const 后缀非法（C++ 法）。"""
        ty = self.parse_type()
        if self.peek()[0] == "KW" and self.peek()[1] == "operator":
            nm = self.op_name()
        else:
            nm = self.expect("ID")[1]
        if nm == "main":
            raise ParseError("main 必须是 int main()")
        self.expect("PUNCT", "(")
        params, defaults = self.parse_params()
        self.expect("PUNCT", ")")
        if self.accept("KW", "const"):
            raise ParseError("自由函数无 const 后缀（const 仅限成员函数）")
        if any(d is not None for d in defaults):
            raise ParseError("默认实参暂不支持（后续版本）") if c_link else None
        if self.peek()[1] == ";" and c_link:
            self.next()
            body = None                   # 原型声明（链接面）
        else:
            body = self.block()
        fd = MethodDecl(nm, ty, params, False, body)
        fd.defaults = defaults
        fd.ns = None                      # nsdef 会覆盖为命名空间名
        fd.c_link = c_link
        return fd

    def classdef(self, kind=None, name=None):
        """kind/name 预传=类模板分支已消费 class/struct+名（0.0.41）。"""
        if kind is None:
            kind = self.next()[1]                     # class/struct（0.0.28 默认访问级）
        acc = "private" if kind == "class" else "public"
        if name is None:
            name = self.expect("ID")[1]
        base = None
        bases = []                             # 0.0.43 多基类全列表
        if self.accept("PUNCT", ":"):
            while True:
                self.expect("KW", "public")
                bases.append(self.qual_name(self.expect("ID")[1]))
                if not self.accept("PUNCT", ","):
                    break
        base = bases[0] if bases else None     # 位 0 基类（0.0.26 兼容）
        if self.accept("PUNCT", ";"):
            cd = ClassDecl(name, base)
            cd.bases = bases
            cd.forward = True                  # 前向声明（不完整类型）
            return cd
        self.expect("PUNCT", "{")
        cd = ClassDecl(name, base)
        cd.bases = bases
        while not self.accept("PUNCT", "}"):
            t = self.peek()
            if t[0] == "KW" and t[1] in ("public", "private", "protected"):
                self.next()
                self.expect("PUNCT", ":")
                acc = t[1]                            # 0.0.28 访问级切换
            elif t[0] == "KW" and t[1] == "virtual":
                self.next()                    # 0.0.24：虚函数标记
                ty = self.parse_type()
                nm = self.expect("ID")[1]
                self.expect("PUNCT", "(")
                params, defaults = self.parse_params()
                self.expect("PUNCT", ")")
                is_const = bool(self.accept("KW", "const"))
                md = MethodDecl(nm, ty, params, is_const, None)
                md.defaults = defaults
                md.is_virtual = True
                md.is_pure = False
                md.access = acc              # 0.0.28
                if self.accept("PUNCT", "="):   # 0.0.27：纯虚 = 0（无体）
                    if self.expect("NUM")[1] != "0":
                        raise ParseError(f"L{t[2]}: 纯虚声明限 = 0")
                    md.is_pure = True
                    self.expect("PUNCT", ";")
                else:
                    md.body = self.block()
                cd.methods.append(md)
            elif t[0] == "KW" and t[1] == "static":
                self.next()
                ty = self.parse_type()
                if self.peek()[0] == "KW" and self.peek()[1] == "operator":
                    self.next()                   # 0.0.30 显式 static 分配器
                    nxt = self.next()
                    if nxt[1] not in ("new", "delete"):
                        raise ParseError(
                            f"L{t[2]}: static 后仅支持 operator new/delete")
                    self.expect("PUNCT", "(")
                    params, defaults = self.parse_params()
                    self.expect("PUNCT", ")")
                    md = MethodDecl("operator " + nxt[1], ty, params,
                                    False, self.block())
                    md.defaults = defaults
                    md.is_static = True
                    md.is_special = nxt[1]
                    md.access = acc
                    cd.methods.append(md)
                    continue
                nm = self.expect("ID")[1]
                if self.peek()[1] == "(":
                    cd.methods.append(self.methoddef_static(nm, ty, acc))
                else:
                    cd.statics.append((nm, ty))
                    cd.static_access[nm] = acc    # 0.0.28
                    self.expect("PUNCT", ";")
            elif t[0] == "KW" and t[1] == "explicit":
                self.next()                       # 0.0.30 explicit ctor
                cd.ctors.append(self.ctordef(name, explicit=True))
            elif t[0] == "ID" and t[1] == name and self.peek(1)[1] == "(":
                cd.ctors.append(self.ctordef(name))
            elif t[0] == "KW" and t[1] == "int":
                # int val; 成员 vs int f(...) / int& f(...) / int operator+(...) 方法
                if (self.peek(1)[0] == "ID" and self.peek(2)[1] == "(") \
                        or self.peek(1)[1] == "&" \
                        or (self.peek(1)[0] == "KW" and self.peek(1)[1] == "operator"):
                    cd.methods.append(self.methoddef(acc))
                else:
                    self.memberdecl(cd, acc)
            elif (t[0] == "KW" and t[1] in ("char", "void", "bool", "const")) or \
                    (t[0] == "KW" and t[1] == "operator") or \
                    (t[0] == "ID" and self.peek(1)[1] == "*"):
                if t[0] == "ID":
                    self.memberdecl(cd, acc)    # B* p; 指针成员
                else:
                    cd.methods.append(self.methoddef(acc))
            elif (t[0] == "ID" and self.peek(1)[0] == "ID" and
                    self.peek(2)[1] == "(") or \
                    (t[0] == "ID" and self.peek(1)[0] == "KW" and
                     self.peek(1)[1] == "operator"):
                cd.methods.append(self.methoddef(acc))
            elif t[0] == "KW" and t[1] in ("class", "struct"):
                inner = self.classdef()
                outer = f"{cd.ns}::{cd.name}" if cd.ns else cd.name
                inner.nested_in = outer
                inner.ns = outer               # ns 机制同构：key=outer::inner
                self.collected_inner.append(inner)
            elif t[0] == "KW" and t[1] == "friend":
                self.next()
                self.parse_type()
                self.expect("ID")[1]
                self.expect("PUNCT", "(")
                while self.peek()[1] != ")":
                    self.next()
                self.expect("PUNCT", ")")
                self.expect("PUNCT", ";")   # friend 声明：无访问控制子集=语义 no-op
            elif t[0] == "PUNCT" and t[1] == "~":
                self.next()
                dn = self.expect("ID")[1]
                if dn != name:
                    raise ParseError(f"L{t[2]}: 析构名 ~{dn} 与类 {name} 不符")
                self.expect("PUNCT", "(")
                self.expect("PUNCT", ")")
                cd.dtors.append(DtorDecl(self.block()))
            elif t[0] == "ID" and self.peek(1)[0] == "ID" and \
                    self.peek(2)[1] in (";", "=", "["):
                self.memberdecl(cd, acc)    # 类类型成员 T v;（0.0.41 模板）
            else:
                raise ParseError(f"L{t[2]}: 类体内不支持 {t[1]!r}"
                                 f"（成员限 int；char 成员/访问控制外语法=后续版本）")
        self.expect("PUNCT", ";")
        return cd

    def memberdecl(self, cd, acc="public"):
        """成员：int x; / B* p;（指针成员=0.0.21）。"""
        ty = self.parse_type()
        while True:
            nm = self.expect("ID")[1]
            cd.members.append((nm, ty))
            cd.member_access[nm] = acc        # 0.0.28
            if not self.accept("PUNCT", ","):
                break
        self.expect("PUNCT", ";")

    def parse_type(self):
        """['const'] (int|char|void|bool|ID) ['&'] → Type。"""
        is_const = bool(self.accept("KW", "const"))
        t = self.next()
        if t[0] == "KW" and t[1] == "int":
            ty = tint(is_const)
        elif t[0] == "KW" and t[1] == "char":
            ty = tchar(is_const)
        elif t[0] == "KW" and t[1] == "bool":
            ty = tbool(is_const)
        elif t[0] == "KW" and t[1] == "void":
            ty = tvoid()                      # const void 不可表达
        elif t[0] == "ID":
            if t[1] == "size_t":
                ty = tint(is_const)             # 0.0.30 size_t=int 别名
            else:
                ty = tcls(self.qual_name(t[1]), is_const)   # geo::Box 限定类型
                if self.peek()[0] == "PUNCT" and self.peek()[1] == "<":
                    tspec = self._try_tspec(ty)   # 0.0.41 类模板实参 Box<int>
                    if tspec is not None:
                        ty = tspec
        else:
            raise ParseError(f"L{t[2]}: 不支持的类型 {t[1]!r}")
        if self.accept("PUNCT", "&"):
            ty = tref(ty)                     # ref(ref) 在构造层即拒绝
        while self.accept("PUNCT", "*"):
            ty = tptr(ty)                     # Box* p（0.0.15 指针声明）
        return ty

    def _try_tspec(self, base):
        """ID 后 <类型序列> 形态 → 平化实例类型 Box<int>→Box__int；
        失配游标复原返回 None（声明位/cast/new 位共用 parse_type——
        表达式位比较不经此）。嵌套 Box<Box<int>>：平化名拆分歧义，
        子集边界文档化拒绝（>> 单 token 词法实证）。"""
        mark = self.i
        try:
            self.expect("PUNCT", "<")
            args = [self.parse_type()]
            while self.accept("PUNCT", ","):
                args.append(self.parse_type())
            self.expect("PUNCT", ">")
        except ParseError:
            self.i = mark
            return None
        flat = "__".join(a.name if a.kind == "class" else a.kind
                         for a in args)
        return tcls(f"{base.name}__{flat.replace('::', '_')}",
                    base.is_const)

    def parse_params(self):
        """返回 (params, defaults)：defaults[i] = 默认实参 AST|None。
        一旦有默认，后续必须全有（后缀规则在 sema 校验）。"""
        ps, dfl = [], []
        if self.peek()[1] == ")" and self.peek()[0] == "PUNCT":
            return ps, dfl
        while True:
            ty = self.parse_type()
            nm = self.expect("ID")[1]
            ps.append((ty, nm))
            d = self.expr() if self.accept("PUNCT", "=") else None
            dfl.append(d)
            if not self.accept("PUNCT", ","):
                break
        return ps, dfl

    def ctordef(self, clsname, explicit=False):
        self.expect("ID", clsname)
        self.expect("PUNCT", "(")
        params, defaults = self.parse_params()
        self.expect("PUNCT", ")")
        inits = []
        body = None
        if self.accept("PUNCT", ":"):
            while True:
                tgt = self.expect("ID")[1]
                self.expect("PUNCT", "(")
                args = [] if self.peek()[1] == ")" else self.exprlist()
                self.expect("PUNCT", ")")
                inits.append((tgt, args))
                if not self.accept("PUNCT", ","):
                    break
            body = self.block()
        elif self.accept("PUNCT", ";"):
            pass                          # ctor 声明（体=外联定义，0.0.41）
        else:
            body = self.block()
        cd = CtorDecl(params, inits, body)
        cd.defaults = defaults
        cd.is_explicit = explicit             # 0.0.30
        return cd

    def methoddef_static(self, nm, ty, acc="public"):
        """static int name(params) { body }——无 this（0.0.20）。"""
        self.expect("PUNCT", "(")
        params, defaults = self.parse_params()
        self.expect("PUNCT", ")")
        if self.accept("KW", "const"):
            raise ParseError("static 方法无 const（无 this 可限定）")
        body = self.block()
        md = MethodDecl(nm, ty, params, False, body)
        md.defaults = defaults
        md.is_static = True
        md.access = acc                       # 0.0.28
        return md

    def op_name(self):
        """operator 后接算子（限算术：+/==）→ 'operator+' 名。"""
        self.expect("KW", "operator")
        t = self.next()
        if t[0] == "PUNCT" and t[1] in ("+", "=", "==", "-", "<", ">", "!="):
            return "operator" + t[1]
        if t[0] == "KW" and t[1] in ("new", "delete"):
            return "operator " + t[1]           # 0.0.30 类专属分配器
        raise ParseError(f"L{t[2]}: 操作符 {t[1]!r} 子集外"
                         f"（限算术：= + == - < > !=）")

    def methoddef(self, acc="public"):
        ty = self.parse_type()
        if self.peek()[0] == "KW" and self.peek()[1] == "operator":
            nm = self.op_name()
            if nm in ("operator new", "operator delete"):
                # 0.0.30 类专属分配器（C++ 法：隐式静态）
                self.expect("PUNCT", "(")
                params, defaults = self.parse_params()
                self.expect("PUNCT", ")")
                if self.accept("KW", "const"):
                    raise ParseError(f"operator new/delete 无 const")
                md = MethodDecl(nm, ty, params, False, self.block())
                md.defaults = defaults
                md.is_static = True
                md.is_special = nm.split()[1]
                md.access = acc
                return md
        else:
            nm = self.expect("ID")[1]
        self.expect("PUNCT", "(")
        params, defaults = self.parse_params()
        self.expect("PUNCT", ")")
        is_const = bool(self.accept("KW", "const"))
        if self.accept("PUNCT", ";"):
            body = None                       # 方法声明（外联定义，0.0.41）
        else:
            body = self.block()
        md = MethodDecl(nm, ty, params, is_const, body)
        md.defaults = defaults
        md.is_static = False
        md.access = acc                       # 0.0.28
        return md

    def maindef(self):
        self.expect("KW", "int")
        self.expect("ID", "main")
        self.expect("PUNCT", "(")
        self.expect("PUNCT", ")")
        body = self.block()
        return body

    # ---- 语句 ----
    def block(self):
        self.expect("PUNCT", "{")
        ss = []
        while not self.accept("PUNCT", "}"):
            ss.append(self.stmt())
        return ss

    def stmt(self):
        t = self.peek()
        if t[0] == "PUNCT" and t[1] == "{":
            return BlockStmt(self.block())
        if t[0] == "KW" and t[1] == "return":
            self.next()
            e = None if self.peek()[1] == ";" else self.expr()
            self.expect("PUNCT", ";")
            return Return(e)
        if t[0] == "KW" and t[1] == "if":
            self.next()
            self.expect("PUNCT", "(")
            c = self.expr()
            self.expect("PUNCT", ")")
            th = self.stmt()
            el = self.stmt() if self.accept("KW", "else") else None
            return If(c, th, el)
        if t[0] == "KW" and t[1] == "while":
            self.next()
            self.expect("PUNCT", "(")
            c = self.expr()
            self.expect("PUNCT", ")")
            return While(c, self.stmt())
        if t[0] == "KW" and t[1] == "throw":
            self.next()                       # 0.0.35 throw（bare=0.0.36）
            e = None if self.peek()[1] == ";" else self.expr()
            self.expect("PUNCT", ";")
            return Throw(e)
        if t[0] == "KW" and t[1] == "try":
            self.next()                       # 0.0.35 try/catch
            tbody = self.block()
            catches = []
            while self.peek() == ("KW", "catch") or \
                    (self.peek()[0] == "KW" and self.peek()[1] == "catch"):
                self.next()
                self.expect("PUNCT", "(")
                if self.accept("PUNCT", "..."):
                    self.expect("PUNCT", ")")
                    catches.append((None, None, self.block()))
                    continue
                cty = self.parse_type()
                cvar = self.expect("ID")[1]
                self.expect("PUNCT", ")")
                catches.append((cty, cvar, self.block()))
            return Try(tbody, catches)
        if t[0] == "KW" and t[1] == "delete":
            self.next()
            e = self.expr()
            self.expect("PUNCT", ";")
            return DeleteStmt(e)
        if t[0] == "KW" and t[1] == "for":
            return self.forstmt()
        if t[0] == "KW" and (t[1] == "const" or t[1] in ("int", "char", "void")):
            ty = self.parse_type()   # const int& x / int& x / void* p / int x
            nm = self.expect("ID")[1]
            init = None
            if self.accept("PUNCT", "="):
                init = self.expr()
            self.expect("PUNCT", ";")
            return VarDecl(nm, ty, init)
        if t[0] == "ID" and self.peek(1)[1] == "*":
            ty = self.parse_type()                 # Box* p = new Box(args);（0.0.15）
            nm = self.expect("ID")[1]
            init = None
            if self.accept("PUNCT", "="):
                init = self.expr()
            self.expect("PUNCT", ";")
            return VarDecl(nm, ty, init)
        if t[0] == "ID" and self.peek(1)[0] == "PUNCT" and \
                self.peek(1)[1] == "<":
            # 0.0.41 类模板实例声明：Box<int> var; / var(args); / var = expr;
            cls = self.qual_name(self.next()[1])
            ty = self._try_tspec(tcls(cls))
            if ty is None:
                raise ParseError(f"L{t[2]}: 语句级 < 后非类型形态"
                                 f"（Box<int> var; 系=子集）")
            cls = ty.name
            if self.peek()[0] == "ID" and self.peek(1)[1] == "=":
                var = self.next()[1]
                self.expect("PUNCT", "=")
                init = self.expr()
                self.expect("PUNCT", ";")
                return CopyInit(cls, var, init)
            if self.peek()[0] == "ID" and self.peek(1)[1] == ";":
                var = self.next()[1]
                self.expect("PUNCT", ";")
                return CtorStmt(cls, var, [])
            if self.peek()[0] == "ID" and self.peek(1)[1] == "(":
                var = self.next()[1]
                self.expect("PUNCT", "(")
                args = [] if self.peek()[1] == ")" else self.exprlist()
                self.expect("PUNCT", ")")
                self.expect("PUNCT", ";")
                return CtorStmt(cls, var, args)
            raise ParseError(
                f"L{t[2]}: {cls} 声明形态限 var;/var(args);/var = expr;"
                f"（Box<T>* p=子集边界）")
        if t[0] == "ID" and self.peek(1)[0] == "ID" and \
                self.peek(2)[1] == "=":
            cls = self.qual_name(self.next()[1])   # X var = expr;（0.0.29）
            var = self.next()[1]
            self.expect("PUNCT", "=")
            init = self.expr()
            self.expect("PUNCT", ";")
            return CopyInit(cls, var, init)
        if t[0] == "ID" and self.peek(1)[0] == "ID" and \
                self.peek(2)[1] == ";":
            cls = self.qual_name(self.next()[1])
            var = self.next()[1]
            self.expect("PUNCT", ";")
            return CtorStmt(cls, var, [])          # X var;（无参构造）
        if t[0] == "ID" and self.peek(1)[1] == "::" and \
                self.peek(2)[0] == "ID" and self.peek(3)[0] == "ID" and \
                self.peek(4)[1] == ";":
            cls = self.qual_name(self.next()[1])
            var = self.next()[1]
            self.expect("PUNCT", ";")
            return CtorStmt(cls, var, [])          # NS::X var;（无参构造）
        if t[0] == "ID" and self.peek(1)[0] == "ID" and \
                self.peek(2)[1] == "(":
            cls = self.qual_name(self.next()[1])
            var = self.next()[1]
            self.expect("PUNCT", "(")
            args = [] if self.peek()[1] == ")" else self.exprlist()
            self.expect("PUNCT", ")")
            self.expect("PUNCT", ";")
            return CtorStmt(cls, var, args)
        if t[0] == "ID" and self.peek(1)[1] == "::" and \
                self.peek(2)[0] == "ID" and self.peek(3)[0] == "ID" and \
                self.peek(4)[1] == "(":
            cls = self.qual_name(self.next()[1])
            var = self.next()[1]
            self.expect("PUNCT", "(")
            args = [] if self.peek()[1] == ")" else self.exprlist()
            self.expect("PUNCT", ")")
            self.expect("PUNCT", ";")
            return CtorStmt(cls, var, args)
        e = self.expr()
        self.expect("PUNCT", ";")
        return ExprStmt(e)

    def forstmt(self):
        self.expect("KW", "for")
        self.expect("PUNCT", "(")
        init = None
        if self.peek()[0] == "KW" and \
                (self.peek()[1] == "const" or self.peek()[1] in ("int", "char")):
            ty = self.parse_type()
            nm = self.expect("ID")[1]
            ie = self.expr() if self.accept("PUNCT", "=") else None
            init = VarDecl(nm, ty, ie)
        elif self.peek()[1] != ";":
            init = ExprStmt(self.expr())
        self.expect("PUNCT", ";")
        cond = None if self.peek()[1] == ";" else self.expr()
        self.expect("PUNCT", ";")
        step = None if self.peek()[1] == ")" else self.expr()
        self.expect("PUNCT", ")")
        return For(init, cond, step, self.stmt())

    # ---- 表达式（优先级爬升） ----
    BINLV = [["||"], ["&&"], ["|"], ["^"], ["&"],
             ["==", "!="], ["<", ">", "<=", ">="],
             ["<<", ">>"], ["+", "-"], ["*", "/", "%"]]

    def expr(self):
        return self.assign()

    def assign(self):
        l = self.binary(0)
        if self.accept("PUNCT", "="):
            return Assign(l, self.assign())
        return l

    def binary(self, lv):
        if lv >= len(self.BINLV):
            return self.unary()
        l = self.binary(lv + 1)
        while self.peek()[0] == "PUNCT" and self.peek()[1] in self.BINLV[lv]:
            op = self.next()[1]
            l = Bin(op, l, self.binary(lv + 1))
        return l

    def unary(self):
        t = self.peek()
        if t[0] == "PUNCT" and t[1] in ("!", "-", "+"):
            self.next()
            return Un(t[1], self.unary())
        if t[0] == "PUNCT" and t[1] == "&":
            self.next()                      # 消费 &（缺它=无限递归）
            return Un("&", self.unary())     # 0.0.21：&lvalue（指针实参面）
        if t[0] == "PUNCT" and t[1] == "*":
            self.next()
            return Un("*", self.unary())   # 0.0.23：解引用（读+写穿）
        return self.postfix()

    def postfix(self):
        e = self.primary()
        while True:
            t = self.peek()
            if t[0] == "PUNCT" and t[1] in (".", "->"):
                self.next()
                nm = self.expect("ID")[1]
                if self.accept("PUNCT", "("):
                    args = [] if self.peek()[1] == ")" else self.exprlist()
                    self.expect("PUNCT", ")")
                    e = MethodCall(e, nm, args)
                else:
                    e = Member(e, nm, arrow=(t[1] == "->"))
            elif t[0] == "PUNCT" and t[1] == "++":
                raise ParseError(f"L{t[2]}: 后缀 ++ 暂不支持（i += 1 替代）")
            elif t[0] == "PUNCT" and t[1] == "--":
                raise ParseError(f"L{t[2]}: 后缀 -- 暂不支持")
            else:
                return e

    def _try_tcall(self, nm):
        """ID 后 <类型序列>( 实参形态 → FreeCall(targs)；否则游标复原
        返回 None（0.0.40 歧义消解：static_cast<int>(x) 同形态先例；
        a < b 比较回退 Bin 层——探针实证 tmax<int>(3,5) 曾被 Bin 误吃）。"""
        mark = self.i
        try:
            self.expect("PUNCT", "<")
            targs = [self.parse_type()]
            while self.accept("PUNCT", ","):
                targs.append(self.parse_type())
            self.expect("PUNCT", ">")
            if not (self.peek()[0] == "PUNCT" and self.peek()[1] == "("):
                self.i = mark
                return None
            self.next()
            args = [] if self.peek()[1] == ")" else self.exprlist()
            self.expect("PUNCT", ")")
            fc = FreeCall(nm, args)
            fc.targs = targs
            return fc
        except ParseError:
            self.i = mark
            return None

    def primary(self):
        t = self.next()
        if t[0] == "NUM":
            s = t[1]
            if "." in s or "e" in s.lower():
                raise ParseError(f"L{t[2]}: 浮点字面量暂不支持 {s!r}")
            return Num(int(s, 0))
        if t[0] == "STR":
            return StrLit(t[1])
        if t[0] == "ID":
            nm = self.qual_name(t[1])          # geo::area → 单一名字串
            tc = self._try_tcall(nm)           # 0.0.40 <T>(…) 形态
            if tc is not None:
                return tc
            if self.peek()[1] == "(" and self.peek()[0] == "PUNCT":
                self.next()
                args = [] if self.peek()[1] == ")" else self.exprlist()
                self.expect("PUNCT", ")")
                return FreeCall(nm, args)
            return Id(nm)
        if t[0] == "KW" and t[1] in ("static_cast", "reinterpret_cast",
                                     "const_cast", "dynamic_cast"):
            kind = t[1]                       # 0.0.31 命名 cast
            self.expect("PUNCT", "<")
            ty = self.parse_type()
            self.expect("PUNCT", ">")
            self.expect("PUNCT", "(")
            e = self.expr()
            self.expect("PUNCT", ")")
            return NamedCast(kind, ty, e)
        if t[0] == "KW" and t[1] == "this":
            return This()
        if t[0] == "KW" and t[1] == "new":
            cn = self.qual_name(self.expect("ID")[1])   # t=next() 已耗 new
            args = []
            if self.accept("PUNCT", "("):
                args = [] if self.peek()[1] == ")" else self.exprlist()
                self.expect("PUNCT", ")")
            return New(cn, args)
        if t[0] == "KW" and t[1] == "delete":
            raise ParseError("delete 是语句：delete p;（表达式位置不支持）")
        if t[0] == "PUNCT" and t[1] == "(":
            e = self.expr()
            self.expect("PUNCT", ")")
            return e
        raise ParseError(f"L{t[2]}: 意外的 {t[1]!r}")

    def exprlist(self):
        es = [self.expr()]
        while self.accept("PUNCT", ","):
            es.append(self.expr())
        return es


class Block:
    pass


def parse(src):
    """源码 → (classes, funcs, globals, sdefs, templates, main)。"""
    return Parser(lex(src)).parse_tu()
