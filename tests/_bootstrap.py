"""模块路径引导（目录重组后统一入口——所有测试在 import trirv_* 前先 import 本模块）。"""
import os as _os
import os
import _bootstrap  # noqa: F401（模块路径引导——统一 sys.path）
import sys as _sys

_R = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))  # 仓库根
for _d in ("isa", "vm", "float", "libc-musl", "cc", "tools", "wasm", ""):
    _p = _os.path.join(_R, _d)
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
