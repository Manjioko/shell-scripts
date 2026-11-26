#!/usr/bin/env python3
"""Textual TUI **视觉/布局**闸门(headless run_test 真布局引擎)。

为什么要有这道闸门:输入框"别被顶到屏幕最后一行 / resize 别出残影 / 别叠出多个框"
这些缺陷在 Textual 全屏下的**形态变了**,字节流层看不见。于是这里用
Textual 自己的 ``App.run_test()``(Pilot)在 headless 下跑**真布局引擎**,直接读每个 widget
布局后的 ``region``(= 屏幕坐标,就是"画到哪一格")做断言——不是手搓 VT 模拟屏。

⚠ 为什么不靠"假终端重放转义流"判 resize:Textual 是 alternate-screen、resize 后做**增量**
重绘(它默认终端里旧内容还在,只补变化的区)。把它接到一张会 reflow 的手搓 VT 屏上,缺的旧帧
会被误判成残影/漏画——这正是本项目反复强调的"模拟屏护不住"。真正的 resize/切 tab/滚动观感
必须**真 GUI 终端**验(见 §6.4 终端矩阵:M3②)。本闸门只钉**确定性的布局不变量**。

判据:
  1. 基线(80×24):恰好 1 个 #content / 1 个 #contentbox / 1 个输入框(TextArea);无重复 widget。
  2. 输入框**贴底**:input.region.bottom == 屏幕高;整宽;在内容区之下(不重叠)。
  3. 内容区从**顶部**起:content.region.y == 0;首个子件 region.y 很小(=消息从提问下一行起,不从底部冒)。
  4. 内容区消息**顺序堆叠**:各 Static 子件 region.y 严格递增、互不重叠、都在内容区内(不超过输入框)。
  5. resize 一组尺寸后:仍恰好 1 输入框 / 1 内容区;输入框每次仍贴底且整宽;内容区仍在上方。
  6. 状态区(auto 开):#status 高度>0,夹在内容区底 与 输入框顶之间;auto 关:高度 0(折叠)。

textual 缺失时:先尝试用带 textual 的解释器(``WCHAT_TUI_PY`` / ``~/.ct/venv`` / ``.scratch/tui_venv``)
**重新执行**本文件;找不到则按 run_gates 约定打印 ``SKIP:`` 并 exit 77(不计失败)。

用法:`python3 tests/tui_visual_test.py`(需能 import textual,否则自动换解释器或 SKIP)。
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ["WCHAT_NO_SETUP"] = "1"
# 默认档现在是极简(-tf=0,隐藏思考/工具)。本闸门验的是"思考/提示等能上屏 + 布局不变量",
# 故显式置 off(-tf=1)来跑;极简默认的行为由 simplest_test/think_view_test 覆盖。
os.environ["WCHAT_TF"] = "1"


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
    # 本进程无 textual:找一个带 textual 的解释器重新执行(避免把"本机没装对解释器"当成产品红)。
    if not os.environ.get("WCHAT_TUI_VISUAL_REEXEC"):
        cands = [os.environ.get("WCHAT_TUI_PY"),
                 os.path.expanduser("~/.ct/venv/bin/python"),
                 os.path.join(REPO, ".scratch", "tui_venv", "bin", "python")]
        for exe in cands:
            if exe and os.path.exists(exe) and _import_textual_ok(exe):
                os.environ["WCHAT_TUI_VISUAL_REEXEC"] = "1"
                os.execv(exe, [exe, os.path.abspath(__file__)] + sys.argv[1:])
    print("SKIP: textual 未安装且找不到带 textual 的解释器(TUI 为可选功能;可设 WCHAT_TUI_PY)")
    sys.exit(77)   # run_gates 约定:环境性缺席 exit 77,不计失败

P = F = 0


def ck(name, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  ok   %s" % name)
    else:
        F += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


import asyncio  # noqa: E402

from wchat_py import tui  # noqa: E402
from textual.widgets import TextArea  # noqa: E402
from textual.containers import VerticalScroll, Vertical  # noqa: E402


def _content_children(app):
    """#content 里的消息 Static,按挂载顺序(= 显示顺序)。"""
    return list(app.query_one("#content", VerticalScroll).children)


def _content_text(app):
    from rich.text import Text
    from rich.markdown import Markdown
    out = []
    for c in _content_children(app):
        r = getattr(c, "content", "")
        if isinstance(r, Text):
            out.append(r.plain)
        elif isinstance(r, Markdown):
            out.append(r.markup)
        else:
            out.append(str(r))
    return "\n".join(out)


def _fix_env():
    """清掉可能从外部 shell 漏进来的 auto 状态(否则 #status 会莫名占高,断言假红)。"""
    for k in ("AUTO_MODE", "WCHAT_AUTO_MODE", "WCHAT_AUTO_ASSUME_YES"):
        os.environ.pop(k, None)


