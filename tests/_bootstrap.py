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


# ---------------------------------------------------------------- 草稿与产物目录
# 测试的中间文件、批量管线的产物一律落在仓库根 tmp/（.gitignore 已忽略），不写系统 /tmp：
# /tmp 重启即清，放在那里的东西项目管不住；而且会有多个会话在同一工作树并行跑回归，
# /tmp 下的同名文件会互相覆盖。
import atexit as _atexit
import shutil as _shutil
import time as _time

_TMP = _os.path.join(_R, "tmp")


def project_tmp(*parts):
    """仓库 tmp/ 下的固定路径（不自动清理）：需要跨运行保留的产物与日志用这个。"""
    p = _os.path.join(_TMP, *parts)
    _os.makedirs(_os.path.dirname(p), exist_ok=True)
    return p


def _stale_run_dir(path, name):
    """run-<pid> 目录能否清扫（它的进程已经不在了）。

    POSIX 用 os.kill(pid, 0) 探测存活。Windows 上 os.kill 对任何信号值都是
    TerminateProcess——信号 0 也不例外——绝不能拿来探测：轻则抛出未预料的异常，
    重则把该 PID 现在所属的进程结束掉。那里退回按修改时间判断：一次回归不过几分钟到
    十几分钟，超过半天没动过的目录视作残留。"""
    if _os.name == "posix":
        try:
            pid = int(name[4:])
        except ValueError:
            return False
        try:
            _os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except OSError:            # 还在（无权限探询）或探测本身出错：一律不删
            return False
        return False
    try:
        return _time.time() - _os.path.getmtime(path) > 6 * 3600
    except OSError:
        return False


_RUN_DIR = None


def run_dir():
    """本进程专属的草稿目录 tmp/scratch/run-<pid>/，进程正常退出时删除。"""
    global _RUN_DIR
    if _RUN_DIR is None:
        scratch_root = _os.path.join(_TMP, "scratch")
        _os.makedirs(scratch_root, exist_ok=True)
        for name in _os.listdir(scratch_root):     # 被杀掉的运行来不及自清：只清扫确已失效的
            if not name.startswith("run-"):
                continue
            stale = _os.path.join(scratch_root, name)
            if _stale_run_dir(stale, name):
                _shutil.rmtree(stale, ignore_errors=True)
        _RUN_DIR = _os.path.join(scratch_root, "run-%d" % _os.getpid())
        _shutil.rmtree(_RUN_DIR, ignore_errors=True)     # PID 复用撞上的旧目录不作数
        _os.makedirs(_RUN_DIR)
        _atexit.register(lambda: _shutil.rmtree(_RUN_DIR, ignore_errors=True))
    return _RUN_DIR


def scratch(*parts):
    """本进程草稿目录下的文件路径（父目录按需创建）。测试的中间文件一律从这里取。"""
    p = _os.path.join(run_dir(), *parts)
    _os.makedirs(_os.path.dirname(p), exist_ok=True)
    return p


def run_musl_batch():
    """跑一遍 libc-musl/batch_musl_compile.py，TRVO 产在本进程的草稿目录里。

    返回 (产物目录, CompletedProcess)——要读批量统计输出的套件用后者。"""
    import subprocess
    out = scratch("musl_batch")
    r = subprocess.run([_sys.executable, _os.path.join(_R, "libc-musl", "batch_musl_compile.py"), out],
                       capture_output=True, text=True)
    return out, r
