# cpp/ —— TriRV C++ 前端（路线 B：cfront 式 C++→C 转译）

> 设计原则：**C 写前端**（cfront-3 同构，无鸡生蛋问题），**复用 trirv_cc 全部后端**。
> 目标：系统软件够用的 C++ 子集（非 C++23 全集）。
> 路线：用户拍板 Route B——C++ 前端输出 C 源码，改动最小，trirv_cc 原样编译。

## 最小可行子集（v1 排序）

1. 引用、`const`、函数重载、默认参数
2. `class`：访问控制、成员函数、`this`、构造/析构
3. 继承 + 虚函数：单继承（多继承/虚继承 v2+）
4. `new/delete`、`operator new/delete`
5. 命名空间、`extern "C"`、名字修饰（Itanium `_Z` 前缀）
6. 模板：函数模板 → 类模板（模板元编程永久不支持）
7. 异常/RTTI：**默认不做**

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
