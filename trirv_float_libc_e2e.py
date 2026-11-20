#!/usr/bin/env python3
"""trirv_float_libc_e2e —— musl_tri 浮点 libc 五函数契约（P4）。

被测：musl_tri/strtod_tri.c（strtod+atof）、musl_tri/ecvt_tri.c（ecvt+fcvt+gcvt）。
Oracle：Fraction 精确算术（宿主 Python 大整数）——"正确舍入"的定义本身，
不依赖任何浮点实现。Oracle 自校验：对随机 double 的精确值必须与
trirv_float.encode 逐位一致（见 §0）。
差分：guest（PyVM × C VM）字节级一致 + 对 oracle 一致。
已知编译器限制：未初始化全局量从零页 207 号字线性铺开，超过 ~1000 词会
踩进字符串/初始化全局区——本套件按 ≤40 case 分块规避（记入立项书 §5.6）。

语义要点（契约锁定，详见 TERNARY_FLOAT_PROPOSAL.md §5.6）：
  - double 形参/返回值 = TF64 raw（45-trit 字，guest 视角就是 int）
  - strtod：≤20 位有效数字正确舍入（Fraction oracle 全量锁定）；
    inf/infinity/nan 接受（不区分大小写）；十六进制浮点不接受
  - ecvt：n∈{1..16} 保留否则 15（musl 无符号钳制怪癖）；±Inf/NaN →
    全 '0' 串 decpt=0 sign=符号位（TriRV 定义，musl 此处未定义）
  - fcvt：n≤lz 返回静态零串切片；有效位 >40 clamp（TriRV 定义）
  - gcvt：%g 规则（X<-4 或 X≥n → e 形态；去尾零）；±0 → "0"/"-0"
"""
import os
import random
import struct
import subprocess
import sys
import tempfile
from fractions import Fraction

import trirv_float as tf
import trirv_vm as vm

QUICK = False
def qcut(seq):
    """--quick 模式下只取前 8 例（TDD 快速迭代）"""
    return seq[:8] if QUICK else seq

HERE = os.path.dirname(os.path.abspath(__file__))
CC = os.path.join(HERE, "trirv_cc")
CVM = os.path.join(HERE, "trirv_vm")

passed = 0
failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
    else:
        failed += 1
        print("FAIL %s: got=%r want=%r" % (name, got, want))


# ============ §0 oracle ============
POW3N = [3 ** i for i in range(0, 700)]


def pow3(x):
    return POW3N[x] if x >= 0 else Fraction(1, POW3N[-x])


_P34 = 3 ** 34
_P33 = 3 ** 33


