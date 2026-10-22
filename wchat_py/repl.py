#!/usr/bin/env python3
"""REPL 读行循环骨架（B 阶段主循环迁移第二刀）。

职责：读行 → `cmdline.classify` → 分发：
  · text   → `on_text(arg)`（默认接 `cli.run_turn`，单轮已有 1c/run_turn）
  · quit   → 结束
  · 其余   → `on_meta(action, arg)`（默认打印"尚未在 Python 主循环实现"占位，副作用逐步迁）

只做**循环与分发**，不碰 tty/信号/网络——便于用假 stream + 假 handler 单测。
"""
import sys

from wchat_py.cmdline import classify


def _default_meta(action, arg):
    if action == "help":
        from wchat_py.cli import help_user
        help_user()
        return True
    if action == "usage":
        from wchat_py.cli import inp_msg
        from wchat_py.session import Session, usage_lines
        for ln in usage_lines(Session()):
            inp_msg(ln)
        return True
    sys.stdout.write("[元命令 %s] 尚未在 Python 主循环实现(仍在 bash)\n" % action)
    return True


def run_repl(stream, on_text, on_meta=None):
    """读 stream（每行一条）直到 EOF 或 -q。on_text(arg)、on_meta(action,arg)->bool。"""
    on_meta = on_meta or _default_meta
    for raw in stream:
        line = raw.rstrip("\n")
        if line == "":
            continue
        action, arg = classify(line)
        if action == "quit":
            return 0
        if action == "text":
            on_text(arg)
        else:
            if on_meta(action, arg) is False:
                return 0
    return 0


if __name__ == "__main__":
    from wchat_py.cli import run_turn
    sys.exit(run_repl(sys.stdin, lambda t: run_turn(t)))