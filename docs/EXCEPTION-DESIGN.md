# TriRV C++ 前端异常设计决策（0.0.34 探针定夺）

> 状态：**方案 C 检查点展开定夺**（探针矩阵 P1–P5 全绿，2026-11-28）

## 探针矩阵（tmp/exprobe/，手写 C → trirv_cc → 双 VM）

| 探针 | 验证面 | 结果 |
|---|---|---|
| P1 | throw→上浮→就地 catch（检查点协议） | ✓ 42 |
| P2 | 匹配失败多帧上浮 | ✓ 42 |
| P3 | 无抛出零异常路径不误触 | ✓ 42 |
| P4 | 嵌套 try 内层不匹配外层接住 | ✓ 20 |
| P5 | 循环内多次 throw/catch | ✓ 6 |

方案 A（setjmp/longjmp）未探即否决：其前提=寄存器级非局部跳转原语
（编译器内建或汇编），而方案 C 的前提（全局跨函数可见 / 提前 return /
ti 比较）全部已被 0.0.19/0.0.32 既有面覆盖——**无需探针即可判优**。
方案 B（表驱动 landing pad）复杂度不可控，否决。

## 定夺：方案 C = 检查点展开（check-point unwinding）

异常传播完全用**结构化 C**表达，零 goto/setjmp/表：

```
throw v       →  __throw_int(v);                    （设置全局 active/obj/ti）
try 体语句    →  if (!__ex_active) { stmt }          （语句级守卫：throw 后跳过）
try 体循环    →  while (cond && !__ex_active)        （条件增补：中段 throw 退出循环）
catch 匹配    →  if (__ex_active) {
                    if (__ex_ti == T) { __ex_active = 0;
                        int e = __ex_obj; <catch 体> }   （就地执行，无跳转）
                    else { return 0; }                   （未捕获上浮——调用者检查点接续）
                }
```

- **零开销路径**：无 throw 时守卫为廉价读（P3 实证）
- **多态扩展**（0.0.38）：catch 匹配复用 0.0.32 typeinfo 链
- **RAII 展开**（0.0.37）：检查点 return 前插本帧局部 dtor 序列（按构造逆序）；
  栈对象自动 dtor 调用与展开路径 dtor 一并设计
- **文档化偏差**：未捕获异常（无 try 覆盖）= 沉默继续（偏离 C++ std::terminate；
  函数级上浮=0.0.36+ 补全）

## 运行时（自含发射，无需独立 .trvo）

TU 含 throw/try 时发射序言（EH_FLAG 门控）：
```c
int __ex_active = 0;
int __ex_obj = 0;
int __ex_ti = 0;      /* 0.0.35：int 类型固定标记 1；0.0.38 换 typeinfo 指针 */
void __throw_int(int v) { __ex_obj = v; __ex_ti = 1; __ex_active = 1; }
```

## 版本映射

- 0.0.35 throw/catch 基础：throw int / try / catch(int e) 单 catch +
  语句守卫 + 循环条件增补 + 未捕获上浮（return 0/return;）+ 序言自含发射
- 0.0.36 多 catch / rethrow（`throw;`）/ catch(...) / 函数级上浮补全
- 0.0.37 RAII 栈展开：检查点 dtor 序列
- 0.0.38 异常×继承：catch 基类引用匹配派生 throw（typeinfo 链复用）
