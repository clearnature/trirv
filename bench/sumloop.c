/* 微基准：分支+算术循环 */
int main(){ int s=0; int i=0; while(i<5000){ s=s+i*3-7; i=i+1; } return s&255; }
