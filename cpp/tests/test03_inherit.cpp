// test03_inherit —— 单继承 + 基类构造/方法继承
class Animal {
    int legs;
public:
    Animal(int l) : legs(l) {}
    int get_legs() { return legs; }
};

class Dog : public Animal {
    int tail;
public:
    Dog(int t) : Animal(4), tail(t) {}
    int get_tail() { return tail; }
};

int main() {
    Dog d(1);
    return d.get_legs() + d.get_tail();  // 4 + 1 = 5
}
