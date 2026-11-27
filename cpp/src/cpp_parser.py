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


class Parser:
    def __init__(self, toks):
        self.toks = toks
        self.i = 0
        self.collected_inner = []      # 嵌套类冒泡（0.0.22）

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
        globals_, sdefs = [], []
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
        return classes, funcs, globals_, sdefs, main

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

    def classdef(self):
        kind = self.next()[1]                         # class/struct（0.0.28 默认访问级）
        acc = "private" if kind == "class" else "public"
        name = self.expect("ID")[1]
        base = None
        if self.accept("PUNCT", ":"):
            self.expect("KW", "public")
            base = self.qual_name(self.expect("ID")[1])
        if self.accept("PUNCT", ";"):
            cd = ClassDecl(name, base)
            cd.forward = True                  # 前向声明（不完整类型）
            return cd
        self.expect("PUNCT", "{")
        cd = ClassDecl(name, base)
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
                nm = self.expect("ID")[1]
                if self.peek()[1] == "(":
                    cd.methods.append(self.methoddef_static(nm, ty, acc))
                else:
                    cd.statics.append((nm, ty))
                    cd.static_access[nm] = acc    # 0.0.28
                    self.expect("PUNCT", ";")
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
            ty = tcls(self.qual_name(t[1]), is_const)   # geo::Box 限定类型
        else:
            raise ParseError(f"L{t[2]}: 不支持的类型 {t[1]!r}")
        if self.accept("PUNCT", "&"):
            ty = tref(ty)                     # ref(ref) 在构造层即拒绝
        while self.accept("PUNCT", "*"):
            ty = tptr(ty)                     # Box* p（0.0.15 指针声明）
        return ty

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

    def ctordef(self, clsname):
        self.expect("ID", clsname)
        self.expect("PUNCT", "(")
        params, defaults = self.parse_params()
        self.expect("PUNCT", ")")
        inits = []
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
        cd = CtorDecl(params, inits, body)
        cd.defaults = defaults
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
        raise ParseError(f"L{t[2]}: 操作符 {t[1]!r} 子集外"
                         f"（限算术：= + == - < > !=）")

    def methoddef(self, acc="public"):
        ty = self.parse_type()
        if self.peek()[0] == "KW" and self.peek()[1] == "operator":
            nm = self.op_name()
        else:
            nm = self.expect("ID")[1]
        self.expect("PUNCT", "(")
        params, defaults = self.parse_params()
        self.expect("PUNCT", ")")
        is_const = bool(self.accept("KW", "const"))
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
        if t[0] == "KW" and t[1] == "delete":
            self.next()
            e = self.expr()
            self.expect("PUNCT", ";")
            return DeleteStmt(e)
        if t[0] == "KW" and t[1] == "for":
            return self.forstmt()
        if t[0] == "KW" and (t[1] == "const" or t[1] in ("int", "char")):
            ty = self.parse_type()   # const int& x / int& x / const int x / int x
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
            if self.peek()[1] == "(" and self.peek()[0] == "PUNCT":
                self.next()
                args = [] if self.peek()[1] == ")" else self.exprlist()
                self.expect("PUNCT", ")")
                return FreeCall(nm, args)
            return Id(nm)
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
    """源码 → (classes, free_funcs, globals, static_defs, main)。"""
    return Parser(lex(src)).parse_tu()
