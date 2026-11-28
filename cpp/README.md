# cpp/ —— TriRV C++ 前端（路线 B：cfront 式 C++→C 转译）

> 设计原则：**C 写前端**（cfront-3 同构，无鸡生蛋问题），**复用 trirv_cc 全部后端**。
> 目标：系统软件够用的 C++ 子集（非 C++23 全集）。
> 路线：用户拍板 Route B——C++ 前端输出 C 源码，改动最小，trirv_cc 原样编译。

## 最小可行子集（v1 排序）

1. 引用、`const`、函数重载、默认参数 ✓（0.0.4–0.0.11）
2. `class`：访问控制、成员函数、`this`、构造/析构 ✓（0.0.1/0.0.19；访问控制强制=0.0.28）
3. 继承 + 虚函数：单继承 ✓✓（0.0.2 + 0.0.26 切片；纯虚/抽象=0.0.27）；**多继承 ✓✓**（0.0.43 布局=嵌套子对象即偏移表 / 0.0.44 分派=per-base vtable+__owner thunk）
4. `new/delete` ✓（0.0.15）、`operator new/delete`（0.0.30）
5. 命名空间、`extern "C"`、名字修饰（Itanium `_Z` 前缀）✓（0.0.13/0.0.14）
6. 模板 ✓✓：函数模板（0.0.39 按需实例化/0.0.40 显式实参+多类型）→ 类模板（0.0.41 惰性实例化+外联定义/0.0.42 多参数+默认参数+全特化；模板元编程永久不支持）
7. 异常/RTTI ✓✓（0.0.32–0.0.38：typeinfo/typeid/dynamic_cast/throw-catch/多 catch/RAII 栈展开/异常×继承）

**0.1.0=完整子集里程碑 ✓**（2026-11-28：五带全收官 A 类语义 0.0.27–31 / B RTTI 32–33 /
C 异常 34–38 / D 模板 39–42 / E 多继承 43–44；集成回归 test45 五带混合 173 双 VM 全绿）。
**0.1.1 回审收尾 ✓**（F1–F7 七 bug + G1–G5 修复；回审模糊入册门禁 74 套）。

## 子集边界清单（冻结为 0.1.x 规格）

- 语法：无字符字面量（char 以数值书写）、无复合赋值 `+=` 系（`x = x + 1` 替代）、
  无后缀 `++`/`--`、无 goto/switch-声明穿透、无 do-while/三目/sizeof(int) 形态
- 生命周期：dtor 粒度=函数级（块退出不触发）
- RTTI：dynamic_cast 多基面=位 0 基指针保持；位>0 基/跨基=E0
- 模板：嵌套类模板实参（`Box<Box<int>>`）拒绝（`>>` 单 token）；函数模板特化=E0；
  多基同名虚槽=E0；模板元编程永久不支持
- 运行时（0.1.1b 拍板）：整数除零/模零（DIV/DIVU/REM/REMU）→ **trap exit 136**（POSIX SIGFPE 惯例；FDIV 浮点面自有语义）；char 243 → wrap=0（tryte 自然溢出）
- 检查（0.1.1 起）：const 对象写=K3（**C 编译器不兜底实证**）；throw 对象
  ≤72 tryte（8 int 槽，`_class_size` 公式与 trirv_cc sizeof 实证对齐）；
  字面量 <3^45；块嵌套 ≤150 / 表达式括号 ≤45 / 同层链 ≤200（超限=诚实
  ParseError 非 crash）；可执行 TU 必须有 main
- C 编译器（0.1.1c 甄别后）：可执行 .trv 必须有 main；未终止注释/零长数组/break-continue 出循环=编译错误；解析深度帽 20000（50000 括号 segv 实证）；**同名局部=槽复用（扁平模型块级遮蔽实现——文档化偏差，块作用域=0.1.x+）**
- 硬编码常量（C 移植规格化）：tiid=seq×11、基本 ti=1/2、`__ex_buf[72]`、
  terminate 134、exit 0xFF（POSIX ABI）、EH 拷贝 stride 9、`__base{i}`、
  `{name}__{type}` 单态化、类尺寸槽单位 9

> **2026-11-28 拍板**：目标升级为**完整子集**。异常/RTTI 原排「默认不做」是基于
> 当时 vtable/运行时面缺失的困难度判断；0.0.26 虚函数切片落地后（vtable/malloc/
> dtor/全局四通道全部就位），前置困难已解决，正式纳入路线。0.1.0=完整子集里程碑
> （多继承落地 + 全带集成回归）。

## 架构

```
.cpp 源码
  → C++ 词法（超集 C 词法 + ::, class, template, new, delete）
  → C++ 解析（class/成员/重载/构造/析构）
  → 语义（重载决议/名字修饰/虚表布局）
  → C 降级（类→struct+fn_ptr、方法→带 this 前缀、构造→X_init()、
     虚调用→vtbl 间接、new→malloc+ctor、名字→_Z 修饰）
  → C 源码
  → trirv_cc（现有后端原样编译）
  → .trv
```

## 开关语义

超出子集的语法**必须报错**——静默错编比不支持更致命：
`-fno-exceptions -fno-rtti -fno-threadsafe-statics -nostdinc++ -ffreestanding`

## 验证工具

- `g++ -fdump-lang-class`：类布局/vtable 对照（一比特不差）
- `clang -Xclang -fdump-record-layouts`：交叉验证
- Godbolt `-O0`：汇编并排比对（构造顺序/虚调用/副本消除）
