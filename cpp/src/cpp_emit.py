#!/usr/bin/env python3
"""cpp_emit —— Layer 6 发射：装配 struct/函数/main 为 trirv_cc 可编译 C 源。"""


def emit(units):
    parts = list(units["structs"]) + list(units["funcs"])
    if units.get("main"):
        parts.append(units["main"])
    return "\n\n".join(parts) + "\n"
