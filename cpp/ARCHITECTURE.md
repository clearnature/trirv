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
| `g(42)`（**非 const** REF 绑临时量） | **编译期拒绝 N8** | C++ 法：非常量左值引用不延寿，永久拒绝 |
| `g(42)`（**const T&** 绑临时量） | `const int __tmp0 = 42; g(&__tmp0);` | 0.0.10 物化延寿（调用者作用域具名临时量） |
| `const int& r = 42;` | `const int __tmp6 = 5; const int *r = &(__tmp6);` | 引用局部延寿：临时量生命周期=作用域 |
| `int x = f();`（调用初始化） | `int x; x = f(...);` | 两步发射（trirv_cc INITLIST 限制的化解，I1 撤销） |
| `int f(int a, int b = 10)` | 调用点补全：`f(&m, 1, 10)`（默认表达式在调用者语境求值） | mangling 不含默认值；默认限常量表达式 |
| `Math m;`（全默认 ctor） | `struct Math m; Math_ctor(&m, 7);` | 全默认构造兼任无参构造 |
| `virtual void f()` | vtable 槽位 + `(*this_->__vtbl->f)(this_)` | v2 |
| `Box* p = new Box(5);` | `struct Box *p = malloc(sizeof(struct Box)); Box_ctor(p, 5);` | 0.0.15：§四 规划行落地；指针穿透 `p->get()` 直传 |
| `delete p;`（有 ~X） | `X_dtor(p); free(p);` | 0.0.19：dtor 对接（X_dtor 约定名，C1/D1 之分=虚基进场再切） |
| `int g = 5;`（全局） | `int g = 5;` 直发 | 0.0.19：全局变量=dtor 可观测通道（跨函数可见） |
| `delete p` | `X_dtor(p); free(p);` | |
| `namespace geo { … }` | 结构体扁平 `struct geo_Box`；函数全 Itanium 嵌套 `_ZN3geo4areaEii` | 0.0.13：ns 内裸名解析（sema 注册期规范化），外部必须限定；嵌套 ns/using=拒绝 |
| `extern "C" { int f(int); }` | 裸名 `int f(int a);`（不 mangle） | 0.0.14：原型直出对接 C 符号；形参限基本型 |
| `int add(int,int)` 自由函数 | `int _Z3addii(int a, int b)` | 0.0.12 透传：引用/物化/默认全通道同享 |
| `int f(Box& b)` 自由函数对象引用 | `int _Z1fR3Box(struct Box *b)` | 接收者指针形态（sema 引用穿透） |
| `int operator+(const T& r)` | `_ZN3TplERK T…`（成员）/ `_ZplRK…RK…`（自由） | 0.0.16：算子表编码 pl/eq 无长度前缀；成员优先自由兜底；限数值结果 |
| `return Vec(x + r.x, …)` | `struct Vec __tmp0; Vec_ctor(&__tmp0, …); return __tmp0;` | 0.0.17：显式构造表达式→栈物化+按值返回（无结构体赋值依赖） |
| `f(Vec(6, 7))` | `struct Vec __tmp1; Vec_ctor(&__tmp1, 6, 7); f(&(__tmp1));` | 0.0.17：对象临时量物化（0.0.16 E0 解除） |
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
| 0.0.10 | **生命周期扩展**：const T& 绑临时量→调用者作用域具名物化（sema bind='mat' 通道 + 后序改写器逐语句 hoists，声明序=依赖序）；引用局部声明 `const int& r = 42` 延寿；VALUE 返回值/嵌套物化；调用初始化两步发射（I1 撤销）。已知偏差：循环条件物化=单次求值（子集文档化）。非 const T& 绑临时量保持 N8 永久拒绝 | ✓ 已验证（e2e 29 项：9 行为 + 1 N8 + 7 ADT，双 VM） |
| 0.0.11 | **默认参数**：调用点文本补全（默认表达式=调用者语境求值）+ 全默认 ctor 兼任无参构造 + 后缀规则/常量表达式限制/非 const REF 与对象形参默认拒绝 + 同 mangling 重定义守卫 + 重载歧义检测。mangling 不含默认值（Itanium 口径） | ✓ 已验证（e2e 31 项：10 行为 + 1 N8 + 7 ADT，双 VM） |
| 0.0.12 | **自由函数透传**：`_Z<len>name<params>` mangling + FreeCall 解析（默认/物化/引用转发全通道同享）+ 对象引用形参 + 方法内裸调自由函数（隐式 this 判定集=本类链方法名，不误绑）+ 引用穿透接收者（ref→pointee 查类）+ 自由函数重载/歧义/重定义守卫 | ✓ 已验证（e2e 33 项：12 行为 + 1 N8 + 7 ADT，双 VM） |
| 0.0.13 | **命名空间**：`::` 限定名 parser 拼接（复用全通道）+ sema 注册期短名→限定键规范化（ns 内裸名解析/外部必须限定=C++ 作用域法则）+ mangle 逐组件编码（_ZN3geo3Box3getEv）+ C 结构体扁平名（geo_Box）+ 同名类异 ns 共存 + ns 内裸调自由函数 | ✓ 已验证（e2e 35 项：13 行为 + 1 N8 + 7 ADT，双 VM） |
| 0.0.14 | **extern "C"**：块/单声明两形态；mangle=裸名直出（C 链接面）；形参/返回限基本型（引用/对象=指针 ABI，后续版本）；无体声明=原型直出（对接既有 C 符号的钥匙）；C 链接无重载（同名=E0） | ✓ 已验证（e2e 37 项：14 行为 + 1 N8 + 7 ADT，双 VM） |
| 0.0.15 | **new/delete**：`new X(args)`→malloc+ctor（§四 规划行）；`delete p`→free（无 dtor 语法=仅回收存储）；TPtr 指针类型层（ptr-of-ptr/ptr-of-ref 构造层拒绝；mangle P 前缀）；`->` 指针穿透接收者；指针形参=拒绝（引用同语义）；test15 需链 cc/trirv_libc.trvo（malloc/free） | ✓ 已验证（e2e 39 项：15 行为 + 1 N8 + 7 ADT，双 VM） |
| 0.0.16 | **操作符重载**：operator+/==（限算术数值结果——对象返回+显式构造表达式=0.0.17）；成员（1 参）/自由（2 参）两形态；sema Bin→调用节点就地转换（e.call，Type/bind 全通道复用）；Itanium 算子表编码 pl/eq（无长度前缀）；对象临时量绑定=E0（0.0.17 物化） | ✓ 已验证（e2e 41 项：16 行为 + 1 N8 + 7 ADT，双 VM） |
| 0.0.17 | **显式构造表达式 + 对象临时量物化**：`Vec(x,y)`（FreeCall+类名→ctor 调用，栈临时+取址）；`return Vec(...)` 物化后按值返回（operator 对象返回面补完）；解除 0.0.16 对象临时量 E0。**勘误（0.0.18）**：本版「结构体赋值缺失实证」是探针误判——250=506 mod 256（POSIX 8 位 exit 通道，v0.0.7 已入册的 ABI），trirv_cc 结构体赋值一直可用 | ✓ 已验证（e2e 43 项，双 VM） |
| 0.0.18 | **调用结果对象绑定（E0 解除）**：类右值一律 bind='mat'（结构体赋值形态 `struct Vec tn; tn = call;`）+ 接收者右值物化（`(c+b)+c` 的 `(c+b)`）；**编译器侧零改动**——0.0.18 实际工作面=解除误判 E0+更正记录 | ✓ 已验证（e2e 45 项：19 行为 + 1 N8 + 7 ADT，双 VM） |
| 0.0.19 | **~X dtor + 全局变量**：`~X() { body }`（至多一个，无参）→ `X_dtor`（约定名）；`delete p` 对接 `X_dtor(p); free(p);`；全局变量通道（带初始化/跨函数可见，探针 16 ✓）=dtor 可观测；gscope 作用域根（方法/自由函数/main 全接线） | ✓ 已验证（e2e 47 项：19 行为 + 1 N8 + 7 ADT，双 VM） |
| 0.0.20 | **static 成员**：类内声明 `static int count;` + 类外定义 `int Counter::count = 0;`（C++ 法）→ 全局 `Counter_count`；static 方法（无 this，is_static 守卫：引用非静态成员/this=E0）→ `_ZN7Counter5totalEv`（static 与成员同编码，Itanium 口径）；双调用形态 `Counter::total()`（限定）/ `a.total()`（经实例省 receiver） | ✓ 已验证（e2e 49 项：21 行为 + 1 N8 + 7 ADT，双 VM） |
| 0.0.21 | **前向声明 + 指针成员/形参解锁**：`class B;`（不完整类型，注册期 incomplete 标记+完整定义填充）→ `B* p` 成员（decl_class 成员类型渲染）+ `f(B* q)` 指针形参（0.0.15 守卫解除）+ `&lvalue` 一元取址（指针实参面，&lvalue→TPtr）+ 指针成员穿透 `peer->get()`（member_obj/recv_expr/form_is_ptr 三通道）；类型渲染 type_c TPtr `struct B *` | ✓ 已验证（e2e 51 项：23 行为 + 1 N8 + 7 ADT，双 VM） |
| 0.0.22 | **嵌套类 + friend no-op**：`class Outer { class Inner {…}; }`——键 `Outer::Inner`（ns 机制同构：限定键/组件 mangle `_ZN5Outer5Inner3getEv`/扁平结构体 `Outer_Inner` 全复用）+ 外层体内裸名可见（resolve 回退 cur_cls 键）+ friend 解析为语义 no-op（子集无访问控制强制，诚实注明） | ✓ 已验证（e2e 53 项：25 行为 + 1 N8 + 7 ADT，双 VM） |
| 0.0.23 | **指针/算子面收尾**：一元 `*` 解引用（读+写穿——对象左值，结构体赋值通道；int 语义写=非法 C++ 由混算 E0 拦）+ 算子表扩 operator-（mi）/<（lt）/>（gt）/!=（ne） | ✓ 已验证（e2e 55 项：27 行为 + 1 N8 + 7 ADT，双 VM） |
| 0.0.24 | **虚函数 vtable 预研**：探针矩阵（fn-ptr 间接调用/赋槽/void* 槽/前向声明死路）+ virtual 解析/is_virtual/vslots 槽序/override 遮蔽/隐式虚继承入册 + 虚调用暂 E0（详见 docs/VIRTUAL-VTABLE-PREFLIGHT.md） | ✓ 预研完成 |
| 0.0.25 | **trirv_cc pointee 缓冲覆写修复**（v3 零开销：eval 后 pointee 与 member_find 相邻，bt=struct 表持有串；golden 165.9s≈基线） | ✓ 已验证 |
| 0.0.26 | **虚函数切片落地（四根因连环修）**：①pointee 覆写 ②共享槽布局（vtbl 槽归根类子对象位 0，上转型零调整）③类型套用拆链 `(*(struct Root_v **)recv)->slot(…)` ④发射序修复（方法先于 ctor——fn-ptr 前向引用=地址 0）。test24 四形态（指针虚分派/describe 二级分派/基指针 upcast/override 槽位复用）=59 双 VM | ✓ 已验证（e2e 57 项） |
| 0.0.27 | **纯虚函数/抽象类**（TDD：test27 先红后绿）：`= 0` 解析（无体 MethodDecl）；纯虚槽照常占 vtbl 位（签名取声明）但 ctor 不赋值（NULL）+ 纯虚声明不发射 C 函数；抽象类实例化（new/栈对象）sema E0——合法程序 NULL 槽不可达（C++ 法）；override 后全槽赋派生 impl 具体化 | ✓ 已验证（e2e 59 项：30 行为 + 2 拒绝 + 7 ADT，双 VM） |
| 0.0.28 | **访问控制强制**（TDD：test28b/c/d 先「错误接受」RED → 实现 → E0 GREEN）：class 默认 private/struct 默认 public + label 切换记录（member_access/static_access/MethodDecl.access）；四个拦截点（Id 裸成员/Id 限定静态/Member obj.member/MethodCall）；可见性矩阵（private 仅自身/protected 沿基链派生/public 任意）。偏差文档化：protected 经基指针未细分、friend 授权未实现、ctor/dtor 访问不强制 | ✓ 已验证（e2e 69 项：31 行为 + 5 拒绝 + 7 ADT，双 VM） |
| 0.0.29 | **拷贝构造/拷贝赋值**（TDD：test29 先红后绿）：`X y = x;` 拷贝初始化（CopyInit 节点）——隐式=两步结构体赋值（p18c 通道）/用户 `X(const X&)`=X_ctor_copy 调用（重载 C 层唯一名约定）/CtorExpr 零拷贝 elision；用户 `operator=`（Itanium aS）经 Assign e.call 直通（void 形态子集——X&+\*this 链式=后续）；类型不匹配 E0；拷贝 ctor 不参与 X(args) 匹配（C++ 法）。**潜伏 bug 修复**：ctor 体检查曾错挂 init-args 循环内（无 init-list 的 ctor 体从未被检查——既有测试全带 init-list 掩盖） | ✓ 已验证（e2e 71 项：32 行为 + 6 拒绝 + 7 ADT，双 VM） |
| 0.0.30 | **operator new/delete + explicit**（TDD：test30 先红后绿）：`static void* operator new(size_t)` → `X_new(int)` 约定名（隐式静态，size_t=int 别名）+ `new X(args)` 分配器接管（`X_new(sizeof)` 替代 malloc）+ `operator delete` → `X_delete` 接管 free（dtor 先于回收）；`explicit` ctor 记录 + CopyInit 隐式转换通道（单参非拷贝 ctor→`X_ctor(&y, v)`，explicit=E0 拒绝，直接构造不受限）；extern "C" 指针形态放宽（0.0.14 原基本型限制——void* 分配器对接=合法 C ABI 面） | ✓ 已验证（e2e 77 项：34 行为 + 7 拒绝 + 7 ADT，双 VM） |
| 0.0.31 | **命名 cast**（TDD：test31 先红后绿，Phase A 收官）：四关键字词法 + `<T>(expr)` 解析（NamedCast 节点）→ C cast 直发 `(T)(expr)`；sema 边界——static=数值↔数值+ptr↔ptr（上行 upcast 零调整）/reinterpret=ptr↔num+ptr↔ptr（数值↔数值 E0 指向 static）/const=仅 const 性变更/dynamic=E0（0.0.33）；配套 TPtr VarDecl 通用指针渲染（`const int *cp`——原仅 New 路径）+ void 局部路由（void* 指针声明） | ✓ 已验证（e2e 83 项：36 行为 + 9 拒绝 + 7 ADT，双 VM） |
| 0.0.32 | **RTTI·typeinfo + typeid**（TDD：test32 先红后绿）：vtbl 扩展 `__ti` 首槽（0.0.26 共享槽布局直接复用）+ 每类全局 `char X_ti[] = "key"`（有虚类随 vtbl / 非虚类独立发射，去重守卫）；ctor 期 `X_vt.__ti = X_ti`；`typeid(对象)` 有虚类=动态 ti（基指针穿越取派生——多态可观测）/非虚类=静态 ti 直发；`typeid(类名)`=静态 ti（Id 类名回退通道：未声明标识符终端路径新增类引用解析）；指针同一性比较。坑：8 位 exit 截断三犯（1101→77）——测试位权重改 1/2/4/8/16 | ✓ 已验证（e2e 89 项：38 行为 + 10 拒绝 + 7 ADT，双 VM） |
| 0.0.33 | **RTTI·dynamic_cast**（TDD：test33 先红后绿，Phase B 收官）：上行/同类=直达恒真（共享槽零调整）；下行=运行期 ti 同一性检查（`__dc_N(obj, dyn)` helper——可接受集=target ∪ 编译期枚举 descendants，null 守卫首行，注册表后置发射避开前向引用）；无关类型=编译期 E0（sema 静态可判）；test31c 拒绝契约转正（0.0.33 落地后 dynamic_cast 可用） | ✓ 已验证（e2e 93 项：41 行为 + 11 拒绝 + 7 ADT，双 VM） |
| 0.0.34 | **异常探针+决策**：setjmp/longjmp 支持（trirv_cc/musl_tri）vs 表驱动 unwinding；cpprt_tri 运行时设计文档 | 规划 |
| 0.0.35 | **异常·throw/catch 基础**（TDD：test35 先红后绿；方案 C 检查点展开落地——0.0.34 决策）：`throw v` → `__throw_int(v)` **+ 立即 return**（本帧展开起点）；try 体语句守卫 `if (!__ex_active)` + **VarDecl 声明提升**（decl/uses 作用域割裂防御）+ 循环条件增补（AST 级 `cond && !__ex_active`）；`catch(int e)` 单类型匹配（__ex_ti==1）+ 未捕获上浮（`return 0`/`return;` 按 ret_void）；EH 序言自含发射（EH_FLAG 门控，无需独立 .trvo）。限 int 抛出/捕获（对象=0.0.38）；bare rethrow/catch(...)/多 catch=0.0.36 | ✓ 已验证（e2e 95 项：43 行为 + 12 拒绝 + 7 ADT，双 VM） |
| 0.0.36 | **异常·多 catch / rethrow / catch(...) / 函数级上浮**（TDD：test36 先红后绿）：多 catch=`__ex_ti` 链（int=1/char=2）if/else-if；未匹配 else=**fall-through 无 return**（外层 try catch 接续——0.0.35 的 return 0 会截断外层）；catch arm 清位**后置**（`__ex_rth` 标记条件化——bare rethrow/new throw 替换语义）+ bare rethrow=`__ex_rth=1`（须在 catch 内=CUR_CATCH 计数 E0）；catch(...)=if(1) 化（bare 块不可接 else——trirv_cc 解析实证）；**函数级上浮**=非 try 调用点后 `if (__ex_active) { ret }`（EH_FLAG+TRYD 双门控；main=terminate 134 / 非 main 按签名转发）——0.0.35「沉默继续」偏差消除 | ✓ 已验证（e2e 101 项：47 行为 + 13 拒绝 + 7 ADT，双 VM） |
| 0.0.37 | **异常·RAII 栈展开**（TDD：test37 先红后绿）：sema `fn_dtors` 清单（CtorStmt/CopyInit 且类有 dtor → (var, key) 追踪；Try 体 body_dtors 子清单）；lower 三发射面——函数尾统一 dtor（构造逆序；粒度偏差文档化：块退出=后续版本）/检查点 `_eh_ret` 前插 so_far 逆序（`__dtors_so_far__` 运行时追踪——未声明对象防御）/Return 全分支前插 so_far 逆序；Throw TRYD 分型（体内=fall-through 守卫跳过→catch 检查接住；函数级=即返上浮）。期望值三次修正实录（23→18→8——函数级粒度时序：r3 dtor 于 main 尾触发在 b 读取之后） | ✓ 已验证（e2e 105 项：49 行为 + 13 拒绝 + 7 ADT，双 VM） |
| 0.0.38 | **异常×继承**（TDD：test38 先红后绿，Phase C 收官）：`throw obj` 类左值 → `__throw_obj(&obj, sizeof, X_tiid)` 拷入 `__ex_buf` 全局缓冲（检查点展开=thrower 帧即返→须拷贝；**tiid 值语义**——`int X_tiid = N*11` 全局唯一值，零指针转换，`(int)&X_ti` 地址转换 trirv_cc 盲区实证后弃用）；`catch(ExBase& e)` 类引用 → `__cm_N(__ex_ti)` 可接受集=target∪descendants（0.0.33 `__dc_N` 同模式，CM registry 后置发射）+ 形参=`(struct X*)__ex_obj`（引用→指针，共享槽零调整）；基本型 ti（1/2）与类 tiid（11/22…）不相交；catch 体 `__eh_suppress__` 键停用函数级上浮检查点（active=处理中异常，清位在 arm 末）；catch 按值/throw 类右值=E0 子集文档化 | ✓ 已验证（e2e 107 项：51 行为 + 13 拒绝 + 7 ADT，双 VM） |
| 0.0.39 | **模板·函数模板**（TDD：test39 先红后绿，Phase D 首任务）：parser `FuncTemplate{tvar, decl}`（MethodDecl 形态，T 复用类通道 tcls("T")）+ parse_tu 六元组（+templates）；sema `templates`/`instantiated` 双表——FreeCall 推导分支（args 逐个 check → 首实参去 ref 即 T → 其余实参同 T 一致性检查，不一致=E0，先拒后实例化）；`instantiate`=deepcopy 模板体 + `_subst`（ret/params/VarDecl/Try 四通道 T→concrete）+ 按具体类型重 check（cur_method=实例，fn_dtors 独立收集）+ 注册 funcs/funcs_list；单态化名 `{name}__{type}` 约定式（tmax__int/tmax__char——每类型一实例，二次调用复用缓存）；模板体不发射（仅实例发射）。修正实录：MethodDecl 返回类型字段 `.ret`（_subst 初版误写 .ty）；main 为语句 list 不可挂属性（lower_main 自有 scope list 收集 main 尾 dtor——0.0.37 机制自洽）。`tmax<int>(...)` 显式实参的 `>` vs 大于歧义消解探针=0.0.40 前置（0.0.39 推导调用无 <> 形态，未触发） | ✓ 已验证（e2e 107 项全绿：38 行为用例×双 VM + 12 拒绝用例×2 检查 + 7 ADT） |
| 0.0.40 | **模板·显式实参/多类型参数**（TDD：test40 先红后绿）：**`<>` 歧义消解探针先行**——`tmax<int>(3,5)` 实证 `[ParseError] 意外的 'int'`（Bin 路径误吃 `<`）→ parser `_try_tcall`（primary ID 后 `<类型序列>(` 形态=模板实参，`static_cast<int>(x)` 同形态先例；失配游标复原回退 Bin 比较层）；`templatedef` 多 typename 循环（`FuncTemplate.tvars` 列表）；sema 显式实参路径（`e.targs` 数≠类型参数数=E0，无部分显式/空 `<>`）+ **推导泛化**——param i 类型 Tj ← arg i（去 ref）按参数位定位，同 Tj 多参数不一致=E0（「实参类型不一致」子串保留供 test39b），默认实参不参与推导，无法推导=E0；单态化名 `{name}__{t1}__{t2}…`（类名 `::`→`_` 平化）；`tsum__int__char(int a, char b)` 多类型实例实证。e2e 111 项全绿（39 行为×双 VM + 13 拒绝×2 + 7 ADT） | ✓ 已验证 |
| 0.0.41 | **模板·类模板（单参数）+ 成员外联定义**（TDD：test41 先红后绿）：探针实证 `template<typename T> class` → `不支持的类型 'class'`（templatedef 只认函数形态）+ `>>` 单 token（嵌套实参=子集边界文档化）。parser：ClassTemplate 节点（tvars+decl+ext_methods/ext_ctors）、templatedef 三分支（类模板/ctor 外联 `Box<T>::Box(...)`/方法外联 `T Box<T>::get(...)`，外联限 tvars 恒等映射——具体实参外联=ParseError）、`_try_tspec`（`Box<int>`→`Box__int` 平化，parse_type/语句级共四形态 var;/var(args);/var=expr;/成员）、**ctor/方法声明形态**（类体 `Box(T x);`/`T get();` 分号结尾无体——外联定义配套，lower_ctors/sema 双守卫）。sema：**惰性实例化挂 resolve_class_name**（平化名前缀匹配模板名+基本型反解；Box__int 首次引用→deepcopy+_subst_class+外联并入=**体并入类内声明**（C++ 法，重复 mangling 实证后修）+ClassInfo 构建（Phase C 复刻 `_fill_class_info`）+方法体重 check（`_check_class_body`）+decls_list 就地注入→lower 零改动）。降级形态：`struct Box__int { int v; }`+`_ZN8Box__int3getEv`——实例类全走既有 mangling/emit 通道。修正实录：**peek() 三元组比较恒假第三次复发**（`== ("PUNCT", "<")` 三处——警示录升级）；_subst 声明形态 None body 守卫 ×2 | ✓ 已验证（e2e 115 项全绿：40 行为×双 VM + 14 拒绝×2 + 7 ADT） |
| 0.0.42 | **模板·多参数/默认参数/全特化**（TDD：test42 先红后绿，**Phase D 4/4 收官**）：①多参数类模板**探针实证免费**（0.0.41 机制泛化——`Pair<int,char>`→`Pair__int__char` 一次通过，test 为回归覆盖）；②默认模板参数 `typename U = char`（templatedef tvar_dfls [(名, Type|None)]；`_try_class_inst` 尾部缺省位补齐——**非默认位缺失=E0** test42b；函数模板显式实参同补齐 `tsum<int>`）；③全特化 `template<> class Desc<int> {...}`（空 typename 头；特化须在主模板后 ParseError；specializations 字典 flat 名直查——`_instantiate_spec` deepcopy 无替换；特化体内联方法/ctor，函数模板特化=子集边界）。偏差文档化：默认补齐实例名不展开默认段（`Box2<int>` 补 U=char→`Box2__int`，与显式 `Box2<int,char>`=双实例非错）。修正实录：特化声明分支三元组比较 bug（第 4 处——自查即改未入册为 bug） | ✓ 已验证（e2e 119 项全绿：41 行为×双 VM + 15 拒绝×2 + 7 ADT） |
| 0.0.43 | **多继承·布局：base 偏移表 + this 调整**（TDD：test43 先红后绿，Phase E 首任务）：解析 `: public A, public B` 多基类（`ClassDecl.bases` 全列表+`base`=位 0 别名兼容）；**布局=嵌套子对象顺次嵌入**（`struct C { struct A __base; struct B __base1; int c; }`——位 0 保 `__base` 名=0.0.26 共享槽零迁移，附加基类 `__base{i}`；嵌套 struct 即偏移表，C 编译器天然处理）；ctor 多基类 init（位 0=`(struct A*)this_` 零调整/附加=`&this_->__base1` 子对象取址）；**上转型偏移**=sema `_note_upcast`（Assign/VarDecl-init 两通道；`Un&.up_base=i` 标注；位 0 不标注=零调整；复杂接收形态=Id/This 限定 E0）→ lower 发射 `&obj.__base1`；**方法接收者调整**=MethodCall `recv_base` 标注（声明类≠接收者类且非位 0 且非虚）→ `&(obj.__base1)`；this_recv 多基偏移；成员/方法查找链多基 DFS 声明序（派生遮蔽=先序先得；**两分支同名=E0 二义** test43b）。修正实录：**单参 SemaError 潜伏 ×7**（错误路径全 crash 而非诚实报错——P35 顺藤挖出，全改双参）。虚分派/dynamic_cast 多基面=0.0.44 | ✓ 已验证（e2e 123 项全绿：42 行为×双 VM + 16 拒绝×2 + 7 ADT） |
| 0.0.44 | **多继承·分派**（TDD：test44 先红后绿，**Phase E 2/2 收官**）：探针实证三缺陷——`__base1.__vtbl` 悬空/单表无法承载双基分派/覆写 impl this 形态不匹配（C 无负偏移+地址转换=trirv_cc 盲区）。设计：**`__owner` 完整对象回指槽**（虚根类 struct=`{__vtbl; __owner}`——thunk this 回调通道，cfront 式天然解）+ **per-base vtable**（派生类为每个虚根子对象各设一表：位 0=`D_vt` 主实现直填零调整；位 i>0=`D_vt{i}`（基表形态 `{base}_v`）+ **thunk** `{D}_th{i}_{slot}(struct {base}*)→主实现(this_->__owner)`）；ctor 全虚子对象表/owner 设定；vslots=多基并集（位 0 子树全序→位 1 新槽；**同名虚槽=E0** test44b）；typeinfo 跟派生（各表 `__ti=D_ti`）；**虚调用根=接收者静态类型**（P50 修正——多基下 sig_owner 根≠接收者子对象根）。修正实录：cast 优先级塌陷（`(struct VA *)this_->__vtbl` 解析为 `this_->__vtbl` 先行——recv 加外层括号）；**虚根类自身表设定丢失**（vset 重写 bkeys=[] 循环零次——e2e 无纯虚根直接虚调用用例未抓到，test44 RED 抓到）。dynamic_cast/typeinfo 多基面=子集边界（位 0 基指针下转型保持现状语义；位>0 基/跨基=E0 文档化） | ✓ 已验证（e2e 127 项全绿：43 行为×双 VM + 17 拒绝×2 + 7 ADT） |
| 0.1.0 | **完整子集里程碑**（2026-11-28）：**五带全收官**——A 类语义 0.0.27–31 / B RTTI 0.0.32–33 / C 异常 0.0.34–38 / D 模板 0.0.39–42 / E 多继承 0.0.43–44；集成回归 test45 五带混合（模板×类/异常穿虚分派/typeid 动态穿基指针/dynamic_cast 下转/多继承 RAII dtor g_log 可观测）**173 双 VM 一次通过**；子集边界文档化入 README（字符字面量/块级 dtor 粒度/dc 多基面/嵌套类模板实参/函数模板特化/多基同名虚槽/模板元编程）。修正实录：虚分派根三易其稿——**终规则=持有槽名的虚根**（sig_owner=覆写实现者非原声明者，root_of(D)=VA 错表；接收者另做子对象调整 &recv.__base{i}，位 0 基址恒等零调整）——0.0.44 的「接收者静态类型根」为误判（D 值调 VB 槽拿 VA 根表），test45 集成用例一次挖出 | ✓ 已验证（e2e 129 项全绿：44 行为×双 VM + 17 拒绝×2 + 7 ADT） |
| **0.1.0a** | **0.0.x 冻结前回审**（tools/audit_probe.py 模糊探测 40 用例：error-path 崩溃狩猎+极限边界）：**P0 已修 3**——F1 `class A : public A` 无限循环（_root_of 环防御+sema E0）/ F2 `raise X if c else None` 条件表达式 raise 陷阱→`raise None` TypeError crash（**自由函数默认实参自 0.0.11 起从未工作**）/ F3 `f<int>(1)` 非模板函数 targs 静默忽略→按普通调用编译（子集泄漏）；**P1 规格级待决 3**——const 局部写端到端漏检（「交 C 编译器」承诺失效：trirv_cc 接受 `const int x=1; x=2;`）/ `__ex_buf[72]` 无容量检查（>8 int 成员对象 throw=静默内存破坏——溢出幸存实证）/ 超大字面量静默截断（99999999999→255）；**P2 诚实化 3**——深嵌套 RecursionError ×3（C 移植必须显式深度限制）/ 空 TU 通过（无 main）/ 错误路径覆盖 ~13%（127 raise 点 vs 20 REJECT）。硬编码清单冻结为常量：tiid×11、ti=1/2、buf 72、terminate 134、exit 0xFF、stride 9、__base{i}、{name}__{type}。e2e 135 项（+3 REJECT） | ✓ 已验证 |
| **0.1.x** | **C 移植（原型冻结为规格，逐层直译 ≤300 行/文件）** | **下一个** ||
| **0.1.0** | **完整子集里程碑**：多继承落地 + 全带集成回归（虚基类/菱形继承=0.2.0+） | 里程碑 |
| 0.1.x | **C 移植**：五层 .c/.h（≤300 行/文件）——Python 原型冻结为规格，逐层直译 | 0.1.0 后启动 |

