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
    # 剥 restrict
    text = text.replace('restrict ', '').replace(' restrict', '')
    # __attribute__((...)) 丢弃
    text = re.sub(r'__attribute__\s*\(\(\s*[^)]*\)\)', '', text)
    # #include 全丢
    text = re.sub(r'#include\s*[<"].*[>"]\n?', '', text)
    # 对象式 #define NAME body（无参数）——展开；函数式宏丢弃行
    defines = {}
    kept = []
    for line in text.split('\n'):
        m = re.match(r'#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\s+(.*)$', line)
        mf = re.match(r'#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\(', line)
        if mf:
            continue                    # 函数式宏（HASZERO 等）：丢行
        if m:
            defines[m.group(1)] = m.group(2).strip()
            continue
        kept.append(line)
    text = '\n'.join(kept)
    # 展开（多轮处理嵌套，上限 5）
    for _ in range(5):
        changed = False
        for name, body in defines.items():
            new = re.sub(r'\b' + name + r'\b', body.replace('\\', '\\\\'), text)
            if new != text:
                text = new; changed = True
        if not changed:
            break
    # UCHAR_MAX / INT_MAX 等常见常量（limits.h 语义，TriRV 45-trit 下按
    # 视图层 8-bit 语义给值：UCHAR_MAX=255）
    text = re.sub(r'\bUCHAR_MAX\b', '255', text)
    text = re.sub(r'\bINT_MAX\b', '2147483647', text)
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
