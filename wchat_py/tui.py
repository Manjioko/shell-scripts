#!/usr/bin/env python3
"""Textual 全屏交互前端(交互唯一入口;缺 textual 无法交互)。

基于 Textual 构建,从根本上消除旧内联输入框的 resize/切 tab 残影与流箱漂移。

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
import re
import sys


def available():
    try:
        import textual  # noqa: F401
        return True
    except Exception:
        return False


# ---------- 终端应答过滤(TUI 输入侧) ----------
# 现象:TUI 输入框(TextArea)里冒出 `^]11;rgb:0000/0000/0000`,回车后还会当正文发给模型。
# 成因:子进程(gh / vim / mdcat / codex / opencode / tmux —— 本机 PATH 上多个可执行文件
#   内嵌 `\x1b]11;?` 这条查询)问终端"背景色是什么",终端**必定**回一段 OSC 应答;子进程早已退出,
#   应答留在 pty 输入队列等**下一个读者**。TUI 下 stdin 归 Textual 的 `XTermParser`:它对
#   OSC(`ESC ]…`)/DCS(`ESC P…`)序列**完全没有分支** —— 既不匹配光标报告/鼠标/模式报告,也不在
#   按键表里,于是走 `reissue_sequence_as_keys` 被**逐字符当按键重放**,ESC→`^`、其余→字面字符,
#   正好拼成用户看到的 `^]11;rgb:…`。实测(Textual 8.2.8):
#     list(XTermParser().feed(b"\x1b]11;rgb:0000/0000/0000\x1b\\"))  → 23 个 Key,
#   拼起来 == '^]11;rgb:0000/0000/0000'。
# 判据**刻意只丢 OSC/DCS**:DA1/DA2、kitty 键盘标志、DECRPM 模式报告、DSR 光标报告这几类
#   Textual 会**正常消费**(实测:0 事件,或正确的 CursorPosition / TerminalSupportsSynchronizedOutput),
#   而且它靠这些做终端能力与光标探测;照搬非 TUI 输入框那套"四类全丢"会破坏键盘协议/同步输出/
#   光标定位。wchat 自身从不发这类查询(测试 T17 有静态守卫),应答只可能来自子进程残留,剥离安全。
# **在读者侧堵**,不追谁查询;只丢整段可识别应答,判不动的一律放行(误吞一个按键比漏一条应答严重得多)。
_OSC_DCS_RESP_RE = re.compile(r"\x1b[\]PX^_][^\x1b\x07]*(?:\x1b\\|\x07)")

# 提示行(dim │ 前缀)剥离:提示行用 `\033[2m│\033[0m ` 加一根暗色竖线来模拟"次要/说明"样式
# (见 cli.inp_msg、render.BAR)。TUI 里不需要这根竖线 —— 统一改成"思考内容灰 + 斜体"
# (见 WchatApp._hint_text),既干净又和 placeholder/状态行同档。
# 只匹配行首这一种 hint 前缀;工具框线的 `   │ `(3 空格 + 裸竖线,无 ANSI)不受影响。
_HINT_BAR_RE = re.compile(r"^\x1b\[2m│\x1b\[0m ?")


def _strip_terminal_responses(s):
    """丢掉整段可识别的 OSC/DCS 终端应答(BEL 或 ST 终止),其余原样返回。"""
    if not s or "\x1b" not in s:
        return s
    return _OSC_DCS_RESP_RE.sub("", s)


def _install_response_filter():
    """把 Textual 的 `XTermParser.feed` 包一层:喂给解析器前先丢掉 OSC/DCS 终端应答。

    为什么包在 `feed` 上:各 driver(linux / linux_inline / web / win32)都
    `from textual._xterm_parser import XTermParser` 后 `parser.feed(...)`;改**类方法**一处即可
    覆盖全部 driver(它们在应用 mode 启动时才 `feed = parser.feed`,取到的是包好的方法)。

    **过滤后为空但原始非空**时直接返回空、绝不喂空串 —— Textual `Parser.feed("")` 是 **EOF 信号**
    (`self._eof=True` + throw ParseEOF),喂空会误关解析器。任何一步拿不到(版本漂移)都静默跳过、
    退回原行为,绝不因加固而崩。"""
    try:
        from textual._xterm_parser import XTermParser
    except Exception:
        return False
    orig = getattr(XTermParser, "feed", None)
    if orig is None:
        return False
    if getattr(orig, "_wchat_osc_filter", False):
        return True

    def feed(self, data):
        if data:
            filtered = _strip_terminal_responses(data)
            if filtered != data and not filtered:
                return ()                       # 整块都是应答 → 不产出事件,也不触发 EOF
            data = filtered
        return orig(self, data)

    feed._wchat_osc_filter = True
    XTermParser.feed = feed
    return True


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


# 前台耗时 shell 的倒计时:跑超过这么多秒仍未结束,才把状态行换成"倒计时 x 秒"
# (快速命令不出现,避免"倒计时600闪一下")。
_CD_THRESHOLD = 3.0


class _SimplestStdout:
    """-simplest 回合内的 sys.stdout shim:工具横幅/结果/输出箱**不显示**,只当作"工具轮边界"信号。

    投 ("simplest_log", ...) 事件;`_drain` 收到后不挂内容,只调 `_simplest_segment_end()`
    (收尾当前正文段 + 在下方起一条新的等待状态行)。这样一轮里"正文↔工具"交替时,状态行能
    像用户期望的那样逐段循环。stderr 仍走 TuiStdout(style='bold red') → 报错照常显示。
    另:前台 shell 的起止经 note_shell() 投 ("shell_countdown_start"/"end") 事件,驱动耗时倒计时。
    """

    def __init__(self, app):
        self._app = app

    def write(self, s):
        if s:
            self._app._q.put(("simplest_log", str(s), None))

    def note_shell(self, phase, total=None):
        """前台 shell 起(start,带限时秒数)/止(end):投事件给 _drain 驱动倒计时覆盖。"""
        try:
            if phase == "start":
                self._app._q.put(("shell_countdown_start", float(total or 0), None))
            else:
                self._app._q.put(("shell_countdown_end", None, None))
        except Exception:
            pass

    def flush(self):
        pass

    def isatty(self):
        return False


def _pretty_path(p):
    """把绝对路径里的家目录缩成 `~`,用于输入框上方的工作目录栏(短小好读)。
    只在**整段是家目录或其子路径**时替换,避免 `/Users/jwanxt` 被误缩成 `~xt`。"""
    try:
        home = os.path.realpath(os.path.expanduser("~"))
        p = os.path.realpath(p)
        if home and home != "/" and (p == home or p.startswith(home + os.sep)):
            return "~" + p[len(home):]
    except Exception:
        pass
    return p


_SYSTEM_DIRS = ("/usr", "/etc", "/var", "/bin", "/sbin", "/System", "/Library")


def _validate_workdir(path):
    """校验候选工作目录能否作为 WORK_ROOT。返回 (ok, realpath 或 拒绝原因)。

    为什么这些必须拒:WORK_ROOT 是硬墙(P1–P3)与 auto 的信任边界。把它设成 `/` 或 `~` 会让
    `pathutil.in_root_exempt` / `hardwall._creds_all_exempt` 的"工作根==HOME 或 / 则边界失效"
    特判生效 —— 整台机器都成"目录内",墙形同虚设;系统目录与他人家目录同理(会把一堆系统/凭据
    文件算进"根内"而被豁免)。所以这些一律拒,从源头堵死。"""
    try:
        p = os.path.realpath(os.path.expanduser(path))
    except Exception:
        return False, "路径无法解析"
    if not os.path.exists(p):
        return False, "目录不存在: %s" % p
    if not os.path.isdir(p):
        return False, "不是目录: %s" % p
    home = os.path.realpath(os.path.expanduser("~"))
    if p == "/":
        return False, "不能把工作目录设为根目录 /(会让安全边界失效)"
    if home and p == home:
        return False, "不能把工作目录设为家目录本身(会让安全边界失效)"
    for d in _SYSTEM_DIRS:
        rd = os.path.realpath(d)
        if p == rd or p.startswith(rd + os.sep):
            return False, "不能把工作目录设为系统目录 %s" % d
    for base in ("/Users", "/home"):
        rb = os.path.realpath(base)
        if p == rb:
            return False, "不能把工作目录设为用户家目录基 %s" % base
        if os.path.dirname(p) == rb and p != home:
            return False, "不能把工作目录设为他人的家目录 %s" % p
    return True, p


def _open_path_in_file_manager(path):
    """用系统文件管理器打开目录(用户点击工作目录栏「在文件管理器打开」时调用)。
    这是**用户主动的 GUI 动作**,不经过模型工具链,与硬墙无关。返回 (ok, msg)。"""
    import subprocess as _sp
    try:
        if sys.platform == "darwin":
            cmd = ["open", path]
        elif sys.platform.startswith("win"):
            cmd = ["explorer", path]
        else:
            cmd = ["xdg-open", path]
        _sp.Popen(cmd, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
        return True, "已打开"
    except Exception as e:
        return False, "打不开文件管理器(%s)" % e


def make_app(handle_line, *, banner="", on_meta=None, first_turn="", on_workdir_change=None):
    """构造(不 run)WchatApp。handle_line(text, emit, stream, reason[, poll_pending]):
       emit(s)   -> log 通道(横幅/工具/错误)
       stream(s) -> live 区正文(普通亮度)
       reason(s) -> live 区思考(暗色 dim)
    第 5 个可选参数 poll_pending():返回运行期补充消息列表(M4 边输出边输入)。只有
    handle_line 接受 5 参时才传,否则按 4 参调用(向后兼容)。
    on_meta(action, arg) 处理元命令(-q 之外的 -u/-n/... );-q 由 app 直接退出。
    on_workdir_change(new_root) 在工作目录切换成功后调用(供 cli 侧按新根重建会话 system 提示;
    可选,缺省 None 则只改 cwd/env,不碰会话)。
    first_turn:非空时,进入后自动把这句话作为**第一轮**发出去(对应 `wchat "内容"`)。
    textual 缺失抛 RuntimeError。"""
    if not available():
        raise RuntimeError("textual 未安装")
    # 终端应答过滤(见 _install_response_filter):在 driver 输入线程起来前装好。
    # 幂等、失败静默退回原行为,不影响 TUI 启动。
    _install_response_filter()
    from textual.app import App, ComposeResult
    from textual.widgets import Static, Button, Label, TextArea, Input, OptionList
    from textual.widgets.option_list import Option
    from textual.containers import Vertical, Horizontal, Container, VerticalScroll
    from textual.screen import ModalScreen
    from textual.binding import Binding
    from textual.message import Message
    from rich.text import Text
    from wchat_py.mdstyle import build_markdown, markdown_theme
    from textual import work
    import threading
    import inspect

    # ---- 可选/可复制的 markdown 正文 ----
    # 为什么需要它:正文若用 rich.markdown.Markdown(一个 rich *renderable*)渲染,
    # `Static._render()` 得到的是 `RichVisual`。Textual 的拖拽选中靠屏幕坐标→文本偏移的
    # 映射,而这个映射来自渲染片段的 `style.meta["offset"]` 元数据 —— 只有 `Content`
    # (Text 渲染后的产物)才带它,RichVisual 不带。于是 `get_widget_and_offset_at()` 在正文上
    # 返回 `offset=None`,选中状态机根本建不出针对正文的 Selection → 正文永远选不中、复制不了
    # (而思考/工具是 Text→Content,带 offset,所以可选)。这是"正文不可选"的真因。
    # 修法:把 markdown **按控件当前宽度**预渲成 ANSI,再转成 rich `Text`(→ `Content`)显示。
    # 这样既有样式(颜色/框线都在 ANSI 里),又带 offset 元数据 → 可被原生选中/复制。
    # 宽度变化时(on_resize)按新宽度重渲,保证"显示折行"与"复制文本"始终一致。
    def _render_md_to_text(md_source, width):
        import io as _io
        from rich.console import Console as _Console
        from rich.text import Text as _RText
        buf = _io.StringIO()
        con = _Console(width=width, file=buf, force_terminal=True,
                       color_system="truecolor", highlight=False)
        try:
            con.push_theme(markdown_theme())
        except Exception:
            pass
        con.print(build_markdown(md_source), end="")
        return _RText.from_ansi(buf.getvalue())

    class _MarkdownStatic(Static):
        def __init__(self, md_source, *args, **kwargs):
            self._md_source = md_source          # 原始 markdown 源串(宽度变化时重渲用)
            super().__init__("", *args, **kwargs)
            # **先按兜底宽度渲一次**,保证 .content 立即可读 —— 否则从构造到 on_mount 的
            # 真正宽度确定之前,控件内容是空串,任何"按 .content 取正文"的调用(测试的
            # _content_text、剪贴/断言的采样)会偶发看不到刚挂上的答案(实测会让 M4 队列
            # 用例 ~1/3 概率红)。拿到真实宽度后 on_mount/on_resize 会重渲覆盖。
            self._rendered_w = -1
            try:
                self.update(_render_md_to_text(md_source, 100))
            except Exception:
                pass

        def _reflow(self):
            try:
                w = self.content_size.width or self.size.width or 0
                if w <= 0 or w == self._rendered_w:
                    return
                self._rendered_w = w
                self.update(_render_md_to_text(self._md_source, w))
            except Exception:
                pass

        def on_mount(self):
            self._reflow()
            # 首次挂载时宽度可能尚未确定(0)→ 布局完成后再补一次,避免正文空着。
            try:
                self.call_after_refresh(self._reflow)
            except Exception:
                pass

        def on_resize(self, event):
            self._reflow()

        def get_selection(self, selection):
            # offset 现在由 Content 原生提供,父类能正确抽取;这里只做一次"去行尾补白",
            # 让复制出来的正文干净(rich 渲成定宽会产生行尾空格)。
            res = super().get_selection(selection)
            if res is None:
                return None
            text, ending = res
            return "\n".join(ln.rstrip() for ln in text.split("\n")), ending

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
            # Shift+Enter = 换行(对非程序员更直观)。依赖终端用 Kitty 键盘协议把
            # Shift+Enter 与 Enter 区分开(Textual 启动时已发 disambiguate 序列);
            # 终端不支持时它会退化成普通 Enter(提交)——所以下面仍保留"行尾 \ + 回车"
            # 作为**全终端通用**的续行兜底,二者并存、不冲突。
            Binding("shift+enter", "insert_newline", "换行", priority=True),
            Binding("ctrl+f", "pick_file", "选文件"),
            Binding("ctrl+v", "paste_image", "粘贴图片"),
            Binding("ctrl+g", "paste_image", "粘贴图片"),
        ]

        class Submitted(Message):
            def __init__(self, value):
                self.value = value
                super().__init__()

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            # 多行粘贴折叠:占位令牌 → 粘贴全文(提交前换回)。超过一行就折叠成
            # `📋 [已粘贴 N 行文本内容]`,内容存侧表、提交时换回。如实粘贴会把输入框撑爆、
            # 上下已输入的内容被顶走,故折叠。
            self._pastes = {}
            self._paste_seq = 0

        async def _on_key(self, event):
            """`/` 菜单打开时把 ↑↓/Esc/Tab 抢过来(焦点始终留在输入框,绝不让 OptionList 抢焦点)。
            Enter **不**在这里拦:它有 priority 绑定,直接落到 action_submit_or_continue(见那里)。
            实测(Textual 8.2.8):enter 的 priority 绑定不进 _on_key;up/down/escape/tab 会进,
            event.stop() 后 TextArea 不再移动光标 / 不插 Tab。"""
            app = getattr(self, "app", None)
            if app is not None and getattr(app, "_slash_is_open", lambda: False)():
                k = event.key
                if k == "up":
                    app._slash_move(-1); event.stop(); event.prevent_default(); return
                if k == "down":
                    app._slash_move(1); event.stop(); event.prevent_default(); return
                if k == "escape":
                    app._slash_back(); event.stop(); event.prevent_default(); return
                if k == "tab":
                    app._slash_accept(); event.stop(); event.prevent_default(); return
            await super()._on_key(event)

        async def _on_paste(self, event):
            """终端粘贴(含括号粘贴):单行原样插入;**多行折叠成一行占位** `📋 [已粘贴 N 行文本内容]`,
            全文存 `self._pastes`,提交时由 `_restore_pastes` 换回。占位行带零宽标记 ⇒ 复用一个退格
            即可整行删除(与图片/文件占位同一套原子删除)。

            ⚠ 必须自己清空 `event.text`(见下方注释):Textual 的 `_get_dispatch_methods` 会**遍历整个
            MRO**,对每个类 `__dict__` 里的 `_on_paste` 各调一次(ChatInput 的 + TextArea 基类的),
            不是只调最派生的那个。基类 `_on_paste` 会用 `event.text` 把原文再插一遍 → 占位+原文双写。"""
            if self.read_only:
                return
            data = (getattr(event, "text", "") or "").replace("\r\n", "\n").replace("\r", "\n")
            # 关键:先记下 data,再把 event.text 清空 —— 随后按 MRO 还会跑 TextArea 基类的 `_on_paste`,
            # 它读 event.text(现已空)→ `_replace_via_keyboard("")` 是空操作,不会再插原文。
            try:
                event.text = ""
            except Exception:
                pass
            if data == "":
                # 空粘贴 = 剪贴板无文本、很可能装的是**图片**(终端把图片粘贴过来时就是空文本)。
                # 非 TUI 输入框正是这个约定(Keys.BracketedPaste data==""→嗅探剪贴板图片)。
                # 关键:WSL2 的 Windows Terminal 等会把 Ctrl+V 在**终端层**接管成"粘贴",app 收不到
                # ctrl+v 键 → action_paste_image 永远不触发,只能靠这条空粘贴路径去抓图,否则按了没反应。
                try:
                    self.action_paste_image()
                except Exception:
                    pass
                return
            if "\n" not in data:
                # 单行:自己插(不再 await super(),否则会与 MRO 里的基类 _on_paste 各插一遍)。
                r = self._replace_via_keyboard(data, *self.selection)
                if r:
                    self.move_cursor(r.end_location)
                    self.focus()
                return
            # 末尾的 \n 是粘贴终止符非内容(与旧流/非 TUI 一致,剥一个)
            if data.endswith("\n"):
                data = data[:-1]
            n_lines = data.count("\n") + 1
            self._paste_seq += 1
            # 令牌尾部拼 N 个零宽空格作**唯一标记**(同非 TUI):提交时按整串换回,多次同规模
            # 粘贴互不混淆;末尾标记也让 _placeholder_rows 认出这一行、退格整行删除。
            token = "📋 [已粘贴 %d 行文本内容]" % n_lines + self._PH_MARK * self._paste_seq
            self._pastes[token] = data
            try:
                self._insert_placeholder_line(token)
            except Exception:
                pass

        def _restore_pastes(self, text):
            """提交前把占位令牌换回粘贴全文(逐串精确替换,与非 TUI 同法)。
            占位被部分编辑过→令牌匹配不上,原样留下(与非 TUI 同样的已知边界)。"""
            for token, content in self._pastes.items():
                if token in text:
                    text = text.replace(token, content)
            return text

        def action_insert_newline(self):
            """Shift+Enter:在当前光标处插入一个换行(多行输入),不提交。"""
            try:
                self.insert("\n")
            except Exception:
                pass

        def action_submit_or_continue(self):
            # `/` 菜单打开时,Enter 的语义是"选中当前高亮项",不是发送。Enter 的 priority 绑定
            # 直接落到这里(不进 _on_key),所以必须在这拦。
            app = getattr(self, "app", None)
            if app is not None and getattr(app, "_slash_is_open", lambda: False)() and app._slash_accept():
                return
            text = self.text
            row, _col = self.cursor_location
            lines = text.split("\n")
            cur = lines[row] if row < len(lines) else ""
            # 行尾 `\` + 回车 = 续行(吃掉那个反斜杠,换到下一行)。这是**全终端通用**的
            # 换行兜底(不依赖终端把 Shift+Enter 报成独立按键),与上面的 Shift+Enter 并存。
            if row == len(lines) - 1 and cur.rstrip().endswith("\\"):
                lines[row] = cur.rstrip()[:-1]
                self.text = "\n".join(lines) + "\n"
                try:
                    self.cursor_location = (len(lines), 0)
                except Exception:
                    pass
                return
            text = self._restore_pastes(text)     # 粘贴占位 → 全文(在清空前、投递前换回)
            self.text = ""
            try:
                self.cursor_location = (0, 0)
            except Exception:
                pass
            self.post_message(self.Submitted(text))

        # 占位行(选中的文件/粘贴的图片)以零宽空格 U+200B 结尾作**原子标记**:退格/删除
        # 碰到占位行任意位置都整行删除,而不是一个字符一个字符删。否则用户想撤掉一张
        # "选中/粘贴"的图,得按几十下退格(实测)。
        _PH_MARK = "\u200b"

        def _placeholder_rows(self):
            """当前文本里所有占位行(含零宽标记)的行号集合。"""
            return {i for i, ln in enumerate(self.text.split("\n")) if self._PH_MARK in ln}

        def _insert_placeholder_line(self, line):
            """插入一行占位(带零宽标记 + 换行),且保证它**独占一行**:
            光标所在行已有内容时先补一个换行,否则占位会和正文挤在同一行 —— 那样
            整行会被当字面文本提交、图片丢失,原子删除也失效。"""
            if not line.endswith(self._PH_MARK):
                line = line + self._PH_MARK
            row, _col = self.cursor_location
            lines = self.text.split("\n")
            line_before = lines[row] if row < len(lines) else ""
            if line_before:
                self.insert("\n" + line + "\n")
            else:
                self.insert(line + "\n")

        def _delete_row(self, row):
            """整行删除第 row 行(连同它的换行);返回是否执行。"""
            lines = self.text.split("\n")
            if row < 0 or row >= len(lines):
                return False
            if row < len(lines) - 1:
                start, end = (row, 0), (row + 1, 0)     # 本行内容 + 行尾换行
                dest = (row, 0)
            else:                                        # 末行:本行内容 + 与上一行间的换行
                start = (row - 1, len(lines[row - 1])) if row > 0 else (0, 0)
                end = (row, len(lines[row]))
                dest = (row - 1, len(lines[row - 1])) if row > 0 else (0, 0)
            try:
                self.delete(start, end, maintain_selection_offset=False)
                self.move_cursor(dest)
            except Exception:
                return False
            return True

        def _atomic_delete(self, forward=False):
            """退格/删除时的占位感知:若删除点紧邻占位行,整行删掉;否则返回 False
            让默认逐字符删除接手。返回 True 表示已整行删除、调用方应吞掉该按键。"""
            sel = self.selection
            if not sel.is_empty:
                return False                             # 有选区:交给默认删除(删选区)
            rows = self._placeholder_rows()
            if not rows:
                return False
            row, col = self.cursor_location
            lines = self.text.split("\n")
            if not forward:
                # 退格:光标在占位行内(含行尾) → 删本行;光标在下一行行首 → 删上一行(占位)
                if row in rows and col > 0:
                    return self._delete_row(row)
                if col == 0 and (row - 1) in rows:
                    return self._delete_row(row - 1)
            else:
                # 前向删除:光标在占位行内 → 删本行;光标在行尾且下一行是占位 → 删下一行
                if row in rows and col < len(lines[row]):
                    return self._delete_row(row)
                if row + 1 in rows and col >= len(lines[row]):
                    return self._delete_row(row + 1)
            return False

        def action_delete_left(self):
            """退格:占位行整段删除(一次性)。默认行为逐字符删,用户实测删一张图要按几十下。"""
            if not self.read_only and self._atomic_delete(forward=False):
                return
            super().action_delete_left()

        def action_delete_right(self):
            """Delete 键:与退格对称的占位行整段删除。"""
            if not self.read_only and self._atomic_delete(forward=True):
                return
            super().action_delete_right()

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
                    self._insert_placeholder_line(_file_placeholder(p))
                except Exception:
                    pass

        def action_paste_image(self):
            """Ctrl+V/Ctrl+G:从系统剪贴板抓图片,插入 `📋 [图片] <路径>` 占位行。
            提交时 session 的 parse_placeholders 会把它读进 input_images(与 clipimg 同管线)。"""
            try:
                from wchat_py import clipimg as _cimg
                from wchat_py import repl as _repl
            except Exception:
                return
            ct = os.environ.get("CT_HOME") or os.path.join(os.environ.get("HOME", ""), ".ct")
            clip_dir = os.path.join(ct, "clip")
            try:
                path = _cimg._clip_image_file(clip_dir)
            except Exception:
                path = None
            if path:
                try:
                    # 与 Ctrl-F 一致带零宽标记:整行原子删除(否则退格要一个个删);
                    # parse_placeholders 会先剥掉零宽再取路径,不会污染路径。
                    self._insert_placeholder_line(_repl._PLACE_IMG + path)
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
            # 三段式(用户给的样式参考):①标题(大字)→ ②`命中: xxx` 一行(单独、带标签、
            # 可换行,不截断;用户原话"弹窗里没有任何命中的文案,根本不知道为啥要授权")→
            # ③底部一行:左侧提问「是否执行?」、右侧「是 (y/Enter)」「否 (n/Esc)」两个按钮。
            with Container(classes="dialog"):
                yield Label(Text(self._title, style="bold"), classes="title")
                if self._mark:
                    yield Label(Text("命中: " + self._mark), classes="mark")
                with Horizontal(classes="row"):
                    _q = self._question or ""
                    _q = _q if _q.endswith(("?", "？")) else (_q + "?")
                    yield Label(Text(_q), classes="ask")
                    with Horizontal(classes="btns"):
                        yield Button("是 (y/Enter)", variant="success", id="yes")
                        yield Button("否 (n/Esc)", variant="error", id="no")

        def on_button_pressed(self, event):
            self.dismiss(event.button.id == "yes")

        def action_yes(self):
            self.dismiss(True)

        def action_no(self):
            self.dismiss(False)

    class InputModal(ModalScreen):
        """单行文本输入弹窗(如开启 auto 的 AUTO 签字)。dismiss(输入的字符串);
        Esc 取消 → dismiss(None)。区分"回车提交空串"与"取消":前者是""(用户确认空),
        后者是 None(用户按 Esc 放弃),由调用方决定语义(如 -l 取消不应默认选第 1 条)。"""
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
            self.dismiss(None)          # None = 取消(区别于回车提交的空串)

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
                rows.append("%d. %s" % (i + 1, name[:80]))   # 1 基(与 list_history 解析一致)
            try:
                self.query_one("#hist_list", Static).update(Text("\n".join(rows) or "(无)"))
                hint = "第 %d/%d 页 · l=上一页 n=下一页" % (self._page + 1, self._pages)
                self.query_one("#hist_page", Label).update(Text(hint, style="dim"))
            except Exception:
                pass

        def on_input_submitted(self, event):
            self.dismiss(event.value)

        def action_cancel(self):
            self.dismiss(None)          # None = 取消(区别于回车提交的空串 → 默认第 1 条)

    class WorkdirMenuModal(ModalScreen):
        """点击输入框上方的工作目录栏弹出的小菜单:在文件管理器打开 / 更换工作目录。
        dismiss(action):"filemgr" / "change" / None(Esc 关闭)。"""
        BINDINGS = [("escape", "cancel", "关闭"),
                    ("o", "filemgr", "文件管理器打开"),
                    ("d", "change", "更换目录")]

        def __init__(self, full, compact):
            super().__init__()
            self._full = full
            self._compact = compact

        def compose(self):
            with Container(classes="dialog"):
                yield Label(Text("📁 工作目录"), classes="title")
                yield Label(Text(self._compact, style="italic"), classes="path")
                with Horizontal(classes="btns"):
                    yield Button("在文件管理器打开 (o)", id="filemgr")
                    yield Button("更换工作目录… (d)", id="change")

        def on_button_pressed(self, event):
            self.dismiss(event.button.id)

        def action_filemgr(self):
            self.dismiss("filemgr")

        def action_change(self):
            self.dismiss("change")

        def action_cancel(self):
            self.dismiss(None)

    class WorkdirBar(Static):
        """输入框上方常驻的工作目录栏(可点)。点击 → 弹 WorkdirMenuModal。
        用独立控件(而非 App.on_click 判 id)是因为 event 由目标控件自己接住最可靠。"""

        def on_click(self, event):
            try:
                event.stop()
            except Exception:
                pass
            try:
                self.app._open_workdir_menu()
            except Exception:
                pass

    class WchatApp(App):
        CSS = """
        Screen { layout: vertical; padding: 0; }
        #contentbox { height: 1fr; border: none; padding: 0; margin: 0; }
        /* 空态欢迎提示:首次进入(或清屏 -cl/-n 后)内容区为空时,在正中显示一段用法提示,
           免得一进来大片空白、也让新用户第一眼知道怎么用。
           独立成一个 #welcome widget 挂在 #contentbox 里(**不进 #content 消息列表**),避免污染
           对话正文与"内容区无纯空白子件"那类判据;有逻辑块时它隐藏、让 #content 独占整块。
           配色与思考文本同一档灰(#6a6a6a)+ 斜体,深色终端也读得清。 */
        #welcome { display: none; height: 1fr; width: 1fr;
                   content-align: center middle; text-align: center;
                   color: #6a6a6a; text-style: italic; padding: 0 2; }
        /* 空态:藏起 #content、亮出 #welcome(由 _set_welcome 切 #contentbox 的 .empty 类) */
        #contentbox.empty #content { display: none; }
        #contentbox.empty #welcome { display: block; }
        #content { height: 1fr; padding: 0 1; margin: 0; border: none;
                   scrollbar-size-vertical: 1;
                   scrollbar-color: #444444;
                   scrollbar-color-hover: #555555;
                   scrollbar-color-active: #666666;
                   scrollbar-background: transparent;
                   scrollbar-background-hover: transparent;
                   scrollbar-background-active: transparent; }
                 #content .reason { text-wrap: nowrap; text-overflow: ellipsis; }
         /* -tf=on 的思考是**忠实多行**:允许自然折行、不裁字(否则 nowrap+ellipsis 会把每行
            掐成一截,且补空格会互相顶——正是要修掉的观感)。两条同特异性,后者覆盖前者。 */
         #content .reason-full { text-wrap: wrap; text-overflow: clip; }
        /* 逻辑块之间空一行(用户要求:内容/输入回显/思考/工具块之间别太挤)。
           用 margin-top 而不是插空 Static:空 Static 会被"无纯空白子件"的闸门判红,
           且会污染消息列表。continuation(工具块的框线/状态行)不加 .blk。 */
        #content .blk { margin-top: 1; }
        #status { height: 0; padding: 0; margin: 0; }
        /* 与输入框 placeholder 同一灰 + 斜体(#6a6a6a italic),深色终端上也看得清。
           旧值是 #666666:auto 标记又被 \033[2m(dim) 压暗成 ~#494949,几乎看不见。 */
        #status.on { height: auto; max-height: 6; padding: 0 1; color: #6a6a6a; text-style: italic; }
        /* 输入框上方常驻「工作目录」栏(可点):单行、超宽尾部省略。
           与 placeholder/状态行同一档灰;hover 变紫提示可点(与 / 菜单选中色一致)。 */
        #workdirbar { height: auto; padding: 0 1; color: #6a6a6a;
                      text-wrap: nowrap; text-overflow: ellipsis; }
        #workdirbar:hover { color: #bd93f9; }
        /* 所有弹窗统一:同一 `round #6a6a6a` 灰边框(用户认可的那条灰线),背景遮罩加深到 70%
           让弹窗从背景里更"浮"出来、显得独立。注:Textual 8.x 无 box-shadow,试过用单边
           outline 模拟投影会渲染成偏离边框的断开矩形(看着像坏了),故不用阴影、只靠加深遮罩。 */
        WorkdirMenuModal { align: center middle; background: $background 70%; }
        WorkdirMenuModal .dialog { width: 66; height: auto; max-height: 18; border: round #6a6a6a;
                                   background: $surface; padding: 1 2; }
        WorkdirMenuModal .title { width: 1fr; text-style: bold; color: $text; }
        WorkdirMenuModal .path { width: 1fr; color: #6a6a6a; text-style: italic; margin-top: 1; }
        WorkdirMenuModal .btns { height: auto; margin-top: 1; }
        /* 按钮统一:白色线条边框 + 浅灰文字 + **透明背景**(聚焦/悬停都只让边框与文字更亮,
           不铺白色高亮底 —— 用户实报"不要白色的字体背景色")。 */
        WorkdirMenuModal .btns Button { min-width: 18; margin: 0 1 0 0;
            background: transparent; color: #a8a8a8; border: round #d0d0d0; text-style: none; }
        WorkdirMenuModal .btns Button .button--label { background: transparent; color: #a8a8a8; }
        WorkdirMenuModal .btns Button:hover { background: transparent; color: #e6e6e6; border: round #ffffff; }
        WorkdirMenuModal .btns Button:hover .button--label { background: transparent; color: #e6e6e6; }
        WorkdirMenuModal .btns Button:focus { background: transparent; color: #ffffff; border: round #ffffff; text-style: none; }
        WorkdirMenuModal .btns Button:focus .button--label { background: transparent; color: #ffffff; }
        /* 干掉不同 variant 带来的彩色边框/底色(本菜单所有按钮同一套灰白观感)。 */
        WorkdirMenuModal .btns Button.-style-default,
        WorkdirMenuModal .btns Button.-style-primary,
        WorkdirMenuModal .btns Button.-style-warning,
        WorkdirMenuModal .btns Button.-style-success,
        WorkdirMenuModal .btns Button.-style-error {
            background: transparent; color: #a8a8a8; border: round #d0d0d0; }
        /* `/` 命令菜单:整宽带边框面板,贴在输入框正上方。默认 display:none,打 / 时加 .open。
           OptionList 本身不出边框(边框给外层 panel),背景透明与终端底融合。 */
        #slashpanel { height: auto; max-height: 14; border: round #6a6a6a; margin: 0 1;
                      padding: 0 1; background: transparent; display: none; }
        #slashpanel.open { display: block; }
        #slashlist { height: auto; max-height: 12; background: transparent; border: none;
                     padding: 0;
                     scrollbar-size-vertical: 1; scrollbar-color: #444444;
                     scrollbar-color-hover: #555555; scrollbar-color-active: #666666;
                     scrollbar-background: transparent;
                     scrollbar-background-hover: transparent;
                     scrollbar-background-active: transparent; }
        /* 选中项**只靠文字变紫**(像 claude code):整行文字都变紫,不要背景块、不要加粗。
           直接把 OptionList 自带高亮(背景+字色+bold)覆盖成:透明底 + 紫字 + 无 bold。 */
        #slashlist > .option-list--option-highlighted { background: transparent; color: #bd93f9; text-style: none; }
        #slashlist:focus > .option-list--option-highlighted { background: transparent; color: #bd93f9; text-style: none; }
        #slashlist > .option-list--option-hover { background: transparent; }
        #slash_title { height: auto; color: $text; text-style: bold; padding: 0; }
        #slash_hint { height: auto; color: #6a6a6a; text-style: italic; padding: 0; }
        #inputrow { height: auto; max-height: 14; border: round #6a6a6a;
                    background: transparent; padding: 0 1; align-vertical: top; }
        /* 常驻提示符 ❯:独立 widget,输入/清空都在,placeholder 消失它也不走(像 claude code)。
           宽度 2 给字符后留一格;height:auto 让多行输入时它停在第一行顶部。 */
        #prompt { width: 2; height: auto; color: #6a6a6a; padding: 0; }
        #input { width: 1fr; height: auto; max-height: 12; background: transparent;
                 border: none; padding: 0; }
        #input:focus { background: transparent; }
        /* placeholder 与思考文本同档灰、斜体,用户好区分(默认 $text 40% 偏亮) */
        #input .text-area--placeholder { color: #6a6a6a; text-style: italic; }
        /* 选中文字高对比:默认 ansi-dark 用 ansi_bright_blue,部分终端配色下几乎看不见(用户实报
           "选中色十分暗,基本看不到")。改用不依赖终端 ansi 映射的固定蓝底白字,深色终端也清晰。
           输入框(text-area--selection)与内容区(Screen 选中)统一同一套。 */
        #input .text-area--selection { background: #3a6df0; color: #ffffff; }
        Screen > .screen--selection { background: #3a6df0; color: #ffffff; }
        ConfirmModal { align: center middle; background: $background 70%; }
        /* 三段式布局(参考用户给的图):标题 / 命中 / 底行(提问 + 两个按钮)。加宽、放宽高度上限,
           让命中(可能是长路径)能换行显示完整;命中用灰、提问正常亮度。
           边框与其它弹窗统一(round #6a6a6a)。 */
        ConfirmModal .dialog { width: 76; height: auto; max-height: 16; border: round #6a6a6a;
                               background: $surface; padding: 1 2; }
        ConfirmModal .title { width: 1fr; text-style: bold; color: $text; }
        ConfirmModal .mark { width: 1fr; color: #6a6a6a; margin-top: 1; }
        ConfirmModal .row { height: auto; margin-top: 1; }
        ConfirmModal .ask { width: 1fr; height: 3; content-align: left middle; color: $text; }
        ConfirmModal .btns { width: auto; height: auto; }
        ConfirmModal .btns Button { min-width: 14; margin: 0 0 0 1; }
        InputModal { align: center middle; background: $background 70%; }
        InputModal .dialog { width: 64; height: auto; max-height: 14; border: round #6a6a6a;
                             background: $surface; padding: 1 2; }
        HistoryPickModal { align: center middle; background: $background 70%; }
        HistoryPickModal .dialog { width: 74; height: auto; max-height: 24; border: round #6a6a6a;
                                   background: $surface; padding: 1 2; }
        HistoryPickModal #hist_list { height: auto; max-height: 12; }
        HistoryPickModal #hist_page { height: auto; margin-top: 1; }
        """
        # 复制/退出:mac 常规 cmd+c=super+c 复制选中(内容区或输入框);ctrl+c 有选中则复制、
        # 无选中则按原逻辑(空闲退出/生成中打断)。**priority=True** 让 App 优先于 TextArea 的
        # ctrl+c/super+c,统一在一处判断"先复制选区",避免被输入框的 copy 吞掉(用户实报:
        # 内容区选中后 cmd+c 复制不了、只剩个别机器 ctrl-c 能用)。
        BINDINGS = [
            Binding("ctrl+c", "ctrl_c", "复制/打断", priority=True, show=False),
            Binding("super+c", "copy_selection", "复制", priority=True, show=False),
            # Ctrl+D 不再退出(唯一退出是 /q);保留按键只为在跑着时也能打断,且不显示在底栏。
            Binding("ctrl+d", "maybe_quit", "打断", show=False),
        ]
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
            self._has_blocks = False        # 内容区是否已有"逻辑块"(决定是否加块间空行)
            self._pending = queue.Queue()   # M4:运行期补充消息(线程安全);worker 每小轮 poll 注入
            # `/` 命令菜单状态。_slash_level=1 一级(命令列表)/2 二级(枚举选项);
            # _slash_items 是**当前展示**的列表(一级:catalog 条目;二级:选项元组);
            # _slash_second 在二级时记录 {"name":命令,"opts":[...]},供回退/拼接。
            self._slash_open = False
            self._slash_level = 1
            self._slash_items = []
            self._slash_second = None
            # 输入框上方的工作目录栏:全路径 + 是否展开(点「显示完整路径」后展开看全)。
            self._workdir_full = os.path.realpath(os.environ.get("WORK_ROOT") or os.getcwd())
            self._workdir_expanded = False
            # 切换工作目录成功后的回调(cli 侧用它按新根重建会话 system;可为 None)。
            self._on_workdir_change = on_workdir_change

        def compose(self) -> ComposeResult:
            with Vertical(id="contentbox"):
                yield VerticalScroll(id="content")
                yield Static(self._welcome_text(), id="welcome")
            yield Static("", id="status")
            # 输入框**上方常驻**的工作目录栏(可点 → 在文件管理器打开 / 更换工作目录)。
            # 放 #status 与 #slashpanel 之间:既在输入框上方,又不打乱「/ 面板贴输入框正上方」的排布。
            yield WorkdirBar(self._workdir_bar_text(), id="workdirbar")
            # `/` 命令菜单:贴在输入框**正上方**的整宽带边框面板(默认隐藏,打 / 时弹出)。
            # 放 #status 与 #inputrow 之间 —— 出现时自然把内容区往上顶,像 claude code。
            # 面板复用于一级菜单与二级枚举菜单:顶部标题、中间 OptionList、底部快捷键提示。
            with Vertical(id="slashpanel"):
                yield Label("", id="slash_title")
                yield OptionList(id="slashlist")
                yield Label("", id="slash_hint")
            with Horizontal(id="inputrow"):
                yield Static("❯", id="prompt")
                yield ChatInput(id="input", placeholder="请输入问题,回车发送")

        async def on_event(self, event):
            """应用级接管终端**粘贴/拖拽**:无论焦点在不在输入框,都把 Paste 内容送进 #input。

            为什么需要(用户实报):从文件管理器把文件/图片**拖拽**进 wchat,非 TUI(PTK inline,
            没有鼠标捕获)由终端把路径当"粘贴"插进输入框;而 TUI 是 Textual 全屏,App 的 on_event
            会把 Paste **只转给当前焦点控件** —— 焦点若不在输入框,Paste 就落到别处、输入框收不到,
            现象是"拖了个寂寞"。这里在 App 层拦下 Paste,直接喂给 ChatInput 的粘贴处理(单行路径=
            插入;多行=占位;空=抓图,全沿用原语义),再回焦输入框。

            仅在**主屏**(screen_stack 只有 1 层,无弹窗)时接管:InputModal/HistoryPickModal 里也有
            Input,那些场景的粘贴应归弹窗自己的输入框,不能被这里抢走。
            """
            try:
                from textual import events as _ev
            except Exception:
                _ev = None
            if _ev is not None and isinstance(event, _ev.Paste) \
                    and not getattr(event, "is_forwarded", False):
                try:
                    _base_only = len(self.screen_stack) == 1
                except Exception:
                    _base_only = True
                if _base_only:
                    try:
                        _inp = self.query_one("#input", ChatInput)
                        await _inp._on_paste(event)      # 复用其单行插入/多行占位/空粘贴抓图语义
                        try:
                            _inp.focus()
                        except Exception:
                            pass
                        return
                    except Exception:
                        pass
            await super().on_event(event)

        # ---- 空态欢迎提示 ----
        @staticmethod
        def _welcome_text():
            """内容区为空时居中显示的用法提示(纯文本,颜色/斜体交给 CSS #welcome)。
            文案对齐真实命令(见 helptext.py):-h 帮助、-q 退出、Ctrl+F/Ctrl+V 选图/贴图。"""
            return (
                "欢迎使用 WCHAT\n"
                "\n"
                "直接输入你的问题,按回车发送\n"
                "输入 / 弹出命令菜单(如 /q 退出 · /h 帮助)\n"
                "输入 /h 显示更多用法\n"
                "Ctrl+F 选择文件 · Ctrl+G 粘贴图片(Ctrl+V 被终端占用时用 Ctrl+G)"
            )

        def _set_welcome(self, show):
            """切换空态欢迎:#contentbox 加/去 .empty 类 → 藏 #content、亮 #welcome(见 CSS)。
            幂等;拿不到控件(极早期/已卸载)时静默跳过,绝不因提示而崩。"""
            try:
                self.query_one("#contentbox").set_class(bool(show), "empty")
            except Exception:
                pass

        # ---- `/` 命令菜单(一级命令列表 + 二级枚举菜单) ----
        def _slash_is_open(self):
            return getattr(self, "_slash_open", False)

        def _slash_set_labels(self, title, hint):
            try:
                self.query_one("#slash_title", Label).update(Text(title or ""))
                self.query_one("#slash_hint", Label).update(Text(hint or "", style="dim"))
            except Exception:
                pass

        # 选中项文字用的紫色(对标 claude code:选中那行整行文字变紫,不用背景/加粗)
        _SLASH_SEL_COLOR = "#bd93f9"

        def _slash_item_text(self, item, selected=False):
            """渲染一行文案(无编号,像 claude code):命令名(左列,按 _slash_namew 补齐) +
            描述(右列,对齐同一列)。**选中时整行(名字+描述)文字变紫**(claude code 的做法),
            不铺背景、不加粗。"""
            t = Text()
            _col = self._SLASH_SEL_COLOR if selected else ""
            if getattr(self, "_slash_level", 1) == 2:
                _val, label, desc = item
                t.append(label, style=_col)
                if desc:
                    t.append("   ")
                    t.append(desc, style=(_col if selected else "dim"))
            else:
                name, desc, _kind, _opts = item
                w = getattr(self, "_slash_namew", 0) or len(name)
                t.append(name.ljust(w), style=_col)
                t.append("  ")
                t.append(desc, style=(_col if selected else "dim"))
            return t

        def _slash_colorize(self):
            """按当前 ol.highlighted 重画所有行的文字颜色(选中行整行紫,其余常态)。
            只改文案颜色、不改行高,故不会像早期"重画全部行"那样与滚动打架;
            重画后由调用方再 _slash_scroll_into_view 兜一次滚动。"""
            try:
                ol = self.query_one("#slashlist", OptionList)
            except Exception:
                return
            items = getattr(self, "_slash_items", [])
            hl = ol.highlighted
            for i, item in enumerate(items):
                try:
                    ol.replace_option_prompt_at_index(i, self._slash_item_text(item, i == hl))
                except Exception:
                    pass

        def _slash_size_list(self, ol):
            """给 OptionList 一个**固定行高**(不超过 8 行):显式 height 覆盖 CSS 的 auto,
            让它确定成为滚动容器(否则真终端里它撑成全高、被面板裁掉 → 滚动条/scroll_y 都不生效)。
            行数少时按实际行数(不留空)。"""
            try:
                n = ol.option_count
                h = max(1, min(n, 8))
                ol.styles.height = h
                ol.styles.max_height = 8
            except Exception:
                pass

        def _slash_set_hl(self, new):
            """把选中游标设到某行,并**手动保证它滚进视窗**。
            实测:真终端里 OptionList 的 scroll_to_highlight() 不生效(highlighted 变了但视图不滚,
            选中项掉到可见区外);headless run_test 里却会滚 —— 这种"headless 绿、真终端坏"的坑
            必须信真终端。这里直接按 _index_to_line/_heights 算并设 scroll_y,不依赖内部滚动。"""
            try:
                ol = self.query_one("#slashlist", OptionList)
                n = ol.option_count
                if n <= 0:
                    return
                new = max(0, min(n - 1, new))
                ol.highlighted = new
                try:
                    ol.scroll_to_highlight()
                except Exception:
                    pass
                self._slash_colorize()              # 选中行整行变紫
                self._slash_scroll_into_view(ol, new)
            except Exception:
                pass

        def _slash_scroll_into_view(self, ol, idx):
            """确保 idx 这一项落在 OptionList 可视区内(手工算 y,直接设 scroll_y)。"""
            try:
                ol._update_lines()
                y = ol._index_to_line.get(idx, idx)
                h = ol._heights.get(idx, 1)
                view = ol.scrollable_content_region.height or 1
                cur = int(ol.scroll_y or 0)
                if y < cur:
                    ol.scroll_y = y
                elif y + h > cur + view:
                    ol.scroll_y = max(0, y + h - view)
            except Exception:
                pass

        def _slash_refresh(self, text):
            """按输入框文本刷新**一级**命令菜单。**仅**当单行、以 / 开头、且还没打空格时才显示;
            一旦有空格/换行,视为转入正文或参数,收起(避免 /usr/bin、8/9、a b 被误弹)。"""
            try:
                panel = self.query_one("#slashpanel")
                ol = self.query_one("#slashlist", OptionList)
            except Exception:
                return
            show = (text is not None and "\n" not in text
                    and text.startswith("/") and " " not in text)
            if not show:
                self._slash_close()
                return
            from wchat_py.cmdline import SLASH_COMMANDS
            prefix = text[1:].lower()
            entries = [e for e in SLASH_COMMANDS if e[0][1:].lower().startswith(prefix)]
            if not entries:
                self._slash_close()
                return
            self._slash_level = 1
            self._slash_second = None
            self._slash_items = entries
            self._slash_open = True
            # 命令名补到统一宽度 → 描述列对齐(像 claude code 两列)
            self._slash_namew = max([len(e[0]) for e in entries] or [0])
            ol.clear_options()
            for item in entries:
                ol.add_option(Option(self._slash_item_text(item), id=item[0]))
            panel.add_class("open")
            self._slash_size_list(ol)
            self._slash_set_labels("", "输入以过滤 · ↑↓ 选择 · Enter/Tab 确认 · Esc 关闭 · 也可鼠标点选")
            self._slash_set_hl(0)

        def _slash_open_second(self, name, opts):
            """进入二级枚举菜单(Claude `Select model` 那种):列出该命令的固定选项。"""
            try:
                ol = self.query_one("#slashlist", OptionList)
            except Exception:
                return
            self._slash_level = 2
            self._slash_second = {"name": name, "opts": opts}
            self._slash_items = list(opts)
            self._slash_open = True
            self._slash_namew = max([len(o[1]) for o in opts] or [0])
            ol.clear_options()
            for item in self._slash_items:
                ol.add_option(Option(self._slash_item_text(item), id=item[0]))
            self._slash_size_list(ol)
            try:
                from wchat_py.cmdline import SLASH_COMMANDS
                _desc = next((e[1] for e in SLASH_COMMANDS if e[0] == name), "")
                self._slash_set_labels("选择:%s   %s" % (name, _desc),
                                       "↑↓ 选择 · Enter/Tab 应用 · Esc 返回 · 也可鼠标点选")
            except Exception:
                pass
            self._slash_set_hl(0)

        def _slash_close(self):
            self._slash_open = False
            self._slash_level = 1
            self._slash_items = []
            self._slash_second = None
            try:
                self.query_one("#slashpanel").remove_class("open")
                self.query_one("#slashlist", OptionList).clear_options()
            except Exception:
                pass

        def _slash_back(self):
            """Esc:二级 → 回到一级(按当前输入文本重刷);一级 → 关闭。"""
            if getattr(self, "_slash_level", 1) == 2:
                try:
                    t = self.query_one("#input", ChatInput).text
                except Exception:
                    t = ""
                self._slash_level = 1
                self._slash_second = None
                self._slash_refresh(t)
            else:
                self._slash_close()

        def _slash_move(self, delta):
            if not getattr(self, "_slash_open", False):
                return
            try:
                ol = self.query_one("#slashlist", OptionList)
                n = ol.option_count
                if n <= 0:
                    return
                cur = ol.highlighted if ol.highlighted is not None else 0
                self._slash_set_hl((cur + delta) % n)      # 环形;原生 watch 自带滚动跟随
            except Exception:
                pass

        def _slash_accept_at(self, idx):
            """按索引触发当前层级的选中动作;返回是否处理了。"""
            if getattr(self, "_slash_level", 1) == 2:
                return self._slash_accept_second(idx)
            items = getattr(self, "_slash_items", [])
            if idx is None or not (0 <= idx < len(items)):
                return False
            name, _desc, kind, opts = items[idx]
            try:
                inp = self.query_one("#input", ChatInput)
            except Exception:
                inp = None
            if kind == "file":
                # /img:清空输入框 → 弹原生选择器 → 插入图片占位(不自动发送;用户可继续写问题)
                self._slash_close()
                if inp is not None:
                    try:
                        inp.text = ""
                        inp.cursor_location = (0, 0)
                    except Exception:
                        pass
                    try:
                        inp.action_pick_file()
                    except Exception:
                        pass
                    try:
                        inp.focus()
                    except Exception:
                        pass
                return True
            if kind == "enum":
                # 带固定选项 → 进二级菜单(选中后自动执行)
                self._slash_open_second(name, opts or [])
                return True
            # run:清空输入框后把命令交给统一入口
            self._slash_close()
            if inp is not None:
                try:
                    inp.text = ""
                    inp.cursor_location = (0, 0)
                except Exception:
                    pass
            self._handle_line_text(name)
            return True

        def _slash_accept(self):
            """选中当前高亮项;返回是否真的处理了(Enter/Tab 守卫据此决定是否吞掉)。"""
            if not getattr(self, "_slash_open", False):
                return False
            try:
                idx = self.query_one("#slashlist", OptionList).highlighted
            except Exception:
                idx = 0
            return self._slash_accept_at(idx)

        def _slash_accept_second(self, idx):
            sec = getattr(self, "_slash_second", None) or {}
            opts = sec.get("opts") or []
            name = sec.get("name") or ""
            if idx is None or not (0 <= idx < len(opts)) or not name:
                return False
            val = opts[idx][0]
            try:
                inp = self.query_one("#input", ChatInput)
            except Exception:
                inp = None
            self._slash_close()
            if inp is not None:
                try:
                    inp.text = ""
                    inp.cursor_location = (0, 0)
                except Exception:
                    pass
            self._handle_line_text("%s=%s" % (name, val))   # 选中即执行(自动发送)
            return True

        def on_option_list_option_highlighted(self, event):
            """鼠标悬停(或程序设 highlighted)使高亮变化 → 重着色,让悬停项也整行变紫。
            键盘路径已在 _slash_set_hl 同步着色;这里兜鼠标悬停。幂等,不会自激。"""
            try:
                if event.option_list is not self.query_one("#slashlist", OptionList):
                    return
            except Exception:
                return
            if getattr(self, "_slash_open", False):
                self._slash_colorize()

        def on_option_list_option_selected(self, event):
            """单击某个选项 → 选中并执行(与键盘 Enter 同一套 _slash_accept_at)。"""
            try:
                if event.option_list is not self.query_one("#slashlist", OptionList):
                    return
            except Exception:
                return
            if not getattr(self, "_slash_open", False):
                return
            self._slash_accept_at(event.option_index)

        def on_text_area_changed(self, event):
            """输入框内容变化 → 刷新 `/` 菜单。只有主输入框(#input)才触发(弹窗里的 Input 不是 TextArea)。"""
            try:
                ta = event.text_area
            except Exception:
                ta = None
            if ta is None or getattr(ta, "id", None) != "input":
                return
            # 二级菜单期间不因文本事件把用户踢回一级(用户应在选项间导航);只有文本被清空才收起。
            if getattr(self, "_slash_level", 1) == 2:
                if not (ta.text or "").strip():
                    self._slash_close()
                return
            self._slash_refresh(ta.text)

        # ---- 内容区(消息列表:每段一个 Static,顺序 append,自然从提问下一行开始) ----
        def _content(self):
            return self.query_one("#content", VerticalScroll)

        def _scroll_end(self):
            try:
                self._content().scroll_end(animate=False)
            except Exception:
                pass

        def _mount(self, renderable, classes="", sep=True):
            """挂一个消息 widget 到内容区。
            `sep=True`(默认):这是一个**逻辑块起点** → 若已有内容,加 `.blk`(margin-top:1)空一行。
            `sep=False`:工具块的续行(框线 `│`/状态 `└─`)→ 不加间距,紧跟上一个块。"""
            try:
                cls = classes or ""
                if sep and getattr(self, "_has_blocks", False):
                    cls = (cls + " blk").strip()
                self._set_welcome(False)          # 有内容了 → 收起空态欢迎、亮出 #content
                w = Static(renderable, classes=cls) if cls else Static(renderable)
                self._content().mount(w)
                if sep:
                    self._has_blocks = True
                self._scroll_end()
            except Exception:
                pass

        def _mount_markdown(self, md_text, sep=True):
            """把助手正文挂成一个**可选/可复制**的 markdown 块(见 _MarkdownStatic)。
            与 _mount 同样的 `.blk` 块间距规则;正文按控件宽度预渲成带样式 Text→Content,
            从而带 offset 元数据、可被原生拖拽选中复制。"""
            try:
                cls = "blk" if (sep and getattr(self, "_has_blocks", False)) else ""
                self._set_welcome(False)          # 有正文了 → 收起空态欢迎、亮出 #content
                w = _MarkdownStatic(md_text, classes=cls) if cls else _MarkdownStatic(md_text)
                self._content().mount(w)
                if sep:
                    self._has_blocks = True
                self._scroll_end()
            except Exception:
                pass

        def _ensure_live(self):
            if self._live_widget is None:
                cls = "blk" if self._has_blocks else ""
                self._set_welcome(False)          # 开始流式 → 收起空态欢迎、亮出 #content
                w = Static("", classes=cls)
                try:
                    self._content().mount(w)
                    self._scroll_end()
                except Exception:
                    pass
                self._live_widget = w
                self._has_blocks = True

        def _simplest_on(self):
            try:
                from wchat_py.simplest import enabled
                return enabled()
            except Exception:
                return False

        def _simplest_status_text(self, phase, remaining=None):
            """状态行文本:走马灯点(1→2→3 循环)+ 文字颜色滑动渐变(蓝→紫→蓝)。

            phase 每帧 +1:既驱动结尾点循环(内部 %3),又驱动渐变相位(整串颜色随之流动)。
            `remaining` 非 None 时显示"倒计时 x 秒"(耗时 shell 执行中);否则显示"正在处理中"。
            颜色是逐字符 style,plain 文本不变(不影响任何按 plain 取字的断言)。
            """
            from wchat_py.simplest import working_text, countdown_text
            s = countdown_text(remaining, phase) if remaining is not None else working_text(phase)
            A = (120, 160, 255)          # 柔蓝
            B = (232, 140, 255)          # 柔紫
            period = 10.0                # 一串里约 10 个字符走完一个 A→B→A 的往返
            t = Text()
            for i, ch in enumerate(s):
                frac = ((i + phase) % period) / period
                wv = 1 - abs(2 * frac - 1)          # 三角波 0→1→0
                r = int(A[0] + (B[0] - A[0]) * wv)
                g = int(A[1] + (B[1] - A[1]) * wv)
                b = int(A[2] + (B[2] - A[2]) * wv)
                t.append(ch, style="italic #%02x%02x%02x" % (r, g, b))
            return t

        def _simplest_show_waiting(self):
            """挂一条「正在处理中,请稍后.」状态行(带跑马灯点 + 颜色渐变),并起定时器让它动。

            每进入一个"等待下一段正文"的阶段调用一次(turn 开始、每次正文段结束之后)。
            """
            try:
                self._simplest_stop_timer()
                self._cd_active = False          # 新一段等待不继承上一段的倒计时
                cls = "blk" if getattr(self, "_has_blocks", False) else ""
                self._set_welcome(False)
                txt = self._simplest_status_text(0)
                w = Static(txt, classes=cls) if cls else Static(txt)
                self._content().mount(w)
                self._simplest_status = w
                self._simplest_phase = 0
                self._has_blocks = True
                self._scroll_end()
                # 0.4s 一帧 → 点从 1 增到 3 再回到 1(约 1.2s 一轮)+ 颜色随相位流动。
                self._simplest_timer = self.set_interval(0.4, self._simplest_tick)
            except Exception:
                pass

        def _simplest_begin(self):
            """-simplest:本轮开始挂一条等待状态行。"""
            if not self._simplest_on():
                return
            self._simplest_show_waiting()

        def _simplest_flip_done(self):
            """把当前「正在处理中」状态行改成「处理完成」并停动画(正文一开始就调用)。"""
            w = getattr(self, "_simplest_status", None)
            self._simplest_status = None
            self._simplest_stop_timer()
            if w is None:
                return
            try:
                from wchat_py.simplest import DONE
                w.update(Text(DONE, style="dim italic"))
            except Exception:
                pass

        def _simplest_segment_end(self):
            """工具轮边界(该段正文已输出完):定稿当前正文块,再在下方起一条新的等待状态行。

            幂等:若当前已在等待(_simplest_status 非空,即这段还没出正文),不做任何事,
            避免一轮里多次工具调用/多行 stdout 反复打断。只有"正文已出来(status 已被翻成
            处理完成)"且确有正文时,才收尾并开新段。
            """
            if not self._simplest_on():
                return
            if getattr(self, "_simplest_status", None) is not None:
                return
            if not (self._answer or "").strip() and not (self._reason or "").strip():
                return
            # 正文可能还没物化成 live widget(极快的连续事件在同一 tick 内到达)→ 先物化再定稿,
            # 否则 _finalize_live 见 _live_widget 为 None 会直接返回,这段正文就丢了。
            if self._live_widget is None:
                self._ensure_live()
                try:
                    self._live_widget.update(self._live_display())
                except Exception:
                    pass
            self._finalize_live()
            self._simplest_show_waiting()

        def _simplest_shell_start(self, total):
            """前台 shell 开始执行:记住起始时刻与限时(秒),到点后由 tick 切换成倒计时。

            只在**跑起来且超过 _CD_THRESHOLD 秒仍未结束**时才真的显示倒计时 —— 快速命令
            根本不会亮,避免"倒计时600闪一下"。
            """
            try:
                self._cd_active = True
                self._cd_start = __import__("time").monotonic()
                self._cd_total = float(total or 0)
            except Exception:
                pass

        def _simplest_shell_end(self):
            """前台 shell 结束(未到/已到限时):撤掉倒计时,状态行回到覆盖前的样子(正在处理中)。"""
            self._cd_active = False

        def _simplest_tick(self):
            """定时器回调(UI 线程):相位 +1,推进跑马灯点与颜色渐变;耗时 shell 期间显示倒计时。"""
            w = getattr(self, "_simplest_status", None)
            if w is None:
                self._simplest_stop_timer()
                return
            try:
                self._simplest_phase = getattr(self, "_simplest_phase", 0) + 1
                _rem = None
                if getattr(self, "_cd_active", False):
                    import time as _t
                    _el = _t.monotonic() - getattr(self, "_cd_start", 0.0)
                    if _el >= _CD_THRESHOLD:            # 跑超过 ~3s 才亮倒计时
                        import math as _math
                        _rem = max(0, _math.ceil(getattr(self, "_cd_total", 0.0) - _el))
                w.update(self._simplest_status_text(self._simplest_phase, _rem))
            except Exception:
                pass

        def _simplest_stop_timer(self):
            t = getattr(self, "_simplest_timer", None)
            self._simplest_timer = None
            if t is not None:
                try:
                    t.stop()
                except Exception:
                    pass

        def _simplest_done(self):
            """-simplest:本轮结束,把仍在等待的状态行收成「处理完成」并停动画。"""
            self._simplest_flip_done()

        def on_mount(self):
            if banner:
                self._mount(Text.from_ansi(banner))
            # 首次进入:内容区为空(无 banner/无历史)时显示居中欢迎提示;有 banner 则收着。
            self._set_welcome(not self._has_blocks)
            # 统一 markdown 观感:给 app 控制台压入与 mdrender 同一份现代主题。
            # Textual 的 RichVisual 渲染 mounted 的 rich.markdown.Markdown 时用的就是
            # app.console,所以这里 push 之后,TUI 里的标题/代码/链接/表格等样式与
            # 非 TUI 的 render_markdown 完全一致(以前 TUI 用的是 rich 纯默认:标题居中、
            # 品红/青色、粗体黑底代码块)。
            try:
                self.console.push_theme(markdown_theme())
            except Exception:
                pass
            self.query_one("#input", ChatInput).focus()
            self._refresh_workdir()   # 初始化输入框上方的工作目录栏(挂载后按真实宽度渲染)
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
            # `wchat "内容"`:进入后自动把这句话作为第一轮发出去(旧 plain/PTK 交互曾用
            # WCHAT_FIRST_TURN 消费;TUI 以前漏了这条,导致内容被静默丢弃 —— 现补上)。
            if first_turn:
                self.set_timer(0.05, lambda: self._inject_initial(first_turn))

        def _inject_initial(self, text):
            """把预置首轮当作一次用户提交(text 分支:回显 + 起一轮)。"""
            text = (text or "").strip()
            if not text or getattr(self, "_busy", False):
                return
            self._mount(Text("❯ " + text, style="reverse"))
            self._busy = True
            self._simplest_begin()
            self._run_turn(text)

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
            给了 choices 时弹**分页列表选择框**(HistoryPickModal),否则普通单行输入框。
            返回值:str = 用户提交的内容(可能空串);None = 用户按 Esc 取消(不能和空串混为一谈,
            否则 -l 取消会被当成"回车=第 1 条")。"""
            if threading.current_thread() is threading.main_thread():
                return None
            ev = threading.Event()
            box = {}

            def _done(result):
                # None(取消)原样保留,空串(回车提交空)也是空串 —— 两者语义不同,别在这一层抹平。
                box["r"] = result
                ev.set()

            def _show():
                try:
                    if choices:
                        self.push_screen(HistoryPickModal(question or "", choices), _done)
                    else:
                        self.push_screen(InputModal(question or ""), _done)
                except Exception:
                    box["r"] = None
                    ev.set()

            self.call_from_thread(_show)
            ev.wait()
            return box.get("r")

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
            self._has_blocks = False
            self._set_welcome(False)          # 准备挂历史 → 亮出 #content;挂完再按是否为空决定欢迎
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
                    self._mount(self._render_reason(reasoning), classes=self._reason_classes())
                if role == "tool":
                    name = tc_names.get(m.get("tool_call_id") or "")
                    self._mount(Text(("tool → %s" % name) if name else "tool", style="dim"))
                    disp = content.replace("\\n", "\n").lstrip("\ufeff")
                    lines = [ln for ln in disp.splitlines() if ln.strip()]
                    if lines:
                        shown = "\n".join(_repl._clip_line(ln) for ln in lines[:5])
                        if len(lines) > 5:
                            shown += "\n…(其余 %d 行已省略)" % (len(lines) - 5)
                        self._mount(Text(shown, style="dim"), sep=False)
                elif role == "user":
                    self._mount(Text("❯ " + content, style="reverse"))
                else:   # assistant / 其它
                    if content.strip():
                        try:
                            self._mount_markdown(content)
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
            self._set_welcome(not self._has_blocks)   # 历史为空 → 回到欢迎;有内容则收着

        def emit(self, s):
            self._q.put(("log", str(s), None))

        def stream(self, s):
            self._q.put(("live", str(s), False))

        def reason(self, s):
            # -simplest:思考文本完全不展示(整轮只留状态行 + 正文)。
            if self._simplest_on():
                return
            self._q.put(("live", str(s), True))

        def _live_display(self):
            """流式 live 区的显示文本。**思考与正文之间换一行**:用户实测思考末尾紧贴正文很别扭,
            希望思考结束后正文另起一行(仅流式期;md 定稿阶段 reason/answer 是两个独立 widget,本就分行)。"""
            t = Text()
            if self._reason:
                t.append(self._reason, style="dim")
            if self._answer:
                # 思考非空且正文开始:确保正文另起一行(思考没以换行结尾就补一个,不重复补)。
                if self._reason and not self._reason.endswith("\n"):
                    t.append("\n")
                t.append(self._answer)
            return t

        def _hint_text(self, t):
            """把带 dim│ 前缀的"提示行"转成 TUI 展示:去掉每行行首的竖线前缀,整行统一成
            "思考内容灰 + 斜体"(dim italic,与 placeholder/状态行同档次要文本样式)。
            保留行内其它 ANSI(如 ✅/⚠ 的颜色)——只把**基础**色与字形统一,不动语义强调。"""
            lines = [_HINT_BAR_RE.sub("", ln, count=1) for ln in t.split("\n")]
            txt = Text.from_ansi("\n".join(lines))
            txt.style = "dim italic"
            return txt

        def _render_reason(self, text):
            """思考渲染(两档):
            · 默认(-tf=off):**折叠成一行**(▸ + 首个非空行、压空白);单行由 CSS
              (`.reason { text-wrap:nowrap; text-overflow:ellipsis }`)保证,超宽自动 … 截断。
            · -tf=on:**按原始分行忠实展示**。不再走 render_reason→render_markdown 那趟
              markdown 重排(它会把思考自带的换行当软换行合并成一段、还把每行补行尾空格到满宽
              → "整段重排 + 补空格 + 流式到定稿形态跳变")。这里只保留模型给的行结构(含内部
              空行),每行两格缩进、整块 dim;超宽由 `.reason-full { text-wrap: wrap }` 自然
              折行,内容一字不丢、也不补行尾空格。"""
            try:
                from wchat_py.render import think_full
                if think_full():
                    lines = (text or "").splitlines()
                    # 去掉首尾纯空行(避免块首/块尾多余空白),**内部空行保留**(段落结构)
                    while lines and not lines[0].strip():
                        lines.pop(0)
                    while lines and not lines[-1].strip():
                        lines.pop()
                    if not lines:
                        return Text("")
                    t = Text()
                    t.append("▸", style="dim")
                    for ln in lines:
                        if ln.strip():
                            t.append("\n  " + ln, style="dim")
                        else:
                            t.append("\n", style="dim")   # 内部空行:不补缩进空格
                    return t
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

        def _reason_classes(self):
            """reason 块要挂的 class:-tf=on 用 `reason reason-full`(可折行);
            默认只用 `reason`(nowrap+ellipsis,折叠单行)。"""
            try:
                from wchat_py.render import think_full
                return "reason reason-full" if think_full() else "reason"
            except Exception:
                return "reason"

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
                self._mount(self._render_reason(reason), classes=self._reason_classes())
            if answer.strip():
                try:
                    self._mount_markdown(answer)
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
                # marker_text() 自带 \033[2m(dim):dim 会把 CSS 灰再压暗一档(实测 #6a6a6a →
                # ~#4a4a4a),深色终端上几乎看不见。这里**剥掉 ANSI**、颜色/斜体交给 CSS
                # (#status.on = 与 placeholder 同一灰+斜体),让状态行清楚可读。
                plain = re.sub(r"\x1b\[[0-9;]*m", "", m).strip() if m else ""
                st.update(Text(plain) if plain else "")
                # 有标记才占高度(#status.on),否则 collapsed 成 0 —— 无 auto 时不留过宽空档
                st.set_class(bool(m), "on")
            except Exception:
                pass

        # ---- 输入框上方的工作目录栏:渲染 / 刷新 / 点击菜单 ----
        def _workdir_bar_text(self):
            """工作目录栏文本:`📁 <路径>  ▾`;折叠态用 `~` 缩家目录,展开态显示全路径。
            尾部 `▾` 提示"可点开菜单"。"""
            full = getattr(self, "_workdir_full", "") or os.path.realpath(
                os.environ.get("WORK_ROOT") or os.getcwd())
            disp = full if getattr(self, "_workdir_expanded", False) else _pretty_path(full)
            return Text("📁 " + disp + "  ▾")

        def _refresh_workdir(self, expand=None):
            """重算全路径并重绘工作目录栏;expand 非 None 时同步展开/折叠态。
            工作目录在 v1 启动即定(切换目录不在本版范围),这里主要用于挂载时初始化与展开切换。"""
            try:
                self._workdir_full = os.path.realpath(
                    os.environ.get("WORK_ROOT") or os.getcwd())
                if expand is not None:
                    self._workdir_expanded = bool(expand)
                bar = self.query_one("#workdirbar", Static)
                bar.update(self._workdir_bar_text())
                bar.set_class(self._workdir_expanded, "full")
            except Exception:
                pass

        def _set_workdir_expanded(self, flag):
            self._refresh_workdir(expand=flag)

        def _open_workdir_menu(self):
            """点击工作目录栏:弹出小菜单(在文件管理器打开 / 更换工作目录)。"""
            full = getattr(self, "_workdir_full", "") or os.path.realpath(
                os.environ.get("WORK_ROOT") or os.getcwd())
            try:
                self.push_screen(
                    WorkdirMenuModal(full, _pretty_path(full)),
                    self._on_workdir_menu)
            except Exception:
                pass

        def _on_workdir_menu(self, action):
            """工作目录菜单回调:action = filemgr(文件管理器打开) / change(更换目录) / None(取消)。"""
            if not action:
                return
            full = getattr(self, "_workdir_full", "") or os.path.realpath(
                os.environ.get("WORK_ROOT") or os.getcwd())
            if action == "filemgr":
                ok, msg = _open_path_in_file_manager(full)
                if not ok:
                    self._mount(Text("📂 %s: %s" % (msg, full), style="dim"), sep=False)
            elif action == "change":
                self._begin_switch_workdir("")
                return          # 切换流程自己会回焦输入框,不走下面统一回焦
            try:
                self.query_one("#input", ChatInput).focus()
            except Exception:
                pass

        def _auto_on(self):
            """当前是否处于 auto 档(AUTO_MODE/WCHAT_AUTO_MODE 任一有值)。"""
            return bool(os.environ.get("AUTO_MODE") or os.environ.get("WCHAT_AUTO_MODE"))

        def _begin_switch_workdir(self, arg=""):
            """更换工作目录入口(工作目录栏菜单「更换工作目录…」与 `/cd` 共用)。
            规则:auto 下禁止(信任边界不可动态平移);忙时禁止;其余弹原生选目录
            (arg 显式给路径则跳过选择器,但仍过合法性校验)。"""
            if self._auto_on():
                self._mount(Text("⚡ auto 开启中,不能更换工作目录。请先 /noauto 关闭 auto,再换目录。",
                                 style="dim"), sep=False)
                self._focus_input_later()
                return
            if getattr(self, "_busy", False):
                self._mount(Text("(上一轮仍在进行,请稍候再换目录)", style="dim"), sep=False)
                self._focus_input_later()
                return
            arg = (arg or "").strip()
            if arg:
                target = arg
            else:
                try:
                    from wchat_py.repl import _pick_dir
                    target = _pick_dir(self._workdir_full)
                except Exception:
                    target = ""
                if not target or target == "CANCELLED":
                    self._focus_input_later()
                    return
            ok, res = _validate_workdir(target)
            if not ok:
                self._mount(Text("⚠ 无法切换工作目录: " + res, style="dim"), sep=False)
                self._focus_input_later()
                return
            self._do_switch_workdir(res)

        def _do_switch_workdir(self, real):
            """执行切换:chdir + 改 WORK_ROOT + (可选)重建会话 system + 刷新工作目录栏。
            不重启进程;不动审计/备份/回滚(auto 与切换互斥,非 auto 下这几项本就不生效)。
            保留当前对话(仅原地改 messages[0] 的 system,见 cli 侧回调)。"""
            try:
                os.chdir(real)
            except Exception as e:
                self._mount(Text("⚠ 切换失败(chdir): %r" % (e,), style="dim"), sep=False)
                self._focus_input_later()
                return
            os.environ["WORK_ROOT"] = real
            cb = getattr(self, "_on_workdir_change", None)
            if cb is not None:
                try:
                    cb(real)          # cli 侧:按新根重建 system 提示(只改 messages[0],保留对话)
                except Exception:
                    pass
            self._refresh_workdir(expand=False)
            self._mount(Text("📁 已切换工作目录: " + _pretty_path(real), style="dim"), sep=False)
            self._focus_input_later()

        def _focus_input_later(self):
            try:
                self.query_one("#input", ChatInput).focus()
            except Exception:
                pass

        def _drain(self):
            # 全屏 TUI:把横幅/状态行的宽度算据覆盖成**内容区实际宽度**(否则按终端全宽算 → 折行)。
            try:
                from wchat_py.render import set_term_cols as _stc
                _w = self._content().size.width
                if _w:
                    _stc(_w)
            except Exception:
                pass
            changed = False
            try:
                while True:
                    ev = self._q.get_nowait()
                    if ev[0] == "shell_countdown_start":
                        self._simplest_shell_start(ev[1]); continue
                    if ev[0] == "shell_countdown_end":
                        self._simplest_shell_end(); continue
                    if ev[0] == "simplest_log":
                        # -simplest:工具横幅/结果/输出箱不显示,只当"工具轮边界"信号
                        # (收尾当前正文段 + 在下方起一条新的等待状态行;幂等)。
                        self._simplest_segment_end()
                        continue
                    if ev[0] == "live":
                        if ev[2]:
                            self._reason += ev[1]
                        else:
                            # -simplest:正文一开始就出现 → 把上头的「正在处理中」翻成「处理完成」。
                            if self._simplest_on() and getattr(self, "_simplest_status", None) is not None:
                                self._simplest_flip_done()
                            self._answer += ev[1]
                        changed = True
                    else:  # log:先定稿 live 再挂内容;auto 标记行只进状态区(不进内容区)
                        t = ev[1]
                        if "本次会话已自动放行" in t:
                            continue
                        # 每个 write 挂成一个 Static;去掉尾部换行(Static 带尾换行会多渲染一空行,
                        # 造成横幅与结果之间/块之间多出空行),纯空白写入直接丢弃(不挂空行)。
                        t = t.rstrip("\n")
                        # 去掉行尾的 **裸 \r**:toolrun 展开档按真终端习惯逐行写 `...\r\n`
                        # (终端里 \r 是回车,无妨;但 TUI 把每次 write 当一条 log,交给
                        # rich `Text.from_ansi` 渲染 —— venv 的 rich 15.0.0 对**结尾带 \r** 的串
                        # 直接返回空串,于是 `-tf=on` 的每一行 shell 输出都变成空白,整段框"没显示"。
                        # 命令真实输出里的 \r 早已被 termwin.clean_line 清掉,这里的 \r 只有行尾这一个,
                        # 去掉它不丢任何内容。\x1b[2K 之类的清行码 from_ansi 本就会静默忽略,无需处理。
                        t = t.replace("\r", "")
                        if not t.strip():
                            continue
                        self._finalize_live()
                        style = ev[2] if len(ev) > 2 else None
                        # 工具块的续行(框线 `│` / 状态 `└─`)**不**加块间空行;横幅(⏺)与其它=新块。
                        # _cont 用**原文**判定:提示行带 dim│ 时也归为续行、保持紧凑(不逐行空行)。
                        _cont = ("└─" in t) or ("│" in t)
                        if style is None and _HINT_BAR_RE.match(t):
                            # 提示行:去掉行首 dim│ 前缀,统一成"思考内容灰 + 斜体"(不再是竖线)。
                            hint = self._hint_text(t)
                            if not hint.plain.strip():
                                continue          # 去前缀后是空行(如 helptext 里的空行)→ 不挂
                            self._mount(hint, sep=(not _cont))
                        else:
                            self._mount(Text(t, style=style) if style else Text.from_ansi(t),
                                        sep=(not _cont))
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
            self._handle_line_text(event.value or "")

        def _handle_line_text(self, text):
            """一行输入的统一入口:分类(元命令/正文)后分发。输入框提交与 `/` 菜单选中都走这里,
            保证两条路径**同一套**语义(不会菜单选中和手敲行为不一致)。"""
            if not text.strip():
                return
            # 元命令(-q/-u/-n/...)先分类;`-q` 直接退出,其余走 on_meta(text 才进模型)
            try:
                from wchat_py.cmdline import classify
                action, arg = classify(text.strip())
            except Exception:
                action, arg = "text", text
            if action == "quit":
                self._force_quit()          # 唯一退出途径:/q —— 清排队消息 + 清取消标志后退出
                return
            if action == "cd":
                # 更换工作目录:在 TUI 主线程里直接处理(要动 cwd + env + UI,不走 worker/on_meta)。
                self._begin_switch_workdir(arg)
                return
            if action != "text":
                # 元命令输出(如 -auto 的开启横幅)照常在**内容区**展示;auto 档标记另由
                # 状态区常驻显示(#status,见 _refresh_status)。
                if self._busy:
                    self._mount(Text("(上一轮仍在进行,请稍候…)", style="dim"), sep=False)
                    return
                self._busy = True
                self._run_meta(action, arg)
                return
            self._mount(Text("❯ " + text, style="reverse"))
            if self._busy:
                # M4:边流式输出边输入 —— 上一轮还在跑时提交不丢弃,进队列;worker 会在下一个
                # 小轮边界把它(们)作为"补充信息"并入本轮;纯问答则本轮结束后自动作为下一轮发。
                self._pending.put(text)
                self._mount(Text("（已加入队列：将在下一小轮或下一轮并入发给模型）", style="dim"),
                            sep=False)
                return
            self._busy = True
            self._simplest_begin()
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
            # 新一轮开始:清除上一轮可能残留的取消标志(防御性)。
            try:
                from wchat_py import cli as _cli
                _cli.clear_llm_cancel()
            except Exception:
                pass
            o_out, o_err = sys.stdout, sys.stderr
            # -simplest:本轮工具横幅/结果/输出箱等 stdout 不显示,但当作"工具轮边界"信号
            # (转到 _SimplestStdout → _drain 里触发 segment 收尾+新状态行);stderr(报错)照常走 TuiStdout。
            sys.stdout = _SimplestStdout(self) if self._simplest_on() else TuiStdout(self)
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
            self._has_blocks = False
            self._set_welcome(True)           # 清屏/新对话后内容区空了 → 重新显示欢迎提示

        def _on_turn_done(self):
            # 本轮结束:清掉取消标志(否则下一轮首个请求会被立刻取消)。
            try:
                from wchat_py import cli as _cli
                _cli.clear_llm_cancel()
            except Exception:
                pass
            self._drain()
            self._finalize_live()
            self._busy = False
            self._simplest_done()
            # M4 兜底:本轮结束若还有排队消息(纯问答/最后一个小轮之后才到的),作为**下一轮**发出。
            # (有工具轮的场合,它们一般已在 orchestrate 的小轮边界被并入本轮,这里队列已空。)
            try:
                _nxt = self._pending.get_nowait()
            except queue.Empty:
                _nxt = None
            if _nxt is not None:
                self._mount(Text("（发送排队消息）", style="dim"))
                self._busy = True
                self._simplest_begin()
                self._run_turn(_nxt)
                return
            try:
                self.query_one("#input", ChatInput).focus()
            except Exception:
                pass
            # 一轮结束按**真实子件数**对账空态:本轮若没留下任何可见块(空回复等),把欢迎提示放回来,
            # 避免内容区空着却一直收着提示(比只靠 _has_blocks 更准;正常有内容时无副作用)。
            try:
                self._set_welcome(len(self._content().children) == 0)
            except Exception:
                pass

        def _force_quit(self):
            """真正退出:丢弃排队消息 + 清取消标志,exit。"""
            try:
                from wchat_py import cli as _cli
                _cli.clear_llm_cancel()
            except Exception:
                pass
            try:
                while True:
                    self._pending.get_nowait()
            except queue.Empty:
                pass
            self.exit()

        def action_maybe_quit(self):
            """Ctrl+C:**永不退出会话**(唯一退出方式是 /q)。
            · 正在跑 → 打断当前轮(可反复按,只是重复打断;已完成部分保留)。
            · 空闲   → 只提示"退出请用 /q",不退出。
            历史上"连按两次强制退出 / 空闲直接退出"违背了 -h 里"Ctrl+C 不会退出"的约定,
            且导致误触关掉整个会话 —— 一并去掉。"""
            if self._busy:
                try:
                    from wchat_py import cli as _cli
                    _cli.request_llm_cancel()
                except Exception:
                    pass
                try:
                    self._mount(Text("⏹ 正在中断本轮…(退出请用 /q)", style="dim"), sep=False)
                except Exception:
                    pass
                return
            # 空闲:不退出,只提示正确退出方式
            try:
                self._mount(Text("（Ctrl+C 不退出;退出会话请输入 /q）", style="dim"), sep=False)
            except Exception:
                pass

        def _copy_selection(self):
            """把当前选区复制到剪贴板:先内容区(Screen 选区),再输入框内选区。返回是否复制了。"""
            try:
                sel = self.screen.get_selected_text()
            except Exception:
                sel = None
            if sel:
                try:
                    self.copy_to_clipboard(sel)
                    return True
                except Exception:
                    pass
            try:
                inp = self.query_one("#input", ChatInput)
                t = getattr(inp, "selected_text", "") or ""
                if t:
                    self.copy_to_clipboard(t)
                    return True
            except Exception:
                pass
            return False

        def action_copy_selection(self):
            """mac 常规 cmd+c(super+c):只复制选中,不退出。弹窗打开时不接管。"""
            if len(self.screen_stack) > 1:
                return
            self._copy_selection()

        def action_ctrl_c(self):
            """Ctrl+C:有选中→复制;无选中→打断当前轮/空闲提示,**永不退出会话**(退出用 /q)。"""
            if len(self.screen_stack) > 1:
                # 弹窗里不接管 Ctrl+C(弹窗用 y/n/Esc;这里不退出以免误关)。
                return
            if self._copy_selection():
                return
            self.action_maybe_quit()

    return WchatApp()


def run_tui(handle_line, *, banner="", on_meta=None, first_turn="", on_workdir_change=None):
    try:
        _app = make_app(handle_line, banner=banner, on_meta=on_meta, first_turn=first_turn,
                        on_workdir_change=on_workdir_change)
        # 鼠标捕获开关:Textual 全屏默认开鼠标捕获(\x1b[?1000h 等),终端会把"文件拖拽"当
        # 鼠标事件(=拖了没反应),而非像非 TUI 那样当"粘贴"发路径。设 WCHAT_TUI_MOUSE=0 关掉
        # 捕获 → 拖拽恢复为粘贴路径(配合 App.on_event 的粘贴接管,路径落入输入框);代价是
        # 失去鼠标滚轮/点选。默认保持开(不改现有 UX)。
        _mouse = True
        _me = os.environ.get("WCHAT_TUI_MOUSE")
        if _me is not None:
            _mouse = str(_me).strip().lower() not in ("0", "false", "no", "off")
        _app.run(mouse=_mouse)
    finally:
        try:
            from wchat_py import render as _render
            _render.set_confirm_hook(None)
            _render.set_line_hook(None)
            _render.set_history_hook(None)
            _render.set_term_cols(0)      # 退出 TUI 恢复真实终端宽度
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