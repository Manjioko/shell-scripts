#!/usr/bin/env python3
"""Textual 全屏交互前端。设计见 docs/tui-redesign.md。

用成熟 TUI 框架(Textual)取代"prompt_toolkit 内联 + StreamBox 相对重绘",从根本上
消除输入框 resize/切 tab 残影与流箱漂移。

M0:外壳 + 输出路由 + 输入→一轮。
M1:流式接入 —— 模型增量逐字进入「live 区」(#live Static),完结后整段落进富文本日志
    (#content RichLog)。用一个**单线程安全事件队列**把 stream(增量) / log(横幅/工具/错误)
    按**发出顺序**排好、由主线程统一消费入 widget:既避免跨线程碰 widget,又保证"流式正文
    与工具横幅"的先后不乱(工具轮前 live 先定稿、再打横幅)。

本模块**懒加载 textual**:没装则 available()=False,由 cli.main 回退旧交互。

公开接口:
    available() -> bool
    make_app(handle_line, *, banner="") -> App   # handle_line(text, emit, stream)
    run_tui(handle_line, *, banner="") -> int
    TuiStdout(app)                                # sys.stdout/stderr 的 shim
"""
import os
import queue
import sys


def available():
    try:
        import textual  # noqa: F401
        return True
    except Exception:
        return False


class TuiStdout:
    """把 sys.stdout/stderr 的写入投进 app 的事件队列(log 通道),由主线程消费入 RichLog。
    这样现有 `sys.stdout.write(横幅/markdown/…)` 不必逐处改,排序也与 stream 一致。"""

    def __init__(self, app, style=None):
        self._app = app
        self._style = style

    def write(self, s):
        if s:
            self._app._q.put(("log", str(s), self._style))

    def flush(self):
        pass

    def isatty(self):
        return False


def make_app(handle_line, *, banner=""):
    """构造(不 run)全屏 WchatApp。handle_line(text, emit, stream) 在工作线程执行:
       emit(s)   -> log 通道(横幅/工具/错误/非流式文本)
       stream(s) -> live 区(模型流式增量,逐字)
    textual 缺失抛 RuntimeError。"""
    if not available():
        raise RuntimeError("textual 未安装")
    from textual.app import App, ComposeResult
    from textual.widgets import RichLog, Input, Static
    from rich.text import Text
    from rich.markdown import Markdown
    from textual import work

    class WchatApp(App):
        CSS = """
        Screen { layout: vertical; }
        #content { height: 1fr; border: round $primary; padding: 0 1; }
        #live { height: auto; max-height: 50%; padding: 0 1; }
        #input { dock: bottom; height: 3; }
        """
        BINDINGS = [("ctrl+c", "maybe_quit", "退出"), ("ctrl+d", "maybe_quit", "退出")]

        def __init__(self):
            super().__init__()
            # 单线程安全事件队列:("live", text) / ("log", text, style)。worker 只 put,
            # 主线程定时 get 并入 widget。
            self._q = queue.Queue()
            self._live = ""
            self._busy = False

        def compose(self) -> ComposeResult:
            yield RichLog(id="content", highlight=False, markup=False, wrap=True)
            yield Static("", id="live")
            yield Input(placeholder="❯ 说点什么…(回车发送 · Ctrl+C 退出)", id="input")

        def on_mount(self):
            log = self.query_one("#content", RichLog)
            if banner:
                log.write(Text.from_ansi(banner))
            log.write(Text("内容区:模型输出在这里逐字出现;底部输入固定不动。", style="dim"))
            self.query_one("#input", Input).focus()
            self.set_interval(0.04, self._drain)

        # 供 worker 直接调用(线程安全:只 put)
        def emit(self, s):
            self._q.put(("log", str(s), None))

        def stream(self, s):
            self._q.put(("live", str(s)))

        def _flush_live_locked(self):
            """把当前 live 缓冲定稿进 RichLog(用 Markdown 渲染),清空 live 区。"""
            if not self._live:
                return
            txt, self._live = self._live, ""
            try:
                self.query_one("#content", RichLog).write(Markdown(txt))
            except Exception:
                try:
                    self.query_one("#content", RichLog).write(Text(txt))
                except Exception:
                    pass
            try:
                self.query_one("#live", Static).update("")
            except Exception:
                pass

        def _drain(self):
            logs = self.query_one("#content", RichLog)
            changed_live = False
            try:
                while True:
                    ev = self._q.get_nowait()
                    if ev[0] == "live":
                        self._live += ev[1]
                        changed_live = True
                    else:  # log:先定稿 live,再写日志,保证流式正文在工具横幅之前
                        self._flush_live_locked()
                        style = ev[2] if len(ev) > 2 else None
                        try:
                            logs.write(Text.from_ansi(ev[1]) if style is None else Text(ev[1], style=style))
                        except Exception:
                            pass
            except queue.Empty:
                pass
            if changed_live:
                try:
                    self.query_one("#live", Static).update(Text(self._live))
                except Exception:
                    pass

        def on_input_submitted(self, event):
            text = (event.value or "").strip()
            event.input.value = ""
            if not text:
                return
            self.query_one("#content", RichLog).write(Text("❯ " + text, style="reverse"))
            if self._busy:
                self.query_one("#content", RichLog).write(Text("(上一轮仍在进行,请稍候…)", style="dim"))
                return
            self._busy = True
            self._run_turn(text)

        @work(thread=True, exclusive=True)
        def _run_turn(self, text):
            # worker:装 stdout/stderr shim(都投进 log 通道),跑一轮,还原。
            o_out, o_err = sys.stdout, sys.stderr
            sys.stdout = TuiStdout(self)
            sys.stderr = TuiStdout(self, style="bold red")
            try:
                handle_line(text, self.emit, self.stream)
            except Exception as e:
                self.emit("\n处理失败: %r\n" % (e,))
            finally:
                sys.stdout, sys.stderr = o_out, o_err
                self.call_from_thread(self._on_turn_done)

        def _on_turn_done(self):
            self._drain()          # 收尾:把残余事件、live 定稿刷干净
            self._busy = False
            try:
                self.query_one("#input", Input).focus()
            except Exception:
                pass

        def action_maybe_quit(self):
            self.exit()

    return WchatApp()


def run_tui(handle_line, *, banner=""):
    make_app(handle_line, banner=banner).run()
    return 0


if __name__ == "__main__":
    def _demo(text, emit, stream):
        for ch in ("收到:" + text):
            stream(ch)
            import time as _t
            _t.sleep(0.01)
        emit("\n")

    sys.exit(run_tui(_demo, banner="wchat TUI 自测(M1 流式)"))

