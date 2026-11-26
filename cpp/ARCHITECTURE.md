# C++ 前端架构（Route B：cfront 式 C++→C 转译器）

> 版本管理：0.0.1 → 0.0.2 → ... → 0.0.100 → 0.1.0 → ...
> 每个增量版本 = 一个可编译可验证的功能点。
> 语言：前端用 **C** 写（cfront-3 同构——无鸡生蛋问题，trirv_cc 自举即可编）。
> 现状：v0.0.8 起 Python 原型按五层模块落地（cpp/src/），**C 移植=逐层直译**
> ——本文件的层/类型模型即 Python 原型的验收规格。

## 一、总体分层

```
.cpp 源文件
    │
    ▼
┌──────────────┐
│  cpp_lexer.c │  词法：C 超集 token（+ ::, class, template, new, delete, try…）
└──────┬───────┘
       ▼ Token 流
┌──────────────┐
│ cpp_parser.c │  语法：C++ 声明/表达式 → AST（类/模板/重载/命名空间）
└──────┬───────┘
       ▼ AST
┌──────────────┐
│ cpp_sema.c   │  语义：符号表（作用域链）/ 重载决议 / 类型检查 / 模板实例化
└──────┬───────┘
       ▼ 标注 AST
┌──────────────┐
│ cpp_lower.c  │  降级：C++ 构造 → C 构造（类→struct/方法→fn/虚→vtbl/new→malloc）
└──────┬───────┘
       ▼ C 源码（trirv_cc 可编译）
┌──────────────┐
│ cpp_emit.c   │  发射：带 Itanium 修饰名的 C 源码 + vtable 常量数组
└──────┬───────┘
       ▼
trirv_cc → .trv → TriRV 双 VM
```

## 二、目录结构（每文件 ≤ 300 行）

> 行数规则约束 **C 移植产物**（本节文件名即 .c/.h 布局）。Python 原型
> （cpp/src/*.py）同层同序但粒度更粗（parser≈470/sema≈400 行）——
> C 移植时按层重切达标；AST 定义届时独立成 ast.h 共享头。

```
cpp/src/
├── cpp_lexer.c        # 词法（Token/关键字/操作符/字符串/数字）
├── cpp_lexer.h
├── cpp_parser.c       # 语法（递归下降；class/namespace/template 声明）
├── cpp_parser.h
├── cpp_sema.c         # 语义（作用域/重载决议/类型推导/模板实例化）
├── cpp_sema.h
├── cpp_lower.c        # 降级（class→struct / 方法→fn / 虚→vtbl / new→malloc）
├── cpp_lower.h
├── cpp_mangle.c       # Itanium 名字修饰（_Z 前缀系）
├── cpp_mangle.h
├── cpp_emit.c         # C 源码发射（struct + fn + vtable 初始化）
└── cpp_emit.h
```

## 三、类型系统（cpp_types + cpp_sema.c）

**ADT 纪律（v0.0.9，参照 sov-math 强类型化）**：类型=构造子集合，非法组合
在构造层不可表达（`data Trit = T0|T1|T2` 精神；`toTritIndex` 投影跨边界
→ `.kind` 投影仅供 lower/mangle 边界，库内一律保持 CType 类型）：

```
TInt(is_const) / TChar / TBool          基本型（const 可）
TVoid()                                 const void 不可表达（无 is_const 形参）
TClass(name, is_const)                  匿名类不可表达（newtype 隔离）
TRef(pointee)                           ref-of-ref 不可表达（C++ 禁）；
                                        is_const = pointee 派生 property，
                                        顶层 const 不可赋值（setter 拒绝）
```

C 移植=tagged union + 构造函数强制不变式（ctype_ref() 拒绝 ref pointee、
ctype_void() 无 const 形参——Python TypeError_ 对应 C 侧 assert+abort）：

```c
typedef enum { TY_VOID, TY_INT, TY_CHAR, TY_CLASS, TY_REF } TypeKind;
typedef struct CType {
    TypeKind kind;
    char *name;             /* TY_CLASS 专用（匿名不可表达） */
    struct CType *pointee;  /* TY_REF 专用（非 CType 即 abort） */
    int is_const;           /* TY_REF 无此槽位——读 = pointee.is_const */
} CType;
/* 构造器即不变式守卫：ctype_ref(pointee) 断言 pointee->kind != TY_REF */
```

Python 原型（cpp_types.py）中对应实现：

typedef struct ClassInfo {
    char *name;
    char *base;             /* 基类名（单继承，NULL=无） */
    int size;               /* sizeof（tryte） */
    Member *members;        /* 数据成员 */
    Method *methods;        /* 成员函数（含隐式 this） */
    int has_vtable;         /* 有虚函数 → 需 vtable */
} ClassInfo;
```

