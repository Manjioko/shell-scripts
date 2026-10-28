#!/usr/bin/env python3
"""wchat markdown 渲染助手:stdin 读 markdown,stdout 输出 rich 渲染的 ANSI。
与输入助手同属 Python 组件通道,三平台输出一致,替代 glow/mdcat 的分平台分装。
OSC-8 超链接序列统一剥离(各终端支持不一,样式链接保留显示);
标题左对齐(rich 默认居中,观感与旧渲染器差异大且占大量空白)。
退出码:0=成功,2=缺 rich 依赖(调用方回退纯文本)。
"""
import io
import os
import re
import sys
import shutil

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
        from rich.console import Console
        from rich.markdown import Markdown as _Markdown
        from rich.markdown import Heading as _Heading
        from rich.text import Text
    except ImportError:
        return 2

    class _LeftHeading(_Heading):
        """标题左对齐(覆盖 rich 默认的居中——居中观感与旧渲染器差异大)"""

        def __rich_console__(self, console, options):
            self.text.justify = "left"
            if self.tag == "h1":
                # H1 保留边框面板,文字左对齐
                from rich.panel import Panel
                from rich import box
                yield Panel(
                    self.text,
                    box=box.HEAVY,
                    style="markdown.h1.border",
                )
            else:
                if self.tag == "h2":
                    yield Text("")
                yield self.text

    class Markdown(_Markdown):
        elements = dict(_Markdown.elements)
        elements["heading_open"] = _LeftHeading

    # 按字节读入再自行解码:输入里可能夹带被切掉一半的 UTF-8 序列(长行按字节
    # 截断的产物、旧版本存进历史文件的坏数据)。sys.stdin.read() 撞上非法字节会
    # 抛 UnicodeDecodeError,整篇文档一个字都渲染不出来;改成 replace 解码,坏
    # 字节降级为一个占位符,后面的内容照常渲染。
    text = sys.stdin.buffer.read().decode("utf-8", "replace")
    buf = io.StringIO()
    console = Console(
        width=_term_width(),
        file=buf,
        force_terminal=True,
        highlight=False,
    )
    try:
        console.print(Markdown(text))
    except Exception:
        return 2

    sys.stdout.write(_OSC8.sub("", buf.getvalue()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
