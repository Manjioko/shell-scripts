#!/usr/bin/env python3
"""Textual TUI 前端闸门(M0,见 docs/tui-redesign.md §6.1)。

用 Textual 的 headless `App.run_test()`(Pilot)在**不需要真终端**的情况下验证:
  1. TuiStdout:write/drain 线程安全缓冲,isatty 归 False(不误判交互)。
  2. WchatApp compose 出且仅出 1 个 RichLog(#content)+ 1 个 Input(#input)。
  3. 输入「hello」回车 → 内容区出现回显「❯ hello」(输入→事件接线通)。
  4. 改终端尺寸(resize)后仍是**1 个** Input、1 个 RichLog(不会叠出多个 widget)。
  5. handle_line 的 emit 会把文本落进内容区(worker→shim→RichLog 通道通)。

textual 未安装 → 打印 SKIP 且 rc=0(不红;TUI 是可选功能)。
用法:`python3 tests/tui_test.py`(要能 import textual;否则 SKIP)。
"""
import asyncio
import io
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ["WCHAT_NO_SETUP"] = "1"


def _import_textual_ok(exe=None):
    import subprocess
    try:
        if exe is None:
            import textual  # noqa: F401
            return True
        return subprocess.run([exe, "-c", "import textual"],
                              capture_output=True, timeout=30).returncode == 0
    except Exception:
        return False


# 本进程无 textual(如 run_gates 用的 system python3 3.9):找一个带 textual 的解释器
# 重新执行本文件,这样闸门在套件里**真的跑**,而不是恒 SKIP。找不到才 SKIP(exit 77)。
if not _import_textual_ok():
    if not os.environ.get("WCHAT_TUI_REEXEC"):
        for _exe in (os.environ.get("WCHAT_TUI_PY"),
                     os.path.expanduser("~/.ct/venv/bin/python"),
                     os.path.join(REPO, ".scratch", "tui_venv", "bin", "python")):
            if _exe and os.path.exists(_exe) and _import_textual_ok(_exe):
                os.environ["WCHAT_TUI_REEXEC"] = "1"
                os.execv(_exe, [_exe, os.path.abspath(__file__)] + sys.argv[1:])
    print("SKIP: textual 未安装且找不到带 textual 的解释器(TUI 为可选功能;可设 WCHAT_TUI_PY)")
    sys.exit(77)

P = F = 0