async def run_checks():
    _fix_env()

    def handle_line(text, emit, stream, reason):
        reason("(思考中:先想一想)")
        for ch in ("回答:" + text):
            stream(ch)
        emit("\n[工具] read_file 已完成\n")

    app = tui.make_app(handle_line, banner="banner-head")
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()

        # ---- 1) 基线:无重复 widget ----
        print("=== 1. 基线 80×24:输入框贴底、内容区在上、无重复 widget ===")
        inp = app.query_one("#input", TextArea)
        cont = app.query_one("#content", VerticalScroll)
        cbox = app.query_one("#contentbox", Vertical)
        ck("恰好 1 个输入框(TextArea)", len(app.query(TextArea)) == 1, "found %d" % len(app.query(TextArea)))
        ck("恰好 1 个 #content", len(app.query("#content")) == 1)
        ck("恰好 1 个 #contentbox", len(app.query("#contentbox")) == 1)
        ck("有 #status 状态区(1 个)", len(app.query("#status")) == 1)

        W, H = app.screen.size.width, app.screen.size.height
        ck("屏幕 80×24", (W, H) == (80, 24), "%dx%d" % (W, H))

        # 输入行结构:外层 #inputrow(带边框,贴底整宽)内 = 常驻 ❯ 提示符 + 可编辑 TextArea。
        ibox = app.query_one("#inputrow")
        prompt = app.query_one("#prompt")
        ck("有常驻提示符 ❯(#prompt)", str(getattr(prompt, "content", "")) == "❯",
           repr(str(getattr(prompt, "content", ""))))
        ck("提示符 ❯ 在输入框内、左沿", ibox.region.x <= prompt.region.x < inp.region.x,
           "ibox=%r prompt=%r input=%r" % (ibox.region, prompt.region, inp.region))
        ck("输入框 TextArea 在 ❯ 右侧(#prompt 与 #input 同一行)",
           prompt.region.y == inp.region.y, "prompt.y=%d input.y=%d" % (prompt.region.y, inp.region.y))

        # ---- 2) 输入框贴底、整宽、在内容区之下 ----
        print("=== 2. 输入框贴底(底部工具条不被顶走的回归锁) ===")
        ir = ibox.region
        cr = cont.region
        ck("输入框贴底:region.bottom == 屏幕高", ir.bottom == H, "input.region=%r H=%d" % (ir, H))
        ck("输入框整宽:region.x==0 且 width==屏宽", ir.x == 0 and ir.width == W, repr(ir))
        ck("内容区在输入框之上(不重叠)", cr.bottom <= ir.y, "content.bottom=%d input.y=%d" % (cr.bottom, ir.y))
        ck("内容区从屏幕顶部起(region.y==0)", cr.y == 0, repr(cr))

        # ---- 3) 内容区行数/顺序:消息从上往下紧接,不从底部冒 ----
        print("=== 3. 消息从上往下紧接(回复不从底部冒出) ===")
        inp.text = "hello"
        await pilot.press("enter")
        for _ in range(30):
            await pilot.pause()
            await asyncio.sleep(0.02)
        txt = _content_text(app)
        kids = _content_children(app)
        ck("内容区出现回显 ❯ hello", "❯ hello" in txt, repr(txt[-120:]))
        ck("流式答案定稿进内容区", "回答:hello" in txt, repr(txt[-160:]))
        ck("思考段进内容区(带 ▸ 折叠标记)", "▸" in txt, repr(txt[-160:]))
        ck("工具横幅进内容区", "已完成" in txt, repr(txt[-160:]))
        ck("消息子件数 ≥4(回显/思考/答案/工具)", len(kids) >= 4, "n=%d" % len(kids))

        # 布局:顺序堆叠、互不重叠、都在内容区内
        regions = [k.region for k in kids if k.region.height > 0]
        ys = [r.y for r in regions]
        ck("首条消息贴着内容区顶部(region.y ≤ 1,无底部大空白)", bool(ys) and ys[0] <= 1, repr(ys[:3]))
        ck("消息自上而下顺序排列(y 单调不减)", ys == sorted(ys), repr(ys))
        no_overlap = all(regions[i].bottom <= regions[i + 1].y for i in range(len(regions) - 1))
        ck("相邻消息不重叠(上一段底 ≤ 下一段顶)", no_overlap,
           repr([(r.y, r.bottom) for r in regions]))
        ck("所有消息都在内容区内(最后一段底 ≤ 内容区底)",
           bool(regions) and regions[-1].bottom <= cr.bottom, "last.bottom=%d cr.bottom=%d" % (
               regions[-1].bottom if regions else -1, cr.bottom))

        # ---- 5) resize 后无重复 widget、输入框仍贴底 ----
        print("=== 5. resize 一组尺寸:仍单输入框、仍贴底、内容仍在上 ===")
        for (w, h) in ((60, 20), (100, 30), (40, 15), (80, 24), (73, 31)):
            try:
                await pilot.resize_terminal(w, h)
            except Exception as e:
                ck("resize %dx%d 不抛异常" % (w, h), False, repr(e))
                continue
            await pilot.pause()
            i2 = app.query_one("#inputrow").region
            c2 = app.query_one("#content", VerticalScroll).region
            SW, SH = app.screen.size.width, app.screen.size.height
            ck("resize %dx%d:仍恰好 1 输入框" % (w, h), len(app.query(TextArea)) == 1)
            ck("resize %dx%d:仍恰好 1 内容区" % (w, h), len(app.query("#content")) == 1)
            ck("resize %dx%d:输入框贴新底(bottom==%d)" % (w, h, SH), i2.bottom == SH,
               "bottom=%d SH=%d" % (i2.bottom, SH))
            ck("resize %dx%d:输入框整宽(x==0,width==%d)" % (w, h, SW),
               i2.x == 0 and i2.width == SW, repr(i2))
            ck("resize %dx%d:内容区在输入框之上、从顶起" % (w, h),
               c2.y == 0 and c2.bottom <= i2.y, "content=%r input.y=%d" % (c2, i2.y))

        # 回到基线尺寸,便于后面的状态区断言
        try:
            await pilot.resize_terminal(80, 24)
            await pilot.pause()
        except Exception:
            pass

        # ---- 6) 状态区:auto 开时夹在内容区与输入框之间;关时折叠为 0 ----
        print("=== 6. 状态区位置(auto 开:内容区↔输入框之间;关:折叠 0) ===")
        from rich.text import Text as _Text
        st = app.query_one("#status")

        # 6a) auto 关:高度 0
        _fix_env()
        app._last_marker = "__force__"   # 逼它重算
        app._refresh_status()
        await pilot.pause()
        ck("auto 关:状态区高度 == 0(不占空档)", st.region.height == 0, repr(st.region))

        # 6b) auto 开:注入固定标记(不读真实 ~/.ct 状态),高度>0 且夹在中间
        import wchat_py.auto_enable as _ae
        _orig = _ae.marker_text
        try:
            _ae.marker_text = lambda: "⚡ auto=full · 已自动放行 3 次"
            os.environ["AUTO_MODE"] = "full"
            app._last_marker = "__force2__"
            app._refresh_status()
            await pilot.pause()
            sr = st.region
            i3 = app.query_one("#inputrow").region
            c3 = app.query_one("#content", VerticalScroll).region
            ck("auto 开:状态区高度 > 0(常驻可见)", sr.height > 0, repr(sr))
            ck("auto 开:状态区在内容区之下(status.y ≥ 内容区底)", sr.y >= c3.bottom,
               "status.y=%d content.bottom=%d" % (sr.y, c3.bottom))
            ck("auto 开:状态区在输入框之上(status底 ≤ 输入框顶)", sr.bottom <= i3.y,
               "status.bottom=%d input.y=%d" % (sr.bottom, i3.y))
            ck("auto 开:状态区文本含档位标记", "auto=full" in str(getattr(st, "content", "")),
               repr(str(getattr(st, "content", ""))[:80]))
        finally:
            _ae.marker_text = _orig
            _fix_env()
            app._last_marker = None
            app._refresh_status()

        # 6c) 状态行配色 = placeholder 同一灰(#6a6a6a)+ 斜体,且**不被 dim 压暗**。
        # 回归:marker_text() 自带 \033[2m(dim),旧实现 Text.from_ansi 会把 #666666 压成
        # ~#494949,深色终端上几乎看不见(用户实测)。这里用真·ANSI 标记复现,从截图 SVG
        # 读该行实际的 fill/font-style,断言脱离 dim、等于 placeholder 灰。
        import re as _re
        _orig2 = _ae.marker_text
        try:
            _ae.marker_text = lambda: "\x1b[2m⚡ auto=full · 本次会话已自动放行 77 次\x1b[0m"
            os.environ["AUTO_MODE"] = "full"
            app._last_marker = "__force3__"
            app._refresh_status()
            await pilot.pause()
            _svg = app.export_screenshot()
            _classes = {}
            for _m in _re.finditer(r"\.(terminal-\d+-r\d+)\s*\{([^}]*)\}", _svg):
                _classes[_m.group(1)] = _m.group(2).replace("\n", " ").strip()
            _rule = ""
            _mm = _re.search(r'<text class="(terminal-\d+-r\d+)"[^>]*>(?:(?!</text>).)*?auto=full',
                             _svg, _re.S)
            if _mm:
                _rule = _classes.get(_mm.group(1), "")
            ck("auto 标记状态行用 placeholder 灰 #6a6a6a", "6a6a6a" in _rule, repr(_rule))
            ck("auto 标记状态行斜体(与 placeholder 一致)", "italic" in _rule, repr(_rule))
            ck("auto 标记状态行不被 dim 压暗(非 #494949/#4a4a4a)",
               ("494949" not in _rule and "4a4a4a" not in _rule), repr(_rule))
        finally:
            _ae.marker_text = _orig2
            _fix_env()
            app._last_marker = None
            app._refresh_status()


