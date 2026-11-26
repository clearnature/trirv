#!/usr/bin/env python3
"""cpp_mangle —— Itanium 名字修饰（对齐 cpp_mangle.c 规划 §五）。

_ZN[K]<len(Class)><Class><len(meth)><meth>E<params>
  K  = const 成员函数（this 进签名——is_const 位由 sema 的 Type 驱动，
       0.0.7 正则版漏 K 的修正）
  R  = T&；RK = const T&（pointee.is_const 驱动）
  类型编码：int→i / char→c / bool→b / void→v / 类→<len>Name
  零参 → v（_ZNK5Boxo… 形式 _ZN3Box3getEv）
构造器：保留 X_ctor 约定（完整对象 C1/基类子对象 C2 之分=虚基/dtor 进场
后再切 ABI，见 ARCHITECTURE.md §五尾注）。
"""


def enc_type(ty):
    if ty.kind == "ref":
        k = "K" if ty.pointee.is_const else ""
        return "R" + k + enc_type(ty.pointee)
    if ty.kind == "class":
        return f"{len(ty.base)}{ty.base}"
    return {"int": "i", "char": "c", "bool": "b", "void": "v"}.get(ty.kind, ty.kind)


def mangle_method(cls_name, meth):
    """MethodDecl → Itanium 修饰名。K 位只看方法 const 性（this 限定）。"""
    prefix = "_ZNK" if meth.is_const else "_ZN"
    enc = "".join(enc_type(pty) for (pty, _pn) in meth.params)
    return f"{prefix}{len(cls_name)}{cls_name}{len(meth.name)}{meth.name}E" + (enc or "v")
