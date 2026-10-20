#!/usr/bin/env python3
"""终端层地基（B 阶段第 3 步）：安全 raw mode。

最高风险项（宪章 §4 风险 2）：raw mode 下异常退出若不恢复 termios，终端会坏在 raw 状态。
本模块只做一件事并把它做对：进入 raw、**任何**退出路径（含异常）都恢复原 termios。
"""
import sys
import termios
import tty
from contextlib import contextmanager


@contextmanager
def raw_mode(fd=None):
    """进入 raw mode，退出时（正常/异常）恢复原设置。

    - 拿不到原 termios（非 tty）时安全降级：不 setraw，也不在退出时瞎设。
    - 恢复用 TCSADRAIN，确保挂在输入队列上的字节先冲干净。
    """
    if fd is None:
        fd = sys.stdin.fileno()
    try:
        old = termios.tcgetattr(fd)
    except (termios.error, OSError, ValueError):
        old = None
    try:
        if old is not None:
            tty.setraw(fd)
        yield fd
    finally:
        if old is not None:
            termios.tcsetattr(fd, termios.TCSANOW, old)