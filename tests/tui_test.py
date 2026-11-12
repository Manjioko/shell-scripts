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
    for k in ("AUTO_MODE", "WCHAT_AUTO_MODE"):
        os.environ.pop(k, None)

try:
    asyncio.run(_meta_path_check())
except Exception as e:
    import traceback
    traceback.print_exc()
    ck("元命令路径无异常", False, repr(e))

print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
sys.exit(0 if F == 0 else 1)