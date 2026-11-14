#!/usr/bin/env python3
"""wchat markdown 渲染助手:stdin 读 markdown,stdout 输出 rich 渲染的 ANSI。
与输入助手同属 Python 组件通道,三平台输出一致,替代 glow/mdcat 的分平台分装。
OSC-8 超链接序列统一剥离(各终端支持不一,样式链接保留显示);
样式(颜色/字形/左对齐标题)统一放在 `wchat_py/mdstyle.py`,与 TUI 渲染共用一份。
退出码:0=成功,2=缺 rich 依赖(调用方回退纯文本)。
"""
import io
import os
import re
import sys
import shutil

# 以脚本方式被调用(render_markdown 走 `python <此文件>`)时,sys.path[0] 是
# 本文件所在目录(wchat_py/),导入不到 `wchat_py.mdstyle`。把仓库根补进 sys.path,
# 这样能按包路径复用一个共享样式模块(而不是在两处各抄一份主题)。
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

_OSC8 = re.compile(r"\x1b\]8;.*?\x1b\\")


def _term_width() -> int:
    """终端宽度探测:/dev/tty ioctl(控制终端,最可靠)→ shutil → COLUMNS
    环境变量 → 兜底 80。渲染保留 4 列边距(与旧 glow/mdcat 一致)。"""
    try:
        with open("/dev/tty", "r") as tty:
            width = os.get_terminal_size(tty.fileno()).columns
    except (OSError, ValueError):
        try:
            width = shutil.get_terminal_size().columns
        except Exception:
            width = 0
    if width <= 0:
        try:
            width = int(os.environ.get("COLUMNS") or "0")
        except ValueError:
            width = 0
    if width <= 0:
        width = 80
    return max(20, width - 4)


def main() -> int:
    try:
        import rich  # noqa: F401  —— 缺 rich 依赖时按契约返回 2(调用方回退纯文本)
        from wchat_py.mdstyle import build_markdown, make_console
    except ImportError:
        return 2

    # 按字节读入再自行解码:输入里可能夹带被切掉一半的 UTF-8 序列(长行按字节
    # 截断的产物、旧版本存进历史文件的坏数据)。sys.stdin.read() 撞上非法字节会
    # 抛 UnicodeDecodeError,整篇文档一个字都渲染不出来;改成 replace 解码,坏
    # 字节降级为一个占位符,后面的内容照常渲染。
    text = sys.stdin.buffer.read().decode("utf-8", "replace")
    buf = io.StringIO()
    console = make_console(
        width=_term_width(),
        file=buf,
        force_terminal=True,
        highlight=False,
    )
    try:
        console.print(build_markdown(text))
    except Exception:
        return 2

    sys.stdout.write(_OSC8.sub("", buf.getvalue()))
    return 0


if __name__ == "__main__":
    sys.exit(main())