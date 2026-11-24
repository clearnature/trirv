#!/usr/bin/env python3
"""TriRV time 日历/时钟契约（批次 8，musl_tri time_tri.c 三值本征实现）。

双 VM（CVM subprocess + PyVM in-process）× 沙箱 CWD：
  8.2 日历换算：gmtime 7 个钉点 × (y/m/d/h/mi/s/wday/yday/isdst) 9 字段
      ——含 t=0 / 闰日 2004-02-29 / 负值 -1 / 2100 世纪非闰域
  8.2 mktime 往返：gmtime→mktime 逐钉点恒等（Hinnant 反向式与 musl 正向式互逆）
  8.2 timegm：手建 tm {2000-03-01, 2004-02-29} 直接换算
  8.3 strftime：%Y %m %d %H %M %S + 补零 + 字面量透传 + 未知格式符
  8.4 时钟接口：time() 走 SYS_CLOCK(113) CLOCK_REALTIME=宿主 epoch
  8.6 difftime

期望值全部由 Python 参考实现（time.gmtime/calendar.timegm）导出后钉死，
musl 口径换算：tm_wday 周日=0（Python 周一=0）、tm_yday 0 基（Python 1 基）。

坑实录：
- LEAPOCH=951868800（2000-03-01）；反向换算早期用错误常量 952217600 且
  Jan/Feb 月累加法跨 era 年界——已换 Hinnant days_from_civil 精确式。
- exit 契约 = a0 & 0xFF。
"""
import os
import sys
import subprocess
import tempfile

import _bootstrap  # noqa: F401
import _trvio
import trirv_vm as vm

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CC = os.path.join(ROOT, "cc", "bin", "trirv_cc")
CVM = os.path.join(ROOT, "vm", "bin", "trirv_vm")
TIME_C = os.path.join(ROOT, "libc-musl", "musl_tri", "time_tri.c")

passed = 0
failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print("PASS %s: %r" % (name, got))
    else:
        failed += 1
        print("FAIL %s: got=%r want=%r" % (name, got, want))


def compile_run(src, sandbox, tag, is_cvm, max_steps=500000):
    """拼接 time_tri.c + guest → 编译 → 双 VM 执行 → exit 码。"""
    full = open(TIME_C).read() + "\n" + src
    cf = tempfile.mktemp(suffix=".c", dir=os.path.abspath(os.path.join(ROOT, "tmp")))
    tf = tempfile.mktemp(suffix=".trv", dir=os.path.abspath(os.path.join(ROOT, "tmp")))
    with open(cf, "w") as f:
        f.write(full)
    r = subprocess.run([CC, cf, tf], capture_output=True)
    if r.returncode != 0:
        return "CFAIL: " + r.stderr.decode("utf-8", "replace")[:160]
    if is_cvm:
        r2 = subprocess.run([CVM, tf], capture_output=True, cwd=sandbox, timeout=60)
        for line in r2.stderr.decode("utf-8", "replace").split():
            if line.startswith("exit="):
                return int(line.split("=")[1])
        return None
    else:
        prog, mem, packed = _trvio.load_any(tf)
        v = vm.VM(prog, mem_init=mem)
        if packed:
            v.packed = True
            v.imem_offs = __import__("trirv_isa").stream_offsets(
                __import__("trirv_isa").pack_stream(prog))
        cwd0 = os.getcwd()
        os.chdir(sandbox)
        try:
            v.run(max_steps=max_steps)
        finally:
            os.chdir(cwd0)
        return v.exit_code


# ---- 8.2 gmtime 7 钉点 9 字段（值由 Python time.gmtime 导出，musl 口径换算）----
G_GMTIME = '''
int main(){
  int ts[7];  ts[0]=0; ts[1]=951868800; ts[2]=1078012800; ts[3]=1234567890;
              ts[4]=4102444800; ts[5]=-1; ts[6]=1000000000;
  int yr[7];  yr[0]=1970; yr[1]=2000; yr[2]=2004; yr[3]=2009; yr[4]=2100; yr[5]=1969; yr[6]=2001;
  int mo[7];  mo[0]=0; mo[1]=2; mo[2]=1; mo[3]=1; mo[4]=0; mo[5]=11; mo[6]=8;
  int dy[7];  dy[0]=1; dy[1]=1; dy[2]=29; dy[3]=13; dy[4]=1; dy[5]=31; dy[6]=9;
  int hh[7];  hh[0]=0; hh[1]=0; hh[2]=0; hh[3]=23; hh[4]=0; hh[5]=23; hh[6]=1;
  int mi[7];  mi[0]=0; mi[1]=0; mi[2]=0; mi[3]=31; mi[4]=0; mi[5]=59; mi[6]=46;
  int ss[7];  ss[0]=0; ss[1]=0; ss[2]=0; ss[3]=30; ss[4]=0; ss[5]=59; ss[6]=40;
  int wd[7];  wd[0]=4; wd[1]=3; wd[2]=0; wd[3]=5; wd[4]=5; wd[5]=3; wd[6]=0;
  int yd[7];  yd[0]=0; yd[1]=60; yd[2]=59; yd[3]=43; yd[4]=0; yd[5]=364; yd[6]=251;
  int tm[9];
  int i;
  for (i = 0; i < 7; i++) {
    gmtime_r(&ts[i], tm);
    if (tm[5]+1900 != yr[i]) return 100+i;
    if (tm[4] != mo[i]) return 110+i;
    if (tm[3] != dy[i]) return 120+i;
    if (tm[2] != hh[i]) return 130+i;
    if (tm[1] != mi[i]) return 140+i;
    if (tm[0] != ss[i]) return 150+i;
    if (tm[6] != wd[i]) return 160+i;
    if (tm[7] != yd[i]) return 170+i;
    if (tm[8] != 0) return 180+i;
  }
  return 42;
}
'''

