/* 0.0.26 编译器 bug 独立复现：Square_ctor 内三处 store
 * （Shape_ctor cast 调用/__vtbl 共享槽/Square_vt.area）均不持久——
 * 位图 exit=0（期望 178）。编译：trirv_cc t24d3-repro.c out.trv -l libc.trvo */
struct Shape_v { int (*area)(void *slf); };

struct Shape_v Shape_vt;

struct Square_v { int (*area)(void *slf); };

struct Square_v Square_vt;

struct Shape { struct Shape_v *__vtbl; int tag; };

struct Square { struct Shape __base; int side; };

void Shape_ctor(struct Shape *this_, int t) { this_->tag = t; ((struct Shape *)this_)->__vtbl = &Shape_vt; Shape_vt.area = _ZN5Shape4areaEv; }

int _ZN5Shape4areaEv(struct Shape *this_) { return 0; }

int _ZN5Shape8describeEv(struct Shape *this_) { return ((struct Shape *)this_)->__vtbl->area(this_); }

void Square_ctor(struct Square *this_, int t, int s) { Shape_ctor((struct Shape *)this_, t); this_->side = s; ((struct Shape *)this_)->__vtbl = &Square_vt; Square_vt.area = _ZN6Square4areaEv; }

int _ZN6Square4areaEv(struct Square *this_) { return (this_->side * this_->side); }

int main(void) {
    struct Square *s = malloc(sizeof(struct Square)); Square_ctor(s, 1, 5);
    int chk2 = (((struct Shape *)s)->__vtbl == (struct Shape_v *)&Square_vt);
    int c1 = ((struct Shape *)s)->__vtbl->area(s);
    free(s);
    return chk2 * 10 + c1; }