## 七、验证策略

1. 每个 0.0.x 版本 = 一个可编译可运行的增量
2. 契约：C++ 源 + 期望 C 输出 + trirv_cc 编译 + 双 VM exit 对拍
3. 集成验证：musl C++ 头文件子集解析（struct/class 声明）
4. Z3 对接：Z3 产 DIMACS + assignment → TriRV cnf_verify_assign 独立校验

## 八、不做的（明确边界）

| 特性 | 理由 |
|---|---|
| C++23 全集 | 人年级工程 |
| 模板元编程（TMP）/变参模板/概念 | 图灵完备子语言，不必要 |
| ~~异常~~ / ~~RTTI~~ | **2026-11-28 拍板废止——纳入范围**（0.0.32–0.0.38 带；困难已解决：vtable/malloc/dtor/全局面全部就位） |
| 多继承虚继承（菱形） | ABI 复杂度爆炸（0.2.0+ 再评估） |
| operator overloading 全集 | 限算术/比较即可（operator[]/()/-> 按需评估） |
| STL 兼容 | 用三值原生数据结构替代 |
| 协程/模块/多线程/freestanding 外运行时 | 与 TriRV 定位冲突 |

## 九、与 trirv_cc 的关系

trirv_cc 原样编译降级 C——**C++ 前端不修改 trirv_cc**。
C++ 前端 = 独立的预处理层（.cpp → .c），trirv_cc 只看到合法 C。
