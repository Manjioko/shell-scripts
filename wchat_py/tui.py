#!/usr/bin/env python3
"""Textual 全屏交互前端。设计见 docs/tui-redesign.md。

用成熟 TUI 框架(Textual)取代"prompt_toolkit 内联 + StreamBox 相对重绘",从根本上
消除输入框 resize/切 tab 残影与流箱漂移。

M0:外壳 + 输出路由 + 输入→一轮。
M1:流式 —— 模型增量逐字进入**内容框内**的 live 区(#live 嵌在带边框的 #contentbox 里,
    看起来就在内容区内),完结后整段落进富文本日志(#content RichLog,markdown 渲染)。
M2:① 确认/审批走 MapConfirmModal(stdin 归事件循环,不能 readline);② 输入改用多行
    TextArea:回车发送、行尾 `\\` 续行、Ctrl+F 选文件;③ `-q` 等元命令经 classify+on_meta
    分发;④ 思考文本 dim 显示。

单线程安全事件队列:stream(增量,区分正文/思考)/log(横幅/工具/错误)按**发出顺序**入队,
主线程定时 drain 入 widget;遇 log 先把 live 定稿再写日志(流式正文在工具横幅之前)。

懒加载 textual:没装则 available()=False,由 cli.main 回退旧交互。

公开接口:
    available() -> bool
    make_app(handle_line, *, banner="", on_meta=None) -> App
    run_tui(handle_line, *, banner="", on_meta=None) -> int
    TuiStdout(app, style=None)
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
    """sys.stdout/stderr 的 shim:写入投进 app 事件队列(log 通道),主线程消费入 RichLog。"""

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


def make_app(handle_line, *, banner="", on_meta=None):
    """构造(不 run)WchatApp。handle_line(text, emit, stream, reason):
       emit(s)   -> log 通道(横幅/工具/错误)
       stream(s) -> live 区正文(普通亮度)
       reason(s) -> live 区思考(暗色 dim)
    on_meta(action, arg) 处理元命令(-q 之外的 -u/-n/... );-q 由 app 直接退出。
    textual 缺失抛 RuntimeError。"""
    if not available():
        raise RuntimeError("textual 未安装")
    from textual.app import App, ComposeResult
    from textual.widgets import Static, Button, Label, TextArea, Input
    from textual.containers import Vertical, Horizontal, Container, VerticalScroll
    from textual.screen import ModalScreen
    from textual.binding import Binding
    from textual.message import Message
    from rich.text import Text
    from rich.markdown import Markdown
    from textual import work
    import threading

    # ---- 输入框:多行 TextArea,回车发送/续行,Ctrl+F 选文件 ----
    class ChatInput(TextArea):
        BINDINGS = [
            Binding("enter", "submit_or_continue", "发送", priority=True),
            Binding("ctrl+f", "pick_file", "选文件"),
        ]

        class Submitted(Message):
            def __init__(self, value):
                self.value = value
                super().__init__()

        def action_submit_or_continue(self):
            text = self.text
            row, _col = self.cursor_location
            lines = text.split("\n")
            cur = lines[row] if row < len(lines) else ""
            # 行尾 `\` + 回车 = 续行(吃掉那个反斜杠,换到下一行),与旧 inputbox 同义
            if row == len(lines) - 1 and cur.rstrip().endswith("\\"):
                lines[row] = cur.rstrip()[:-1]
                self.text = "\n".join(lines) + "\n"
                try:
                    self.cursor_location = (len(lines), 0)
                except Exception:
                    pass
                return
            self.text = ""
            try:
                self.cursor_location = (0, 0)
            except Exception:
                pass
            self.post_message(self.Submitted(text))

        def action_pick_file(self):
            try:
                from wchat_py.repl import _pick_file, _file_placeholder
            except Exception:
                return
            try:
                p = _pick_file()
            except Exception:
                p = ""
            if p and p != "CANCELLED" and os.path.isfile(p):
                try:
                    self.insert(_file_placeholder(p) + "\n")
                except Exception:
                    pass

    class ConfirmModal(ModalScreen):
        """小弹窗(类 window/mac 的小确认框):标题 + 问题 + 是/否。"""
        BINDINGS = [("y", "yes", "是"), ("n", "no", "否"),
                    ("escape", "no", "否"), ("enter", "yes", "是")]

        def __init__(self, title, mark, question):
            super().__init__()
            self._title = title
            self._mark = mark
            self._question = question

        def compose(self):
            head = self._title + (("  " + self._mark) if self._mark else "")
            with Container(classes="dialog"):
                yield Label(Text(head[:78], style="bold"))
                yield Label(Text(self._question[:78]))
                yield Label(Text("y/回车=是  n/Esc=否", style="dim"))
                with Horizontal(classes="btns"):
                    yield Button("是", variant="success", id="yes")
                    yield Button("否", variant="error", id="no")

        def on_button_pressed(self, event):
            self.dismiss(event.button.id == "yes")

        def action_yes(self):
            self.dismiss(True)

        def action_no(self):
            self.dismiss(False)

    class InputModal(ModalScreen):
        """单行文本输入弹窗(如开启 auto 的 AUTO 签字)。dismiss(输入的字符串)。"""
        BINDINGS = [("escape", "cancel", "取消")]

        def __init__(self, question):
            super().__init__()
            self._question = question

        def compose(self):
            with Container(classes="dialog"):
                yield Label(Text(self._question[:120]))
                yield Input(id="imput", placeholder="回车确认 · Esc 取消")

        def on_mount(self):
            try:
                self.query_one("#imput", Input).focus()
            except Exception:
                pass

        def on_input_submitted(self, event):
            self.dismiss(event.value)

        def action_cancel(self):
            self.dismiss("")

    class WchatApp(App):
        CSS = """
        Screen { layout: vertical; }
        #contentbox { height: 1fr; border: round $primary; }
        #content { height: 1fr; padding: 0 1; }
        #input { dock: bottom; height: auto; max-height: 12; border: round $accent; }
        ConfirmModal { align: center middle; background: $background 60%; }
        ConfirmModal .dialog { width: 64; height: auto; max-height: 12; border: thick $warning;
                               background: $surface; padding: 1 2; }
        ConfirmModal .btns { height: auto; }
        ConfirmModal Button { min-width: 10; }
        InputModal { align: center middle; background: $background 60%; }
        InputModal .dialog { width: 64; height: auto; max-height: 14; border: thick $accent;
                             background: $surface; padding: 1 2; }
        """
        BINDINGS = [("ctrl+c", "maybe_quit", "退出"), ("ctrl+d", "maybe_quit", "退出")]

        def __init__(self):
            super().__init__()
            self._q = queue.Queue()
            self._reason = ""
            self._answer = ""
            self._live_widget = None
            self._busy = False

        def compose(self) -> ComposeResult:
            with Vertical(id="contentbox"):
                yield VerticalScroll(id="content")
            yield ChatInput(id="input")

        # ---- 内容区(消息列表:每段一个 Static,顺序 append,自然从提问下一行开始) ----
        def _content(self):
            return self.query_one("#content", VerticalScroll)

        def _scroll_end(self):
            try:
                self._content().scroll_end(animate=False)
            except Exception:
                pass

        def _mount(self, renderable):
            try:
                self._content().mount(Static(renderable))
                self._scroll_end()
            except Exception:
                pass

        def _ensure_live(self):
            if self._live_widget is None:
                w = Static("")
                try:
                    self._content().mount(w)
                    self._scroll_end()
                except Exception:
                    pass
                self._live_widget = w

        def on_mount(self):
            if banner:
                self._mount(Text.from_ansi(banner))
            self._mount(Text("内容区:模型输出在这里逐字出现;底部输入(多行,Ctrl+F 选文件)。", style="dim"))
            self.query_one("#input", ChatInput).focus()
            self.set_interval(0.04, self._drain)
            try:
                from wchat_py import render as _render
                _render.set_confirm_hook(self._confirm_blocking)
                _render.set_line_hook(self._line_blocking)
            except Exception:
                pass

        def _confirm_blocking(self, title, mark, question):
            if threading.current_thread() is threading.main_thread():
                return False
            ev = threading.Event()
            box = {}

            def _show():
                def _done(result):
                    box["r"] = bool(result)
                    ev.set()
                try:
                    self.push_screen(ConfirmModal(title, mark, question), _done)
                except Exception:
                    box["r"] = False
                    ev.set()

            self.call_from_thread(_show)
            ev.wait()
            return box.get("r", False)

        def _line_blocking(self, question=""):
            """被 worker 线程(read_line)调用:弹 InputModal 收一行文本,阻塞等结果。"""
            if threading.current_thread() is threading.main_thread():
                return ""
            ev = threading.Event()
            box = {}

            def _show():
                def _done(result):
                    box["r"] = result if isinstance(result, str) else ""
                    ev.set()
                try:
                    self.push_screen(InputModal(question or ""), _done)
                except Exception:
                    box["r"] = ""
                    ev.set()

            self.call_from_thread(_show)
            ev.wait()
            return box.get("r", "")

        def emit(self, s):
            self._q.put(("log", str(s), None))

        def stream(self, s):
            self._q.put(("live", str(s), False))

        def reason(self, s):
            self._q.put(("live", str(s), True))

        def _live_display(self):
            t = Text()
            if self._reason:
                t.append(self._reason, style="dim")
            if self._answer:
                t.append(self._answer)
            return t

        def _finalize_live(self):
            """把当前 live 缓冲定稿:移除 live 临时 widget,正文按 Markdown、思考按 dim 各挂一段。"""
            if self._live_widget is None:
                return
            w, reason, answer = self._live_widget, self._reason, self._answer
            self._live_widget = None
            self._reason = ""
            self._answer = ""
            try:
                w.remove()
            except Exception:
                pass
            if reason.strip():
                self._mount(Text(reason, style="dim"))
            if answer.strip():
                try:
                    self._mount(Markdown(answer))
                except Exception:
                    self._mount(Text(answer))

        def _drain(self):
            changed = False
            try:
                while True:
                    ev = self._q.get_nowait()
                    if ev[0] == "live":
                        if ev[2]:
                            self._reason += ev[1]
                        else:
                            self._answer += ev[1]
                        changed = True
                    else:  # log:先定稿 live 再挂日志(保证正文先于工具横幅,且都在提问下方顺次出现)
                        self._finalize_live()
                        style = ev[2] if len(ev) > 2 else None
                        self._mount(Text(ev[1], style=style) if style else Text.from_ansi(ev[1]))
            except queue.Empty:
                pass
            if changed and (self._reason or self._answer):
                self._ensure_live()
                try:
                    self._live_widget.update(self._live_display())
                except Exception:
                    pass
                self._scroll_end()

        def on_chat_input_submitted(self, event):
            text = event.value or ""
            if not text.strip():
                return
            self._mount(Text("❯ " + text, style="reverse"))
            # 元命令(-q/-u/-n/...)先分类;`-q` 直接退出,其余走 on_meta(text 才进模型)
            try:
                from wchat_py.cmdline import classify
                action, arg = classify(text.strip())
            except Exception:
                action, arg = "text", text
            if action == "quit":
                self.exit()
                return
            if action != "text":
                if self._busy:
                    self._mount(Text("(上一轮仍在进行,请稍候…)", style="dim"))
                    return
                self._busy = True
                self._run_meta(action, arg)
                return
            if self._busy:
                self._mount(Text("(上一轮仍在进行,请稍候…)", style="dim"))
                return
            self._busy = True
            self._run_turn(text)

        @work(thread=True, exclusive=True)
        def _run_turn(self, text):
            o_out, o_err = sys.stdout, sys.stderr
            sys.stdout = TuiStdout(self)
            sys.stderr = TuiStdout(self, style="bold red")
            try:
                handle_line(text, self.emit, self.stream, self.reason)
            except Exception as e:
                self.emit("\n处理失败: %r\n" % (e,))
            finally:
                sys.stdout, sys.stderr = o_out, o_err
                self.call_from_thread(self._on_turn_done)

        @work(thread=True, exclusive=True)
        def _run_meta(self, action, arg):
            o_out, o_err = sys.stdout, sys.stderr
            sys.stdout = TuiStdout(self)
            sys.stderr = TuiStdout(self, style="bold red")
            try:
                if action == "clear":
                    self.call_from_thread(self._clear_content)
                elif on_meta is not None:
                    on_meta(action, arg)
                else:
                    self.emit("(未接入元命令处理)\n")
            except Exception as e:
                self.emit("\n处理失败: %r\n" % (e,))
            finally:
                sys.stdout, sys.stderr = o_out, o_err
                self.call_from_thread(self._on_turn_done)

        def _clear_content(self):
            try:
                for c in list(self._content().children):
                    c.remove()
            except Exception:
                pass

        def _on_turn_done(self):
            self._drain()
            self._finalize_live()
            self._busy = False
            try:
                self.query_one("#input", ChatInput).focus()
            except Exception:
                pass

        def action_maybe_quit(self):
            self.exit()

    return WchatApp()


def run_tui(handle_line, *, banner="", on_meta=None):
    try:
        make_app(handle_line, banner=banner, on_meta=on_meta).run()
    finally:
        try:
            from wchat_py import render as _render
            _render.set_confirm_hook(None)
            _render.set_line_hook(None)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    def _demo(text, emit, stream, reason):
        reason("(思考中…)")
        import time as _t
        for ch in ("收到:" + text):
            stream(ch)
            _t.sleep(0.01)
        emit("\n")

    sys.exit(run_tui(_demo, banner="wchat TUI 自测(M2)"))