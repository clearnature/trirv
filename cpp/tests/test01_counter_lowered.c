/* test01_counter_lowered —— test01_counter.cpp 的手工降级 C
 * 降级规则：
 *   class Counter → struct Counter { int val; }
 *   Counter() : val(0) → void Counter_ctor(struct Counter *this_)
 *   void increment() → void Counter_increment(struct Counter *this_)
 *   int get() → int Counter_get(struct Counter *this_)
 *   c.increment() → Counter_increment(&c)
 */
struct Counter { int val; };

void Counter_ctor(struct Counter *this_) {
    this_->val = 0;
}

void Counter_increment(struct Counter *this_) {
    this_->val = this_->val + 1;
}

int Counter_get(struct Counter *this_) {
    return this_->val;
}

int main(void) {
    struct Counter c;
    Counter_ctor(&c);
    Counter_increment(&c);
    Counter_increment(&c);
    return Counter_get(&c);
}
