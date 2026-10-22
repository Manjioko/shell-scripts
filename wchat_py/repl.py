#!/usr/bin/env python3
"""REPL 读行循环骨架（B 阶段主循环迁移第二刀）。

职责：读行 → `cmdline.classify` → 分发：
  · text   → `on_text(arg)`（默认接 `cli.run_turn`，单轮已有 1c/run_turn）
  · quit   → 结束
  · 其余   → `on_meta(action, arg)`（默认打印"尚未在 Python 主循环实现"占位，副作用逐步迁）

只做**循环与分发**，不碰 tty/信号/网络——便于用假 stream + 假 handler 单测。
"""
import os
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
        for ln in usage_lines(Session.from_env()):
            inp_msg(ln)
        return True
    if action == "list_history":
        from wchat_py.session import history_list
        d = os.environ.get("WCHAT_HISTORY_DIR", "")
        names = history_list(d)
        if not names:
            sys.stdout.write("(无历史)\n")
        else:
            for n in names:
                sys.stdout.write(n + "\n")
        return True
    if action == "compact":
        from wchat_py.session import Session, compact_status
        cur, th, due = compact_status(Session.from_env())
        if th <= 0:
            sys.stdout.write("压缩阈值未设置,不动\n")
        elif due:
            sys.stdout.write("上下文已达压缩点(%d/%d)，需压缩(apply 需 ask_ai，暂未自动执行)\n" % (cur, th))
        else:
            sys.stdout.write("无需压缩(%d/%d)\n" % (cur, th))
        return True
    if action == "show_msgs":
        from wchat_py.cli import inp_msg
        from wchat_py.session import Session
        inp_msg(Session.from_env().messages_json)
        return True
    if action == "save_history":
        from wchat_py.session import Session, save_history
        d = os.environ.get("WCHAT_HISTORY_DIR", "")
        if not d:
            sys.stdout.write("⚠️ 未设置 WCHAT_HISTORY_DIR,未保存\n")
            return True
        name = os.environ.get("WCHAT_HISTORY_NAME", "py-session.json")
        path = save_history(d, Session.from_env().messages_json, name)
        sys.stdout.write("✅ 已保存: %s\n" % path)
        return True
    if action == "model_status":
        from wchat_py.cli import inp_msg
        from wchat_py.session import Session, model_status_lines
        for ln in model_status_lines(Session.from_env()):
            inp_msg(ln)
        return True
    if action in ("model_pro", "thinking_on", "model_flash"):
        from wchat_py.cli import inp_msg
        from wchat_py.session import Session, set_pro, set_flash, thinking_on
        s = Session.from_env()
        if action == "model_pro":
            inp_msg("✅ 已切换到 pro 模型 (%s)" % set_pro(s))
        elif action == "thinking_on":
            thinking_on(s)
            inp_msg("✅ 已开启思考模式")
        else:
            inp_msg("✅ 已恢复默认 (model=%s, thinking=disabled)" % set_flash(s))
        return True
    if action == "auto_on":
        from wchat_py.cli import inp_msg
        from wchat_py.session import Session, enter_auto
        s = Session.from_env()
        tier = arg or s.auto_mode or (s.auto_tiers[0] if s.auto_tiers else "")
        if enter_auto(s, tier) is None:
            inp_msg("未知档位: %s(可选: %s)" % (tier, " ".join(s.auto_tiers)))
        else:
            inp_msg("✅ 已开启 auto: %s 档(装配/回滚点由 auto_enable 负责，未迁)" % tier)
        return True
    if action == "auto_off":
        from wchat_py.cli import inp_msg
        from wchat_py.session import Session, auto_off
        s = Session.from_env()
        if not s.auto_mode:
            inp_msg("当前未开启 auto")
        else:
            auto_off(s)
            inp_msg("✅ 已关闭 auto,恢复逐次人工确认")
        return True
    if action == "image":
        from wchat_py.cli import inp_msg
        from wchat_py.session import Session, add_image
        if not arg:
            inp_msg("用法: -img <图片路径>")
        else:
            add_image(Session.from_env(), arg)
            inp_msg("[图片] %s" % arg)
        return True
    if action == "clear":
        sys.stdout.write("\033[H\033[2J")
        sys.stdout.flush()
        return True
    if action == "new_session":
        from wchat_py import msg
        from wchat_py.cli import inp_msg
        msg.init()                       # 新会话初始消息(系统注入后续接)
        sys.stdout.write("\033[H\033[2J")
        inp_msg("✅ 已开始新会话")
        return True
    sys.stdout.write("[元命令 %s] 尚未在 Python 主循环实现(仍在 bash)\n" % action)
    return True


def session_text_handler(session, call_llm, run_tool, out=None, write_final=True):
    """返回 on_text 处理器：正文 → orchestrate(Session, ...) → 输出最终文本。

    write_final=False 时不在末尾整段输出（正文已由 call_llm 的 on_text 流式画过）。
    Session 跨轮持久（messages 累积），这是 REPL 与 ask_ai 编排的接线点。
    """
    from wchat_py.session import orchestrate
    out = out if out is not None else sys.stdout

    def handler(text):
        r = orchestrate(session, text, call_llm, run_tool)
        if write_final:
            out.write((r or "") + "\n")

    return handler


def run_repl_loop(next_line, on_text, on_meta=None, on_quit=None):
    """核心循环：next_line()->str|None(EOF)。可注入，便于单测交互与非交互两种来源。

    on_quit() 在读到 -q 时、返回前调用一次（用于退出保存历史）。
    """
    on_meta = on_meta or _default_meta
    while True:
        line = next_line()
        if line is None:
            return 0
        line = line.rstrip("\n")
        if line == "":
            continue
        action, arg = classify(line)
        if action == "quit":
            if on_quit is not None:
                on_quit()
            return 0
        if action == "text":
            on_text(arg)
        else:
            if on_meta(action, arg) is False:
                return 0


def run_repl(stream, on_text, on_meta=None, on_quit=None):
    it = iter(stream)
    return run_repl_loop(lambda: next(it, None), on_text, on_meta, on_quit)


def run_interactive(on_text, on_meta=None, prompt="❯ ", on_quit=None):
    """交互式：从 tty 逐行读。EOF(Ctrl-D) 结束。"""
    def nxt():
        try:
            return input(prompt)
        except EOFError:
            return None
    return run_repl_loop(nxt, on_text, on_meta, on_quit)


if __name__ == "__main__":
    from wchat_py.cli import run_turn
    sys.exit(run_repl(sys.stdin, lambda t: run_turn(t)))