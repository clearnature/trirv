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
    # BITOP 字节位图技巧依赖二进制位运算（tryte 上语义不同）——整文件标记
    if 'BITOP' in text or '<<8 |' in text or '<<24 |' in text:
        text = '#error TRIRV_SKIP: byte-packing trick relies on binary bit ops\n' + text
    if '__volatile__' in text:
        text = '#error TRIRV_SKIP: inline asm barrier\n' + text
    if re.search(r'\[\s*[A-Z][A-Z_]*\s*\]\s*=', text):
        text = '#error TRIRV_SKIP: designated initializers + signal constants (no signal subsystem)\n' + text
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
    # 类型替换
    text = text.replace('size_t', 'int').replace('uintptr_t', 'int')
    text = text.replace('uint32_t', 'int').replace('uint64_t', 'int')
    text = text.replace('uint8_t', 'int').replace('uint16_t', 'int')
    # (void*) cast 保留（编译器支持 void*）
    return text

if __name__ == '__main__':
    src = open(sys.argv[1]).read()
    print(preprocess(src))
