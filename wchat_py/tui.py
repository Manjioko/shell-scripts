#!/usr/bin/env python3
"""Textual 全屏交互前端(M0 原型)。设计见 docs/tui-redesign.md。

目标:用成熟 TUI 框架(Textual)取代"prompt_toolkit 内联 + StreamBox 相对重绘",
从根本上消除输入框 resize/切 tab 残影与流箱漂移那一整类问题。

本模块**懒加载 textual**:没装就由调用方回退旧交互(见 cli.main 的 WCHAT_TUI 分支)。
M0 只做"外壳 + 输出路由 + 输入→一轮"的接线;流式增量、输入特性迁移、确认弹窗见文档 M1/M2。

公开接口:
    available() -> bool               # textual 是否可导入
    run_tui(handle_line, *, banner="") # 跑全屏应用;handle_line(text, emit) 在 worker 里执行
    TuiStdout(app)                     # 把 sys.stdout.write 收进 RichLog 的 shim

线程模型(关键,勿改乱):
  · Textual 事件循环在**主线程**;turn 执行(阻塞,起子进程/等 LLM)放 `@work(thread=True)`。
  · worker 里**不直接碰 widget**,只把文本塞进线程安全缓冲(TuiStdout)。
  · 主线程用 `set_interval` 定时把缓冲 drain 进 RichLog(Text.from_ansi 保留颜色)。
    这样既满足"不冻 UI",又避免跨线程调用 widget 的竞态。
"""
import os
import sys
import threading


def available():
    """textual 是否可用(懒加载探测,不 import 失败即 False)。"""
    try:
        import textual  # noqa: F401
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# stdout -> RichLog 桥:让现有 wchat 代码照常 `sys.stdout.write(...)` 即可上屏,
# 不必改那几十处写死 stdout 的调用点。缓冲 + 主线程定时 drain,天然线程安全。
# ---------------------------------------------------------------------------
class TuiStdout:
    def __init__(self):
        self._lock = threading.Lock()
        self._buf = []

    def write(self, s):
        if not s:
            return
        with self._lock:
            self._buf.append(str(s))

    def flush(self):
        # 主动 flush 不做事:统一由主线程 drain,避免跨线程碰 widget。
        pass

    def isatty(self):
        return False        # 让依赖 isatty 的分支走"非交互"(但它们多半已被 TUI 分支绕过)

    def drain(self):
        with self._lock:
            if not self._buf:
                return ""
            s = "".join(self._buf)
            self._buf = []
        return s


def _install_stdout_shim():
    """把 sys.stdout 换成 TuiStdout,返回 (shim, 原 stdout)。调用方负责 finally 还原。"""
    shim = TuiStdout()
    orig = sys.stdout
    sys.stdout = shim
    return shim, orig


def make_app(handle_line, *, banner=""):
    """构造(但不 run)全屏 WchatApp 实例。供 run_tui 与 headless 闸门共用。
    textual 缺失时抛 RuntimeError。"""
    if not available():
        raise RuntimeError("textual 未安装")
    from textual.app import App, ComposeResult
    from textual.widgets import RichLog, Input
    from rich.text import Text
    from textual import work

    class WchatApp(App):
        CSS = """
        Screen { layout: vertical; }
        #content { height: 1fr; border: round $primary; padding: 0 1; }
        #input { dock: bottom; height: 3; }
        """
        BINDINGS = [
            ("ctrl+c", "maybe_quit", "退出"),
            ("ctrl+d", "maybe_quit", "退出"),
        ]

        def compose(self) -> ComposeResult:
            yield RichLog(id="content", highlight=False, markup=False, wrap=True)
            yield Input(placeholder="❯ 说点什么…(回车发送,Ctrl+C 退出)", id="input")

        def on_mount(self):
            self._busy = False
            self._shim = None
            log = self.query_one("#content", RichLog)
            if banner:
                log.write(Text.from_ansi(banner))
            log.write(Text("内容区:模型输出都在这里;底部输入固定不动。", style="dim"))
            self.query_one("#input", Input).focus()
            # 定时把 worker 写入的 stdout 缓冲 drain 进 RichLog
            self.set_interval(0.05, self._drain_stdout)

        def _drain_stdout(self):
            if self._shim is None:
                return
            s = self._shim.drain()
            if s:
                try:
                    self.query_one("#content", RichLog).write(Text.from_ansi(s))
                except Exception:
                    pass

        def _emit(self, s):
            # 供 handle_line 的 emit 用:直接走 shim,和 stdout 同一条通道
            if self._shim is not None:
                self._shim.write(s)

        def on_input_submitted(self, event):
            text = (event.value or "").strip()
            event.input.value = ""
            if not text:
                return
            log = self.query_one("#content", RichLog)
            log.write(Text("❯ " + text, style="reverse"))
            if self._busy:
                log.write(Text("(上一轮仍在进行,请稍候…)", style="dim"))
                return
            self._busy = True
            self._run_turn(text)

        @work(thread=True, exclusive=True)
        def _run_turn(self, text):
            shim, orig = _install_stdout_shim()
            self._shim = shim
            try:
                handle_line(text, self._emit_threadsafe)
            except Exception as e:
                self.call_from_thread(self._append_err, "处理失败: %r" % (e,))
            finally:
                sys.stdout = orig
                self.call_from_thread(self._on_turn_done)

        def _emit_threadsafe(self, s):
            # worker 线程里只写缓冲,不碰 widget
            if self._shim is not None:
                self._shim.write(s)

        def _append_err(self, msg):
            try:
                self.query_one("#content", RichLog).write(Text(msg, style="bold red"))
            except Exception:
                pass

        def _on_turn_done(self):
            # 收尾前再 drain 一次
            self._drain_stdout()
            self._busy = False
            try:
                self.query_one("#input", Input).focus()
            except Exception:
                pass

        def action_maybe_quit(self):
            self.exit()

    return WchatApp()


def run_tui(handle_line, *, banner=""):
    """跑全屏 TUI。handle_line(text, emit) -> None:执行一轮(阻塞),emit(str) 追加到内容区。

    返回进程退出码(0)。textual 缺失时抛 RuntimeError,由调用方回退。
    """
    make_app(handle_line, banner=banner).run()
    return 0


if __name__ == "__main__":
    # 独立自测:handle_line 回显一句。
    def _demo(text, emit):
        emit("你说:%s\n" % text)

    sys.exit(run_tui(_demo, banner="wchat TUI 自测(仅 M0 外壳)"))