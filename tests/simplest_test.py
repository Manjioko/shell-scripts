#!/usr/bin/env python3
"""-simplest 极简模式闸门。

覆盖面:
  A. 单元:simplest.enabled() 受 WCHAT_SIMPLEST 控制;quiet_stdout 开时吞 stdout、关时透传;
     run_tool 开时吞工具 stdout 且**返回值不变**、关时原样。
  B. TUI headless(Textual run_test):
     · 开:正文照常流式上屏;思考、工具横幅(经 stdout)全隐藏;有一条状态行且结束由
       「正在处理中,请稍后...」改成「处理完成」。
     · 关(不加 -simplest):思考/工具/正文都照旧显示,且**没有**状态行 —— 证明默认档不受影响。

textual 未安装 → SKIP(rc=77,不计红)。用法:python3 tests/simplest_test.py
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


# ---------- A. 单元(与 textual 无关,先跑)----------
from wchat_py import simplest  # noqa: E402


_TFKEYS = ("WCHAT_TF", "WCHAT_SIMPLEST", "WCHAT_THINK_FULL")


def _tfw(mode, fn):
    """临时设 WCHAT_TF=mode(None=清空→默认 0/极简);同时清 WCHAT_SIMPLEST/THINK_FULL,结束后还原。"""
    old = {k: os.environ.get(k) for k in _TFKEYS}
    for k in _TFKEYS:
        os.environ.pop(k, None)
    if mode is not None:
        os.environ["WCHAT_TF"] = mode
    try:
        return fn()
    finally:
        for k in _TFKEYS:
            if old[k] is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old[k]


# 三档派生态:0/默认=极简,1=off,2=on;旧 WCHAT_SIMPLEST=1 仍兼容
ck("默认(无 env)= 极简 enabled=True", _tfw(None, simplest.enabled) is True)
ck("WCHAT_TF=0(极简)→ enabled=True", _tfw("0", simplest.enabled) is True)
ck("WCHAT_TF=1(off)→ enabled=False", _tfw("1", simplest.enabled) is False)
ck("WCHAT_TF=2(on)→ enabled=False", _tfw("2", simplest.enabled) is False)
ck("旧 WCHAT_SIMPLEST=1 → enabled=True(兼容)",
   _tfw(None, lambda: (os.environ.__setitem__("WCHAT_SIMPLEST", "1"), simplest.enabled())[1]) is True)

# 状态行"跑马灯"帧:结尾点数 1→2→3→1 循环
_wt = [simplest.working_text(i) for i in range(4)]
ck("working_text 帧循环 . → .. → ... → .(回到 1 点)",
   _wt == ["正在处理中,请稍后.", "正在处理中,请稍后..",
           "正在处理中,请稍后...", "正在处理中,请稍后."], repr(_wt))
ck("working_text frame=4 继续循环(回到两点)", simplest.working_text(4) == "正在处理中,请稍后..",
   repr(simplest.working_text(4)))


def _quiet_writes():
    """开 simplest:quiet_stdout 期间写 stdout 被吞,出来后恢复。返回 (期间的次数, 是否恢复)。"""
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        with simplest.quiet_stdout() as q:
            sys.stdout.write("SHOULD-BE-SWALLOWED")
        # 退出上下文后 sys.stdout 应恢复成 buf(我们临时设的那个)
        restored = (sys.stdout is buf)
    finally:
        sys.stdout = old
    return buf.getvalue(), restored


_got, _restored = _tfw("0", _quiet_writes)
ck("简化档:quiet_stdout 吞掉 stdout 写", _got == "", repr(_got))
ck("simplest 开:quiet_stdout 退出后恢复 sys.stdout", _restored is True)


def _quiet_passthru():
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        with simplest.quiet_stdout():
            sys.stdout.write("VISIBLE")
    finally:
        sys.stdout = old
    return buf.getvalue()


ck("非极简(-tf=1):quiet_stdout 是 no-op(写透传)", _tfw("1", _quiet_passthru) == "VISIBLE")


def _run_tool_quiet():
    out = io.StringIO()
    old = sys.stdout
    sys.stdout = out
    try:
        r = simplest.run_tool(lambda n, a: (sys.stdout.write("TOOL-OUT"), "RET")[1], "x", "{}")
    finally:
        sys.stdout = old
    return r, out.getvalue()


_r_ret, _r_out = _tfw("0", _run_tool_quiet)
ck("简化档:run_tool 吞工具 stdout", _r_out == "", repr(_r_out))
ck("简化档:run_tool 仍返回工具结果", _r_ret == "RET", repr(_r_ret))

_r_ret2, _r_out2 = _tfw("1", _run_tool_quiet)
ck("非极简(-tf=1):run_tool 原样(输出透传+结果不变)", _r_out2 == "TOOL-OUT" and _r_ret2 == "RET",
   repr((_r_out2, _r_ret2)))


# ---------- B. TUI headless(需 textual)----------
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


if not _import_textual_ok():
    if not os.environ.get("WCHAT_TUI_REEXEC"):
        for _exe in (os.environ.get("WCHAT_TUI_PY"),
                     os.path.expanduser("~/.ct/venv/bin/python"),
                     os.path.join(REPO, ".scratch", "tui_venv", "bin", "python")):
            if _exe and os.path.exists(_exe) and _import_textual_ok(_exe):
                os.environ["WCHAT_TUI_REEXEC"] = "1"
                os.execv(_exe, [_exe, os.path.abspath(__file__)] + sys.argv[1:])
    print("SKIP: textual 未安装且找不到带 textual 的解释器(TUI 为可选功能;可设 WCHAT_TUI_PY)")
    print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
    sys.exit(77 if F == 0 else 1)

from wchat_py import tui  # noqa: E402
from textual.widgets import TextArea  # noqa: E402
from textual.containers import VerticalScroll  # noqa: E402


def _content_text(app):
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


async def _run_once(simplest_on):
    os.environ.pop("WCHAT_SIMPLEST", None)
    os.environ.pop("WCHAT_THINK_FULL", None)
    os.environ["WCHAT_TF"] = "0" if simplest_on else "1"   # 0=极简 1=off

    def handle_line(text, emit, stream, reason):
        reason("(思考中…)")                       # 思考:simplest 下应隐藏
        for ch in ("回答:" + text):
            stream(ch)                            # 正文:走 stream(非 stdout),始终应显示
        sys.stdout.write("[工具] 已完成\n")         # 工具横幅:走 stdout(同真实 _run_tool_tui),simplest 下应隐藏

    app = tui.make_app(handle_line)
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one("#input", TextArea)
        inp.text = "hello"
        await pilot.press("enter")
        await pilot.pause()
        await asyncio.sleep(0.5)
        await pilot.pause()
        return _content_text(app)


# 状态行颜色渐变(需 app 实例构造方法;make_app 不需 run_test 即可调其方法)
try:
    from wchat_py.simplest import working_text as _wt_fn
    _app0 = tui.make_app(lambda *a: None)
    _c0 = [sp.style for sp in _app0._simplest_status_text(0).spans]
    _c1 = [sp.style for sp in _app0._simplest_status_text(1).spans]
    _base_len = len(_wt_fn(0).rstrip("."))          # 去掉结尾点后的"正文"长度,做等长对比
    ck("状态行是渐变(单帧 ≥3 种颜色,不是纯色)", len(set(_c0)) >= 3, repr(len(set(_c0))))
    ck("状态行颜色随相位流动(正文每字颜色序列 phase0≠phase1)",
       _c0[:_base_len] != _c1[:_base_len], repr((_c0[:3], _c1[:3])))
    ck("渐变不改 plain 文本(仍以 请稍后+点 结尾)",
       _app0._simplest_status_text(2).plain == "正在处理中,请稍后...",
       repr(_app0._simplest_status_text(2).plain))
except Exception as _e:
    import traceback
    traceback.print_exc()
    ck("状态行渐变检查无异常", False, repr(_e))


async def _anim_check():
    """确定性验证状态行"跑马灯":直接驱动 begin→tick×3→done,读 widget 文本。

    不依赖真实定时器/墙钟(handle_line 瞬回,定时器来不及跑),直接调 _simplest_tick(),
    证明"每帧把结尾点推进一位并循环"这条逻辑 + widget 更新是通的。
    """
    os.environ["WCHAT_TF"] = "0"
    app = tui.make_app(lambda *a: None)
    seq = []
    async with app.run_test() as pilot:
        await pilot.pause()
        app._simplest_begin()
        await pilot.pause()
        seq.append(_content_text(app))
        for _ in range(3):
            app._simplest_tick()
            await pilot.pause()
            seq.append(_content_text(app))
        app._simplest_done()
        await pilot.pause()
        seq.append(_content_text(app))
    os.environ.pop("WCHAT_TF", None)
    return seq


try:
    _seq = asyncio.run(_anim_check())
    _dots = []
    for _s in _seq[:-1]:
        _line = [ln for ln in _s.split("\n") if "正在处理中" in ln]
        _tail = _line[-1].split("请稍后", 1)[-1] if _line else ""
        _dots.append(_tail.count("."))
    ck("跑马灯:起始 1 点", _dots[:1] == [1], repr(_seq[0][-60:]))
    ck("跑马灯:1→2→3→1 循环(直接驱动 tick)", _dots == [1, 2, 3, 1], repr(_dots))
    ck("跑马灯:结束后变「处理完成」(动画停)",
       "处理完成" in _seq[-1] and "正在处理中" not in _seq[-1], repr(_seq[-1][-60:]))
except Exception as _e:
    import traceback
    traceback.print_exc()
    ck("跑马灯 begin/tick/done 无异常", False, repr(_e))

try:
    _ts = asyncio.run(_run_once(True))
    ck("TUI simplest开:正文流式上屏(回答:hello)", "回答:hello" in _ts, repr(_ts[-160:]))
    ck("TUI simplest开:思考隐藏(无 思考中)", "思考中" not in _ts, repr(_ts[-160:]))
    ck("TUI simplest开:工具横幅隐藏(无 已完成)", "已完成" not in _ts, repr(_ts[-160:]))
    ck("TUI simplest开:状态行结束变成「处理完成」", "处理完成" in _ts, repr(_ts[-160:]))
    ck("TUI simplest开:「正在处理中」已被改写成处理完成(不残留)",
       "正在处理中" not in _ts, repr(_ts[-160:]))

    _tn = asyncio.run(_run_once(False))
    ck("TUI 默认(不加-simplest):正文显示", "回答:hello" in _tn, repr(_tn[-160:]))
    ck("TUI 默认:思考显示(有 思考中)", "思考中" in _tn, repr(_tn[-160:]))
    ck("TUI 默认:工具横幅显示(有 已完成)", "已完成" in _tn, repr(_tn[-160:]))
    ck("TUI 默认:无状态行(无 处理完成)", "处理完成" not in _tn, repr(_tn[-160:]))
except Exception as _e:
    import traceback
    traceback.print_exc()
    ck("TUI simplest/默认 行为检查无异常", False, repr(_e))


# ---- C. 一回合内"正文↔工具"交替 → 状态行逐段循环(正在处理中 → 处理完成,逐段重来)----
async def _run_segments():
    os.environ["WCHAT_TF"] = "0"

    def handle_line(text, emit, stream, reason):
        reason("(思考中…)")
        stream("PROSE-ONE ")                 # 段1正文
        sys.stdout.write("[工具1] run\n")     # 工具轮边界 1
        stream("PROSE-TWO ")                 # 段2正文
        sys.stdout.write("[工具2] run\n")     # 工具轮边界 2
        stream("PROSE-THREE")                # 段3正文(最终)

    app = tui.make_app(handle_line)
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one("#input", TextArea); inp.text = "hi"
        await pilot.press("enter")
        await pilot.pause(); await asyncio.sleep(0.6); await pilot.pause()
        return _content_text(app)


try:
    _seg = asyncio.run(_run_segments())
    ck("逐段循环:三段正文都在", all(k in _seg for k in ("PROSE-ONE", "PROSE-TWO", "PROSE-THREE")),
       repr(_seg[-200:]))
    ck("逐段循环:出现 3 次「处理完成」(每段一次)", _seg.count("处理完成") == 3,
       "count=%d" % _seg.count("处理完成"))
    ck("逐段循环:无「正在处理中」残留(都被翻成完成)", "正在处理中" not in _seg, repr(_seg[-200:]))
    ck("逐段循环:工具横幅隐藏(无 工具1/工具2/run)", "工具1" not in _seg and "工具2" not in _seg,
       repr(_seg[-200:]))
    # 顺序:每个"处理完成"都在其对应 PROSE 之前(状态头在正文上方)
    ck("逐段循环:每段 处理完成 在其正文之前",
       _seg.find("处理完成") < _seg.find("PROSE-ONE")
       and _seg.find("PROSE-ONE") < _seg.find("PROSE-TWO") < _seg.find("PROSE-THREE"),
       repr(_seg[:200]))
except Exception as _e:
    import traceback
    traceback.print_exc()
    ck("逐段循环检查无异常", False, repr(_e))


# ---- D. 前台耗时 shell 倒计时(跑超过 ~3s 才亮,结束回退"正在处理中")----
import time as _time  # noqa: E402


def _last_line(app):
    ls = [x for x in _content_text(app).split("\n") if x.strip()]
    return ls[-1] if ls else ""


# 文案单元
ck("countdown_text 含'倒计时 N 秒'+跑马灯点",
   simplest.countdown_text(600, 0) == "正在处理耗时任务,倒计时 600 秒,请稍后."
   and simplest.countdown_text(0, 2) == "正在处理耗时任务,倒计时 0 秒,请稍后...",
   repr(simplest.countdown_text(600, 0)))
ck("countdown_text 负数归零", simplest.countdown_text(-5, 0).startswith("正在处理耗时任务,倒计时 0 秒"))


async def _countdown_check():
    os.environ["WCHAT_TF"] = "0"
    app = tui.make_app(lambda *a: None)
    seq = {}
    async with app.run_test() as pilot:
        await pilot.pause()
        app._simplest_begin()
        await pilot.pause()
        seq["init"] = _last_line(app)
        # start(600) + 立即 tick(未到 3s)→ 仍是"正在处理中"(快速命令不亮)
        app._simplest_shell_start(600)
        app._simplest_tick()
        await pilot.pause()
        seq["quick"] = _last_line(app)
        # 模拟已过 5s → 显示"倒计时 595 秒"
        app._cd_start = _time.monotonic() - 5
        app._simplest_tick()
        await pilot.pause()
        seq["countdown"] = _last_line(app)
        # 结束 → 回退"正在处理中"
        app._simplest_shell_end()
        app._simplest_tick()
        await pilot.pause()
        seq["end"] = _last_line(app)
    os.environ.pop("WCHAT_TF", None)
    return seq


try:
    _sq = asyncio.run(_countdown_check())
    ck("倒计时:初始是'正在处理中'", "正在处理中" in _sq["init"], repr(_sq["init"]))
    ck("倒计时:快速命令(<3s)不亮倒计时", "正在处理中" in _sq["quick"] and "倒计时" not in _sq["quick"],
       repr(_sq["quick"]))
    ck("倒计时:超过 3s 后显示'倒计时 595 秒'(600-5)", "倒计时 595 秒" in _sq["countdown"], repr(_sq["countdown"]))
    ck("倒计时:shell 结束回退'正在处理中'", "正在处理中" in _sq["end"] and "倒计时" not in _sq["end"],
       repr(_sq["end"]))
except Exception as _e:
    import traceback
    traceback.print_exc()
    ck("倒计时检查无异常", False, repr(_e))

print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
sys.exit(0 if F == 0 else 1)