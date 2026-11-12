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

from textual.widgets import RichLog, Input  # noqa: E402


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

    def handle_line(text, emit, stream):
        seen["text"] = text
        # 模拟流式:M1 用 stream(逐字进 live 区),工具/横幅类走 emit(log 区)
        for ch in ("回答:" + text):
            stream(ch)
        emit("\n[工具] 已完成\n")

    app = tui.make_app(handle_line, banner="banner-X")
    async with app.run_test() as pilot:
        await pilot.pause()
        logs = app.query(RichLog)
        inputs = app.query(Input)
        ck("compose 出 1 个 RichLog", len(logs) == 1, "found %d" % len(logs))
        ck("compose 出 1 个 Input", len(inputs) == 1, "found %d" % len(inputs))

        # 输入 hello 回车 → 回显 + 流式答案 + 工具横幅
        inp = app.query_one("#input", Input)
        inp.value = "hello"
        await pilot.press("enter")
        await pilot.pause()
        await asyncio.sleep(0.35)      # 等 worker 跑完 + 定时 drain
        await pilot.pause()
        txt = "\n".join(str(l) for l in app.query_one("#content", RichLog).lines)
        ck("输入回车后内容区出现回显 ❯ hello", "hello" in txt, repr(txt[-120:]))
        ck("handle_line 收到输入", seen.get("text") == "hello", repr(seen))
        ck("流式答案最终定稿进内容区(回答:hello)", "回答:hello" in txt, repr(txt[-160:]))
        ck("工具横幅经 log 通道出现", "已完成" in txt, repr(txt[-160:]))

        # resize 后仍是单个 Input/RichLog(不叠 widget)
        try:
            await pilot.resize_terminal(70, 30)
            await pilot.pause()
        except Exception:
            pass
        ck("resize 后仍 1 个 Input", len(app.query(Input)) == 1, "found %d" % len(app.query(Input)))
        ck("resize 后仍 1 个 RichLog", len(app.query(RichLog)) == 1, "found %d" % len(app.query(RichLog)))


try:
    asyncio.run(run_app_checks())
except Exception as e:
    import traceback
    traceback.print_exc()
    ck("headless 跑通(无异常)", False, repr(e))

print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
sys.exit(0 if F == 0 else 1)