"""TriRV 统一容器加载器（批次 3.9，ADR-17）：TRV1/TRV2 双格式。

所有测试的容器解析统一走这里；.trv2 文件返回流序词 + packed=True，
调用方用 load_vm() 构造接好流制引擎的 PyVM。
"""
import struct

import trirv_isa as isa
import trirv_vm as vm


def load_any(path):
    """容器文件 → (prog, mem, packed)。TRV2 的 prog 为流序词。"""
    return load_bytes(open(path, "rb").read())


def load_bytes(data):
    """容器字节 → (prog, mem, packed)。load_any 的字节入口（3b.1b：各套件
    手写 TRV1 头解析统一收编到此——读入 bytes 的调用方免二次开文件）。"""
    magic = data[:4]
    n_seg = struct.unpack_from("<I", data, 12)[0]
    mem = {}
    if magic == b"TRV2":
        n_trytes = struct.unpack_from("<I", data, 8)[0]
        stream = list(data[16:16 + n_trytes])
        off = 16 + n_trytes
        prog = vm.StreamProg(isa.stream_to_words(stream))   # 3b.2：标记→VM 自动流制
        packed = True
    elif magic == b"TRV1":
        n_inst = struct.unpack_from("<I", data, 8)[0]
        prog = list(struct.unpack_from("<%dI" % n_inst, data, 16))
        off = 16 + 4 * n_inst
        packed = False
    else:
        raise ValueError("%s: 非 TRV1/TRV2 镜像" % path)
    for _ in range(n_seg):
        addr, ln = struct.unpack_from("<II", data, off)
        off += 8
        mem[addr] = list(data[off:off + ln])
        off += ln
    return prog, mem, packed


def load_vm(path, stdin=b"", max_steps=None):
    """容器文件 → 接好引擎的 PyVM（TRV2 自动流制）。"""
    prog, mem, packed = load_any(path)
    v = vm.VM(prog, mem_init=mem, stdin=stdin)
    if packed:
        v.packed = True
        v.imem_offs = isa.stream_offsets(isa.pack_stream(prog))
    return v


def run_vm(path, stdin=b"", max_steps=100000):
    """加载并跑完 → VM 实例。"""
    v = load_vm(path, stdin=stdin)
    return v.run(max_steps=max_steps)