async def run_welcome_checks():
    """空态欢迎提示:首次进入(banner 空、无历史)时内容区居中显示用法,有内容即收起。

    为什么值得一条闸门:欢迎块是"内容为空"时才出现的**另一套布局**(藏 #content、亮 #welcome),
    任何一处 _mount 忘了收起它,都会让提示压在对话正文上面;反之清屏后不回来又变成大片空白。
    这里用真布局引擎确认:占满内容区、逐行水平居中、与思考文本同档灰+斜体、有内容后收起、清屏后回来。
    """
    import re as _re
    _fix_env()

    def hl(text, emit, stream, reason):
        reason("(思考)")
        for ch in ("回答:" + text):
            stream(ch)
        emit("\n")

    print("=== 7. 空态欢迎提示(首次进入:banner 空、内容为空) ===")
    app = tui.make_app(hl, banner="")
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        wel = app.query_one("#welcome")
        cbox = app.query_one("#contentbox", Vertical)
        content = app.query_one("#content", VerticalScroll)
        W, H = app.screen.size.width, app.screen.size.height

        ck("空态:#contentbox 带 .empty 类", cbox.has_class("empty"))
        ck("空态:welcome 可见(display=block)", str(wel.styles.display) == "block", repr(wel.styles.display))
        ck("空态:#content 收起(display=none)", str(content.styles.display) == "none",
           repr(content.styles.display))

        wr = wel.region
        ck("空态:welcome 占满内容区宽(region.x==0 且 width==屏宽)", wr.x == 0 and wr.width == W, repr(wr))
        ck("空态:welcome 从顶部起且占满内容区高(region.y==0, height>0)",
           wr.y == 0 and wr.height > 0, repr(wr))

        wtxt = str(getattr(wel, "content", ""))
        for line in ("欢迎使用 WCHAT", "输入 /", "命令菜单"):
            ck("空态:提示含 %r" % line, line in wtxt, repr(wtxt))

        # 配色/斜体:与思考文本/placeholder 同一档灰 #6a6a6a + 斜体(读 computed style,稳定)
        _col = wel.styles.color
        ck("空态:welcome 颜色=#6a6a6a(与思考文本同档灰)",
           _col is not None and "106, 106, 106" in str(_col), repr(_col))
        try:
            _ts = wel.styles.text_style
            ck("空态:welcome 斜体", "italic" in str(_ts).lower() or bool(getattr(_ts, "italic", False)),
               repr(_ts))
        except Exception as _e:
            ck("空态:welcome 斜体", False, repr(_e))

        # 逐行水平居中:较短行 x 更大(每行各自居中,而非整块左对齐)
        _svg = app.export_screenshot()

        def _x(needle):
            _m = _re.search(
                r'<text class="terminal-\d+-r\d+"[^>]*x="([\d.]+)"[^>]*y="[\d.]+"[^>]*>'
                r'(?:(?!</text>).)*?%s' % _re.escape(needle), _svg, _re.S)
            return float(_m.group(1)) if _m else None

        _xl = _x("Ctrl+F")       # 最长的一行(最靠左)
        _xs = _x("欢迎使用")      # 较短的一行(应更靠右)
        ck("空态:提示逐行居中(短行 x > 长行 x)",
           _xl is not None and _xs is not None and _xs > _xl,
           "short=%r long=%r" % (_xs, _xl))

        # 提交一条消息 → 欢迎收起、正文进内容区
        inp = app.query_one("#input", TextArea)
        inp.text = "hello"
        await pilot.press("enter")
        for _ in range(80):
            await pilot.pause()
            await asyncio.sleep(0.02)
            if not app._busy and app._live_widget is None:
                break
        await pilot.pause()
        ck("有内容后:.empty 类被移除", not cbox.has_class("empty"))
        ck("有内容后:welcome 收起(display=none)", str(wel.styles.display) == "none", repr(wel.styles.display))
        ck("有内容后:#content 恢复显示(display=block)", str(content.styles.display) == "block",
           repr(content.styles.display))
        ck("有内容后:正文进内容区(回答:hello)", "回答:hello" in _content_text(app),
           repr(_content_text(app)[-80:]))

        # -cl 清屏后回到欢迎空态
        app._clear_content()
        await pilot.pause()
        ck("清屏后:重新显示欢迎(.empty 类回来)", cbox.has_class("empty"))


