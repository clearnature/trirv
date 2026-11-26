#!/usr/bin/env python3
"""cpp_types —— 类型层 ADT（参照 sov-math 强类型纪律）。

sov-math 对照（hs/sov-math README「强类型化对照表」精神）：
  data Trit = T0 | T1 | T2    →  类型=构造子集合，非法组合不可表达
  newtype Q16                 →  TClass 携带 name，与裸串隔离
  GADT MathError 带证据       →  SemaError{code, msg} 证据携带
  投影跨边界（toTritIndex）   →  .kind 投影仅供 lower/mangle 边界（C 形态）

被构造层消灭的非法状态（正则/v0.0.8 时代全部可表示，逐一封死）：
  ref 无 pointee / class 无 name / int 带多余字段 /
  ref(ref(int))（C++ 禁引用的引用）/ const void / ref 顶层 const
  （TRef.is_const = pointee 派生，不可独立赋值）
"""


class TypeError_(Exception):
    """类型构造违例——编译期（transpiler 自身）即失败，不进 sema。"""


class CType:
    _kind = "?"                          # 投影键（边界层用）

    def __init__(self, is_const=False):
        self.is_const = is_const

    @property
    def kind(self):
        return self._kind                # 库内勿用——边界投影（见头注）

    def _reject_extra(self, **kw):
        if kw:
            raise TypeError_(
                f"{type(self).__name__} 不接受字段 {sorted(kw)}——"
                f"非法状态不可表达")

    def __eq__(self, other):
        return (type(self) is type(other)
                and self.is_const == other.is_const
                and getattr(self, "name", None) == getattr(other, "name", None)
                and getattr(self, "pointee", None) == getattr(other, "pointee", None))

    def __hash__(self):
        return hash((type(self).__name__, self.is_const,
                     getattr(self, "name", None),
                     getattr(self, "pointee", None)))

    def __repr__(self):
        c = "const " if self.is_const else ""
        if isinstance(self, TClass):
            return f"{c}{self.name}"
        if isinstance(self, TRef):
            return f"{self.pointee!r}&"          # const 已在 pointee 显示
        return f"{c}{self._kind}"


class TBool(CType):
    _kind = "bool"


class TInt(CType):
    _kind = "int"


class TChar(CType):
    _kind = "char"


class TVoid(CType):
    _kind = "void"

    def __init__(self):
        super().__init__(False)          # const void 不可表达：无 is_const 形参


class TClass(CType):
    _kind = "class"
    __slots__ = ("name",)

    def __init__(self, name, is_const=False):
        if not isinstance(name, str) or not name:
            raise TypeError_("TClass 需要类名——匿名类对象不可表达")
        super().__init__(is_const)
        self.name = name


class TRef(CType):
    """引用：自身无 const 位——is_const = pointee 派生（C++ 语义：
    const T& 的 const 在被引类型上，引用顶层 const 无意义）。"""
    _kind = "ref"
    __slots__ = ("pointee",)

    def __init__(self, pointee):
        if not isinstance(pointee, CType):
            raise TypeError_("TRef 需要 CType pointee——ref 无 pointee 不可表达")
        if isinstance(pointee, TRef):
            raise TypeError_("引用的引用不可表达（C++ 禁）")
        self.pointee = pointee      # 不触 super().__init__——is_const 是
        #                             派生 property（无底层槽位），setter 只拦外部赋值

    @property
    def is_const(self):
        return self.pointee.is_const     # 派生：不可独立赋值

    @is_const.setter
    def is_const(self, _):
        raise TypeError_("ref 顶层 const 不可表达——const 在 pointee 上")


# ---- 基本型速造（含 const 形态缓存不必——构造已是零成本） ----
def tint(is_const=False):
    return TInt(is_const)


def tchar(is_const=False):
    return TChar(is_const)


def tbool(is_const=False):
    return TBool(is_const)


def tvoid():
    return TVoid()


def tcls(name, is_const=False):
    return TClass(name, is_const)


def tref(pointee):
    return TRef(pointee)


def type_name(ty):
    """边界投影：C 形/报错共用的类型名（'int'/'char'/'bool'/'void'/类名）。"""
    if isinstance(ty, TRef):
        return type_name(ty.pointee)
    if isinstance(ty, TClass):
        return ty.name
    return ty._kind
