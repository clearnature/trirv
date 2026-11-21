#!/usr/bin/env python3
"""export_trv —— 把 TriRV C 源编译为 TRV1 程序文件（供 C VM 加载）。

原本属于 /workspace 工具链环境，此处补回以支持 trirv_difftest.py 在本地运行。
TRV1 格式（小端）：
    "TRV1" | uint32 ver=1 | uint32 n_inst | uint32 n_seg
           | n_inst × uint32 (20-trit 指令的原始编码)
           | n_seg × (uint32 addr, uint32 len, bytes data)
"""
import struct
import trirv_cc_backend as cc


def export(c_src_path, trv_path):
    with open(c_src_path, "r") as f:
        src = f.read()
    prog, labels, asm_text, strs = cc.compile_c(src)

    buf = bytearray(b"TRV1")
    buf += struct.pack("<III", 1, len(prog), len(strs))
    for raw in prog:
        buf += struct.pack("<I", raw & 0xFFFFFFFF)
    for addr, data in strs.items():
        buf += struct.pack("<II", addr, len(data))
        buf += bytes(data)

    with open(trv_path, "wb") as f:
        f.write(buf)
    return trv_path


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        print("用法: python3 export_trv.py <src.c> <out.trv>")
        sys.exit(1)
    export(sys.argv[1], sys.argv[2])
