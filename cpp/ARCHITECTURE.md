# C++ 前端架构（Route B：cfront 式 C++→C 转译器）

> 版本管理：0.0.1 → 0.0.2 → ... → 0.0.100 → 0.1.0 → ...
> 每个增量版本 = 一个可编译可验证的功能点。
> 语言：前端用 **C** 写（cfront-3 同构——无鸡生蛋问题，trirv_cc 自举即可编）。

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

## 三、类型系统（cpp_sema.c）

```c
typedef enum { TY_VOID, TY_INT, TY_CHAR, TY_CLASS, TY_PTR, TY_REF, TY_FN } TypeKind;
typedef struct CType {
    TypeKind kind;
    char *name;             /* class 名 / int / void */
    struct CType *pointee;  /* TY_PTR/TY_REF 指向类型 */
    int is_const;
    /* class 专用 */
    struct ClassInfo *cls;  /* TY_CLASS → 类信息 */
} CType;

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
| `void X::f() {}` | `void X_f(struct X *this_) {}` | this→首参数 |
| `int X::g(int x)` | `int X_g(struct X *this_, int x)` | 基本类型形参原样 |
| `int X::d(Y o)` | `int X_d(struct X *this_, struct Y *o_)` | 对象形参→指针，body `o.m`→`o_->m` |
| `int& X::r()` | `int* X_r(struct X *this_)`（`return &(this_->m)`） | 引用返回→指针 |
| `void X::b(int& r)` | `void X_b(struct X *this_, int *r)`（body `r`→`*r`） | 引用形参→指针+自动解引用 |
| `obj.f()` | `X_f(&obj)` | |
| `a.d(b)` | `X_d(&a, &b)` | 对象实参自动加 &（经指针落盘：trirv_cc 支持 `f()=v`） |
| `a.b(v)` | `X_b(&a, &v)` | 引用实参自动加 & |
| `class D : public B` | `struct D { struct B __base; ... };` | 首成员=upcast |
| `B::B()` 调用 | `B_ctor((struct B *)this_)` | 基类构造转发 |
| `virtual void f()` | vtable 槽位 + `(*this_->__vtbl->f)(this_)` | v2 |
| `new X(args)` | `struct X *p = malloc(sizeof(struct X)); X_ctor(p, args);` | |
| `delete p` | `X_dtor(p); free(p);` | |
| `namespace NS {}` | 前缀 `NS_`（扁平化，非嵌套作用域） | |
| `int add(int,int)` 重载 | `_Z3addii`（Itanium mangling） | |

## 五、Itanium 名字修饰（cpp_mangle.c）

```
_Z <len> <name> <params_encoding>
_Z3addii        → add(int, int)
_Z3addff        → add(float, float)
_ZN1X3fooEv     → X::foo()
_ZN1XC1Ev       → X::X() (complete ctor)
_ZN1XC2Ev       → X::X() (base ctor)
```

## 六、版本路线（0.0.x 增量）

| 版本 | 特性 | 状态 |
|---|---|---|
| 0.0.1 | class + 成员变量 + 方法（this→指针）+ 构造 | PoC 已验证 |
| 0.0.2 | + 单继承 + 基类构造/方法 upcast | PoC 已验证 |
| 0.0.3 | + enum 支持（trirv_cc 编译器层） | ✓ 已验证 |
| 0.0.4 | + 引用返回（int&→int*，test04）+ 方法参数（值/对象形参，test02 dot=11） | ✓ 已验证（transpiler v0.0.3） |
| 0.0.5 | + 引用形参（int& n → int* n，body 自动 *n，实参自动 &，test05=25） | ✓ 已验证（transpiler v0.0.5） |
| **0.0.6** | **+ const（const 方法 `f() const` + const 形参）** | **下一个** |
| 0.0.7 | + 函数重载（Itanium mangling） | |
| 0.0.8 | + 默认参数 | |
| 0.0.9 | + 命名空间（前缀扁平化） | |
| 0.0.10 | + extern "C" 块 | |
| 0.0.11 | + new/delete（malloc/free + ctor/dtor） | |
| 0.0.12 | + 操作符重载（operator+/==/<< 限算术） | |
| 0.0.11–0.0.50 | 类：static 成员 / 友元 / 嵌套类 / 前向声明 | |
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