def ck(name, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  ok   %s" % name)
    else:
        F += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


from wchat_py import tui  # noqa: E402

if not tui.available():
    print("SKIP: textual 未安装(TUI 为可选功能;pytest 环境若无则跳过)。安装:pip install textual")
    sys.exit(77)   # run_gates 约定:环境性缺席 exit 77,不计失败

from textual.widgets import Static, TextArea, Input  # noqa: E402
from textual.containers import VerticalScroll  # noqa: E402


def _content_text(app):
    """把 #content(VerticalScroll)里所有 Static 子件的文本拼起来(Text.plain / Markdown.markup / str)。"""
    from rich.text import Text
    from rich.markdown import Markdown
    out = []
    for c in app.query_one("#content", VerticalScroll).children:
        r = getattr(c, "content", "")
        if isinstance(r, Text):
            out.append(r.plain)
        elif isinstance(r, Markdown):
            out.append(r.markup)
        else:
            out.append(str(r))
    return "\n".join(out)


def _status_text(app):
    """读 #status 状态区的文本。"""
    from rich.text import Text
    r = getattr(app.query_one("#status"), "content", "")
    return r.plain if isinstance(r, Text) else str(r)


# ---- 1) TuiStdout 输出桥(投进 app 的事件队列) ----
print("=== 1. TuiStdout 输出桥 ===")
import queue as _q  # noqa: E402


class _FakeApp:
    def __init__(self):
        self._q = _q.Queue()


_fa = _FakeApp()
sh = tui.TuiStdout(_fa)
ck("isatty 为 False(不误判为交互)", sh.isatty() is False)
sh.write("abc")
sh.write("")
sh.write("def\n")
got = []
try:
    while True:
        got.append(_fa._q.get_nowait())
except _q.Empty:
    pass
ck("write 把非空写入投进队列", [e[1] for e in got] == ["abc", "def\n"], repr(got))
ck("空串不投递", all(e[1] for e in got))
ck("flush 不报错", (sh.flush() is None))


# ---- 2/3/4/5) App 外壳端到端(headless) ----
async def run_app_checks():
    seen = {}

    def handle_line(text, emit, stream, reason):
        seen["text"] = text
        reason("(思考中…)")                 # 思考 → dim
        for ch in ("回答:" + text):
            stream(ch)                      # 正文 → 流式
        emit("\n[工具] 已完成\n")            # 工具横幅 → log

    app = tui.make_app(handle_line, banner="banner-X")
    async with app.run_test() as pilot:
        await pilot.pause()
        ck("compose 出 1 个 #content(VerticalScroll)", len(app.query("#content")) == 1)
        ck("compose 出 1 个 TextArea(输入框)", len(app.query(TextArea)) == 1, "found %d" % len(app.query(TextArea)))

        inp = app.query_one("#input", TextArea)
        ck("主输入框有 placeholder 提示文案",
           getattr(inp, "placeholder", "") == "请输入问题,回车发送",
           repr(getattr(inp, "placeholder", "")))
        try:
            _phst = inp.get_visual_style("text-area--placeholder")
            ck("placeholder 样式=灰色#6a6a6a 且斜体",
               bool(_phst.italic) and "106" in str(_phst.foreground),
               repr(_phst))
        except Exception as _e:
            ck("placeholder 样式=灰色#6a6a6a 且斜体", False, repr(_e))

        # 续行:行尾 \ + 回车 = 换行,不提交
        inp.text = "abc\\"
        await pilot.press("enter")
        await pilot.pause()
        ck("行尾 \\ + 回车 → 续行(不提交)", inp.text == "abc\n" and "text" not in seen, repr(inp.text))

        # 正常提交:回车 → 回显 + 流式答案(定稿进内容区)+ 思考 + 工具横幅
        inp.text = "hello"
        await pilot.press("enter")
        await pilot.pause()
        await asyncio.sleep(0.4)
        await pilot.pause()
        txt = _content_text(app)
        ck("输入回车后内容区出现回显 ❯ hello", "❯ hello" in txt, repr(txt[-160:]))
        ck("handle_line 收到输入", seen.get("text", "").strip() == "hello", repr(seen))
        ck("流式答案定稿进内容区(回答:hello)", "回答:hello" in txt, repr(txt[-200:]))
        ck("思考文本进内容区(dim 段)", "思考中" in txt, repr(txt[-200:]))
        ck("思考按折叠档渲染(有 ▸ 前缀,非整段)", "▸" in txt, repr(txt[-200:]))
        ck("工具横幅经 log 通道出现", "已完成" in txt, repr(txt[-200:]))
        # 顺序:回显在前,思考/答案在其后(自然从提问下一行开始,而非从底部冒)
        ck("回显在思考/答案之前(顺序正确)", txt.find("❯ hello") < txt.find("思考中") if "思考中" in txt else True)

        # resize 后仍是单个 widget(不叠)
        try:
            await pilot.resize_terminal(70, 30)
            await pilot.pause()
        except Exception:
            pass
        ck("resize 后仍 1 个 TextArea", len(app.query(TextArea)) == 1)
        ck("resize 后仍 1 个 #content", len(app.query("#content")) == 1)

        # ---- 6) 确认弹窗:worker 线程 _confirm_blocking → 主线程弹 ConfirmModal → 桥回答案 ----
        import threading

        async def _answer_confirm(key):
            res = {}

            def _w():
                res["v"] = app._confirm_blocking("确认测试", "markX", "是否执行")
            th = threading.Thread(target=_w, daemon=True)
            th.start()
            shown = False
            for _ in range(40):
                await pilot.pause()
                await asyncio.sleep(0.03)
                if app.screen.__class__.__name__ == "ConfirmModal":
                    shown = True
                    break
            ck("worker 触发后弹出 ConfirmModal", shown, app.screen.__class__.__name__)
            await pilot.press(key)
            for _ in range(40):
                await pilot.pause()
                await asyncio.sleep(0.03)
                if not th.is_alive():
                    break
            th.join(timeout=2)
            return res.get("v")

        v_yes = await _answer_confirm("y")
        ck("按 y → worker 拿到 True", v_yes is True, repr(v_yes))
        v_no = await _answer_confirm("n")
        ck("按 n → worker 拿到 False", v_no is False, repr(v_no))

        # ---- 6b) 单行输入弹窗:worker 线程 _line_blocking → InputModal → 桥回字符串 ----
        async def _answer_line(text_val):
            res = {}

            def _w():
                res["v"] = app._line_blocking("请输入 AUTO")
            th = threading.Thread(target=_w, daemon=True)
            th.start()
            shown = False
            for _ in range(40):
                await pilot.pause()
                await asyncio.sleep(0.03)
                if app.screen.__class__.__name__ == "InputModal":
                    shown = True
                    break
            ck("worker 触发后弹出 InputModal", shown, app.screen.__class__.__name__)
            im = app.screen.query_one("#imput", Input)
            im.value = text_val
            await pilot.press("enter")
            for _ in range(40):
                await pilot.pause()
                await asyncio.sleep(0.03)
                if not th.is_alive():
                    break
            th.join(timeout=2)
            return res.get("v")

        v_line = await _answer_line("AUTO")
        ck("输入框键入 AUTO → worker 拿到 'AUTO'", v_line == "AUTO", repr(v_line))

        # ---- 6c) 历史编号选择:带 choices → HistoryPickModal 列出候选 + l/n 翻页 + 编号回传 ----
        _choices = ["对话-%02d.json" % i for i in range(15)]   # 15 条 → 2 页(每页10)

        async def _pick_history(value, keypresses=()):
            res = {}

            def _w():
                res["v"] = app._line_blocking("请输入要加载的历史编号(回车=0)", _choices)
            th = threading.Thread(target=_w, daemon=True)
            th.start()
            shown = False
            for _ in range(50):
                await pilot.pause()
                await asyncio.sleep(0.03)
                if app.screen.__class__.__name__ == "HistoryPickModal":
                    shown = True
                    break
            ck("带 choices → 弹出 HistoryPickModal(不是普通 InputModal)", shown, app.screen.__class__.__name__)
            sc = app.screen
            list_txt = str(getattr(sc.query_one("#hist_list"), "content", ""))
            page_txt = str(getattr(sc.query_one("#hist_page"), "content", ""))
            ck("弹窗内列出候选(第 1 页含 0. 与 9. 条目)",
               "0. 对话-00.json" in list_txt and "9. 对话-09.json" in list_txt, repr(list_txt[:80]))
            ck("第 1 页不显示第 11 条(只在第 2 页)", "10. 对话-10.json" not in list_txt, repr(list_txt[-60:]))
            ck("分页提示显示 1/2 页", "1/2" in page_txt and "l=上一页" in page_txt, repr(page_txt))
            for k in keypresses:
                await pilot.press(k)
                await pilot.pause()
                await asyncio.sleep(0.02)
            if "n" in keypresses:
                list_txt2 = str(getattr(sc.query_one("#hist_list"), "content", ""))
                page_txt2 = str(getattr(sc.query_one("#hist_page"), "content", ""))
                ck("按 n 翻到第 2 页(显示 10. 与 14.)",
                   "10. 对话-10.json" in list_txt2 and "14. 对话-14.json" in list_txt2, repr(list_txt2[:80]))
                ck("第 2 页提示 2/2", "2/2" in page_txt2, repr(page_txt2))
            sc.query_one("#imput", Input).value = value
            await pilot.press("enter")
            for _ in range(50):
                await pilot.pause()
                await asyncio.sleep(0.03)
                if not th.is_alive():
                    break
            th.join(timeout=2)
            return res.get("v")

        v_hist = await _pick_history("3", keypresses=("n",))
        ck("历史选择:翻页后键入 3 → worker 拿到 '3'", v_hist == "3", repr(v_hist))

        # ---- 6d) 原生历史渲染:_mount_history 有界 + 折叠更早 + 抬头 ----
        _hmsgs = [{"role": "system", "content": "s"}]
        for _i in range(100):
            _hmsgs.append({"role": "user", "content": "问%d" % _i})
            _hmsgs.append({"role": "assistant", "content": "答%d" % _i})
        app._mount_history(_hmsgs, "切换到 X(共 101 条消息)")
        await pilot.pause()
        _htxt = _content_text(app)
        ck("历史原生渲染:抬头可见", "切换到 X" in _htxt, repr(_htxt[:50]))
        ck("历史原生渲染:更早的折叠提示(条数>HIST_MAX)", "更早的" in _htxt and "已折叠" in _htxt, repr(_htxt[:80]))
        ck("历史原生渲染:保留最近消息(答99)", "答99" in _htxt, repr(_htxt[-60:]))
        ck("历史原生渲染:不渲染最早的(问0 不在)", "问0" not in _htxt, repr(_htxt[:120]))
        _nkids = len(list(app.query_one("#content", VerticalScroll).children))
        ck("历史原生渲染:子件数受控(≤ HIST_MAX*2+2)", _nkids <= app.HIST_MAX * 2 + 2, "n=%d" % _nkids)


try:
    asyncio.run(run_app_checks())
except Exception as e:
    import traceback
    traceback.print_exc()
    ck("headless 跑通(无异常)", False, repr(e))


# ---- 7) render.confirm_block 走钩子(不 readline) ----
print("=== 7. confirm_block 钩子路由 ===")
from wchat_py import render as _R  # noqa: E402

_orig_hook = _R._CONFIRM_HOOK[0]
try:
    _R.set_confirm_hook(lambda t, m, q: True)
    ck("钩子返回 True → confirm_block True", _R.confirm_block("标题", "mark", ask="是否") is True)
    _R.set_confirm_hook(lambda t, m, q: False)
    ck("钩子返回 False → confirm_block False", _R.confirm_block("标题", "mark") is False)
    _got = {}
    _R.set_confirm_hook(lambda t, m, q: _got.update(t=t, m=m, q=q) or True)
    _R.confirm_block("标题X", "命中的规则", ask="是否放行")
    ck("钩子收到 title/mark/question", _got.get("t") == "标题X" and _got.get("m") == "命中的规则"
       and "是否放行" in (_got.get("q") or ""), repr(_got))
finally:
    _R.set_confirm_hook(_orig_hook)

# ---- 8) read_line 钩子路由(auto 的 AUTO 签字走这里) ----
_orig_line = _R._LINE_HOOK[0]
try:
    _R.set_line_hook(lambda q: "AUTO")
    ck("read_line 走钩子返回 'AUTO'", _R.read_line("请输入") == "AUTO")
    _R.set_line_hook(lambda q: "")
    ck("read_line 钩子返回空串", _R.read_line("请输入") == "")
finally:
    _R.set_line_hook(_orig_line)


# ---- 9) 完整元命令路径:输入 "-auto=full" → 弹 InputModal 要 AUTO(真 _default_meta 接线) ----
print("=== 9. -auto=full 走真实 on_meta → InputModal 要 AUTO ===")
async def _meta_path_check():
    for k in ("AUTO_MODE", "WCHAT_AUTO_MODE", "WCHAT_AUTO_ASSUME_YES"):
        os.environ.pop(k, None)
    from wchat_py.session import Session
    from wchat_py import repl as _repl
    _s = Session.from_env()
    app = tui.make_app(lambda t, e, s, r: None,
                       on_meta=lambda a, g: _repl._default_meta(a, g, session=_s))
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one("#input", TextArea)
        inp.text = "-auto=full"
        await pilot.press("enter")
        shown = False
        for _ in range(60):
            await pilot.pause()
            await asyncio.sleep(0.04)
            if app.screen.__class__.__name__ == "InputModal":
                shown = True
                break
        ck("-auto=full → 弹 InputModal 要求 AUTO 签字", shown, app.screen.__class__.__name__)
        if shown:
            app.screen.query_one("#imput", Input).value = "AUTO"
            await pilot.press("enter")
        for _ in range(60):
            await pilot.pause()
            await asyncio.sleep(0.04)
            if app.screen.__class__.__name__ != "InputModal":
                break
        ck("键入 AUTO → auto 切实开启", os.environ.get("AUTO_MODE") == "full", repr(os.environ.get("AUTO_MODE")))
        # 等 _refresh_status(0.04s tick)把常驻标记刷到 #status
        for _ in range(10):
            await pilot.pause()
            await asyncio.sleep(0.03)
        _st = _status_text(app)
        _ct = _content_text(app)
        # -auto 的开启输出(auto 已开启…)应在**内容区**;常驻档标记(已自动放行 N 次)应在**状态区**
        ck("auto 开启输出在内容区(auto 已开启)", "已开启" in _ct, repr(_ct[-160:]))
        ck("auto 档常驻标记在状态区(#status)", "已自动放行" in _st, repr(_st[-160:]))
        ck("auto 档标记不重复进内容区", "本次会话已自动放行" not in _ct, repr(_ct[-160:]))

        # 思考折叠成**一行**(单个 Text、无换行);视觉超宽由 CSS nowrap+ellipsis 截断
        _rt = app._render_reason("word " * 300)
        ck("思考折叠为单行(无换行符)", "\n" not in _rt.plain, repr(_rt.plain[:60]))
        ck("思考折叠带 ▸ 前缀", _rt.plain.startswith("▸ "), repr(_rt.plain[:20]))

        # 没有"纯空白"子件(横幅/结果之间的多余空行来自带尾换行或纯 \n 的 write)
        _blanks = []
        for _i, _c in enumerate(app.query_one("#content", VerticalScroll).children):
            _r = getattr(_c, "content", "")
            _s = _r.plain if hasattr(_r, "plain") else (getattr(_r, "markup", None) or str(_r))
            if not str(_s).strip():
                _blanks.append(_i)
        ck("内容区无纯空白子件(无多余空行)", not _blanks, repr(_blanks))

        # Ctrl+V 粘贴图片:桩 _clip_image_file 返回路径 → 插入 📋[图片] 占位行
        try:
            import wchat_py.inputbox as _ibo
            _oc = _ibo._clip_image_file
            _ibo._clip_image_file = lambda d: "/tmp/probe_img.png"
            try:
                inp.text = ""
                inp.action_paste_image()
                ck("Ctrl+V 粘贴图片插入占位行", "📋 [图片] /tmp/probe_img.png" in inp.text, repr(inp.text))
            finally:
                _ibo._clip_image_file = _oc
                inp.text = ""
        except Exception as _e:
            ck("Ctrl+V 粘贴图片插入占位行", False, repr(_e))
    for k in ("AUTO_MODE", "WCHAT_AUTO_MODE"):
        os.environ.pop(k, None)

try:
    asyncio.run(_meta_path_check())
except Exception as e:
    import traceback
    traceback.print_exc()
    ck("元命令路径无异常", False, repr(e))


# ---- 10) list_history 接线:TUI 下有 line hook 时,把候选列表交给 read_line(choices),
#          且**不再**把整份列表打到 stdout(那正是"看不到历史"的来源:被弹窗遮住的背景)
print("=== 10. list_history:TUI 下把候选交给 read_line(choices),不走 stdout 刷屏 ===")


async def _list_history_check():
    import tempfile
    from wchat_py import repl as _repl
    from wchat_py import render as _render
    td = tempfile.mkdtemp(prefix="tui_hist_")
    try:
        for i in range(13):
            with open(os.path.join(td, "convo-%02d.json" % i), "w", encoding="utf-8") as f:
                f.write("[]")
        os.environ["WCHAT_HISTORY_DIR"] = td
        captured = {}

        def _spy(question="", choices=None):
            captured["q"] = question
            captured["choices"] = choices
            return "9999"   # 越界 → _default_meta 打印"请选择正确的编号"后提前返回(不触发真实加载)

        _orig = _render.read_line
        # 桩一个 line hook 让它判定为 TUI(choices 才会传),同时 read_line 也由桩接管
        _orig_hook = _render._LINE_HOOK[0]
        buf = io.StringIO()
        _orig_out = sys.stdout
        try:
            _render.read_line = _spy
            _render.set_line_hook(lambda q, c=None: "9999")
            sys.stdout = buf
            _repl._default_meta("list_history", "")
        finally:
            sys.stdout = _orig_out
            _render.read_line = _orig
            _render._LINE_HOOK[0] = _orig_hook
            os.environ.pop("WCHAT_HISTORY_DIR", None)
        ch = captured.get("choices")
        ck("read_line 收到 choices(候选条目数=13)", isinstance(ch, list) and len(ch) == 13, repr(type(ch)))
        ck("choices 是历史文件名(含 convo-00.json)", isinstance(ch, list) and any("convo-00.json" in str(x) for x in ch))
        ck("问题文案是'请输入要加载的历史编号'", "历史编号" in (captured.get("q") or ""), repr(captured.get("q")))
        _out = buf.getvalue()
        ck("改走弹窗后,不再把『请从以下编号中选择』刷到 stdout(背景不再重复刷屏)",
           "请从以下编号中选择" not in _out, repr(_out[:120]))
        # _render_history 走 history hook(不写 stdout):给了 hook 时交回 hook 并带上 header
        _seen = []
        _prev_hook = _render.history_hook()
        try:
            _render.set_history_hook(lambda msgs, header="": _seen.append((len(msgs), header)))
            _repl._render_history([{"role": "system", "content": "s"},
                                   {"role": "user", "content": "u"}], header="切换到 Z")
            ck("_render_history 走 history hook(带 header,不写 stdout)",
               _seen == [(2, "切换到 Z")], repr(_seen))
        finally:
            _render.set_history_hook(_prev_hook)
    finally:
        import shutil
        shutil.rmtree(td, ignore_errors=True)


try:
    asyncio.run(_list_history_check())
except Exception as e:
    import traceback
    traceback.print_exc()
    ck("list_history 接线无异常", False, repr(e))


# ---- 11) M4 边流式输出边输入:一轮还在跑时提交不丢弃,进队列,当前轮结束后依次自动发送
print("=== 11. M4:turn 运行期间提交 → 入队 → 依次自动发送 ===")


async def _m4_queue_check():
    import time
    order = []

    def handle_line(text, emit, stream, reason):
        order.append(("start", text))
        time.sleep(0.6)                       # 模拟慢流式(worker 线程里阻塞)
        for ch in ("答:" + text):
            stream(ch)
        order.append(("done", text))

    app = tui.make_app(handle_line, banner="b")
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        inp = app.query_one("#input", TextArea)
        inp.text = "first"
        await pilot.press("enter")
        # 等第一轮真正开始(busy)再提交第二条
        for _ in range(30):
            await pilot.pause()
            await asyncio.sleep(0.02)
            if ("start", "first") in order:
                break
        ck("第一轮已开始(worker 运行中)", ("start", "first") in order, repr(order))
        inp.text = "second"
        await pilot.press("enter")
        await pilot.pause()
        ck("busy 时提交被提示已入队(不丢输入)", "已加入队列" in _content_text(app), repr(_content_text(app)[-80:]))
        # 等两条都跑完
        for _ in range(200):
            await pilot.pause()
            await asyncio.sleep(0.05)
            if ("done", "first") in order and ("done", "second") in order:
                break
        ck("两条都被处理(first 与 second 都 done)",
           ("done", "first") in order and ("done", "second") in order, repr(order))
        try:
            ck("顺序正确(first 先、second 后)",
               order.index(("start", "first")) < order.index(("start", "second")), repr(order))
        except ValueError:
            ck("顺序正确(first 先、second 后)", False, repr(order))
        _txt = _content_text(app)
        ck("两条答案都上屏(答:first 与 答:second)", "答:first" in _txt and "答:second" in _txt,
           repr(_txt[-140:]))


try:
    asyncio.run(_m4_queue_check())
except Exception as e:
    import traceback
    traceback.print_exc()
    ck("M4 队列无异常", False, repr(e))


# ---- 12) M4 注入接线:5 参 handle_line 拿到 poller,运行中提交的消息被**本轮内**取走消费 ----
print("=== 12. M4 注入接线:轮内 poller 取走补充消息(本轮消费,不当独立下一轮) ===")


async def _m4_poll_wired_check():
    got = {}

    def handle_line5(text, emit, stream, reason, poll_pending):
        got.setdefault("started", []).append(text)
        import time
        time.sleep(0.6)                       # 让第一条 busy,给用户时间提交第二条
        pend = poll_pending()                  # 模拟"下一个小轮边界"取补充
        got.setdefault("polled", []).append(list(pend))
        for ch in ("答:" + text):
            stream(ch)
        for p in pend:                        # 把取到的补充也消费掉(证明本轮内被用上)
            for ch in ("补答:" + p):
                stream(ch)

    app = tui.make_app(handle_line5, banner="b")
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        inp = app.query_one("#input", TextArea)
        inp.text = "first"
        await pilot.press("enter")
        for _ in range(40):
            await pilot.pause()
            await asyncio.sleep(0.02)
            if "first" in got.get("started", []):
                break
        inp.text = "second"
        await pilot.press("enter")
        await pilot.pause()
        for _ in range(120):
            await pilot.pause()
            await asyncio.sleep(0.05)
            if got.get("polled") and got["polled"][0]:
                break
        ck("5 参 handle_line 收到并使用 poller:第一轮 poll 到排队消息 'second'",
           bool(got.get("polled")) and "second" in (got["polled"][0] or []), repr(got.get("polled")))
        await asyncio.sleep(0.4)
        await pilot.pause()
        ck("排队消息被本轮消费后,不作为独立下一轮重复处理('second' 不进 started)",
           got.get("started", []).count("second") == 0, repr(got.get("started")))
        _txt = _content_text(app)
        ck("补充消息在本轮内被消费(补答:second 上屏)", "补答:second" in _txt, repr(_txt[-120:]))


try:
    asyncio.run(_m4_poll_wired_check())
except Exception as e:
    import traceback
    traceback.print_exc()
    ck("M4 注入接线无异常", False, repr(e))


# ---- 13) Ctrl+C/退出丢弃队列:action_maybe_quit 清空 _pending ----
print("=== 13. 退出(Ctrl+C)丢弃排队消息 ===")


async def _m4_quit_drops_check():
    import queue as _q
    app = tui.make_app(lambda t, e, s, r: None, banner="b")
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        app._pending.put("x")
        app._pending.put("y")
        try:
            app.action_maybe_quit()
        except Exception:
            pass
        await pilot.pause()
        rem = []
        try:
            while True:
                rem.append(app._pending.get_nowait())
        except _q.Empty:
            pass
        ck("action_maybe_quit 清空排队消息(丢弃)", rem == [], repr(rem))


try:
    asyncio.run(_m4_quit_drops_check())
except Exception as e:
    import traceback
    traceback.print_exc()
    ck("退出丢弃队列无异常", False, repr(e))


# ---- 14) 回归:worker 线程调 pty_run 不得抛 signal ValueError(TUI 把 turn 放 worker 线程跑)----
print("=== 14. worker 线程 pty_run(signal 只能在主线程装 → 不得崩) ===")
import threading as _th

_pres = {}


def _run_pty_in_thread():
    try:
        from wchat_py.termpty import pty_run
        d, f, rc, to, intr = pty_run(["bash", "-c", "echo SIGOK; pwd"], timeout=10)
        _pres["ok"] = True
        _pres["d"] = d
    except Exception as _e:
        _pres["ok"] = False
        _pres["err"] = repr(_e)


_t = _th.Thread(target=_run_pty_in_thread)
_t.start()
_t.join(timeout=20)
ck("worker 线程 pty_run 不抛异常(修复 signal only works in main thread)",
   _pres.get("ok") is True, _pres.get("err", "timeout/无结果"))
ck("worker 线程 pty_run 正常执行(SIGOK 在输出里)",
   any("SIGOK" in ln for ln in (_pres.get("d") or [])), repr(_pres.get("d")))
# 主线程仍正常(非 TUI 零回归)
try:
    from wchat_py.termpty import pty_run as _pr2
    _d2 = _pr2(["bash", "-c", "echo MAINOK"], timeout=10)[0]
    ck("主线程 pty_run 仍正常(MAINOK)", any("MAINOK" in ln for ln in _d2), repr(_d2))
except Exception as _e:
    ck("主线程 pty_run 仍正常(MAINOK)", False, repr(_e))

print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
sys.exit(0 if F == 0 else 1)