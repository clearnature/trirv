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
import copy as _copy
from cpp_parser import (CUR_CATCH, EH_FLAG, ClassTemplate,
 FuncTemplate, New, DeleteStmt, DtorDecl, CopyInit, NamedCast, Throw, Try, Num, StrLit, Id, This, Member, MethodCall,
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
        self.bases = []        # 0.0.43 多基类键全列表（base=位 0 别名）
        self.members = {}      # name → Type
        self.methods = []      # MethodDecl
        self.ctors = []        # CtorDecl
        self.dtor = None       # DtorDecl（0.0.19）
        self.static_members = {}   # name → Type（0.0.20）
        self.vslots = []           # 虚槽名序（0.0.24，base 先序+override 保位）
        self.member_access = {}    # name → 访问级（0.0.28）
        self.static_access = {}    # static 成员访问级（0.0.28）
        self.key = None


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
        EH_FLAG[0] = False
        self.fn_dtors = []            # 本帧 dtor 对象 [(var, class_key)]（0.0.37）
        self.main_dtors = []
        self.templates = {}           # name → FuncTemplate（0.0.39 按需实例化）
        self.instantiated = {}        # inst_name → MethodDecl（单态化缓存）
        self.class_templates = {}     # name → ClassTemplate（0.0.41）
        self.cls_insts = {}           # inst_key → True（类单态化缓存）
        self.decls_list = None        # decls 别名（实例类就地注入→lower）
        self.classes = {}
        self.cur_method = None
        self.cur_cls = None
        self.cur_ns = None

    # ============ 类表 ============
    def _cur_key(self):
        """当前类规范键（ClassDecl.key，回退 name 兼容）。"""
        return getattr(self.cur_cls, "key", None) or self.cur_cls.name

    def resolve_class_name(self, name, ns):
        """短名/限定名 → 规范键；找不到返回 None。
        0.0.22：cur_cls 键作伪 ns 回退（外层类体内裸名可见内层类）。"""
        if name in self.classes:
            return name
        if ns and f"{ns}::{name}" in self.classes:
            return f"{ns}::{name}"
        if self.cur_cls is not None:
            k = getattr(self.cur_cls, "key", self.cur_cls.name)
            if f"{k}::{name}" in self.classes:
                return f"{k}::{name}"
        k = self._try_class_inst(name)     # 0.0.41 类模板惰性实例化
        if k is not None:
            return k
        return None

    def _try_class_inst(self, name):
        """平化实例名 → 类模板前缀匹配 → 惰性实例化（0.0.41）。
        Box__int 前缀 Box（模板名）→ 单段 "__" 拆实参平化名 → 反解
        → 实例化。T 形态（定义面 Box__T）不匹配任何具体反解 → None。"""
        for tkey, tmpl in self.class_templates.items():
            prefix = tkey + "__"
            if not name.startswith(prefix):
                continue
            # 0.0.42 全特化命中：特化体直接用（无替换）
            if name in tmpl.specializations:
                return self._instantiate_spec(
                    tkey, name, tmpl.specializations[name])
            argstrs = name[len(prefix):].split("__")
            dfls = tmpl.tvar_dfls
            if len(argstrs) > len(dfls):
                continue
            if len(argstrs) < len(dfls):
                # 0.0.42 默认模板参数：尾部缺省位须全有默认
                miss = range(len(argstrs), len(dfls))
                if any(dfls[i][1] is None for i in miss):
                    raise SemaError(
                        "E0", f"模板 {tkey} 实参数 {len(argstrs)} 不足"
                        f"且缺省位无默认（{len(dfls)} 类型参数）")
                argstrs = argstrs + [
                    type_name(dfls[i][1]).replace("::", "_")
                    for i in miss]
            concs = []
            for a in argstrs:
                ty = self._tflat(a)
                if ty is None:
                    return None            # T 形态/嵌套/未知平化名
                concs.append(ty)
            return self._instantiate_class(tkey, tmpl, concs, name)
        return None

    def _instantiate_spec(self, tkey, inst_name, spec_cd):
        """全特化实例化：特化体已具体——deepcopy 无替换（0.0.42）。"""
        if inst_name in self.cls_insts:
            return inst_name               # 已实例化（每类型一实例）
        self.cls_insts[inst_name] = True
        clone = _copy.deepcopy(spec_cd)
        clone.name = inst_name
        clone.ns = None
        clone.nested_in = None
        clone.base = None
        self._fill_class_info(clone, inst_name)
        self._check_class_body(clone)
        self.decls_list.append(clone)      # 就地注入：lower 同列表可见
        return inst_name

    def _tflat(self, a):
        """平化名反解：int/char/bool→基本型；其他=None（类实参嵌套
        =子集边界文档化拒绝；size_t 别名同 int）。"""
        return {"int": tint(), "size_t": tint(), "char": tchar(),
                "bool": tbool()}.get(a)

    def _instantiate_class(self, tkey, tmpl, concs, inst_name):
        """类模板单态化：deepcopy+T→concrete 替换+外联并入+ClassInfo
        构建（Phase C 复刻）+方法体重 check。"""
        if inst_name in self.cls_insts:
            return inst_name               # 已实例化（每类型一实例）
        self.cls_insts[inst_name] = True
        clone = _copy.deepcopy(tmpl.decl)
        clone.name = inst_name
        clone.ns = None
        clone.nested_in = None
        clone.base = None
        pairs = list(zip(tmpl.tvars, concs))
        self._subst_class(clone, tkey, pairs)
        # 外联成员并入：同名同参数量 → 体并入类内声明（C++ 法——
        # 声明+外联定义=一方法）；无声明 → 子集放宽（C++ 要求声明）
        for (cfn, md) in tmpl.ext_methods:
            m2 = _copy.deepcopy(md)
            self._subst(m2, pairs)
            hit = next((m for m in clone.methods
                        if m.name == m2.name
                        and len(m.params) == len(m2.params)), None)
            if hit is not None:
                hit.body, hit.params = m2.body, m2.params
                hit.is_const = m2.is_const
            else:
                clone.methods.append(m2)
        for (cfn, ct2) in tmpl.ext_ctors:
            c2 = _copy.deepcopy(ct2)
            for (tvar, conc) in pairs:
                for i, (pty, _pn) in enumerate(c2.params):
                    if pty.kind == "class" and pty.name == tvar:
                        c2.params[i] = (_copy.deepcopy(conc), _pn)
            hit = next((c for c in clone.ctors
                        if len(c.params) == len(c2.params)
                        and c.body is None), None)
            if hit is not None:
                hit.body, hit.inits, hit.params = c2.body, c2.inits, c2.params
            else:
                clone.ctors.append(c2)
        self._fill_class_info(clone, inst_name)
        self._check_class_body(clone)
        self.decls_list.append(clone)      # 就地注入：lower 同列表可见
        return inst_name

    def _subst_class(self, cd, prefix, pairs):
        """类模板体替换：members/statics 类型 + ctor params + 方法
        （经 _subst）+ dtor 体 + 体内 CtorStmt/New 类名。"""
        for (tvar, conc) in pairs:
            inst_flat = f"{prefix}__" + type_name(conc).replace("::", "_")
            cd.members = [(n, _copy.deepcopy(conc)
                           if ty.kind == "class" and ty.name == tvar else ty)
                          for (n, ty) in cd.members]
            cd.statics = [(n, _copy.deepcopy(conc)
                           if ty.kind == "class" and ty.name == tvar else ty)
                          for (n, ty) in getattr(cd, "statics", [])]
            for c in cd.ctors:
                for i, (pty, _pn) in enumerate(c.params):
                    if pty.kind == "class" and pty.name == tvar:
                        c.params[i] = (_copy.deepcopy(conc), _pn)
            for dt in getattr(cd, "dtors", []):
                for ss in dt.body:
                    self._subst_stmt(ss, tvar, conc, prefix, inst_flat)
            for m in cd.methods:
                self._subst(m, pairs)
                for ss in (m.body or []):
                    self._subst_stmt(ss, tvar, conc, prefix, inst_flat)
            for c in cd.ctors:
                for ss in (c.body or []):   # 声明 ctor 无体（0.0.41）
                    self._subst_stmt(ss, tvar, conc, prefix, inst_flat)

    def _fill_class_info(self, cd, key):
        """Phase C 复刻（单类）：ClassInfo 构建+mangling。"""
        info = ClassInfo(cd.name, None)
        info.key = key
        self.classes[key] = info
        for (mn, mty) in cd.members:
            info.members[mn] = mty
        for meth in cd.methods:
            if not hasattr(meth, "is_static"):
                meth.is_static = False
            if not hasattr(meth, "access"):
                meth.access = "public"
        info.methods.extend(cd.methods)
        info.ctors.extend(cd.ctors)
        info.member_access.update(getattr(cd, "member_access", {}))
        info.static_access.update(getattr(cd, "static_access", {}))
        for (sn, sty) in getattr(cd, "statics", []):
            info.static_members[sn] = sty
        if getattr(cd, "dtors", []):
            info.dtor = cd.dtors[0]
        info.has_op_new = any(getattr(m, "is_special", None) == "new"
                              for m in cd.methods)
        info.has_op_delete = any(getattr(m, "is_special", None) == "delete"
                                 for m in cd.methods)
        seen = {}
        for meth in cd.methods:
            if getattr(meth, "is_special", None):
                meth.mname = meth.name
                continue
            self.check_defaults(meth)
            mn2 = mangle_method(key, meth)
            if mn2 in seen:
                raise SemaError("E0",
                                f"{key}.{meth.name} 重定义：mangled "
                                f"{mn2} 撞 {seen[mn2]}（默认实参不进签名）")
            seen[mn2] = meth.name
            meth.mname = mn2
        for ctor in cd.ctors:
            self.check_defaults(ctor)
        cd.key = key
        return info

    def _check_class_body(self, cd):
        """实例类方法体重 check（check() 类循环复刻；无继承面）。"""
        key = cd.key
        self.cur_cls, self.cur_method = cd, None
        self.cur_ns = None
        visible = self.visible_method_names(key)
        for ctor in cd.ctors:
            if ctor.body is not None:
                rewrite_implicit_this(ctor.body, visible)
        for meth in cd.methods:
            if meth.body is not None:
                rewrite_implicit_this(meth.body, visible)
        if self.classes[key].dtor:
            rewrite_implicit_this(self.classes[key].dtor.body, visible)
        for ctor in cd.ctors:
            sc = Scope(self.gscope)
            for (pty, pn) in ctor.params:
                sc.declare(pn, pty)
            for (tgt, args) in ctor.inits:
                if self.find_member(key, tgt) is None:
                    raise SemaError("E0",
                                    f"构造初始化目标 {tgt} 不是 "
                                    f"{cd.name} 成员")
                for a in args:
                    self.check_expr(a, sc, False)
            if ctor.body is not None:
                self.check_stmts(ctor.body, sc, False)
        for meth in cd.methods:
            self.cur_method = meth
            sc = Scope(self.gscope)
            for (pty, pn) in meth.params:
                sc.declare(pn, pty)
            self.fn_dtors = []
            if meth.body is not None:
                self.check_stmts(meth.body, sc, meth.is_const)
            meth.fn_dtors = list(self.fn_dtors)
        if self.classes[key].dtor:
            self.cur_method = None
            sc = Scope(self.gscope)
            self.check_stmts(self.classes[key].dtor.body, sc, False)

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
        elif isinstance(s, CopyInit):
            s.cls = self.resolve_class_name(s.cls, ns) or s.cls
            self.canon_expr(s.init, ns)
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
            elif isinstance(e, FreeCall):
                r = self.resolve_class_name(e.name, ns)
                if r:
                    e.name = r               # CtorExpr 类名 → 键（lower flat）
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
                prev = self.classes[key]
                if not getattr(prev, "incomplete", False) or \
                        getattr(cd, "forward", False):
                    raise SemaError("E0", f"类 {key} 重复定义")
                # 前向声明后的完整定义：填充
            cd.key = key
            if key not in self.classes:
                ci = ClassInfo(cd.name, None)
                ci.key = key
                ci.incomplete = bool(getattr(cd, "forward", False))
                self.classes[key] = ci
            else:
                self.classes[key].key = key
        # Phase B：类型/主体规范化（ns 内短名 → 键；基类解析）
        for cd in decls:
            if getattr(cd, "forward", False):
                self.classes[cd.key].incomplete = True
                continue                     # 不完整类型：无体可规范
            ns = cd.ns
            for meth in cd.methods:
                self.canon_ty(meth.ret, ns)
                for (pty, _pn) in meth.params:
                    self.canon_ty(pty, ns)
                for ss in (meth.body or []):   # 纯虚声明无体
                    self.canon_stmt(ss, ns)
            for ctor in cd.ctors:
                for (pty, _pn) in ctor.params:
                    self.canon_ty(pty, ns)
                for ss in ctor.body:
                    self.canon_stmt(ss, ns)
            for dt in getattr(cd, "dtors", []):
                for ss in dt.body:
                    self.canon_stmt(ss, ns)
            cd.bases = getattr(cd, "bases", None) or                 ([cd.base] if cd.base else [])
            cd.bases_keys = []
            for b in cd.bases:
                bkey = self.resolve_class_name(b, ns)
                if bkey is None:
                    raise SemaError("E0", f"基类 {b} 未定义")
                if bkey == cd.key:
                    raise SemaError("E0", f"类 {cd.key} 继承自身"
                                    f"（审计 F1：曾致 _root_of 死循环）")
                cd.bases_keys.append(bkey)
            cd.base_key = cd.bases_keys[0] if cd.bases_keys else None
        # Phase C：完整构建 + mangling 守卫
        for cd in decls:
            if getattr(cd, "forward", False):
                continue
            info = self.classes[cd.key]
            info.bases = list(getattr(cd, "bases_keys", []))
            if cd.base:
                info.base = getattr(cd, "base_key", None)
            for (mn, mty) in cd.members:
                info.members[mn] = mty
            for meth in cd.methods:
                if not hasattr(meth, "is_static"):
                    meth.is_static = False
                if not hasattr(meth, "access"):
                    meth.access = "public"    # 0.0.28 默认（旧路径兜底）
            info.methods.extend(cd.methods)
            info.ctors.extend(cd.ctors)
            info.member_access.update(getattr(cd, "member_access", {}))
            info.static_access.update(getattr(cd, "static_access", {}))
            for (sn, sty) in getattr(cd, "statics", []):
                info.static_members[sn] = sty
            if len(getattr(cd, "dtors", [])) > 1:
                raise SemaError("E0", f"{cd.key} 多个析构（至多一个）")
            if getattr(cd, "dtors", []):
                info.dtor = cd.dtors[0]
            info.has_op_new = any(getattr(m, "is_special", None) == "new"
                                  for m in cd.methods)
            info.has_op_delete = any(getattr(m, "is_special", None) == "delete"
                                     for m in cd.methods)
            # 0.0.24 vtable 槽序；0.0.44 多基并集（位 0 子树全序→位 1 新槽…）
            if info.bases:
                info.vslots = []
                for b in info.bases:
                    for sl in self.classes[b].vslots:
                        if sl in info.vslots:
                            raise SemaError(
                                "E0", f"{key} 多基类同名虚槽 {sl}"
                                f"（子集拒绝——二义分派）")
                        info.vslots.append(sl)
            elif info.base:
                info.vslots = list(self.classes[info.base].vslots)
            for meth in cd.methods:
                if meth.name in info.vslots:
                    meth.is_virtual = True   # 重写虚槽=隐式虚（C++ 法）
                if getattr(meth, "is_virtual", False) and \
                        meth.name not in info.vslots:
                    info.vslots.append(meth.name)
            seen = {}
            for meth in cd.methods:
                if getattr(meth, "is_special", None):
                    meth.mname = meth.name          # 约定名由 lower 发射
                    continue
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

    def _access_ok(self, owner, level):
        """0.0.28 访问控制可见性：private=仅 owner 自身方法；protected=
        owner 链上派生类方法；public=任意。偏差（文档化）：protected 经
        基指针访问未细分；friend 授权未实现（0.0.22 no-op 延续）；
        ctor/dtor 访问不强制。"""
        if level == "public":
            return True
        cur = getattr(self.cur_cls, "key", None) \
            if self.cur_cls is not None else None
        if cur is None:
            return False
        if cur == owner:
            return True
        if level == "protected":
            c = cur
            while c:
                if c == owner:
                    return True
                c = self.classes[c].base
        return False

    def _guard_method_access(self, owner, meth):
        """方法访问守卫（0.0.28）——复用成员守卫通道。"""
        lvl = getattr(meth, "access", "public")
        if not self._access_ok(owner, lvl):
            cn = {"private": "私有", "protected": "受保护"}.get(lvl, lvl)
            raise SemaError("E0",
                            f"访问控制：{owner}::{meth.name} 是{cn}方法，"
                            f"当前语境不可访问")

    def _guard_access(self, owner, name, table):
        lvl = table.get(name, "public")
        if not self._access_ok(owner, lvl):
            cn = {"private": "私有", "protected": "受保护"}.get(lvl, lvl)
            raise SemaError("E0",
                            f"访问控制：{owner}::{name} 是{cn}成员，"
                            f"当前语境不可访问")

    def chain_impl(self, ckey, slot):
        """链上查找槽 impl——body None（纯虚声明）不算 impl。"""
        c = ckey
        while c:
            for m in self.classes[c].methods:
                if m.name == slot and m.body is not None:
                    return m
            c = self.classes[c].base
        return None

    def _is_ancestor(self, anc, cur):
        """anc 是否 cur 的祖先（含相等）——0.0.33 dynamic_cast 分型。"""
        c = cur
        while c:
            if c == anc:
                return True
            c = self.classes[c].base
        return False

    def is_abstract(self, ckey):
        """任一虚槽链上无 impl → 抽象类。"""
        info = self.classes[ckey]
        for slot in info.vslots:
            if self.chain_impl(ckey, slot) is None:
                return True
        return False

    def _is_derived_from(self, k, base_cls):
        """k（类键）是否派生自 base_cls（类名/键）——多基 DFS。"""
        if k is None or base_cls is None:
            return False
        seen = set()
        stack = [k]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            info = self.classes.get(cur)
            if info is None:
                continue
            if cur == base_cls or info.name == base_cls:
                return True
            for b in (getattr(info, "bases", None)
                      or ([info.base] if info.base else [])):
                stack.append(b)
        return False

    def _base_index(self, k, base_cls):
        """base_cls 在 k 的基类子对象序（位 0=0）。"""
        info = self.classes.get(k)
        if info is None:
            return None
        bkeys = getattr(info, "bases", None)             or ([info.base] if info.base else [])
        for i, b in enumerate(bkeys):
            if b == base_cls:
                return i
        return None

    def _ptr_int_guard(self, lt, rt, rhs):
        """审计 F6：指针↔整数隐式转换双向拒绝（0.0.31 只查了显式
        reinterpret_cast——隐式赋值/初始化漏查，静默错编实证）。
        Num 0 → 任何指针（零指针语义，test33 np=0 先例）。"""
        from cpp_parser import Num
        rb = rt.pointee if rt.kind == "ref" else rt
        lb = lt.pointee if lt.kind == "ref" else lt
        if lb.kind == "ptr" and rb.kind == "int":
            if not (isinstance(rhs, Num) and rhs.v == 0):
                raise SemaError("E0",
                                "整数→指针隐式转换"
                                "（reinterpret_cast=0.0.31 子集拒绝；"
                                "零指针字面量 0 除外）")
        if lb.kind == "int" and rb.kind == "ptr":
            raise SemaError("E0",
                            "指针→整数隐式转换"
                            "（reinterpret_cast=0.0.31 子集拒绝）")

    def _note_upcast(self, rhs, lt):
        """派生→基指针初始化/赋值：rhs=Un&(类对象) 且目标为基类指针
        → rhs.up_base=子对象序（位 0 不标注=零调整）。复杂接收形态
        （非 Id/This）=E0（子集）。"""
        from cpp_parser import Un, Id, This
        if not (isinstance(rhs, Un) and rhs.op == "&"):
            return
        if not (lt.kind == "ptr" and lt.pointee.kind == "class"):
            return
        ot = getattr(rhs.e, "ty", None)
        if ot is None or ot.kind != "class":
            return
        k = self.resolve_class_name(ot.name, self.cur_ns) or ot.name
        bp = lt.pointee.name
        if not self._is_derived_from(k, bp):
            return
        idx = self._base_index(k, bp)
        if idx is None or idx == 0:
            return                      # 位 0：&obj 零调整（现状）
        if not isinstance(rhs.e, (Id, This)):
            raise SemaError("E0",
                            f"上转型接收形态限 Id/This（{bp}* = &复杂表达式"
                            f"=子集边界）")
        rhs.up_base = idx               # lower 发射 &recv.__base{idx}

    def find_member(self, cname, name):
        r = self.find_member_owner(cname, name)
        return r[0] if r else None

    def find_member_owner(self, cname, name):
        """(Type, 声明该成员的类名)——cast 一致性的归属喂点。
        0.0.43 多基类 DFS（声明序）；两分支同名=E0 二义。"""
        matches = []
        seen = set()
        def dfs(k):
            if k is None or k in seen:
                return
            seen.add(k)
            info = self.classes.get(k)
            if info is None:
                return
            if name in info.members:
                matches.append((info.members[name], k))
            for b in (getattr(info, "bases", None)
                      or ([info.base] if info.base else [])):
                dfs(b)
        dfs(cname)
        if len(matches) > 1:
            raise SemaError("E0",
                            f"{cname}.{name} 成员二义"
                            f"（多基类同名字面——子集拒绝）")
        return matches[0] if matches else None

    def find_static_owner(self, cname, name):
        c = cname
        while c:
            info = self.classes[c]
            if name in info.static_members:
                return (info.static_members[name], c)
            c = info.base
        return None

    def find_method(self, cname, name, nargs):
        r = self.find_method_owner(cname, name, nargs)
        return r[0] if r else None

    def find_method_owner(self, cname, name, nargs):
        """(MethodDecl, 声明类名)——跨类调用的 cast 喂点。
        0.0.11：缺省实参可补全；歧义报错。
        0.0.24：override 语义——查找止于首个含该方法名的类（派生遮蔽基类）。
        0.0.43 多基类 DFS（声明序：位 0 子树全查 → 位 1 子树——先序先得
        即遮蔽）；同层歧义仍 E0。"""
        hits = []
        seen = set()

        def visit(c):
            if c is None or c in seen:
                return False              # False=未命中声明
            seen.add(c)
            cinfo = self.classes.get(c)
            if cinfo is None:
                raise SemaError("E0",
                                f"类 {c} 未定义（{name} 解析——审计 F7）")
            found_here = False
            for m in cinfo.methods:
                if m.name != name:
                    continue
                found_here = True
                n = len(m.params)
                if n == nargs:
                    hits.append((m, c))
                elif n > nargs and all(
                        m.defaults[i] is not None for i in range(nargs, n)):
                    hits.append((m, c))
            if found_here and hits:
                return True               # 本类有声明：遮蔽基类
            info = self.classes[c]
            for b in (getattr(info, "bases", None)
                      or ([info.base] if info.base else [])):
                if visit(b):
                    return True
            return False

        visit(cname)
        if len(hits) > 1:
            cands = ", ".join(f"{k[1]}::{name}/{len(k[0].params)}参"
                              for k in hits)
            raise SemaError("E0", f"{name}({nargs} 参) 重载歧义：{cands}")
        return hits[0] if hits else None

    def build_funcs(self, funcs):
        """自由函数符号表：ns 函数以 ns::name 为键（限定调用 + ns 内裸调）。
        审计 F7：签名类型未定义 → E0（裸 T 曾静默注册、体内算子解析
        才 KeyError crash——诚实于声明处）。"""
        self.funcs = {}            # call-name → [MethodDecl…]
        self.funcs_list = funcs
        for f in funcs:
            fkey = getattr(f, "key", None) or f.name
            for (pty, _pn) in f.params:
                if pty.kind == "class" and \
                        self.resolve_class_name(pty.name, None) is None:
                    raise SemaError("E0",
                                    f"{fkey}: 形参类型 {pty.name} 未定义")
            if f.ret.kind == "class" and \
                    self.resolve_class_name(f.ret.name, None) is None:
                raise SemaError("E0",
                                f"{fkey}: 返回类型 {f.ret.name} 未定义")
        for f in funcs:
            clink = getattr(f, "c_link", False)
            if clink:
                # C 链接面（0.0.30 放宽：指针=合法 C ABI 面——void* 分配器对接）
                if f.ret.kind not in ("int", "char", "bool", "void") \
                        and f.ret.kind != "ptr":
                    raise SemaError("E0",
                                    f'extern "C" {f.name} 返回类型限基本型/指针')
                for (pty, _pn) in f.params:
                    if pty.kind not in ("int", "char", "bool") \
                            and pty.kind != "ptr":
                        raise SemaError("E0",
                                        f'extern "C" {f.name} 形参限基本型/指针'
                                        f"（引用/对象=后续版本）")
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
    def build_templates(self, tdecls):
        """模板注册（体延迟——实例化时按具体类型 check）。
        FuncTemplate → 函数模板；ClassTemplate → 类模板（0.0.41）。"""
        for t in tdecls:
            if isinstance(t, ClassTemplate):
                self.class_templates[t.decl.name] = t
            else:
                self.templates[t.decl.name] = t

    def instantiate(self, fname, concs):
        """单态化：deepcopy 模板体 + 类型参数→concrete 替换 + 重 check +
        注册（0.0.40 多类型：concs 与 tvars 对齐，类名 :: 平化为 _）。"""
        tmpl = self.templates[fname]
        inst_name = fname + "".join(
            "__" + type_name(c).replace("::", "_") for c in concs)
        if inst_name in self.instantiated:
            return self.instantiated[inst_name]
        clone = _copy.deepcopy(tmpl.decl)
        clone.name = inst_name
        self._subst(clone, list(zip(tmpl.tvars, concs)))
        clone.mname = inst_name                     # 约定名（单态化）
        clone.call_key = inst_name
        self.instantiated[inst_name] = clone
        # 按具体类型重 check（cur_method=实例）
        self.cur_cls, self.cur_method = None, clone
        self.cur_ns = None
        sc = Scope(self.gscope)
        for (pty, pn) in clone.params:
            sc.declare(pn, pty)
        self.fn_dtors = []
        self.check_stmts(clone.body, sc, False)
        clone.fn_dtors = list(self.fn_dtors)
        self.funcs.setdefault(clone.call_key, []).append(clone)
        self.funcs_list.append(clone)
        return clone

    def _subst(self, decl, pairs):
        """类型参数→concrete 替换（ret/params/body 类型通道；0.0.40 多对）。"""
        for (tvar, conc) in pairs:
            if decl.ret.kind == "class" and decl.ret.name == tvar:
                decl.ret = _copy.deepcopy(conc)
            for i, (pty, pn) in enumerate(decl.params):
                if pty.kind == "class" and pty.name == tvar:
                    decl.params[i] = (_copy.deepcopy(conc), pn)
            for ss in (decl.body or []):   # 声明形态方法无体（0.0.41）
                self._subst_stmt(ss, tvar, conc)

    def _subst_stmt(self, s, tvar, conc, prefix=None, inst_flat=None):
        if isinstance(s, VarDecl):
            if s.ty.kind == "class" and s.ty.name == tvar:
                s.ty = _copy.deepcopy(conc)
        elif isinstance(s, CtorStmt):
            # 0.0.41 体内 Box<T>(…) → Box__int(…)（限同类模板前缀）
            if prefix and s.cls == f"{prefix}__{tvar}":
                s.cls = inst_flat
        elif isinstance(s, New):
            if prefix and s.cls == f"{prefix}__{tvar}":
                s.cls = inst_flat
        elif isinstance(s, Try):
            for x in s.try_body:
                self._subst_stmt(x, tvar, conc, prefix, inst_flat)
            for (_ct, _cv, cb) in s.catches:
                for x in cb:
                    self._subst_stmt(x, tvar, conc, prefix, inst_flat)

    def check(self, decls, funcs, gdecls, sdefs, tdecls, main):
        self.decls_list = decls       # 别名：实例类就地注入（lower 同列表）
        self.build_classes(decls)
        self.build_funcs(funcs)
        self.build_templates(tdecls)
        self.gscope = Scope()
        for (gn, gty, _gi) in gdecls:
            self.gscope.declare(gn, gty)
        self.gdecls = gdecls
        for (cn, nm, ty, _init) in sdefs:
            ckey = self.resolve_class_name(cn, None)
            if ckey is None:
                raise SemaError("E0", f"静态成员定义 {cn}::{nm}：类未定义")
            if nm not in self.classes[ckey].static_members:
                raise SemaError("E0",
                                f"{ckey} 无静态成员 {nm}（需类内声明）")
        for cd in decls:
            self.cur_cls, self.cur_method = cd, None
            self.cur_ns = cd.ns
            visible = self.visible_method_names(cd.key)
            for ctor in cd.ctors:
                if ctor.body is not None:     # 声明 ctor 无体（0.0.41）
                    rewrite_implicit_this(ctor.body, visible)
            for meth in cd.methods:
                if meth.body is not None:
                    rewrite_implicit_this(meth.body, visible)
            if self.classes[cd.key].dtor:
                rewrite_implicit_this(self.classes[cd.key].dtor.body, visible)
            for ctor in cd.ctors:
                sc = Scope(self.gscope)
                for (pty, pn) in ctor.params:
                    sc.declare(pn, pty)
                for (tgt, args) in ctor.inits:
                    btgt = self.resolve_class_name(tgt, cd.ns) or tgt
                    if btgt in getattr(cd, "bases_keys", []):
                        tgt = btgt           # 规范化为限定键（lower 用）
                    elif self.find_member(cd.key, tgt) is None:
                        raise SemaError("E0",
                                        f"构造初始化目标 {tgt} 不是 "
                                        f"{cd.name} 成员/基类")
                    for a in args:
                        self.check_expr(a, sc, False)
                if ctor.body is not None:    # 声明 ctor：体=外联定义（0.0.41）
                    self.check_stmts(ctor.body, sc, False)   # 0.0.29 修：体检查
                    # 曾错挂在 init-args 循环内（无 init-list 的 ctor 体从未被
                    # 检查——既有测试全带 init-list 掩盖的潜伏 bug）
            for meth in cd.methods:
                self.cur_method = meth
                sc = Scope(self.gscope)
                for (pty, pn) in meth.params:
                    sc.declare(pn, pty)   # 0.0.21 指针形参放行
                self.fn_dtors = []
                if meth.body is not None:      # 纯虚声明无体
                    self.check_stmts(meth.body, sc, meth.is_const)
                meth.fn_dtors = list(self.fn_dtors)
            if self.classes[cd.key].dtor:
                self.cur_method = None
                sc = Scope(self.gscope)
                self.check_stmts(self.classes[cd.key].dtor.body, sc, False)
        for f in self.funcs_list:
            self.cur_cls, self.cur_method = None, f
            self.cur_ns = None            # extern "C" 仅全局（子集）
            self.fn_dtors = []
            if f.body is None:
                continue                  # 原型声明：无体可查
            sc = Scope(self.gscope)
            for (pty, pn) in f.params:
                sc.declare(pn, pty)
            self.check_stmts(f.body, sc, False)
            f.fn_dtors = list(self.fn_dtors)
        self.cur_cls, self.cur_method = None, None
        self.cur_ns = None
        if main is not None:
            self.fn_dtors = []
            self.check_stmts(main, Scope(self.gscope), False)
            self.main_dtors = list(self.fn_dtors)
        # 0.0.10 生命周期扩展：const T& 绑临时量 → 调用者作用域具名物化
        self._tmpn = 0
        for cd in decls:
            for ctor in cd.ctors:
                self._mat_body(ctor.body)
            for meth in cd.methods:
                if meth.body is not None:
                    self._mat_body(meth.body)
            if self.classes[cd.key].dtor:
                self._mat_body(self.classes[cd.key].dtor.body)
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
                    raise SemaError("E0",
                                    f"引用声明 {s.name} 需初始化器")
                self.check_expr(s.init, sc, is_const)
                if not s.ty.pointee.is_const \
                        and not getattr(s.init, "lval", False) \
                        and s.init.ty.kind != "ref":
                    raise SemaError("N8",
                                    f"非 const 引用 {s.name} 绑临时量"
                                    f"（{_fmt(s.init)}）"
                                    f"——C++ 法：保持拒绝")
                # 物化改写在 check 末尾统一跑
            elif s.init is not None:
                it = self.check_expr(s.init, sc, is_const)
                self._ptr_int_guard(s.ty, it, s.init)   # 审计 F6
                self._note_upcast(s.init, s.ty)   # 0.0.43：声明类型为准
                # 调用初始化不再报错——lower 两步发射
            sc.declare(s.name, s.ty)
        elif isinstance(s, CtorStmt):
            ckey = self.resolve_class_name(s.cls, self.cur_ns)
            if ckey is None:
                raise SemaError("E0", f"类 {s.cls} 未定义")
            if self.is_abstract(ckey):
                raise SemaError("E0",
                                f"抽象类 {ckey} 不可实例化"
                                f"（含未覆盖的纯虚函数）")
            s.cls = ckey
            hits = []
            for c in self.classes[s.cls].ctors:
                if _is_copy_ctor(c, ckey):
                    continue          # 拷贝 ctor 不参与 X(args) 形态（C++ 法）
                n = len(c.params)
                if n == len(s.args) or (n > len(s.args) and all(
                        c.defaults[i] is not None
                        for i in range(len(s.args), n))):
                    hits.append(c)
            if len(hits) > 1:
                raise SemaError("E0",
                                f"{s.cls} 构造歧义（{len(s.args)} 实参）")
            if not hits:
                raise SemaError("E0",
                                f"{s.cls} 无 {len(s.args)} 参构造")
            s.sig = hits[0]
            while len(s.args) < len(s.sig.params):      # 默认补全
                s.args.append(s.sig.defaults[len(s.args)])
            for a in s.args:
                self.check_expr(a, sc, is_const)
            sc.declare(s.var, tobj(s.cls))
            if self.classes[ckey].dtor:
                self.fn_dtors.append((s.var, ckey))    # 0.0.37 RAII 追踪
        elif isinstance(s, Throw):
            EH_FLAG[0] = True                 # 0.0.35 序言发射门控
            if s.expr is None:
                if CUR_CATCH[0] == 0:
                    raise SemaError("E0",
                                    "bare rethrow 须在 catch 内")
                return                        # 0.0.36 转正（无表达式可查）
            self.check_expr(s.expr, sc, is_const)
            if s.expr.ty.kind == "class" and getattr(s.expr, "lval", False):
                s.throw_cls = s.expr.ty.name    # 0.0.38 类对象抛出（左值）
            elif s.expr.ty.kind not in ("int", "char"):
                raise SemaError("E0",
                                f"throw 操作数限 int/char/类左值"
                                f"（得 {s.expr.ty.kind}）——类右值抛出=E0 子集")
        elif isinstance(s, Try):
            EH_FLAG[0] = True
            self.check_stmts(s.try_body, Scope(sc), is_const)
            CUR_CATCH[0] += 1
            for (cty, cvar, cbody) in s.catches:
                if cty is None:
                    self.check_stmts(cbody, Scope(sc), is_const)   # catch(...)
                    continue
                if cty.kind == "ref" and cty.pointee.kind == "class":
                    pass                          # 0.0.38 类引用捕获 ✓
                elif cty.kind not in ("int", "char"):
                    raise SemaError("E0",
                                    f"catch 类型限 int/char/类引用"
                                    f"（得 {cty.kind}）——按值捕获=E0 子集")
                csc = Scope(sc)
                if cty.kind == "ref":
                    csc.declare(cvar, cty)            # 类引用形参（0.0.38）
                else:
                    csc.declare(cvar, tval(cty.kind))
                self.check_stmts(cbody, csc, is_const)
            CUR_CATCH[0] -= 1
        elif isinstance(s, CopyInit):
            ckey = self.resolve_class_name(s.cls, self.cur_ns)
            if ckey is None:
                raise SemaError("E0", f"类 {s.cls} 未定义")
            if self.is_abstract(ckey):
                raise SemaError("E0",
                                f"抽象类 {ckey} 不可实例化"
                                f"（含未覆盖的纯虚函数）")
            s.cls = ckey
            rt = self.check_expr(s.init, sc, is_const)
            base_t = rt.pointee if rt.kind == "ref" else rt
            if base_t.kind != "class" or base_t.name != ckey:
                # 0.0.30 隐式转换：单参非拷贝 ctor（explicit 拒绝）
                if base_t.kind in ("int", "char"):
                    hits = [c for c in self.classes[ckey].ctors
                            if not _is_copy_ctor(c, ckey)
                            and len(c.params) == 1]
                    if len(hits) != 1:
                        raise SemaError("E0",
                                        f"拷贝初始化无唯一转换构造：{ckey}"
                                        f" ← {base_t.kind}")
                    if getattr(hits[0], "is_explicit", False):
                        raise SemaError("E0",
                                        f"explicit 构造 {ckey} 禁止隐式转换"
                                        f"（{ckey} ← {base_t.kind}）")
                    s.conv = hits[0]
                    s.user_copy = False
                    s.ty = tobj(ckey)
                    sc.declare(s.var, tobj(ckey))
                    return
                from cpp_types import type_name as _tn
                raise SemaError("E0",
                                f"拷贝初始化类型不匹配：{ckey} ← "
                                f"{_tn(base_t)}")
            s.user_copy = any(_is_copy_ctor(c, ckey)
                              for c in self.classes[ckey].ctors)
            s.ty = tobj(ckey)                # lower scope 登记（0.0.29）
            if self.classes[ckey].dtor:
                self.fn_dtors.append((s.var, ckey))    # 0.0.37 RAII 追踪
            if s.user_copy and not getattr(s.init, "lval", False):
                raise SemaError("E0",
                                "用户拷贝构造仅支持左值源"
                                "（右值物化=后续版本）")
            sc.declare(s.var, tobj(ckey))
        elif isinstance(s, DeleteStmt):
            et = self.check_expr(s.expr, sc, is_const)
            if et.kind != "ptr" or et.pointee.kind != "class":
                raise SemaError("E0",
                                "delete 操作数须为对象指针（Box*）——子集内"
                                "delete 仅回收对象存储（无 dtor 语法）")
            s.ty = et
            dinfo = self.classes.get(et.pointee.name)
            s.dtor_key = dinfo.key if (dinfo and dinfo.dtor) else None
            s.op_delete = dinfo.key \
                if (dinfo and getattr(dinfo, "has_op_delete", False)) \
                else None
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
                raise SemaError("R1",
                                "引用返回绑非左值——临时量物化"
                                "=0.0.9 生命周期扩展")
            if meth.is_const:
                raise SemaError("K1",
                                "const 方法引用返回绑自身成员"
                                "——写通道穿透 const")
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
            if getattr(self.cur_method, "is_static", False):
                raise SemaError("E0", "static 方法无 this")
            e.ty, e.lval = tobj(self._cur_key(), is_const=is_const), True
        elif isinstance(e, Id):
            vt = sc_find(sc, e.name)
            if vt is not None:
                e.ref_kind, e.ty, e.lval = "var", vt, True
            elif self.cur_cls is not None:
                hit = self.find_member_owner(self._cur_key(), e.name)
                shit = self.find_static_owner(self._cur_key(), e.name)
                if hit is not None:
                    mt, owner = hit
                    if getattr(self.cur_method, "is_static", False):
                        raise SemaError("E0",
                                        f"静态方法引用非静态成员 {e.name}")
                    self._guard_access(owner, e.name,
                                       self.classes[owner].member_access)
                    e.ref_kind, e.ty, e.lval, e.owner = "member", mt, True, owner
                elif shit is not None:
                    mt, owner = shit
                    self._guard_access(owner, e.name,
                                       self.classes[owner].static_access)
                    e.ref_kind, e.ty, e.lval, e.owner = "static", mt, True, owner
                else:
                    ckey = self.resolve_class_name(e.name, self.cur_ns)
                    if ckey is None:
                        raise SemaError("E0", f"未声明标识符 {e.name!r}")
                    e.ref_kind, e.ty, e.lval = "class", tobj(ckey), False
                    e.cname = ckey              # 0.0.32 类名引用（typeid 面）
            elif "::" in e.name:
                # Cls::static_member 限定访问
                parts2 = e.name.rsplit("::", 1)
                ckey = self.resolve_class_name(parts2[0], self.cur_ns)
                shit = self.find_static_owner(ckey, parts2[1]) if ckey else None
                if shit is None:
                    raise SemaError("E0", f"未声明标识符 {e.name!r}")
                mt, owner = shit
                self._guard_access(owner, parts2[1],
                                   self.classes[owner].static_access)
                e.ref_kind, e.ty, e.lval, e.owner = "static", mt, True, owner
                e.name = parts2[1]           # 短名（lower 拼全局名用）
            else:
                ckey = self.resolve_class_name(e.name, self.cur_ns)
                if ckey is None:
                    raise SemaError("E0", f"未声明标识符 {e.name!r}")
                e.ref_kind, e.ty, e.lval = "class", tobj(ckey), False
                e.cname = ckey              # 0.0.32 类名引用（含限定名）
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
            shit = self.find_static_owner(ot.name, e.name)
            if hit is None and shit is not None:
                mt, e.owner = shit
                self._guard_access(e.owner, e.name,
                                   self.classes[e.owner].static_access)
                e.ref_kind, e.ty, e.lval = "static", mt, True
                return e.ty
            if hit is None:
                raise SemaError("E0", f"{ot.name} 无成员 {e.name!r}")
            mt, e.owner = hit
            self._guard_access(e.owner, e.name,
                               self.classes[e.owner].member_access)
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
            self._guard_method_access(e.sig_owner, meth)   # 0.0.28
            # 0.0.43 接收者子对象调整：声明类≠接收者类且非位 0 → 标注
            # recv_base（lower 发射 &recv.__base{i}）；虚调用/指针接收者
            # 不标注（虚=根槽共享；指针=已是指向子对象的类型）
            if ot.name != e.sig_owner                     and not getattr(meth, "is_static", False)                     and not getattr(meth, "is_virtual", False)                     and ot.kind == "class":
                rkey = self.resolve_class_name(ot.name, self.cur_ns)                     or ot.name
                idx = self._base_index(rkey, e.sig_owner)
                if idx:
                    e.recv_base = idx
            # 0.0.26：cfront 共享槽布局落地——虚调用解锁
            if getattr(meth, "is_static", False):
                e.static_call = True
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
            if self.is_abstract(ckey):
                raise SemaError("E0",
                                f"抽象类 {ckey} 不可实例化"
                                f"（含未覆盖的纯虚函数）")
            e.cls = ckey
            hits = []
            for c in self.classes[ckey].ctors:
                if _is_copy_ctor(c, ckey):
                    continue
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
            ckey = self.resolve_class_name(e.name, self.cur_ns)
            if ckey is not None:
                # 0.0.17 显式构造表达式：Vec(x, y)（类名+括号=ctor 调用）
                hits = [c for c in self.classes[ckey].ctors
                        if len(c.params) == len(e.args)]
                if len(hits) != 1:
                    raise SemaError("E0",
                                    f"{ckey} 显式构造：无/多 {len(e.args)} 参构造")
                e.sig = hits[0]
                for a in e.args:
                    self.check_expr(a, sc, is_const)
                    a.bind = "value"
                e.ty, e.lval = tobj(ckey), False
                return e.ty
            if e.name not in self.templates and \
                    self.resolve_class_name(e.name, self.cur_ns) is None:
                if e.targs is not None:
                    # 审计 F3：f<int>(1) 曾静默忽略 targs 按普通调用编译
                    raise SemaError("E0",
                                    f"{e.name} 非模板函数"
                                    f"（显式模板实参不适用）")
                pass
            elif e.name in self.templates:
                # 0.0.39/40 模板调用：显式实参或按参数位推导 → 单态化实例
                tmpl = self.templates[e.name]
                tvars = tmpl.tvars
                if e.targs is not None:
                    # 0.0.40 显式模板实参；0.0.42 缺省位默认补齐
                    if len(e.targs) > len(tvars):
                        raise SemaError(
                            "E0", f"模板 {e.name} 显式实参数 "
                            f"{len(e.targs)} ≠ 类型参数数 {len(tvars)}")
                    concs = list(e.targs)
                    if len(concs) < len(tvars):
                        dfls = tmpl.tvar_dfls
                        miss = range(len(concs), len(tvars))
                        if any(dfls[i][1] is None for i in miss):
                            raise SemaError(
                                "E0", f"模板 {e.name} 显式实参数 "
                                f"{len(concs)} 不足且缺省位无默认")
                        concs = concs + [_copy.deepcopy(dfls[i][1])
                                         for i in miss]
                    inst = self.instantiate(e.name, concs)
                    while len(e.args) < len(inst.params):
                        e.args.append(inst.defaults[len(e.args)])
                    self.check_args(e.args, inst, sc, is_const)
                    e.sig, e.ty, e.lval = inst, inst.ret, False
                    return e.ty
                # 推导：param i 类型 Tj ← arg i（去 ref）；同 Tj 多参数须一致
                if len(e.args) > len(tmpl.decl.params):
                    raise SemaError("E0",
                                    f"模板 {e.name} 实参数超形参数")
                arg_tys = []
                for a in e.args:
                    at = self.check_expr(a, sc, is_const)
                    arg_tys.append(
                        at.pointee if at.kind == "ref" else at)
                concs = [None] * len(tvars)
                for i, (pty, _pn) in enumerate(tmpl.decl.params):
                    if i >= len(arg_tys):
                        break   # 默认实参不参与推导（子集边界）
                    if pty.kind == "class" and pty.name in tvars:
                        j = tvars.index(pty.name)
                        if concs[j] is None:
                            concs[j] = arg_tys[i]
                        elif concs[j] != arg_tys[i]:
                            raise SemaError(
                                "E0",
                                f"模板 {e.name} 实参类型不一致"
                                f"（{type_name(concs[j])} vs "
                                f"{type_name(arg_tys[i])}）")
                if any(c is None for c in concs):
                    raise SemaError("E0",
                                    f"模板 {e.name} 无法推导全部类型参数"
                                    f"（默认实参不参与推导）")
                inst = self.instantiate(e.name, concs)
                while len(e.args) < len(inst.params):
                    e.args.append(inst.defaults[len(e.args)])
                self.check_args(e.args, inst, sc, is_const)
                e.sig, e.ty, e.lval = inst, inst.ret, False
                return e.ty
            if e.name == "typeid":
                # 0.0.32 RTTI·typeid：类名=静态 ti / 对象左值=动态 ti（有虚类）
                if len(e.args) != 1:
                    raise SemaError("E0", "typeid 恰一操作数")
                arg = e.args[0]
                at = self.check_expr(arg, sc, is_const)
                ab = at.pointee if at.kind == "ref" else at
                if getattr(arg, "ref_kind", None) == "class":
                    e.ti_kind, e.ti_class = "static", arg.cname
                elif ab.kind == "class" and getattr(arg, "lval", False) \
                        and self.classes.get(ab.name) \
                        and self.classes[ab.name].vslots:
                    e.ti_kind, e.ti_class = "dyn", ab.name
                elif ab.kind == "class":
                    e.ti_kind, e.ti_class = "static", ab.name
                else:
                    raise SemaError("E0",
                                    "typeid 操作数须为对象/类名"
                                    f"（得 {ab.kind}）")
                from cpp_types import tchar as _tch, tptr as _tp
                e.ty, e.lval = _tp(_tch(True)), False   # const char*
                return e.ty
            f = None
            if "::" in e.name:
                # Cls::static_method —— 限定静态方法
                parts2 = e.name.rsplit("::", 1)
                ckey = self.resolve_class_name(parts2[0], self.cur_ns)
                if ckey:
                    for m in self.classes[ckey].methods:
                        if m.name == parts2[1] and m.is_static and \
                                len(m.params) == len(e.args):
                            f = m
                            break
            if f is None:
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
            self._ptr_int_guard(lt, rt, e.rhs)   # 审计 F6
            self._note_upcast(e.rhs, lt)     # 0.0.43 派生→基指针偏移
            self.const_guard(e.lhs, is_const)
            if not getattr(e.lhs, "lval", False) and lt.kind != "ref":
                raise SemaError("E0", "赋值目标非左值且非引用")
            if lt.kind == "class":
                from cpp_types import type_name as _tn2
                rb = rt.pointee if rt.kind == "ref" else rt
                if rb.kind != "class" or rb.name != lt.name:
                    raise SemaError("E0",
                                    f"拷贝赋值类型不匹配：{lt.name} = "
                                    f"{_tn2(rb)}")
                hit = self.find_method_owner(lt.name, "operator=", 1)
                if hit is not None:
                    meth = hit[0]
                    node = MethodCall(e.lhs, "operator=", [e.rhs])
                    self.check_args(node.args, meth, sc, is_const)
                    node.sig, node.ty, node.lval = meth, meth.ret, False
                    node.sig_owner = hit[1]
                    e.call = node
                    e.ty, e.lval = node.ty, False
                    return e.ty
                # 无用户 operator= → 隐式成员级浅复制（C 结构体赋值直通）
            e.ty, e.lval = lt, False
        elif isinstance(e, Bin):
            self.check_expr(e.l, sc, is_const)
            self.check_expr(e.r, sc, is_const)
            lcls = e.l.ty.kind == "class"
            rcls = e.r.ty.kind == "class"
            if lcls or rcls:
                if e.op not in ("+", "==", "-", "<", ">", "!="):
                    raise SemaError("E0",
                                    f"操作符 {e.op} 对象子集外"
                                    f"（限算术：+ == - < > !=）")
                if not (lcls and rcls):
                    raise SemaError("E0",
                                    f"{e.op} 运算对象类型不匹配"
                                    f"（对象与基本型混算，无隐式转换）")
                self.resolve_binop(e, sc, is_const)
            else:
                e.ty, e.lval = tval("int"), False
        elif isinstance(e, NamedCast):
            ut = self.check_expr(e.expr, sc, is_const)
            tt = e.ty
            ub = ut.pointee if ut.kind == "ref" else ut
            if e.kind == "dynamic_cast":
                # 0.0.33：单继承 dynamic_cast（上行直达/下行运行期检查）
                if tt.kind != "ptr" or tt.pointee.kind != "class":
                    raise SemaError("E0", "dynamic_cast 目标须为类指针")
                if ub.kind != "ptr" or ub.pointee.kind != "class":
                    raise SemaError("E0", "dynamic_cast 源须为类指针")
                tgt_cls = tt.pointee.name
                src_cls = ub.pointee.name
                if tgt_cls == src_cls or self._is_ancestor(tgt_cls, src_cls):
                    e.dc_kind = "up"          # 上行/同类：恒真直达
                elif self._is_ancestor(src_cls, tgt_cls):
                    e.dc_kind = "down"        # 下行：运行期 ti 检查
                else:
                    raise SemaError("E0",
                                    f"dynamic_cast 无关类型："
                                    f"{src_cls} ↔ {tgt_cls}")
                e.ty, e.lval = tt, False
                return e.ty
            ub = ut.pointee if ut.kind == "ref" else ut
            num = ("int", "char", "bool")
            if e.kind == "static_cast":
                if not ((ub.kind in num and tt.kind in num)
                        or (ub.kind == "ptr" and tt.kind == "ptr")):
                    from cpp_types import type_name as _tn3
                    raise SemaError("E0",
                                    f"static_cast {_tn3(ub)}→{_tn3(tt)} 子集外"
                                    f"（ptr↔num 用 reinterpret_cast）")
            elif e.kind == "reinterpret_cast":
                if ub.kind in num and tt.kind in num:
                    raise SemaError("E0",
                                    "数值↔数值用 static_cast"
                                    "（reinterpret 限 ptr 面）")
            elif e.kind == "const_cast":
                if tt.kind != ub.kind or (tt.kind == "ptr"
                                          and tt.pointee.kind != ub.pointee.kind):
                    raise SemaError("E0",
                                    "const_cast 仅限 const 性变更")
            e.ty, e.lval = tt, False
        elif isinstance(e, Un):
            ut = self.check_expr(e.e, sc, is_const)
            if e.op == "&":
                if not getattr(e.e, "lval", False):
                    raise SemaError("E0", "& 操作数须为左值")
                from cpp_types import TPtr as _TP
                e.ty, e.lval = _TP(ut), False     # &x → 指针右值
            elif e.op == "*":
                from cpp_types import TPtr as _TP2
                if not isinstance(ut, _TP2):
                    raise SemaError("E0", "* 操作数须为指针")
                e.ty, e.lval = ut.pointee, True   # 解引用=左值（读+写穿）
            else:
                e.ty, e.lval = tval("int"), False
        else:
            raise SemaError("E0", f"未知表达式 {type(e).__name__}")
        return e.ty

    def resolve_binop(self, e, sc, is_const):
        """对象 +/-/== → 成员形态（lhs 1 参）优先，自由形态（2 参）兜底。
        就地构建调用节点存 e.call（lower 直发），Type/bind 全通道复用。"""
        name = "operator" + e.op
        hit = self.find_method_owner(e.l.ty.name, name, 1)
        if hit is not None:
            meth, owner = hit
            node = MethodCall(e.l, name, [e.r])
            if is_const and not meth.is_const and is_this_receiver(e.l):
                raise SemaError("K2",
                                f"const 方法经 this 调非 const 方法 {name}()")
            self.check_args(node.args, meth, sc, is_const)
            node.sig, node.ty, node.lval = meth, meth.ret, False
            node.sig_owner = owner
        else:
            f = self.find_free_func(name, 2)
            if f is None:
                raise SemaError("E0",
                                f"{type_name(e.l.ty)} 无 {name} 重载"
                                f"（成员 1 参/自由 2 参均未定义）")
            node = FreeCall(name, [e.l, e.r])
            self.check_args(node.args, f, sc, is_const)
            node.sig, node.ty, node.lval = f, f.ret, False
        e.call = node
        e.ty, e.lval = node.ty, False
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
                    raise SemaError("N8",
                                    f"非 const 引用形参绑定临时量"
                                    f"（{_fmt(a)}）——C++ 法：非常量"
                                    f"左值引用不延寿，保持拒绝")
            elif pty.kind == "class":
                if at.kind == "ref" or getattr(a, "lval", False):
                    a.bind = "addr"          # 对象左值取址
                else:
                    a.bind = "mat"           # 0.0.18：对象右值一律物化
                    # （0.0.17 的 E0 是误判——250=506 mod 256，POSIX 8 位
                    #  exit 通道；p18c-e 复验 trirv_cc 结构体赋值一直可用）
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
        if isinstance(s, CopyInit):
            self._mat_expr(s.init, h)        # 拷贝初始化源表达式（0.0.29）
        elif isinstance(s, VarDecl):
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
                ce = s.expr
                if isinstance(ce, FreeCall) and \
                        type(getattr(ce, "sig", None)).__name__ == "CtorDecl":
                    tn = self._fresh()       # return Vec(...) → 物化后按值返回
                    h.append((tn, ce))
                    rid = Id(tn)
                    rid.ty, rid.lval = ce.ty, True
                    s.expr = rid
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
                ot = getattr(e.obj, "ty", None)
                if ot is not None and ot.kind == "class" and \
                        not getattr(e.obj, "lval", False):
                    tn = self._fresh()       # 接收者右值物化（(a+b)+c）
                    h.append((tn, e.obj))
                    rid = Id(tn)
                    rid.ty, rid.lval = ot, True
                    e.obj = rid
                # 0.0.26：虚分派由 lower 类型套用拆链发射（无需 sema 提升）
                self._mat_expr(e.obj, h)     # FreeCall 无接收者
            for a in e.args:
                self._mat_expr(a, h)
            meth = getattr(e, "sig", None)
            if meth is None:
                return                          # typeid 等特殊 FreeCall（0.0.32）
            for i, a in enumerate(e.args):
                if getattr(a, "bind", None) == "mat":
                    tn = self._fresh()
                    h.append((tn, a))
                    rid = Id(tn)
                    rid.ty, rid.lval = a.ty, True   # 类临时=class VALUE 左值
                    e.args[i] = rid
                    e.args[i].bind = "addr"
        elif isinstance(e, Bin):
            if getattr(e, "call", None) is not None:
                self._mat_expr(e.call, h)    # 算子调用节点参数通道
            else:
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


def _vroot(c, classes):
    """虚方法所属类的根（vtbl 共享槽所在子对象）。"""
    while c and classes.get(c) and classes[c].base:
        c = classes[c].base
    return c or "Object"


def _is_copy_ctor(ctor, ckey):
    """拷贝 ctor 判定：单参 const Own&（0.0.29，非 const 形态=子集外）。"""
    if len(ctor.params) != 1:
        return False
    pty = ctor.params[0][0]
    return (pty.kind == "ref" and pty.pointee.kind == "class"
            and pty.pointee.name == ckey and pty.pointee.is_const)
