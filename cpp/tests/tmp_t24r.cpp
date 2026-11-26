class Shape {
    int tag;
public:
    Shape(int t) : tag(t) {}
    virtual int area() { return 0; }
};
class Square : public Shape {
    int side;
public:
    Square(int t, int s) : Shape(t), side(s) {}
    int area() { return side * side; }
};
int main() {
    Square* s = new Square(1, 5);
    int r;
    r = s->area();
    delete s;
    return r;
}
