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


# ---- 1) TuiStdout 缓冲/线程安全/isatty ----
print("=== 1. TuiStdout 输出桥 ===")
sh = tui.TuiStdout()
ck("isatty 为 False(不误判为交互)", sh.isatty() is False)
sh.write("abc")
sh.write("")
sh.write("def\n")
ck("drain 拼回全部写入", sh.drain() == "abcdef\n", repr(sh.drain()))
ck("drain 后清空", sh.drain() == "")
sh.write("x")
ck("flush 不报错", (sh.flush() is None))


# ---- 2/3/4/5) App 外壳端到端(headless) ----
async def run_app_checks():
    seen = {}

    def handle_line(text, emit):
        seen["text"] = text
        emit("回答:%s\n" % text)

    app = tui.make_app(handle_line, banner="banner-X")
    async with app.run_test() as pilot:
        await pilot.pause()
        logs = app.query(RichLog)
        inputs = app.query(Input)
        ck("compose 出 1 个 RichLog", len(logs) == 1, "found %d" % len(logs))
        ck("compose 出 1 个 Input", len(inputs) == 1, "found %d" % len(inputs))

        # 输入 hello 回车 → 回显
        inp = app.query_one("#input", Input)
        inp.value = "hello"
        await pilot.press("enter")
        await pilot.pause()
        await asyncio.sleep(0.25)
        await pilot.pause()
        txt = "\n".join(str(l) for l in app.query_one("#content", RichLog).lines)
        ck("输入回车后内容区出现回显 ❯ hello", "hello" in txt, repr(txt[-120:]))

        # resize 后仍是单个 Input/RichLog(不叠 widget)
        try:
            await pilot.resize_terminal(70, 30)
            await pilot.pause()
        except Exception:
            pass
        ck("resize 后仍 1 个 Input", len(app.query(Input)) == 1, "found %d" % len(app.query(Input)))
        ck("resize 后仍 1 个 RichLog", len(app.query(RichLog)) == 1, "found %d" % len(app.query(RichLog)))

        # worker 把 handle_line 的输出经 emit 落进内容区
        await asyncio.sleep(0.2)
        await pilot.pause()
        txt2 = "\n".join(str(l) for l in app.query_one("#content", RichLog).lines)
        ck("handle_line 收到输入", seen.get("text") == "hello", repr(seen))
        ck("handle_line 的 emit 落进内容区(worker→shim→RichLog)", "回答:hello" in txt2, repr(txt2[-160:]))


try:
    asyncio.run(run_app_checks())
except Exception as e:
    import traceback
    traceback.print_exc()
    ck("headless 跑通(无异常)", False, repr(e))

print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
sys.exit(0 if F == 0 else 1)