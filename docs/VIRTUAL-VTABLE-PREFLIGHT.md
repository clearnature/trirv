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


## 0.0.26 增量：cfront 共享槽布局（已落地）+ 新编译器 bug（待修）

**布局修正**：vtbl 槽只在根类子对象位 0（`struct Shape { Shape_v *__vtbl; int tag; }`），
派生类 `struct Square { Shape __base; int side; }` 经 __base 共享同一槽位
（派生 ctor 以 `((struct Shape *)this_)->__vtbl = &Square_vt` 改指派生表），
上转型零调整——修复 0.0.25 前的「自有 vtbl 最前 + __base 次位」布局
（该布局使基类子对象位于偏移 9，上转型零调整写读错位 = d26e 150 实证）。

**新编译器 bug（0.0.27）**：ctor 内经 cast 指针的成员 store 不持久。
复现 `cpp/tests/t24d3-repro.c`：Square_ctor 内三处 store
（Shape_ctor cast 调用/共享槽写/全局槽写）全部落空（位图 exit=0，
期望 178）。对照：p24m（main 内同形态）✓、p24g（函数内非 cast）✓。
嫌疑：N_CAST on struct-ptr param 的求值/寄存器复用。


## 0.0.26 追加定位（q26a–j 七连探针）

复现持续收敛：失败点在 trirv_cc **C 编译器前端**（手写 C 直接复现，
完全绕开 cpp transpiler；PyVM/CVM 双机一致排除 VM；同形态指令在
别处工作排除 ISA 能力面）。精确触发序列尚未钉死（q26j 全组合探针
p24l/p24m2 均通过——触发条件比「先调用后访问全局」更细），但已
锁定证据链：
  ①手写最小 C（d26c/q26a，零 transpiler 参与）在 trirv_cc 编译
    期/运行期复现——责任层=C 编译器前端；
  ②失败形态=「函数体内访问全局 struct 的 fn-ptr 成员」在特定
    上下文（此前有 struct 嵌套声明+多 struct 声明+ctor 形态函数）
    报 undeclared identifier / unknown member；
  ③q26i 实证：含该失败访问的函数，其前的 cast 实参调用/形参成员
    访问不是必要条件（q26j 去cast仍挂），但 q26g/h（无 ctor 形态
    函数）全通过——触发与「ctor 形态函数体」强相关。
0.0.27 继续从 q26g→q26i 增量路径二分（下一步：q26i 去掉
`((struct Shape*)this_)->__vtbl = ...` 一行测试）。
