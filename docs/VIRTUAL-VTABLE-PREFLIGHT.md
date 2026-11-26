# 虚函数 vtable 预研（v0.0.24–0.0.25，进行中）

> cpp 前端虚函数切片的前置研究。状态：**设计就绪，trirv_cc 侧一项编译器 bug 待修**。

## 探针矩阵（tmp/p24a–i + b25a–g + t24diag）

| 能力 | 判定 | 应对 |
|---|---|---|
| fn-ptr 间接调用 `o.vt->area(o)` | ✓ | 直接采用 |
| 主函数内赋槽 / 函数内赋槽（p24h） | ✓ | — |
| `void *slf` 槽 + vtbl struct 前置发射 | ✓ | 绕开 `struct X;` 前向死路 |
| `struct X;` 前向声明 | ✗ 不支持 | void* 槽方案 |
| 全局 fn 地址初始化 | ✗ | ctor 运行时幂等赋槽 |
| **ctor 内赋槽持久性** | ✗ **全零位图** | **0.0.25 工作面（见下）** |

## 已修根因（0.0.25，trirv_cc 求值先行重排）

`pointee()` 返回单一 static 缓冲——双箭头求值时内层 eval 的 pointee
调用覆写外层已算好的类型串 → `o->w->q` 报 unknown member。
修复（v2，零分配）：eval N_MEMBER 改**求值先行**——`base=eval(lv->l)`
先算，随后 `bt=pointee(expr_type(lv->l))` 与 member_find 相邻无间隙；
strdup 快照版（v1）有 2 倍编译期开销（golden 165→305s）已弃。
修后：双箭头数据成员/双箭头 fn-ptr 链全绿（b25g/b25d=25），
golden 38/0 @ 376s wall < 600s 限时。

## 剩余复现（ctor 赋槽不持久，全零位图）

`tmp/t24diag.c`（生成的 C，可脱离管线直接编译复现）：
ctor 序正确（`Shape_ctor(cast) → side → __vtbl=&Square_vt → Square_vt.area=impl`），
main 读回：`Square_vt.area==0`、`Shape_vt.area==0`、`s->__vtbl==&Square_vt` 为假——
**ctor 的全部 store（含纯指针 store）不持久**。已排除：成员名双下划线
（改名无效）、mangled RHS（p24m ✓）、函数内赋槽本身（p24g/j 修后 ✓）、
结构体赋值（p18c ✓）。对照：test15 同管线 ctor store 持久 ✓——差分点=
`virtual` 的 vset 发射与 vtbl struct 前置。

## 设计（就绪，解锁即落地）

槽序=base 先序+override 保位+隐式虚继承（重写虚槽即虚）；`__vtbl`
最前布局（单继承零 this 调整）；ctor 运行时幂等赋槽（全局 fn 地址
初始化不支持）；虚调用 `recv->__vtbl->slot(recv, args)`；override
遮蔽（查找止于首含类）。sema 已入册：virtual 解析/is_virtual/vslots。
