// test44_multiinherit_virtual —— 0.0.44 多继承·分派（Phase E 2/2）
// 先算降级形态（本测试即验收规格）：
//   虚根类 struct = { __vtbl; __owner; }——__owner=完整对象回指
//     （位 i>0 子对象 thunk 的 this 回调通道——C 无负偏移、地址转换
//     是 trirv_cc 盲区，回指字段是 cfront 式天然解）
//   per-base vtable：D 为每个虚基类子对象各设一表——位 0=D_vt
//     （槽=主实现 _ZN1D2faEv，recv=D 基址零调整）；位 1=D_vt1
//     （槽=thunk D_th1_fb(struct VB*)→主实现(this_->__owner)）
//   D ctor 设全部虚子对象槽：__base.__vtbl=&D_vt / __owner=this_ /
//     __base1.__vtbl=&D_vt1 / __base1.__owner=this_（探针实证悬空槽）
//   vslots=多基并集（位 0 子树全序→位 1 新槽→本类新虚；两基同名
//     虚槽=E0）；typeinfo 跟派生（D_vt1.__ti=D_ti）
//   虚调用形态不变：(*(struct VB_v **)pb)->fb(pb)——pb 指向子对象，
//     子对象首槽即其表（0.0.26 共享槽机制按子对象实例化）
//   dynamic_cast/typeinfo 多基面=子集边界（位 0 基指针下转型保持；
//     位>0 基/跨基=E0 文档化）
class VA {
public:
    VA() {}
    virtual int fa() { return 1; }
};
class VB {
public:
    VB() {}
    virtual int fb() { return 2; }
};
class D : public VA, public VB {
public:
    D() {}
    int fa() { return 10; }        // 覆写位 0 基类虚槽
    int fb() { return 20; }        // 覆写位 1 基类虚槽（thunk 通道）
};
int main() {
    D obj;
    VA* pa = &obj;                 // 位 0：零调整
    VB* pb = &obj;                 // __base1：偏移调整
    int r = pa->fa() + pb->fb();   // 虚分派：10 + 20 = 30
    VA va;
    VB vb;
    int s = va.fa() + vb.fb();     // 纯基类对象回归：1 + 2 = 3
    return r + s;                  // 33
}