def exact_round(num, den):
    """正确舍入 num/den → TF64 raw（half-up，对齐 trirv_float）。num/den > 0"""
    x = 0
    # 归一化：bit_length 精确估计 3^k 大步长 + 单位步校正
    # （纯逐 ×3 在 den ≈ 10^643 的 denormal 案例上是 ~1400 次大整数乘，分钟级）
    k = max(0, (num.bit_length() - den.bit_length()) * 6309 // 10000 - 33)
    if k:
        den *= 3 ** k
        x -= k
    while num >= den * _P34:
        den *= 3
        x -= 1
    while num < den * _P33:
        num *= 3
        x += 1
    q, r = divmod(num, den)
    if 2 * r >= den:
        q += 1
    if q >= _P34:
        q = _P33
        x += 1
    return q + (29524 + 33 - x) * _P34


def oracle_strtod(s):
    """复刻 strtod_tri 的解析 + Fraction 精确舍入。返回 (raw, consumed, kind)。
    kind: 'num'/'inf'/'nan'/'zero'（zero = 无有效数字）。"""
    i = 0
    while i < len(s) and s[i] in " \t\n\v\f\r":
        i += 1
    neg = False
    if i < len(s) and s[i] == '+':
        i += 1
    elif i < len(s) and s[i] == '-':
        neg = True
        i += 1
    low = s[i:].lower()
    if low.startswith('infinity'):
        return (tf.TF64.INF_NEG if neg else tf.TF64.INF_POS), i + 8, 'inf'
    if low.startswith('inf'):
        return (tf.TF64.INF_NEG if neg else tf.TF64.INF_POS), i + 3, 'inf'
    if low.startswith('nan'):
        j = i + 3
        if j < len(s) and s[j] == '(':
            k = s.find(')', j)
            j = (k + 1) if k != -1 else j
        return tf.TF64.NAN, j, 'nan'
    digs = []
    dot = False
    exp10 = 0
    start = i
    seen = False
    consumed = False
    while i < len(s):
        c = s[i]
        if c == '.' and not dot:
            dot = True
            i += 1
            continue
        if not c.isdigit():
            break
        consumed = True
        i += 1
        if dot:
            exp10 -= 1
        if seen or c != '0':
            seen = True
            digs.append(int(c))
    # 指数
    if i < len(s) and s[i] in 'eE':
        j = i + 1
        eneg = False
        if j < len(s) and s[j] in '+-':
            eneg = s[j] == '-'
            j += 1
        ev = 0
        anyd = False
        while j < len(s) and s[j].isdigit():
            ev = min(ev * 10 + int(s[j]), 20000)
            anyd = True
            j += 1
        if anyd:
            exp10 += (-ev if eneg else ev)
            i = j
    if not seen:
        # 无数字被消费：无转换，end = nptr（POSIX，offset 0）；
        # 消费过 0 串（'-0'/'0.000'）则是合法 ±0 转换，end = 消费结束处
        return 0, (i if consumed else 0), 'zero'
    acc = 0
    for d in digs[:20]:
        acc = acc * 10 + d
    P = exp10
    sig = len(digs[:20])
    mag = P + sig
    if mag >= 14100:
        raw = tf.TF64.INF_NEG if neg else tf.TF64.INF_POS
        return raw, i, 'inf'
    if mag <= -14130:
        raw = 3 ** 44 if neg else 0
        return raw, i, 'num'
    v = Fraction(acc) * Fraction(10) ** P
    raw = exact_round(v.numerator, v.denominator)
    if neg:
        raw += 3 ** 44
    return raw, i, 'num'


# oracle ecvt 数字生成（精确）
def oracle_digits(v, n):
    """正 Fraction v → (digits str of length n, decpt)。half-up + sticky 视作 exact。"""
    num, den = v.numerator, v.denominator
    D = 0
    while num >= den * 10:
        den *= 10
        D += 1
    while num < den:
        num *= 10
        D -= 1
    # [1,10)：取 n 位 + 舍入
    num2, den2 = num, den
    for _ in range(n - 1):
        num2 *= 10
    q, r = divmod(num2, den2)
    if 2 * r >= den2:
        q += 1
    ds = str(q)
    if len(ds) > n:
        D += 1
        ds = ds[:n]
    ds = ds + '0' * (n - len(ds))
    return ds, D + 1


def oracle_ecvt_parts(raw):
    """raw → (sign 0/1, is_special, Fraction |None)"""
    s = 1 if raw >= 3 ** 44 else 0
    rest = raw - s * 3 ** 44
    E = rest // 3 ** 34
    M = rest - E * 3 ** 34
    if E >= 59048:
        return s, True, None
    if E == 0 and M == 0:
        return s, True, Fraction(0)
    e3 = E - 29524
    if e3 >= 0:
        v = Fraction(M * 3 ** e3, 3 ** 33)
    else:
        v = Fraction(M, 3 ** 33 * 3 ** (-e3))
    return s, False, v


def oracle_ecvt(raw, n):
    s, special, v = oracle_ecvt_parts(raw)
    if n < 1 or n > 16:
        n = 15
    if special:
        # ±0 → decpt=1（TriRV 定义）；±Inf/NaN → decpt=0
        return '0' * n, (1 if v == 0 else 0), s
    ds, D = oracle_digits(v, n)
    return ds, D, s


def oracle_fcvt(raw, n):
    s, special, v = oracle_ecvt_parts(raw)
    if n > 1400:
        n = 1400
    if n < 0:
        n = 0
    if special and v != 0:
        ds, D, sg = oracle_ecvt(raw, n)
        return ds, 0, s, 'ecvt'
    if v == 0:
        # 零串切片 "000..."+14-n：可见字符 = n+1（musl +1 quirk）
        if n > 14:
            n = 14
        return '0' * (n + 1), 1, (1 if raw >= 3 ** 44 else 0), 'zeros'
    D = oracle_ecvt(raw, 1)[1]
    lz = -D
    if n <= lz:
        # 零串切片：可见字符 = n+1（musl +1 quirk）
        if n > 14:
            n = 14
        if n < 0:
            n = 0
        return '0' * (n + 1), 1, s, 'zeros'
    ds, DD, sg = oracle_ecvt(raw, n + D)
    return ds, DD, sg, 'ecvt'


def oracle_gcvt(raw, nd):
    if nd <= 0:
        nd = 1
    if nd > 16:
        nd = 16
    s, special, v = oracle_ecvt_parts(raw)
    # NaN 判定：E==max && M!=0（必须在 Inf 早退前分清）
    rest = raw - (1 if raw >= 3 ** 44 else 0) * 3 ** 44
    E = rest // 3 ** 34
    if E >= 59048:
        M = rest - E * 3 ** 34
        head = '-' if raw >= 3 ** 44 else ''
        return head + ('nan' if M != 0 else 'inf')
    if v == 0:
        return ('-0' if s else '0')
    ds, D, sg = oracle_ecvt(raw, nd)
    X = D - 1
    body = None
    if X < -4 or X >= nd:
        last = nd - 1
        while last > 0 and ds[last] == '0':
            last -= 1
        frac = ds[1:last + 1]
        body = ds[0] + ('.' + frac if frac else '')
        e = X
        body += 'e' + ('+' if e >= 0 else '-')
        e = abs(e)
        body += '%02d' % e if e < 100 else str(e)
    else:
        if D <= 0:
            body = '0.' + '0' * (-D) + ds
            body = body.rstrip('0')
            if body.endswith('.'):
                body += '0'
        elif D >= nd:
            body = ds + '0' * (D - nd)
        else:
            body = ds[:D] + '.' + ds[D:]
            body = body.rstrip('0')
            if body.endswith('.'):
                body = body[:-1]
    return ('-' if s else '') + body


# ============ §1 oracle 自校验（vs encode）============
def self_check():
    random.seed(7)
    bad = 0
    n = 0
    for i in range(300):
        d = abs(random.uniform(-1e6, 1e6)) * 10 ** random.randint(-280, 280)
        d = float(d)
        if d == 0 or d == float('inf'):
            continue
        n += 1
        oraw = exact_round(*d.as_integer_ratio())
        if oraw != tf.TF64.encode(d):
            bad += 1
    check("oracle 自校验 vs encode", bad, 0)


# ============ §2 guest 程序构造与运行 ============
def raw_to_trytes(raw):
    return bytes((raw // 243 ** i) % 243 for i in range(9))


def run_guest(src_path, link_paths, pyvm=None):
    if pyvm is None:
        # PyVM 双 VM 差分默认仅在 TRV_FLOAT_E2E_FULL=1 时跑
        # （per-call 隔离后 2350 个小程序 × PyVM ≈ +8 分钟；C VM × oracle 已是全量正确性主检）
        pyvm = (not QUICK) and os.environ.get("TRV_FLOAT_E2E_FULL") == "1"
    trv = src_path.replace('.c', '.trv')
    args = [CC, src_path, trv]
    for p in link_paths:
        args += ['-l', p]
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        return None, None, None, "COMPILE: " + r.stderr.strip()[:300]
    # C VM（quick 模式只跑这个；PyVM 双差分在全量模式）
    r2 = subprocess.run([CVM, trv], capture_output=True, timeout=300)
    c_out = r2.stdout[:G_OUT_BYTES]
    if not pyvm:
        return None, c_out, 0, None
    # PyVM
    data = open(trv, 'rb').read()
    n_inst, n_seg = struct.unpack_from('<II', data, 8)
    prog = list(struct.unpack_from('<%dI' % n_inst, data, 16))
    off = 16 + 4 * n_inst
    mem = {}
    for _ in range(n_seg):
        addr, ln = struct.unpack_from('<II', data, off)
        off += 8
        mem[addr] = list(data[off:off + ln])
        off += ln
    c = vm.VM(prog, mem_init=mem).run(max_steps=2000000000)
    py_out = bytes(c.output)[:G_OUT_BYTES]
    return py_out, c_out, c.exit_code, None


G_OUT_BYTES = 0


# ============ §3 strtod 套件 ============
def suite_strtod():
    global G_OUT_BYTES
    random.seed(421)
    cases = []
    # 随机数字串
    for i in range(300):
        ndig = random.randint(1, 20)
        digs = str(random.randint(1, 9)) + ''.join(random.choice('0123456789')
                                                   for _ in range(ndig - 1))
        style = random.choice(['plain', 'dot', 'lead', 'exp', 'dotexp'])
        dotpos = random.randint(0, ndig)
        nfrac = ndig - dotpos
        if style == 'plain':
            s = digs
            P = 0
        elif style == 'dot':
            s = digs[:dotpos] + '.' + digs[dotpos:]
            P = -nfrac
        elif style == 'lead':
            s = '0.' + '0' * random.randint(0, 3) + digs
            P = -nfrac - random.randint(0, 3)
        elif style == 'exp':
            s = digs + 'e%d' % random.randint(-30, 30)
            P = 0
        else:
            s = digs[:dotpos] + '.' + digs[dotpos:] + 'e%d' % random.randint(-30, 30)
            P = -nfrac
        cases.append((s, int(digs), P))
    # tie / 边界构造
    for s in ["0.5", "1.5", "2.5", "0.05", "12.5", "0.0005", "6.5e2", "0.025",
              "1.25e1", "0.1", "0.2", "0.3", "1e-1", "2e-1", "3e-1",
              "12345678901234567890", "0.12345678901234567890123456789",
              "99999999999999999999", "1e308", "1e-308", "4.9e-324",
              "8.9e307", "2.2250738585072014e-308", "0", "-0", "0.0", "-0.0"]:
        m, _, ex = s.partition('e')
        E = int(ex) if ex else 0
        if '.' in m:
            ip, fp = m.split('.')
        else:
            ip, fp = m, ''
        ds = (ip + fp)
        neg = ds.startswith('-')
        core = ds.lstrip('0') or '0'
        cases.append((s, int(core), E - len(fp)))
    # 特殊值
    for s in ["inf", "INF", "Infinity", "-inf", "nan", "NAN", "nan(abc)",
              "-nan", "abc", "", "  ", "  42x", "1e", "e5", "-", "+", "."]:
        cases.append((s, None, None))

    cases = qcut(cases)
    N = len(cases)
    print("-- suite_strtod (cases=%d) --" % N, flush=True)
    CHUNK = 50                         # 50-call 程序：PyVM 秒级、全局槽安全（见文件头）
    for c0 in range(0, N, CHUNK):
        cs = cases[c0:c0 + CHUNK]
        nc = len(cs)
        G_OUT_BYTES = 27 * nc
        lines = []
        for i, (s, acc, P) in enumerate(cs):
            lines.append('    { char *e = 0; const char *q = %s;'
                         ' out[3*%d] = strtod(q, &e); out[3*%d+1] = (int)(e - q);'
                         ' out[3*%d+2] = 0; }' % (repr_cstr(s), i, i, i))
        srcg = ("int strtod(const char *s, char **e);\n"
                "int out[%d];\n"
                "int main(void) {\n" % (3 * nc)
                + "\n".join(lines) +
                "\n    write(1, out, %d);\n    return 0;\n}\n" % (27 * nc))
        sp = os.path.join(tempfile.mkdtemp(prefix="sd_e2e_"), "sd_main.c")
        with open(sp, 'w') as f:
            f.write(srcg)
        py_out, c_out, exitc, err = run_guest(sp, [os.path.join(HERE, "musl_tri/strtod_tri.c")])
        if err:
            check("strtod 编译/运行 chunk@%d" % c0, err, None)
            continue
        if py_out is not None:
            check("strtod PyVM/CVM 输出一致 chunk@%d" % c0, py_out == c_out, True)
        gbuf = py_out if py_out is not None else c_out
        for i, (s, acc, P) in enumerate(cs):
            base = 9 * 3 * i
            def rd(k, gbuf=gbuf, base=base):
                return sum(gbuf[base + 9 * k + j] * 243 ** j for j in range(9))
            gr = rd(0)
            ge = rd(1)
            oraw, oend, kind = oracle_strtod(s)
            check("strtod %r raw" % s, gr, oraw)
            check("strtod %r end" % s, ge, oend)


def repr_cstr(s):
    out = '"'
    for ch in s:
        if ch == '"':
            out += '\\"'
        elif ch == '\\':
            out += '\\\\'
        else:
            out += ch
    return out + '"'


# ============ §4 ecvt/fcvt/gcvt 套件 ============
def suite_ecvt_fcvt_gcvt():
    global G_OUT_BYTES
    CH = 40                            # 分块防全局踩字符串区
    random.seed(97)
    raws = []
    for i in range(150):
        e3 = random.randint(-200, 200)
        M = random.randint(3 ** 33, 3 ** 34 - 1)
        raws.append(M + (e3 + 29524) * 3 ** 34)
    raws += [0, 3 ** 44, tf.TF64.INF_POS, tf.TF64.INF_NEG, tf.TF64.NAN,
             3 ** 33 + 29524 * 3 ** 34,          # 1.0
             2 * 3 ** 33 + 29524 * 3 ** 34]      # 2.0
    N = len(raws)

    # ---- ecvt/fcvt/gcvt：每 call 一个独立 guest 程序 ----
    # trirv 链接器对多单元全局槽不重定位：主文件的提升局部量/全局数组会与
    # 库单元全局在同一槽碰撞（实测 ec_core 的 g_ec_w 被踩后 ec_mul 死循环）。
    # 单 call 程序已证 48/48 全过（bisector），故按 call 隔离。
    import trirv_vm as tvm

    def rd_word(buf, word_off):
        return sum(buf[word_off * 9 + k] * 243 ** k for k in range(9))

    def suite_one(fn_name, decl_head, call_specs, check_fn):
        for k, (call_src, oracle_args) in enumerate(call_specs):
            srcg = (decl_head +
                    "int main(void) {\n" + call_src + "\n    return 0;\n}\n")
            sp = os.path.join(tempfile.mkdtemp(prefix="ec_e2e_"), fn_name + "_main.c")
            with open(sp, 'w') as f:
                f.write(srcg)
            py_out, c_out, exitc, err = run_guest(sp, [os.path.join(HERE, "musl_tri/ecvt_tri.c")])
            if err:
                check("%s#%d 编译/运行" % (fn_name, k), err, None)
                continue
            if py_out is not None:
                check("%s#%d PyVM/CVM 输出一致" % (fn_name, k), py_out == c_out, True)
            check_fn(k, c_out, oracle_args)

    # ---- ecvt ----
    ns = [1, 5, 15, 16, 0, 20]

    def ecvt_check(k, buf, oracle_args):
        raw, nn = oracle_args
        eff = nn if 1 <= nn <= 16 else 15
        dp = tvm.s(rd_word(buf, 0))
        sg = tvm.s(rd_word(buf, 1))
        digs = bytes(buf[18 + 9 * i] for i in range(eff)).decode(errors="replace")
        ods, odp, osg = oracle_ecvt(raw, nn)
        check("ecvt raw#%d n=%d digits" % (k, nn), digs, ods)
        check("ecvt raw#%d n=%d dp/sig" % (k, nn), (dp, sg), (odp, osg))

    call_specs = []
    for nn in ns:
        eff = nn if 1 <= nn <= 16 else 15      # musl 钳制：n<1||n>16 → 15
        for raw in raws:
            call_src = ('    { int rv = %d; int dp = -9, sg = -9;'
                        ' int *b = ecvt(rv, %d, &dp, &sg);'
                        ' write(1, &dp, 9); write(1, &sg, 9);'
                        ' write(1, b, 9 * %d); }' % (raw, eff, eff + 1))
            call_specs.append((call_src, (raw, nn)))
    suite_one("ecvt", "int *ecvt(int raw, int n, int *decpt, int *sign);\n",
              call_specs, ecvt_check)

    # ---- fcvt ----
    nsf = [0, 2, 6, 15, 1000]

    _fc_dbg = {"n": 0}

    def fcvt_check(k, buf, oracle_args):
        raw, nn = oracle_args
        try:
            _fc_inner(k, buf, raw, nn)
        except IndexError:
            print("FCVT OOB call#%d buflen=%d" % (k, len(buf)), flush=True)
            raise

    def _fc_inner(k, buf, raw, nn):
        dp = tvm.s(rd_word(buf, 0))
        sg = tvm.s(rd_word(buf, 1))
        chars = []
        wi = 0
        maxw = len(buf) // 9
        while 2 + wi < maxw:
            w = rd_word(buf, 2 + wi)
            if w == 0:
                break
            chars.append(chr(w % 243))
            wi += 1
            if wi > 17:
                break
        ds = ''.join(chars)
        ods, odp, osg, _ = oracle_fcvt(raw, nn)
        check("fcvt raw#%d n=%d digits" % (k, nn), ds, ods)
        check("fcvt raw#%d n=%d dp/sig" % (k, nn), (dp, sg), (odp, osg))

    call_specs = []
    for nn in nsf:
        for raw in raws:
            call_src = ('    { int rv = %d; int dp = -9, sg = -9;'
                        ' char *b = fcvt(rv, %d, &dp, &sg);'
                        ' write(1, &dp, 9); write(1, &sg, 9);'
                        ' write(1, b, 144); }' % (raw, nn))
            call_specs.append((call_src, (raw, nn)))
    suite_one("fcvt", "int *fcvt(int raw, int n, int *decpt, int *sign);\n",
              call_specs, fcvt_check)

    # ---- gcvt ----
    nsg = [1, 6, 13, 17, 0]

    def gcvt_check(k, buf, oracle_args):
        raw, nn = oracle_args
        gs = bytes(buf[i] for i in range(64)).split(b'\x00')[0].decode(errors="replace")
        og = oracle_gcvt(raw, nn)
        check("gcvt raw#%d nd=%d" % (k, nn), gs, og)

    call_specs = []
    for nn in nsg:
        for raw in raws:
            call_src = ('    { int rv = %d; char GB[64]; char *b = gcvt(rv, %d, GB);'
                        ' write(1, GB, 64); }' % (raw, nn))
            call_specs.append((call_src, (raw, nn)))
    suite_one("gcvt", "int *gcvt(int raw, int n, char *buf);\n",
              call_specs, gcvt_check)


def main():
    global QUICK
    QUICK = '--quick' in sys.argv
    self_check()
    suite_strtod()
    suite_ecvt_fcvt_gcvt()
    print("\n浮点 libc 契约：%d PASS / %d FAIL" % (passed, failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