async def run_hint_style_checks():
    """提示行去 │ 竖线 + 统一成"思考内容灰 + 斜体"。

    回归:旧输出给每行提示加 `\\033[2m│\\033[0m ` 暗色竖线来"模拟次要样式"(cli.inp_msg /
    render.BAR)。TUI 里保留这根竖线既丑又和思考/placeholder 观感不统一,用户要求去掉,改成
    "思考内容颜色 + 斜体"。这里既测单元(_hint_text 去前缀+样式),也测端到端(内容区无 │、
    提示行颜色==思考内容颜色、且斜体),并反向锁:工具框线的 `   │ `(裸竖线)不受影响。
    """
    import re as _re
    _fix_env()

    print("=== 8. 提示行去 │ 竖线 → 思考内容灰 + 斜体 ===")

    # a) 单元:_hint_text 去行首 │ 前缀,整行 dim+italic
    app0 = tui.make_app(lambda *a: None, banner="")
    _ht = app0._hint_text("\x1b[2m│\x1b[0m ✅ 测试提示")
    ck("提示行去掉行首 │", "│" not in _ht.plain, repr(_ht.plain))
    ck("提示行文本保留(✅ 测试提示)", "✅ 测试提示" in _ht.plain.replace("\xa0", " "), repr(_ht.plain))
    _st = str(_ht.style)
    ck("提示行基础样式=dim+italic(dim=思考内容灰)", "dim" in _st and "italic" in _st, repr(_ht.style))
    _hm = app0._hint_text("\x1b[2m│\x1b[0m 第一行\n\x1b[2m│\x1b[0m 第二行")
    ck("多行提示每行都去掉 │(第一/第二行都在)",
       "│" not in _hm.plain and "第一行" in _hm.plain and "第二行" in _hm.plain, repr(_hm.plain))
    # 反向锁:工具框线的裸竖线 `   │ `(无 ANSI)不是提示前缀,不被 _hint_text 动到(该路径本就不进 _hint_text,
    # 这里锁正则不匹配它,保证进了也不会被误判)
    ck("裸竖线工具框行不被识别成提示前缀(不误伤)", not tui._HINT_BAR_RE.match("   │ tool out"))

    # b) 端到端:emit 提示 → 内容区无 │、提示在、颜色==思考内容色、斜体
    def hl(text, emit, stream, reason):
        from wchat_py.cli import inp_msg
        reason("思考内容样本")
        inp_msg("✅ 已开启思考模式")
        for ch in "正文":
            stream(ch)
        emit("\n")

    app = tui.make_app(hl, banner="")
    async with app.run_test(size=(70, 20)) as pilot:
        await pilot.pause()
        inp = app.query_one("#input", TextArea)
        inp.text = "hi"
        await pilot.press("enter")
        for _ in range(80):
            await pilot.pause()
            await asyncio.sleep(0.02)
            if not app._busy and app._live_widget is None:
                break
        await pilot.pause()
        txt = _content_text(app)
        ck("提示进内容区(✅ 已开启思考模式)", "已开启思考模式" in txt, repr(txt[-120:]))
        ck("内容区不再出现提示竖线 │", "│" not in txt, repr(txt))

        _svg = app.export_screenshot()
        _cls = {}
        for _m in _re.finditer(r"\.(terminal-\d+-r\d+)\s*\{([^}]*)\}", _svg):
            _cls[_m.group(1)] = _m.group(2)

        def _style_of(needle):
            _mm = _re.search(
                r'<text class="(terminal-\d+-r\d+)"[^>]*>(?:(?!</text>).)*?%s' % _re.escape(needle),
                _svg, _re.S)
            return _cls.get(_mm.group(1), "") if _mm else ""

        def _fill(rule):
            _f = _re.search(r"fill:\s*(#[0-9a-fA-F]+)", rule)
            return _f.group(1).lower() if _f else None

        _hint_rule = _style_of("已开启思考模式")
        _reason_rule = _style_of("思考内容样本")
        ck("提示行颜色与思考内容一致",
           _fill(_hint_rule) is not None and _fill(_hint_rule) == _fill(_reason_rule),
           "hint=%r reason=%r" % (_hint_rule, _reason_rule))
        ck("提示行斜体(思考内容本身不强制斜体,提示行加斜体)", "italic" in _hint_rule, repr(_hint_rule))


