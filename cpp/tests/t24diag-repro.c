struct Shape_v { int (*area)(void *slf); };

struct Shape_v Shape_vt;

struct Square_v { int (*area)(void *slf); };

struct Square_v Square_vt;

struct Shape { struct Shape_v *__vtbl; int tag; };

struct Square { struct Square_v *__vtbl; struct Shape __base; int side; };

void Shape_ctor(struct Shape *this_, int t) { this_->tag = t; this_->__vtbl = &Shape_vt; Shape_vt.area = _ZN5Shape4areaEv; }

int _ZN5Shape4areaEv(struct Shape *this_) { return 0; }

void Square_ctor(struct Square *this_, int t, int s) { Shape_ctor((struct Shape *)this_, t); this_->side = s; this_->__vtbl = &Square_vt; Square_vt.area = _ZN6Square4areaEv; }

int _ZN6Square4areaEv(struct Square *this_) { return (this_->side * this_->side); }

int main(void) {
    struct Square *s = malloc(sizeof(struct Square));
    Square_ctor(s, 1, 5);
    int chk = (Square_vt.area != 0);
    int chk2 = (Shape_vt.area != 0);
    int vt_ok = (s->__vtbl == &Square_vt);
    int via = s->__vtbl->area(s);
    free(s);
    return chk + chk2 * 10 + vt_ok * 100 + via;   /* 位图：槽/基槽/vtbl 指向/调用 */
}
