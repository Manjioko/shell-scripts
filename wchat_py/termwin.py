#!/usr/bin/env python3
"""终端窗口渲染的纯格式化部分（B 阶段第 3 步 3b-1）。

只搬**不碰信号/进程/O**的部分：`clean_line`（剥 \\r/ANSI/退格残留）与 `paint`（把窗口
缓冲画成一段转义字节）。FIFO/进程组/信号/job control 仍在 bash，后续切片再碰。

逐字对齐 wchat.sh 1981–2013（含 `\\r\\n` 显式行尾、整行擦除 `\\033[2K`、边框 `\\033[2m│\\033[0m`）。
"""
import re

# bash extglob: \e\[+([0-9;?])[[:alpha:]]  =>  ESC '[' 一个或多个 [0-9;?] 再一个字母
_ANSI = re.compile(r"\x1b\[[0-9;?]+[A-Za-z]")


def clean_line(line):
    s = line.replace("\r", "")
    s = _ANSI.sub("", s)
    s = s.replace("^D\b\b", "")   # BSD script 在 EOF 时的 "^D"+退格残留
    s = s.replace("\b", "")
    return s


def paint(buf, partial, drawn):
    """返回一段字符串，等价 bash `paint` 的 printf 输出。

    `drawn` 是上一帧已画的完整行数（用于 \033[%dA 上移）；`partial` 为空则藏光标。
    调用方画完后应把 drawn 更新为 len(buf)。
    """
    out = ["\r"]
    if drawn > 0:
        out.append("\x1b[%dA" % drawn)
    for i in buf:
        out.append("\x1b[2K\x1b[2m│\x1b[0m %s\r\n" % i)
    out.append("\x1b[2K")
    if partial:
        out.append("\x1b[2m│\x1b[0m \x1b[33m%s\x1b[0m\x1b[?25h" % partial)
    else:
        out.append("\x1b[?25l")
    return "".join(out)