async def run_workdir_checks():
    """输入框上方「工作目录栏」:常驻可见、贴输入框上方、可点击弹三选项菜单(复制路径 /
    在文件管理器打开 / 更换工作目录),且不破坏既有布局(输入框仍贴底、状态区仍折叠)。

    回归价值:这一栏若高度错、被顶到内容区里、或点击没反应,用户就找不到当前工作目录、
    也换不了。用真布局引擎 + Pilot 点击钉死:①存在且贴输入框上方;②点了真弹菜单;
    ③「显示完整路径」展开成全路径(带 .full 类,允许折行);④复制真写进剪贴板;
    ⑤Finder 动作真拿到全路径;⑥auto 关时状态区仍折叠为 0(本栏不占状态区高度)。
    """
    _fix_env()
    import wchat_py.tui as _t

    _saved_wr = os.environ.get("WORK_ROOT")
    # 造一个"在 home 下"的工作目录,验证折叠成 ~(显示不必真实存在,realpath 照样能算)
    _wd = os.path.join(os.path.expanduser("~"), "wchat-wd-visual-test")
    os.environ["WORK_ROOT"] = _wd

    # 记录 Finder 打开的真实入参(不真弹 Finder)
    _calls = []
    _orig_open = _t._open_path_in_file_manager
    _t._open_path_in_file_manager = lambda p: (_calls.append(p), (True, "ok"))[1]
    try:
        app = _t.make_app(lambda *a: None, banner="")
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            print("=== 9. 工作目录栏:显示 / 贴输入框上方 / 点击弹菜单 / 三动作 ===")
            bar = app.query_one("#workdirbar")
            inp = app.query_one("#inputrow")
            cont = app.query_one("#content", VerticalScroll)
            W = app.screen.size.width
            _full = os.path.realpath(_wd)

            ck("有工作目录栏(#workdirbar,1 个)", len(app.query("#workdirbar")) == 1)
            _txt = str(getattr(bar, "content", ""))
            ck("工作目录栏文本含 📁 图标", "📁" in _txt, repr(_txt))
            # home 下 → 折叠成 ~ 开头(不显示完整 /Users/...)
            ck("home 下的路径折叠成 ~ 开头", _txt.replace("\xa0", " ").find("~") != -1
               and "/Users/" not in _txt and _wd.split(os.sep)[-1] in _txt, repr(_txt))

            # 布局:贴输入框上方、在内容区之下、整宽、高度 ≥1
            ck("工作目录栏整宽", bar.region.x == 0 and bar.region.width == W, repr(bar.region))
            ck("工作目录栏在输入框上方(栏底 ≤ 输入行顶)", bar.region.bottom <= inp.region.y,
               "bar=%r input=%r" % (bar.region, inp.region))
            ck("工作目录栏在内容区之下(栏顶 ≥ 内容区底)", bar.region.y >= cont.region.bottom,
               "bar.y=%d content.bottom=%d" % (bar.region.y, cont.region.bottom))
            ck("工作目录栏高度 ≥ 1(常驻可见)", bar.region.height >= 1, repr(bar.region))

            # auto 关:状态区仍折叠为 0(本栏不占用状态区)
            app._last_marker = "__force__"
            app._refresh_status()
            await pilot.pause()
            ck("auto 关:状态区仍折叠为 0(工作目录栏不占状态区高度)",
               app.query_one("#status").region.height == 0)

            # 点击 → 弹菜单
            ck("点击前无弹窗(仅 1 层 screen)", len(app.screen_stack) == 1)
            await pilot.click("#workdirbar")
            await pilot.pause()
            _tops = [type(s).__name__ for s in app.screen_stack]
            ck("点击工作目录栏 → 弹出 WorkdirMenuModal", len(app.screen_stack) == 2
               and "WorkdirMenuModal" in _tops, repr(_tops))

            # 菜单只有两项:在文件管理器打开 / 更换工作目录;且不再有 复制路径/显示完整路径
            ck("菜单有「在文件管理器打开」按钮", len(app.screen.query("#filemgr")) == 1)
            ck("菜单有「更换工作目录」按钮", len(app.screen.query("#change")) == 1)
            ck("菜单已移除「复制路径」按钮", len(app.screen.query("#copy")) == 0)
            ck("菜单已移除「显示完整路径」按钮", len(app.screen.query("#show")) == 0)

            # 「在文件管理器打开」:动作拿到全路径(平台中立:mac/Linux/WSL 都走它)
            await pilot.click("#filemgr")
            await pilot.pause()
            ck("「在文件管理器打开」拿到全路径", _calls and _calls[-1] == _full, repr(_calls))
            ck("「在文件管理器打开」后弹窗关闭", len(app.screen_stack) == 1, repr(_tops))

            # Esc:打开后 Esc 关闭
            await pilot.click("#workdirbar")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            ck("Esc 关闭工作目录菜单", len(app.screen_stack) == 1)
    finally:
        _t._open_path_in_file_manager = _orig_open
        if _saved_wr is None:
            os.environ.pop("WORK_ROOT", None)
        else:
            os.environ["WORK_ROOT"] = _saved_wr


