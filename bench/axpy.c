/* 微基准：访存密集（数组 axpy） */
int a[200]; int b[200];
int main(){ int i=0; while(i<200){ a[i]=i*2; i=i+1; }
  i=0; while(i<200){ b[i]=a[i]*5+3; i=i+1; }
  int s=0; i=0; while(i<200){ s=s+b[i]; i=i+1; } return s&255; }
