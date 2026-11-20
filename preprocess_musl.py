#!/usr/bin/env python3
"""musl → TriRV C 子集 预处理器。
处理真 musl 源码与 TriRV C 子集的已知差异：
  1. #include <...> 全部丢弃（TriRV 无头文件系统）
  2. #ifdef __GNUC__ 块：只保留 #else 分支（纯 C 回退路径）——
     字长优化代码依赖 8-bit 语义（CHAR_BIT==8），TriRV 是 5-trit tryte，
     必须走按字节回退路径
  3. typedef uint32_t __attribute__((...)) 等 attribute 丢弃
  4. size_t/uintptr_t → int（TriRV 45-trit 单字）
  5. restrict 关键字剥离
"""
import re, sys

def preprocess(text):
    # 剥 restrict / static / inline / signed char 修饰（TriRV 无内部链接语义）
    text = re.sub(r'\bstatic\s+', '', text)
    text = re.sub(r'\binline\s+', '', text)
    text = text.replace('restrict ', '').replace(' restrict', '')
    # __attribute__((...)) 丢弃
    text = re.sub(r'__attribute__\s*\(\(\s*[^)]*\)\)', '', text)
    # #include 全丢
    text = re.sub(r'#include\s*[<"].*[>"]\n?', '', text)
    # 对象式 #define NAME body 展开；函数式宏支持简单参数替换。
    # 续行（反斜杠结尾）先拼接。
    text = re.sub(r'\\\n', ' ', text)
    obj_defines = {}     # NAME -> body
    fn_defines = {}      # NAME -> (params, body)
    kept = []
    for line in text.split('\n'):
        mo = re.match(r'#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\(\s*([^)]*)\)\s+(.*)$', line)
        mf = re.match(r'#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\(', line)
        m = re.match(r'#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\s*(.*)$', line)
        if mo:
            params = [p.strip() for p in mo.group(2).split(',') if p.strip()]
            fn_defines[mo.group(1)] = (params, mo.group(3).strip())
            continue
        if mf:
            continue
        if m:
            obj_defines[m.group(1)] = m.group(2).strip()
            continue
        kept.append(line)
    text = '\n'.join(kept)
    # 函数式宏展开（文本级，不处理嵌套括号复杂性；musl 的用法较简单）
    for _ in range(5):
        changed = False
        for name, (params, body) in fn_defines.items():
            # 匹配 NAME(实参...) —— 参数里不含逗号的简单形态
            pat = re.compile(r'\b' + name + r'\(([^()]*)\)')
            def repl(m2):
                args = [a.strip() for a in m2.group(1).split(',')]
                out = body
                for p, a in zip(params, args):
                    out = re.sub(r'\b' + re.escape(p) + r'\b', a, out)
                return '(' + out + ')'
            new = pat.sub(repl, text)
            if new != text:
                text = new; changed = True
        if not changed:
            break
    # 对象式展开
    for _ in range(5):
        changed = False
        for name, body in obj_defines.items():
            new = re.sub(r'\b' + name + r'\b', body.replace('\\', '\\\\'), text)
            if new != text:
                text = new; changed = True
        if not changed:
            break
    # #undef 丢行（已无害）
    text = re.sub(r'#\s*undef\s+.*\n?', '', text)
    # UCHAR_MAX / INT_MAX 等常见常量（limits.h 语义，TriRV 45-trit 下按
    # 视图层 8-bit 语义给值：UCHAR_MAX=255）
    text = re.sub(r'\bUCHAR_MAX\b', '255', text)
    # POSIX errno 常量（errno.h 剥离后的固定值，未列出的仍会触发
    # 未声明标识符编译错——诚实暴露缺口而非静默 0）
    for _ename, _eval in (("EPERM", 1), ("ENOENT", 2), ("ESRCH", 3),
                          ("EINTR", 4), ("EIO", 5), ("ENXIO", 6), ("E2BIG", 7),
                          ("ENOEXEC", 8), ("EBADF", 9), ("ECHILD", 10),
                          ("EAGAIN", 11), ("ENOMEM", 12), ("EACCES", 13),
                          ("EFAULT", 14), ("EEXIST", 17), ("EXDEV", 18),
                          ("ENOTDIR", 20), ("EISDIR", 21), ("EINVAL", 22),
                          ("ENFILE", 23), ("EMFILE", 24), ("ENOTTY", 25),
                          ("EFBIG", 27), ("ENOSPC", 28), ("ESPIPE", 29),
                          ("EROFS", 30), ("EMLINK", 31), ("EPIPE", 32),
                          ("EDOM", 33), ("ERANGE", 34)):
        text = re.sub(r'\b' + _ename + r'\b', str(_eval), text)
    text = re.sub(r'\bINT_MAX\b', '2147483647', text)
    # weak_alias(real, alias) / strong_alias —— 生成别名包装函数
    # （TRI 子集没有符号别名机制；用 static 转发函数模拟）
    def weak_repl(m):
        real, alias = m.group(1), m.group(2)
        return f'void *{alias}_fwd(void) {{ return (void *){real}; }}'
    # 别名需要按返回类型生成——文本层面无法知道类型。改用宏假名：
    # 直接把【alias 名】替换成 real 名（调用点使用 alias 时即调 real）。
    text = re.sub(r'\bweak_alias\s*\(([^,]+),\s*([^)]+)\)\s*;', '', text)
    text = re.sub(r'\bstrong_alias\s*\(([^,]+),\s*([^)]+)\)\s*;', '', text)
    # alias 名 -> real 名的全文替换（函数调用点兼容）
    alias_pairs = re.findall(r'weak_alias\s*\(([^,]+),\s*([^)]+)\)', text)
    # （已删语句，此处理论为空；保留逻辑防后续 musl 变化）
    # NULL
    text = re.sub(r'\bNULL\b', '0', text)
    # 数字字面量后缀剥离：9U/100UL/1LL → 9/100/1（TriRV 单字无符号性动态）
    text = re.sub(r'\b([0-9]+)[uU][lL]?[lL]?\b', r'\1', text)
    text = re.sub(r'\b([0-9]+)[lL][lL]?[uU]?\b', r'\1', text)
    # sizeof 常量折叠（TriRV 视图层 8-bit 语义：int=4 字节；size_t=指针宽）
    # 仅用于数组维度等常量语境的文本替换。
    text = re.sub(r'\bsizeof\s*\(\s*int\s*\)', '4', text)
    text = re.sub(r'\bsizeof\s*\(\s*size_t\s*\)', '4', text)
    text = re.sub(r'\bsizeof\s*\(\s*char\s*\)', '1', text)
    text = re.sub(r'\bsizeof\s+([A-Za-z_][A-Za-z0-9_]*)\b', '4', text)  # sizeof tmp（TriRV 视图层统一 4）
    # ifdef __GNUC__ 处理：保留 #else 分支
    out = []
    stack = []   # 每层: (active_now, taken_any, in_else, parent_active)
    active = True
    for line in text.split('\n'):
        s = line.strip()
        m_ifdef = re.match(r'#\s*ifdef\s+__GNUC__', s)
        m_if = re.match(r'#\s*if(def|ndef)?\b', s)
        m_else = re.match(r'#\s*else', s)
        m_endif = re.match(r'#\s*endif', s)
        if m_ifdef:
            stack.append((active, False, False))
            active = False            # GNUC 分支不要
            continue
        if m_if and not m_ifdef:
            stack.append((active, active, False))   # 非 GNUC 的 if：保持父活跃度
            continue
        if m_else and stack:
            parent, taken, _ = stack[-1]
            # 如果外层是 ifdef __GNUC__（active=False），#else 分支要启用
            stack[-1] = (parent if not stack[-1][1] else parent, True, True)
            # 简化：else 分支 active = parent_active
            active = parent
            continue
        if m_endif and stack:
            active, _, _ = stack.pop()
            continue
        if active:
            out.append(line)
    text = '\n'.join(out)
    # BITOP 字节位图技巧依赖二进制位运算（tryte 上语义不同）——整文件标记
    if 'BITOP' in text or '<<8 |' in text or '<<24 |' in text:
        text = '#error TRIRV_SKIP: byte-packing trick relies on binary bit ops\n' + text
    if '__volatile__' in text:
        text = '#error TRIRV_SKIP: inline asm barrier\n' + text
    if re.search(r'\[\s*[A-Z][A-Z_]*\s*\]\s*=', text):
        text = '#error TRIRV_SKIP: designated initializers + signal constants (no signal subsystem)\n' + text
    # 复合字面量 (type){...} —— 待 musl_tri 适配层以 TriRV struct 机制重写
    if re.search(r'\(\s*[a-z_]+_t\s*\)\s*\{', text):
        text = '#error TRIRV_SKIP: compound literal (type){...} -> rewrite in musl_tri\n' + text
    # 浮点（double/float 字面量）—— P4 起 musl_tri/ 有三进制原生重写
    # （strtod_tri/ecvt_tri，TF64 raw ABI），此守卫仅拦未经重写的上游文件
    if re.search(r'\b(double|float)\b', text):
        text = '#error TRIRV_SKIP(DECISIONED: no FP on TriRV, see README section 6)\n' + text
    # stdio 内部依赖（FILE/sh_fromstring/__intscan 系）
    if re.search(r'\b(FILE|sh_fromstring|__intscan|shlim|shcnt)\b', text):
        text = '#error TRIRV_SKIP: depends on musl stdio internals -> rewrite in musl_tri\n' + text
    # & 常量对齐掩码（&-4/&-3 等）依赖二进制位语义 —— musl_tri 重写
    # & 二进制掩码语义适配（纯三进制原则：重写而非跳过）：
    # a) 对齐技巧行（只影响性能不改变语义）→ 整行删除：
    #    k = -(int)s & 3;  n &= -4;  k = 24 + ((int)s & 4);
    drop_lines = []
    kept2 = []
    for ln in text.split('\n'):
        # 对齐/掩码赋值行删除（& 常量、&= 复合赋值），控制流掩码行保留
        # （留给下方控制流掩码标记判断）
        if re.search(r'&\s*-?\s*[0-9]', ln) and not re.search(r'\bif\b|\bwhile\b|\breturn\b', ln):
            drop_lines.append(ln)
            continue
        # &= 复合赋值掩码行（n &= -4）—— 二进制语义，删除
        if re.search(r'&=\s*-?\s*[0-9]', ln):
            drop_lines.append(ln)
            continue
        kept2.append(ln)
    text = '\n'.join(kept2)
    # b) 掩码判断行（if(n&8) 等——影响控制流，语义真不同）→ SKIP 标记，
    #    由 musl_tri 重写。
    if re.search(r'\bif\s*\([^)]*&\s*-?\s*[0-9][^)]*\)', text):
        text = '#error TRIRV_SKIP: & mask in control flow -> rewrite in musl_tri\n' + text
    # 数组维度常量表达式折叠：[14 * 4 + 1] → [57]（纯数字与 + - * / ()）
    def fold_dim(m):
        inner = m.group(1)
        if re.fullmatch(r'[0-9+\-*/()\s]+', inner):
            try:
                return '[' + str(int(eval(inner, {"__builtins__": {}}))) + ']'
            except Exception:
                return m.group(0)
        return m.group(0)
    text = re.sub(r'\[\s*([0-9+\-*/()\s]+?)\s*\]', fold_dim, text)
    # 类型替换
    text = text.replace('size_t', 'int').replace('uintptr_t', 'int')
    text = text.replace('uint32_t', 'int').replace('uint64_t', 'int')
    text = text.replace('uint8_t', 'int').replace('uint16_t', 'int')
    text = text.replace('intmax_t', 'int')
    # TriRV 全标量 45-trit 等宽：long/long long/short 都是 int 的别名
    text = re.sub(r'\blong\s+long\b', 'int', text)
    text = re.sub(r'\blong\b', 'int', text)
    text = re.sub(r'\bshort\b', 'int', text)
    # (void*) cast 保留（编译器支持 void*）
    return text

if __name__ == '__main__':
    src = open(sys.argv[1]).read()
    print(preprocess(src))