## 四、降级规则（cpp_lower.c）

| C++ 构造 | 降级 C | 备注 |
|---|---|---|
| `class X { int a; }` | `struct X { int a; };` | |
| `X::X() : a(0) {}` | `void X_ctor(struct X *this_) { this_->a = 0; }` | |
| `void X::f() {}` | `void _ZN1X1fEv(struct X *this_) {}` | 全 Itanium（零参也修饰） |
| `int X::get() const {}` | `int _ZNK1X3getEv(const struct X *this_)` | **K 位 + const this 原样传递** |
| `int X::g(int x)` | `int _ZN1X1gEi(struct X *this_, int x)` | 基本类型形参原样 |
| `int X::d(Y o)` | `int _ZN1X1dE1Y(struct X *this_, struct Y *o_)` | 对象形参→指针（sema 记 ref 形态） |
| `int& X::r()` | `int* _ZN1X1rEv(struct X *this_)`（`return &(this_->m)`） | 引用返回→指针 |
| `void X::b(int& r)` | `void _ZN1X1bERi(struct X *this_, int *r)`（body `(*r)`） | R/RK 由 Type 驱动 |
| `void X::c(const int& r)` | `void _ZN1X1cERKi(struct X *this_, const int *r)` | RK = const T& |
| `obj.f()` | `_ZN1X1fEv(&obj)` | |
| `a.d(b)` | `_ZN1X1dE1Y(&a, &b)` | 对象实参 &（bind 注记：sema 决策） |
| `a.b(v)` | `_ZN1X1bERi(&a, &v)` | 引用实参 &（bind=addr） |
| `a.b2(a.r())` | `_ZN1X2b2ERi(&a, _ZN1X1rEv(&a))` | **REF→REF 指针转发（无 &）** |
| `x.r() = 99` | `*(_ZN1X1rEv(&x)) = 99` | VALUE 语境写穿解引用 |
| `f()`（方法内裸调） | `_ZN1X1fEv(this_)` | 隐式 this（sema AST 预改写） |
| const 方法内 `((B*)this_)` | **`((const struct B *)this_)`** | cast 带同款 const——唯一自证点 |
| `class D : public B` | `struct D { struct B __base; ... };` | 首成员=upcast |
| `B::B()` 调用 | `B_ctor((struct B *)this_)` | 基类构造转发 |
| `g(42)`（REF 绑临时量） | **编译期拒绝 N8** | 要么对、要么报错；0.0.9 物化 |
| `virtual void f()` | vtable 槽位 + `(*this_->__vtbl->f)(this_)` | v2 |
| `new X(args)` | `struct X *p = malloc(sizeof(struct X)); X_ctor(p, args);` | |
| `delete p` | `X_dtor(p); free(p);` | |
| `namespace NS {}` | 前缀 `NS_`（扁平化，非嵌套作用域） | |
| `int add(int,int)` 重载 | `_Z3addii`（Itanium mangling） | |

## 五、Itanium 名字修饰（cpp_mangle）

```
_ZN[K]<len(Class)><Class><len(meth)><meth>E<params>
_Z3addii        → add(int, int)（自由函数重载=后续版本）
_ZN1X3fooEv     → X::foo()（零参 → v）
_ZNK1X3getEv    → X::get() const（K=this 限定进签名）
_ZN1X1bERi      → X::b(int&)（R=T&）
_ZN1X1cERKi     → X::c(const int&)（RK=const T&）
_ZN1X1dE1Y      → X::d(Y)（类→<len>Name）
_ZN1XC1Ev       → X::X() complete ctor（现约定 X_ctor，虚基/dtor 进场切 ABI）
_ZN1XC2Ev       → X::X() base ctor
```

