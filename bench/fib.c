/* 微基准：递归分支密集（ADR-31 固定集） */
int fib(int n){ if(n<2) return n; return fib(n-1)+fib(n-2); }
int main(){ return fib(24); }