async def run_workdir_switch_checks():
    """第二版:真正切换工作目录。钉死:
    ①合法性校验(拒 /、~、系统目录、他人家目录;允许 /tmp 与家目录子目录);
    ②auto 下禁止切换(拦截 + 提示,WORK_ROOT/cwd/回调都不动);
    ③非 auto:经菜单「更换工作目录」→ chdir + WORK_ROOT + 回调 + 工作目录栏一起变;
    ④`/cd <路径>` 直接切;⑤非法路径被拒、不切。"""
    _fix_env()
    import shutil as _sh
    import tempfile as _tf
    import wchat_py.tui as _t
    import wchat_py.repl as _repl

    # ---- ① 纯函数:合法性校验 ----
    print("=== 10. 工作目录切换:合法性校验 ===")
    home = os.path.realpath(os.path.expanduser("~"))
    for bad in ("/", "/usr", "/etc", "/var", "/bin", "/sbin", home, "/nonexistent-xyz-123"):
        ok, _why = _t._validate_workdir(bad)
        ck("拒绝工作目录 %s" % bad, not ok, repr(_why))
    ok_file, _ = _t._validate_workdir(os.path.abspath(__file__))
    ck("拒绝把文件当工作目录", not ok_file)
    _tgt = _tf.mkdtemp(dir="/tmp")
    _tgt2 = _tf.mkdtemp(dir="/tmp")
    ok_tmp, real_tmp = _t._validate_workdir(_tgt)
    ck("允许 /tmp 下的真实目录", ok_tmp and real_tmp == os.path.realpath(_tgt), repr(real_tmp))
    ok_repo, _ = _t._validate_workdir(REPO)
    ck("允许家目录内的项目子目录(repo)", ok_repo)

    # ---- ②③④⑤ 走真 app ----
    _saved = {"cwd": os.getcwd(),
              "WORK_ROOT": os.environ.get("WORK_ROOT"),
              "AUTO_MODE": os.environ.get("AUTO_MODE"),
              "WCHAT_AUTO_MODE": os.environ.get("WCHAT_AUTO_MODE")}
    _orig_pick = _repl._pick_dir
    _picked = {"v": _tgt}
    _repl._pick_dir = lambda default=None: _picked["v"]
    _changed = []
    _origin = REPO
    try:
        os.environ["WORK_ROOT"] = _origin
        for _k in ("AUTO_MODE", "WCHAT_AUTO_MODE"):
            os.environ.pop(_k, None)
        app = _t.make_app(lambda *a: None, banner="",
                          on_workdir_change=lambda r: _changed.append(r))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            bar = app.query_one("#workdirbar")

            # ② auto 下禁止
            print("=== 11. auto 下禁止更换工作目录 ===")
            os.environ["AUTO_MODE"] = "full"
            os.environ["WCHAT_AUTO_MODE"] = "full"
            _changed.clear()
            await pilot.click("#workdirbar"); await pilot.pause()
            await pilot.click("#change"); await pilot.pause()
            ck("auto 下切换被拦:WORK_ROOT 未变", os.environ.get("WORK_ROOT") == _origin,
               os.environ.get("WORK_ROOT"))
            ck("auto 下切换被拦:回调未触发", not _changed, repr(_changed))
            ck("auto 下切换被拦:cwd 未变", os.getcwd() == _saved["cwd"], os.getcwd())
            _txt = _content_text(app)
            ck("auto 下切换被拦:给出提示(含 auto/不能更换)",
               "auto" in _txt and "不能更换" in _txt, repr(_txt[-140:]))
            for _k in ("AUTO_MODE", "WCHAT_AUTO_MODE"):
                os.environ.pop(_k, None)
            app._last_marker = "__force__"; app._refresh_status(); await pilot.pause()

            # ③ 非 auto:菜单「更换工作目录」→ 切到 /tmp 目标
            print("=== 12. 非 auto:切换工作目录(菜单→选择器) ===")
            _changed.clear()
            _picked["v"] = _tgt
            await pilot.click("#workdirbar"); await pilot.pause()
            await pilot.click("#change"); await pilot.pause()
            ck("切换后 cwd 变为目标", os.getcwd() == os.path.realpath(_tgt), os.getcwd())
            ck("切换后 WORK_ROOT 变为目标", os.environ.get("WORK_ROOT") == os.path.realpath(_tgt),
               os.environ.get("WORK_ROOT"))
            ck("切换后调用 on_workdir_change 回调",
               _changed and _changed[-1] == os.path.realpath(_tgt), repr(_changed))
            ck("切换后工作目录栏显示新目录",
               _t._pretty_path(os.path.realpath(_tgt)) in str(getattr(bar, "content", "")),
               repr(str(getattr(bar, "content", ""))))

            # ④ /cd <路径> 直接切
            print("=== 13. /cd <路径> 直接切换 ===")
            _changed.clear()
            app._handle_line_text("/cd " + _tgt2)
            await pilot.pause()
            ck("/cd <路径>:cwd 变为目标", os.getcwd() == os.path.realpath(_tgt2), os.getcwd())
            ck("/cd <路径>:WORK_ROOT 变为目标", os.environ.get("WORK_ROOT") == os.path.realpath(_tgt2),
               os.environ.get("WORK_ROOT"))
            ck("/cd <路径>:回调触发", _changed and _changed[-1] == os.path.realpath(_tgt2), repr(_changed))

            # ⑤ /cd 非法路径被拒
            print("=== 14. /cd 非法路径被拒 ===")
            _before = os.environ.get("WORK_ROOT")
            _changed.clear()
            app._handle_line_text("/cd /etc")
            await pilot.pause()
            ck("/cd /etc 被拒:WORK_ROOT 不变", os.environ.get("WORK_ROOT") == _before,
               os.environ.get("WORK_ROOT"))
            ck("/cd /etc 被拒:回调未触发", not _changed)
            ck("/cd /etc 被拒:给出提示", "无法切换工作目录" in _content_text(app),
               repr(_content_text(app)[-140:]))
    finally:
        _repl._pick_dir = _orig_pick
        try:
            os.chdir(_saved["cwd"])
        except Exception:
            pass
        for _k in ("WORK_ROOT", "AUTO_MODE", "WCHAT_AUTO_MODE"):
            if _saved[_k] is None:
                os.environ.pop(_k, None)
            else:
                os.environ[_k] = _saved[_k]
        for _d in (_tgt, _tgt2):
            try:
                _sh.rmtree(_d, ignore_errors=True)
            except Exception:
                pass