## 六、版本路线（0.0.x 增量）

| 版本 | 特性 | 状态 |
|---|---|---|
| 0.0.1 | class + 成员变量 + 方法（this→指针）+ 构造 | PoC 已验证 |
| 0.0.2 | + 单继承 + 基类构造/方法 upcast | PoC 已验证 |
| 0.0.3 | + enum 支持（trirv_cc 编译器层） | ✓ 已验证 |
| 0.0.4 | + 引用返回（int&→int*，test04）+ 方法参数（值/对象形参，test02 dot=11） | ✓ 已验证（transpiler v0.0.3） |
| 0.0.5 | + 引用形参（int& n → int* n，body 自动 *n，实参自动 &，test05=25） | ✓ 已验证（transpiler v0.0.5） |
| 0.0.6 | + const 方法 `f() const` + const 形参（限定符剥离，test06=19） | ✓ 已验证（transpiler v0.0.6） |
| 0.0.7 | + 方法重载（Itanium mangling 前身——零参不修饰，K 位缺失） | ✓ 已验证（transpiler v0.0.7，被 0.0.8 取代） |
| 0.0.8 | **sema 重构（正则三遍退役）**：五层模块 cpp/src/ + `Type{kind: VALUE\|REF, is_const}` + N8（引用绑临时量拒绝）+ const 原样传递（cast 带同款 const：`(const struct B *)this_`）+ 全 Itanium（零参 `_ZN…Ev`、const 方法 `_ZNK…`、`T&`→R / `const T&`→RK）+ 隐式 this 调用 + K1/K2 const 规则 | ✓ 已验证（e2e 20 项：8 行为 + 2 N8 拒绝，双 VM） |
| 0.0.9 | **类型层 ADT 化（参照 sov-math）**：cpp_types 构造子集合——ref-of-ref / const void / ref 顶层 const / 匿名类 构造层不可表达；.kind 投影仅供 lower/mangle 边界；SemaError 证据携带（GADT MathError 精神）；e2e +7 ADT 不变式机检（27 项） | ✓ 已验证（transpiler v0.0.9） |
| **0.0.10** | **+ 生命周期扩展：const T& 绑临时量 → 调用者作用域具名物化** | **下一个** |
| 0.0.11 | + 默认参数 | |
| 0.0.12 | + 自由函数透传（引用形参同通道） | |
| 0.0.13 | + 命名空间（前缀扁平化） | |
| 0.0.14 | + extern "C" 块 | |
| 0.0.15 | + new/delete（malloc/free + ctor/dtor） | |
| 0.0.16 | + 操作符重载（operator+/==/<< 限算术） | |
| 0.0.16–0.0.50 | 类：static 成员 / 友元 / 嵌套类 / 前向声明 | |
| 0.0.51–0.0.80 | 模板：函数模板 → 类模板 → 按需实例化 | |
| 0.0.81–0.0.100 | 虚函数 + vtable + 纯虚/抽象类 | |
| **0.1.0** | 多继承（不含虚继承） | 里程碑 |

## 七、验证策略

1. 每个 0.0.x 版本 = 一个可编译可运行的增量
2. 契约：C++ 源 + 期望 C 输出 + trirv_cc 编译 + 双 VM exit 对拍
3. 集成验证：musl C++ 头文件子集解析（struct/class 声明）
4. Z3 对接：Z3 产 DIMACS + assignment → TriRV cnf_verify_assign 独立校验

## 八、不做的（明确边界）

| 特性 | 理由 |
|---|---|
| C++23 全集 | 人年级工程 |
| 模板元编程（TMP） | 图灵完备子语言，不必要 |
| 异常（EH/landing pad） | 复杂度/体积不可控 |
| RTTI/typeid | 依赖 EH |
| 多继承虚继承 | ABI 复杂度爆炸 |
| operator overloading 全集 | 限算术/比较即可 |
| STL 兼容 | 用三值原生数据结构替代 |

## 九、与 trirv_cc 的关系

trirv_cc 原样编译降级 C——**C++ 前端不修改 trirv_cc**。
C++ 前端 = 独立的预处理层（.cpp → .c），trirv_cc 只看到合法 C。
