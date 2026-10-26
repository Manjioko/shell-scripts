#!/usr/bin/env python3
"""运行时自举:系统 Python 依赖齐全就直接用;否则创建/复用 venv 并 re-exec。

设计:
  · 常态路径零安装:只用 importlib.util.find_spec 探测(不 import,避免启动变慢)；
  · venv 放 ~/.ct/venv(按用户共享),多 tty 共用;首次创建用 fcntl.flock 串行化；
  · .ready 记录 requirements 哈希 + Python 版本,匹配即跳过；
  · WCHAT_NO_BOOTSTRAP=1 完全跳过(测试/CI 用)；
  · Pillow 视为可选:缺它只影响图片缩放(会退回 sips),不因此建 venv。
"""
import hashlib
import importlib.util
import os
import subprocess
import sys

_REQUIRED = ("openai", "prompt_toolkit", "rich")
_MARK = ".ready"


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _venv_dir():
    return os.environ.get("WCHAT_VENV") or os.path.join(
        os.environ.get("CT_HOME") or os.path.join(os.environ.get("HOME", ""), ".ct"),
        "venv")


def _deps_ok():
    try:
        return all(importlib.util.find_spec(m) is not None for m in _REQUIRED)
    except Exception:
        return False


def _fingerprint():
    req = os.path.join(_repo_root(), "requirements.txt")
    try:
        with open(req, "rb") as f:
            h = hashlib.sha256(f.read()).hexdigest()[:16]
    except OSError:
        h = "noreq"
    return "%s-py%d.%d" % (h, sys.version_info.major, sys.version_info.minor)


def _ready(venv):
    try:
        with open(os.path.join(venv, _MARK), encoding="utf-8") as f:
            return f.read().strip() == _fingerprint()
    except OSError:
        return False


def _venv_python(venv):
    return os.path.join(venv, "bin", "python")


def _log(msg):
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def _re_exec(venv):
    py = _venv_python(venv)
    if not os.path.isfile(py):
        return False
    os.environ["WCHAT_BOOTSTRAPPED"] = "1"
    argv = [py, os.path.abspath(sys.argv[0])] + sys.argv[1:]
    try:
        os.execv(py, argv)
    except OSError as e:
        _log("⚠ venv 启动失败(%s),回退系统 Python" % e)
        return False


def _install(venv):
    """创建 venv 并安装依赖;调用方须已持有锁。成功返回 True。"""
    py = _venv_python(venv)
    if not os.path.isfile(py):
        base = sys.executable or "python3"
        _log("📦 首次运行:创建虚拟环境 %s …" % venv)
        r = subprocess.run([base, "-m", "venv", venv])
        if r.returncode != 0 or not os.path.isfile(py):
            _log("⚠ 创建 venv 失败;继续用系统 Python(可能缺依赖)")
            return False
    req = os.path.join(_repo_root(), "requirements.txt")
    _log("📦 安装依赖(仅首次)…")
    r = subprocess.run([py, "-m", "pip", "install", "-q", "-r", req])
    if r.returncode != 0:
        _log("⚠ 依赖安装失败;请手动: %s -m pip install -r %s" % (py, req))
        return False
    try:
        with open(os.path.join(venv, _MARK), "w", encoding="utf-8") as f:
            f.write(_fingerprint())
    except OSError:
        pass
    return True


def _with_lock(fn):
    lock = _venv_dir() + ".lock"
    try:
        os.makedirs(os.path.dirname(lock), exist_ok=True)
        import fcntl
        with open(lock, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                return fn()
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
    except Exception:
        return fn()


def ensure_runtime():
    if os.environ.get("WCHAT_NO_BOOTSTRAP") == "1":
        return
    if os.environ.get("WCHAT_BOOTSTRAPPED") == "1":
        return
    # 已在 venv 中 / 系统依赖齐全 -> 直跑
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        return
    if _deps_ok() and os.environ.get("WCHAT_BOOTSTRAP_FORCE") != "1":
        return
    venv = _venv_dir()
    if _ready(venv):
        _re_exec(venv)
        return

    def _do():
        if _ready(venv):          # 等锁期间别人已装好
            _re_exec(venv)
            return
        if _install(venv):
            _re_exec(venv)
    _with_lock(_do)


if __name__ == "__main__":
    ensure_runtime()