def run_modal_style_checks():
    """所有弹窗的窗口线条统一(round #6a6a6a、粗细一致),背景遮罩加深到 70% 凸显独立性。
    回归:用户要求把各弹窗边框换成与工作目录菜单同一灰。曾试过用单边 outline 模拟投影,
    但 Textual 8.x 无 box-shadow,outline 会渲染成偏离边框的断开矩形(看着像坏了),已弃用。
    这里解析 app.CSS,逐弹窗断言 .dialog = round #6a6a6a,背景遮罩 70%,且无 outline 残留、
    无旧边框色($warning / thick $accent)。"""
    import re as _re
    print("=== 15. 弹窗边框统一(round #6a6a6a)+ 遮罩 70%(无 outline 残影) ===")
    css = tui.make_app(lambda *a: None, banner="").CSS
    modals = ["ConfirmModal", "InputModal", "HistoryPickModal", "WorkdirMenuModal"]
    for m in modals:
        _mm = _re.search(r"%s\s+\.dialog\s*\{([^}]*)\}" % m, css)
        _body = _mm.group(1) if _mm else ""
        ck("%s .dialog 用统一灰边框 round #6a6a6a" % m,
           "border: round #6a6a6a" in _body, repr(_body[:120]))
        _back = _re.search(r"%s\s*\{([^}]*)\}" % m, css)
        _bb = _back.group(1) if _back else ""
        ck("%s 背幕遮罩加深到 70%%" % m, "background: $background 70%" in _bb, repr(_bb[:100]))
    # 弃用 outline 模拟阴影(会渲染成断开矩形),不许残留
    ck("无 outline 模拟阴影残留(会渲染成断开矩形)", "outline-right" not in css and "outline-bottom" not in css)
    # 不再残留旧边框色(用户要求统一)
    ck("无弹窗残留 $warning 边框", "border: round $warning" not in css)
    ck("无弹窗残留 thick $accent 边框", "border: thick $accent" not in css)
    # 工作目录菜单按钮:白/浅色线条 + 浅灰文字 + 透明背景(无白色字体背景色)
    _b = _re.search(r"WorkdirMenuModal\s+\.btns Button\s*\{([^}]*)\}", css)
    _bb = _b.group(1) if _b else ""
    ck("菜单按钮:白/浅色线条边框(#d0d0d0)", "border: round #d0d0d0" in _bb, repr(_bb[:140]))
    ck("菜单按钮:浅灰文字(#a8a8a8)", "color: #a8a8a8" in _bb, repr(_bb[:140]))
    ck("菜单按钮:背景透明(无白色文字背景)", "background: transparent" in _bb, repr(_bb[:140]))
    _f = _re.search(r"WorkdirMenuModal\s+\.btns Button:focus\s*\{([^}]*)\}", css)
    _ff = _f.group(1) if _f else ""
    ck("菜单按钮聚焦态:背景透明(不铺白底)", "background: transparent" in _ff, repr(_ff[:140]))


def main():
    try:
        asyncio.run(run_checks())
    except Exception as e:
        import traceback
        traceback.print_exc()
        ck("headless 跑通(无异常)", False, repr(e))
    try:
        asyncio.run(run_welcome_checks())
    except Exception as e:
        import traceback
        traceback.print_exc()
        ck("空态欢迎 headless 跑通(无异常)", False, repr(e))
    try:
        asyncio.run(run_hint_style_checks())
    except Exception as e:
        import traceback
        traceback.print_exc()
        ck("提示行样式 headless 跑通(无异常)", False, repr(e))
    try:
        asyncio.run(run_workdir_checks())
    except Exception as e:
        import traceback
        traceback.print_exc()
        ck("工作目录栏 headless 跑通(无异常)", False, repr(e))
    try:
        asyncio.run(run_workdir_switch_checks())
    except Exception as e:
        import traceback
        traceback.print_exc()
        ck("工作目录切换 headless 跑通(无异常)", False, repr(e))
    try:
        run_modal_style_checks()
    except Exception as e:
        import traceback
        traceback.print_exc()
        ck("弹窗样式检查跑通(无异常)", False, repr(e))
    print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
    return 0 if F == 0 else 1


if __name__ == "__main__":
    sys.exit(main())