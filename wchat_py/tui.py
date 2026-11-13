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
    """sys.stdout/stderr 的 shim:写入投进 app 事件队列。status=True 走状态区(输入框上方),
    否则走内容区。这样 auto 横幅/usage 等"侧栏信息"不污染对话正文。"""

    def __init__(self, app, style=None, status=False):
        self._app = app
        self._style = style
        self._status = status

    def write(self, s):
        if s:
            self._app._q.put(("status" if self._status else "log", str(s), self._style))

    def flush(self):
        pass

    def isatty(self):
        return False


def make_app(handle_line, *, banner="", on_meta=None):
    """构造(不 run)WchatApp。handle_line(text, emit, stream, reason[, poll_pending]):
       emit(s)   -> log 通道(横幅/工具/错误)
       stream(s) -> live 区正文(普通亮度)
       reason(s) -> live 区思考(暗色 dim)
    第 5 个可选参数 poll_pending():返回运行期补充消息列表(M4 边输出边输入)。只有
    handle_line 接受 5 参时才传,否则按 4 参调用(向后兼容)。
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
    import inspect

    # handle_line 是否接受第 5 个参数(poll_pending):据此决定要不要把轮内补充消息的取件器传进去。
    # 保持向后兼容——旧的 4 参 handle_line(tests 里那种)照旧调用,不会 TypeError。
    try:
        _sig = inspect.signature(handle_line)
        _has_var = any(p.kind == p.VAR_POSITIONAL for p in _sig.parameters.values())
        _npos = len([p for p in _sig.parameters.values()
                     if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)])
        _hl_takes_poll = _has_var or _npos >= 5
    except (TypeError, ValueError):
        _hl_takes_poll = False

    # ---- 输入框:多行 TextArea,回车发送/续行,Ctrl+F 选文件 ----
    class ChatInput(TextArea):
        BINDINGS = [
            Binding("enter", "submit_or_continue", "发送", priority=True),
            Binding("ctrl+f", "pick_file", "选文件"),
            Binding("ctrl+v", "paste_image", "粘贴图片"),
            Binding("ctrl+g", "paste_image", "粘贴图片"),
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

        def action_paste_image(self):
            """Ctrl+V/Ctrl+G:从系统剪贴板抓图片,插入 `📋 [图片] <路径>` 占位行。
            提交时 session 的 parse_placeholders 会把它读进 input_images(与老 inputbox 同管线)。"""
            try:
                from wchat_py import inputbox as _ibo
                from wchat_py import repl as _repl
            except Exception:
                return
            ct = os.environ.get("CT_HOME") or os.path.join(os.environ.get("HOME", ""), ".ct")
            clip_dir = os.path.join(ct, "clip")
            try:
                path = _ibo._clip_image_file(clip_dir)
            except Exception:
                path = None
            if path:
                try:
                    # 不加零宽标记(TUI 无 PTK 原子删除需求;零宽会污染路径)。
                    self.insert(_repl._PLACE_IMG + path + "\n")
                except Exception:
                    pass
            else:
                try:
                    self.app.bell()
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

    class _PageInput(Input):
        """只收数字、把 l/← / n/→ 解释成翻页的输入框(用于历史编号选择)。
        为什么覆写 _on_key 而不是用 Binding(priority=True):本版 Textual 里 ModalScreen 上的
        priority 绑定压不过聚焦 Input 的字符处理(实测 'l','n' 被当正文输入)。在 Input.**自己**
        的按键层拦截才可靠:l/n 调 owner.page_by() 翻页,其余交给原生 Input(数字照常输入)。
        用直接回调而非 post_message:nested Message 的处理函数命名在本版不可靠(实测 on_page_input_page
        没被调到),直连最稳。"""

        def __init__(self, owner=None, **kw):
            super().__init__(**kw)
            self._owner = owner

        async def _on_key(self, event):
            if self._owner is not None and event.key in ("l", "left"):
                self._owner.page_by(-1)
                event.stop()
                event.prevent_default()
                return
            if self._owner is not None and event.key in ("n", "right"):
                self._owner.page_by(1)
                event.stop()
                event.prevent_default()
                return
            await super()._on_key(event)

    class HistoryPickModal(ModalScreen):
        """从编号列表里选一条(如加载历史):候选(每页 10 条)列在输入框上方,l/n 翻页,
        底下输入编号。dismiss 输入的字符串(与 read_line 一致)。"""
        BINDINGS = [("escape", "cancel", "取消")]
        PER = 10

        def __init__(self, question, choices):
            super().__init__()
            self._question = question
            self._choices = [str(c) for c in (choices or [])]
            self._page = 0

        @property
        def _pages(self):
            n = len(self._choices)
            return max(1, (n + self.PER - 1) // self.PER)

        def compose(self):
            with Container(classes="dialog history"):
                yield Label(Text(self._question[:120]))
                yield Static("", id="hist_list")
                yield Label(Text("", style="dim"), id="hist_page")
                yield _PageInput(self, id="imput", placeholder="输入编号 · 回车确认 · Esc 取消")

        def on_mount(self):
            self._render_page()
            try:
                self.query_one("#imput", _PageInput).focus()
            except Exception:
                pass

        def page_by(self, delta):
            if delta < 0 and self._page > 0:
                self._page -= 1
                self._render_page()
            elif delta > 0 and self._page < self._pages - 1:
                self._page += 1
                self._render_page()

        def _render_page(self):
            start = self._page * self.PER
            end = min(start + self.PER, len(self._choices))
            rows = []
            for i in range(start, end):
                name = self._choices[i].replace("\n", " ").replace("\r", " ").replace("\t", " ")
                rows.append("%d. %s" % (i, name[:80]))
            try:
                self.query_one("#hist_list", Static).update(Text("\n".join(rows) or "(无)"))
                hint = "第 %d/%d 页 · l=上一页 n=下一页" % (self._page + 1, self._pages)
                self.query_one("#hist_page", Label).update(Text(hint, style="dim"))
            except Exception:
                pass

        def on_input_submitted(self, event):
            self.dismiss(event.value)

        def action_cancel(self):
            self.dismiss("")

    class WchatApp(App):
        CSS = """
        Screen { layout: vertical; padding: 0; }
        #contentbox { height: 1fr; border: none; padding: 0; margin: 0; }
        #content { height: 1fr; padding: 0 1; margin: 0; border: none;
                   scrollbar-size-vertical: 1;
                   scrollbar-color: #444444;
                   scrollbar-color-hover: #555555;
                   scrollbar-color-active: #666666;
                   scrollbar-background: transparent;
                   scrollbar-background-hover: transparent;
                   scrollbar-background-active: transparent; }
        #content .reason { text-wrap: nowrap; text-overflow: ellipsis; }
        #status { height: 0; padding: 0; margin: 0; }
        #status.on { height: auto; max-height: 6; padding: 0 1; color: #666666; }
        #input { height: auto; max-height: 12; background: transparent; border: round #6a6a6a; }
        #input:focus { background: transparent; }
        /* placeholder 与思考文本同档灰、斜体,用户好区分(默认 $text 40% 偏亮) */
        #input .text-area--placeholder { color: #6a6a6a; text-style: italic; }
        ConfirmModal { align: center middle; background: $background 60%; }
        ConfirmModal .dialog { width: 64; height: auto; max-height: 12; border: thick $warning;
                               background: $surface; padding: 1 2; }
        ConfirmModal .btns { height: auto; }
        ConfirmModal Button { min-width: 10; }
        InputModal { align: center middle; background: $background 60%; }
        InputModal .dialog { width: 64; height: auto; max-height: 14; border: thick $accent;
                             background: $surface; padding: 1 2; }
        HistoryPickModal { align: center middle; background: $background 60%; }
        HistoryPickModal .dialog { width: 74; height: auto; max-height: 24; border: thick $accent;
                                   background: $surface; padding: 1 2; }
        HistoryPickModal #hist_list { height: auto; max-height: 12; }
        HistoryPickModal #hist_page { height: auto; margin-top: 1; }
        """
        BINDINGS = [("ctrl+c", "maybe_quit", "退出"), ("ctrl+d", "maybe_quit", "退出")]
        # 加载历史时最多原生渲染这么多条(其余折叠;见 _mount_history)。避免整份几百条塞进
        # 内容区导致布局/渲染爆掉(那是 TUI 下"切换提示有了、正文空白"的真因)。
        HIST_MAX = 60

        def __init__(self):
            super().__init__()
            # 主题用 ansi-dark:Screen 背景发 \e[49m(=终端默认背景),与终端背景彻底融合、
            # 消除"内容区 vs 终端"的色差细缝;我们显式写死的 hex(灰边框/滚动条/灰字)不受影响。
            # (textual-dark 会把整屏涂成 #121212,与用户终端背景不同 → 有缝。)
            self.theme = "ansi-dark"
            self._q = queue.Queue()
            self._reason = ""
            self._answer = ""
            self._live_widget = None
            self._last_marker = None
            self._busy = False
            self._pending = queue.Queue()   # M4:运行期补充消息(线程安全);worker 每小轮 poll 注入

        def compose(self) -> ComposeResult:
            with Vertical(id="contentbox"):
                yield VerticalScroll(id="content")
            yield Static("", id="status")
            yield ChatInput(id="input", placeholder="请输入问题,回车发送")

        # ---- 内容区(消息列表:每段一个 Static,顺序 append,自然从提问下一行开始) ----
        def _content(self):
            return self.query_one("#content", VerticalScroll)

        def _scroll_end(self):
            try:
                self._content().scroll_end(animate=False)
            except Exception:
                pass

        def _mount(self, renderable, classes=""):
            try:
                w = Static(renderable, classes=classes) if classes else Static(renderable)
                self._content().mount(w)
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
            self.query_one("#input", ChatInput).focus()
            self.set_interval(0.04, self._drain)
            # TUI 把一轮 turn 放 worker 线程跑,而 signal handler 只能在**主线程**装
            # (pty_run/install_signal_cleanup 在 worker 里装会被跳过)。这里(on_mount 是主线程)
            # 先把"被 kill/SIGHUP 时收掉前台 shell 进程组 + MCP daemon"的清理装上,否则 shell 工具
            # 在 worker 里装不上 → wchat 被杀时前台命令变孤儿。幂等,失败不影响启动。
            try:
                from wchat_py.termpty import install_signal_cleanup
                try:
                    from wchat_py import mcp_boot as _mbc
                    install_signal_cleanup(extra=_mbc._atexit_stop)
                except Exception:
                    install_signal_cleanup()
            except Exception:
                pass
            try:
                from wchat_py import render as _render
                _render.set_confirm_hook(self._confirm_blocking)
                _render.set_line_hook(self._line_blocking)
                _render.set_history_hook(self._render_loaded_history)
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

        def _line_blocking(self, question="", choices=None):
            """被 worker 线程(read_line)调用:弹输入框收一行文本,阻塞等结果。
            给了 choices 时弹**分页列表选择框**(HistoryPickModal),否则普通单行输入框。"""
            if threading.current_thread() is threading.main_thread():
                return ""
            ev = threading.Event()
            box = {}

            def _show():
                def _done(result):
                    box["r"] = result if isinstance(result, str) else ""
                    ev.set()
                try:
                    if choices:
                        self.push_screen(HistoryPickModal(question or "", choices), _done)
                    else:
                        self.push_screen(InputModal(question or ""), _done)
                except Exception:
                    box["r"] = ""
                    ev.set()

            self.call_from_thread(_show)
            ev.wait()
            return box.get("r", "")

        def _render_loaded_history(self, msgs, header=""):
            """history hook(worker 线程):把加载的历史交给主线程**原生逐条渲染**。"""
            try:
                self.call_from_thread(self._mount_history, msgs, header)
            except Exception:
                pass

        def _mount_history(self, msgs, header=""):
            """主线程:清空内容区后,把历史**逐条**挂成消息 widget(复用回显/思考/Markdown/工具横幅
            的样式),只渲染最近 HIST_MAX 条(更早的折叠成一行提示)。这样避免把几百条历史整份
            render_markdown 成一个巨型 Static(真终端会卡死/空白)。"""
            try:
                for c in list(self._content().children):
                    c.remove()
            except Exception:
                pass
            if header:
                self._mount(Text(header, style="bold"))
            from wchat_py import repl as _repl
            body = [m for m in msgs[1:] if isinstance(m, dict)]   # 跳过 system
            folded = max(0, len(body) - self.HIST_MAX)
            if folded:
                self._mount(Text("…（更早的 %d 条消息已折叠，可用 -msgs 查看全部）" % folded,
                                 style="dim"))
            tc_names = {}
            for m in body:
                for tc in (m.get("tool_calls") or []) if isinstance(m, dict) else []:
                    tc_names[tc.get("id") or ""] = (tc.get("function") or {}).get("name") or "?"
            for m in body[-self.HIST_MAX:]:
                role = m.get("role", "")
                content = _repl._msg_content_text(m.get("content"))
                reasoning = m.get("reasoning_content") or ""
                if reasoning.strip():
                    self._mount(self._render_reason(reasoning), classes="reason")
                if role == "tool":
                    name = tc_names.get(m.get("tool_call_id") or "")
                    self._mount(Text(("tool → %s" % name) if name else "tool", style="dim"))
                    disp = content.replace("\\n", "\n").lstrip("\ufeff")
                    lines = [ln for ln in disp.splitlines() if ln.strip()]
                    if lines:
                        shown = "\n".join(_repl._clip_line(ln) for ln in lines[:5])
                        if len(lines) > 5:
                            shown += "\n…(其余 %d 行已省略)" % (len(lines) - 5)
                        self._mount(Text(shown, style="dim"))
                elif role == "user":
                    self._mount(Text("❯ " + content, style="reverse"))
                else:   # assistant / 其它
                    if content.strip():
                        try:
                            self._mount(Markdown(content))
                        except Exception:
                            self._mount(Text(content))
                    else:
                        self._mount(Text("（本轮无文本内容）", style="dim"))
                    if m.get("tool_calls"):
                        tcl = ["🔧 %s: %s" % ((tc.get("function") or {}).get("name") or "?",
                               ((tc.get("function") or {}).get("arguments") or "")
                               .replace("\n", " ").replace("\r", " "))
                               for tc in m["tool_calls"]]
                        self._mount(Text("\n".join(_repl._clip_line(x) for x in tcl[:6]), style="dim"))
            self._scroll_end()

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

        def _render_reason(self, text):
            """思考渲染:默认**折叠成一行**(▸ + 首个非空行、压空白);单行由 CSS
            (`.reason { text-wrap:nowrap; text-overflow:ellipsis }`)保证,超宽自动 … 截断。
            -tf=on 时整段(render_reason)。"""
            try:
                from wchat_py.render import think_full, render_reason as _rr
                if think_full():
                    return Text.from_ansi(_rr(text))
                first = ""
                for ln in (text or "").splitlines():
                    s = " ".join(ln.split())
                    if s:
                        first = s
                        break
                if not first:
                    return Text("")
                return Text("▸ " + first, style="dim")
            except Exception:
                return Text(text or "", style="dim")

        def _finalize_live(self):
            """定稿 live 缓冲:移除临时 widget;思考按折叠档、正文按 Markdown 各挂一段。"""
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
                self._mount(self._render_reason(reason), classes="reason")
            if answer.strip():
                try:
                    self._mount(Markdown(answer))
                except Exception:
                    self._mount(Text(answer))

        def _refresh_status(self):
            """状态区=**当前 auto 档位标记**(输入框上方常驻)。auto 未开则清空。
            已自动放行次数会随工具放行变化,每 tick 重算,变了才重绘。"""
            try:
                from wchat_py.auto_enable import marker_text
                m = marker_text() if (os.environ.get("AUTO_MODE") or os.environ.get("WCHAT_AUTO_MODE")) else ""
            except Exception:
                m = ""
            if self._last_marker == m:
                return
            self._last_marker = m
            try:
                st = self.query_one("#status", Static)
                st.update(Text.from_ansi(m) if m else "")
                # 有标记才占高度(#status.on),否则 collapsed 成 0 —— 无 auto 时不留过宽空档
                st.set_class(bool(m), "on")
            except Exception:
                pass

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
                    else:  # log:先定稿 live 再挂内容;auto 标记行只进状态区(不进内容区)
                        t = ev[1]
                        if "本次会话已自动放行" in t:
                            continue
                        # 每个 write 挂成一个 Static;去掉尾部换行(Static 带尾换行会多渲染一空行,
                        # 造成横幅与结果之间/块之间多出空行),纯空白写入直接丢弃(不挂空行)。
                        t = t.rstrip("\n")
                        if not t.strip():
                            continue
                        self._finalize_live()
                        style = ev[2] if len(ev) > 2 else None
                        self._mount(Text(t, style=style) if style else Text.from_ansi(t))
            except queue.Empty:
                pass
            if changed and (self._reason or self._answer):
                self._ensure_live()
                try:
                    self._live_widget.update(self._live_display())
                except Exception:
                    pass
                self._scroll_end()
            self._refresh_status()

        def on_chat_input_submitted(self, event):
            text = event.value or ""
            if not text.strip():
                return
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
                # 元命令输出(如 -auto 的开启横幅)照常在**内容区**展示;auto 档标记另由
                # 状态区常驻显示(#status,见 _refresh_status)。
                if self._busy:
                    self._mount(Text("(上一轮仍在进行,请稍候…)", style="dim"))
                    return
                self._busy = True
                self._run_meta(action, arg)
                return
            self._mount(Text("❯ " + text, style="reverse"))
            if self._busy:
                # M4:边流式输出边输入 —— 上一轮还在跑时提交不丢弃,进队列;worker 会在下一个
                # 小轮边界把它(们)作为"补充信息"并入本轮;纯问答则本轮结束后自动作为下一轮发。
                self._pending.put(text)
                self._mount(Text("（已加入队列：将在下一小轮或下一轮并入发给模型）", style="dim"))
                return
            self._busy = True
            self._run_turn(text)

        def _drain_pending(self):
            """worker(orchestrate)在每小轮边界调用:取出并清空累积的补充消息(线程安全)。"""
            out = []
            try:
                while True:
                    out.append(self._pending.get_nowait())
            except queue.Empty:
                pass
            return out

        @work(thread=True, exclusive=True)
        def _run_turn(self, text):
            o_out, o_err = sys.stdout, sys.stderr
            sys.stdout = TuiStdout(self)
            sys.stderr = TuiStdout(self, style="bold red")
            try:
                if _hl_takes_poll:
                    handle_line(text, self.emit, self.stream, self.reason, self._drain_pending)
                else:
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
            # M4 兜底:本轮结束若还有排队消息(纯问答/最后一个小轮之后才到的),作为**下一轮**发出。
            # (有工具轮的场合,它们一般已在 orchestrate 的小轮边界被并入本轮,这里队列已空。)
            try:
                _nxt = self._pending.get_nowait()
            except queue.Empty:
                _nxt = None
            if _nxt is not None:
                self._mount(Text("（发送排队消息）", style="dim"))
                self._busy = True
                self._run_turn(_nxt)
                return
            try:
                self.query_one("#input", ChatInput).focus()
            except Exception:
                pass

        def action_maybe_quit(self):
            # Ctrl+C/Ctrl+D 退出:按用户要求**丢弃**排队中的补充消息(不再自动续发)。
            try:
                while True:
                    self._pending.get_nowait()
            except queue.Empty:
                pass
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
            _render.set_history_hook(None)
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