# ---- 8.2 mktime 往返（gmtime→mktime 恒等）----
G_MKTIME = '''
int main(){
  int ts[7];  ts[0]=0; ts[1]=951868800; ts[2]=1078012800; ts[3]=1234567890;
              ts[4]=4102444800; ts[5]=-1; ts[6]=1000000000;
  int tm[9];
  int i;
  for (i = 0; i < 7; i++) {
    gmtime_r(&ts[i], tm);
    int back = mktime(tm);
    if (back != ts[i]) return 200+i;
  }
  return 42;
}
'''

# ---- 8.2 timegm 手建 tm（2000-03-01 与闰日 2004-02-29）----
G_TIMEGM = '''
int main(){
  int a[9];  a[0]=0; a[1]=0; a[2]=0; a[3]=1; a[4]=2; a[5]=100; a[6]=0; a[7]=0; a[8]=0;
  int b[9];  b[0]=0; b[1]=0; b[2]=0; b[3]=29; b[4]=1; b[5]=104; b[6]=0; b[7]=0; b[8]=0;
  if (timegm(a) != 951868800) return 1;
  if (timegm(b) != 1078012800) return 2;
  return 42;
}
'''

# ---- 8.3 strftime 主格式（%Y-%m-%d %H:%M:%S 两钉点）----
G_STRFTIME = '''
int tri_eq(char *a, char *b){
  int i = 0;
  while (a[i] && b[i]) { if (a[i] != b[i]) return 0; i++; }
  return a[i] == b[i];
}
int main(){
  int tm[9];
  char buf[64];
  int t1 = 1078012800;
  gmtime_r(&t1, tm);
  strftime(buf, 64, "%Y-%m-%d %H:%M:%S", tm);
  if (!tri_eq(buf, "2004-02-29 00:00:00")) return 1;
  int t2 = 1000000000;
  gmtime_r(&t2, tm);
  strftime(buf, 64, "%Y-%m-%d %H:%M:%S", tm);
  if (!tri_eq(buf, "2001-09-09 01:46:40")) return 2;
  return 42;
}
'''

# ---- 8.3 strftime 字面量 + 未知格式符 + 补零 ----
G_STRFTIME2 = '''
int tri_eq(char *a, char *b){
  int i = 0;
  while (a[i] && b[i]) { if (a[i] != b[i]) return 0; i++; }
  return a[i] == b[i];
}
int main(){
  int tm[9];
  char buf[64];
  int t = 1078012800;
  gmtime_r(&t, tm);
  strftime(buf, 64, "Q%Q-%Y", tm);
  if (!tri_eq(buf, "QQ-2004")) return 1;      /* 未知格式符 → 字面透传 */
  strftime(buf, 64, "%m/%d", tm);
  if (!tri_eq(buf, "02/29")) return 2;        /* 两位补零 */
  return 42;
}
'''

# ---- 8.4 时钟接口：time() = 宿主 epoch（2026-11 ≈ 1795e6，宽窗锚定）----
G_TIME = '''
int main(){
  int now = time(0);
  int via;
  int ts[2];
  ts[0] = 0; ts[1] = 0;
  clock_gettime(0, ts);
  via = ts[0];
  if (now < 1700000000 || now > 1900000000) return 1;
  if (via != now) return 2;                   /* time 与 clock_gettime 同源 */
  return 42;
}
'''

# ---- 8.6 difftime ----
G_DIFFTIME = '''
int main(){
  int d = difftime(1234567890, 1078012800);
  if (d != 156555090) return 1;
  return 42;
}
'''


def main():
    sandbox = _bootstrap.scratch("time_sandbox")
    os.makedirs(sandbox, exist_ok=True)

    cases = [
        ("8.2 gmtime 7钉点9字段", G_GMTIME),
        ("8.2 mktime 往返恒等", G_MKTIME),
        ("8.2 timegm 手建tm", G_TIMEGM),
        ("8.3 strftime 主格式", G_STRFTIME),
        ("8.3 strftime 字面量+补零", G_STRFTIME2),
        ("8.4 time/CLOCK_REALTIME", G_TIME),
        ("8.6 difftime", G_DIFFTIME),
    ]
    for name, src in cases:
        for tag, is_cvm in (("CVM", True), ("PyVM", False)):
            got = compile_run(src, sandbox, tag, is_cvm)
            check("%s·%s" % (name, tag), got, 42)

    print("\ntime 日历契约：%d PASS / %d FAIL" % (passed, failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
