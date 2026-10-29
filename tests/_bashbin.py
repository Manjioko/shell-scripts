#!/usr/bin/env python3
"""bash 5+ 解析器(Python 侧)—— 与 tests/lib_bash.sh **同一套顺序**,不要各写一份。

为什么需要:测试里写死 "/opt/homebrew/bin/bash" 在 Linux 上不会抛
FileNotFoundError 之外的清晰信号,而是让子进程里的一切失败后"输出为空"——
断言于是拿到空字符串,红得很像产品 bug(实测:工具运行 parity 全红)。
"""
import os
import shutil
import subprocess


def _ok(path):
    if not path or not os.access(path, os.X_OK):
        return False
    try:
        out = subprocess.run([path, "-c", "echo ${BASH_VERSINFO[0]:-0}"],
                             capture_output=True, timeout=10)
    except Exception:
        return False
    v = out.stdout.decode("utf-8", "replace").strip()
    return v.isdigit() and int(v) >= 5


def bash_bin():
    """返回一个 bash>=5 的绝对路径;找不到抛 RuntimeError(而不是静默给个坏的)。"""
    cands = [
        os.environ.get("WCHAT_BASH"),
        os.environ.get("BASH_BIN"),
        shutil.which("bash"),
        "/opt/homebrew/bin/bash",
        "/usr/local/bin/bash",
        os.path.expanduser("~/.linuxbrew/bin/bash"),
        "/bin/bash",
        "/usr/bin/bash",
    ]
    for c in cands:
        if _ok(c):
            return c
    raise RuntimeError("需要 bash 5+(装一个 bash5,或设 WCHAT_BASH=/path/to/bash)")

