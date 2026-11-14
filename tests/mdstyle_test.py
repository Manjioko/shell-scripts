#!/usr/bin/env python3
"""markdown 现代样式闸门(mdstyle)。

锁住"两条渲染路径共用一份现代主题"这件事,防止被悄悄退回 rich 默认(标题居中、
品红/青色、粗体黑底代码块)。断言只取跨 rich 版本稳定的性质:
  · 标题左对齐(默认 h1 居中 → lead>0,这里必须 lead==0);
  · H1 是"加粗 + 强调色",且**不再带下划线**(默认是 bold underline);
  · 正文段落无样式(正常亮度,不被灰化——与 toolui 的"正文不灰"约定一致);
  · 行内代码去掉默认的 bold + 黑底;
  · 横线/分隔线走 `─` 而非 ASCII `-`。

rich 版本取产品运行期那份(~/.ct/venv);当前解释器缺 rich 时自动换过去,找不到才 SKIP。
用法:`python3 tests/mdstyle_test.py`
"""
import io
import os
import re
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


def _has_rich(exe=None):
    import subprocess
    try:
        if exe is None:
            import rich  # noqa: F401
            return True
        return subprocess.run([exe, "-c", "import rich"],
                              capture_output=True, timeout=30).returncode == 0
    except Exception:
        return False


# 统一用产品运行期的解释器(rich 版本一致),避免 system rich 过旧导致样式串差异。
if not os.environ.get("WCHAT_MDSTYLE_REEXEC"):
    for _exe in (os.environ.get("WCHAT_MDSTYLE_PY"),
                 os.path.expanduser("~/.ct/venv/bin/python"),
                 os.path.join(REPO, ".scratch", "tui_venv", "bin", "python")):
        if _exe and os.path.exists(_exe) and _has_rich(_exe):
            os.environ["WCHAT_MDSTYLE_REEXEC"] = "1"
            os.execv(_exe, [_exe, os.path.abspath(__file__)] + sys.argv[1:])
if not _has_rich():
    print("SKIP: 找不到带 rich 的解释器(markdown 渲染为可选依赖;可设 WCHAT_MDSTYLE_PY)")
    sys.exit(77)


def _plain(line):
    return re.sub(r"\x1b\[[0-9;]*m", "", line)


def _segments(md_text, width=40):
    from wchat_py.mdstyle import build_markdown, make_console
    c = make_console(width=width, force_terminal=True, highlight=False)
    all_segs = list(c.render(build_markdown(md_text)))
    raw = "".join(s.text for s in all_segs)
    segs = [(s.text, str(s.style)) for s in all_segs if s.text.strip()]
    return segs, raw


print("=== markdown 现代样式(mdstyle)===")

# 1) build_markdown 用的是自定义 heading 渲染器(不是 rich 默认)
try:
    from wchat_py import mdstyle
    from wchat_py.mdstyle import build_markdown, markdown_theme
    from rich.markdown import Heading
    md = build_markdown("# x")
    ho = type(md).elements["heading_open"]
    ck("heading_open 是自定义类(非 rich 默认 Heading)",
       issubclass(ho, Heading) and ho is not Heading, ho.__name__)
except Exception as e:
    ck("mdstyle 可导入且 build_markdown 可用", False, repr(e))

# 2) 主题里确实覆盖了关键 markdown.* 名字
try:
    _t = markdown_theme()
    _names = set(getattr(_t, "styles", {}) or {})
    ck("主题覆盖 markdown.h1", "markdown.h1" in _names)
    ck("主题覆盖 markdown.code", "markdown.code" in _names)
    ck("主题覆盖 markdown.table.border", "markdown.table.border" in _names)
except Exception as e:
    ck("markdown_theme() 返回主题", False, repr(e))

# 3) H1 左对齐(默认居中 → lead>0;现代=lead==0)
try:
    segs, raw = _segments("# 标题标题\n\n正文段落文字\n")
    lines = [(_plain(l)) for l in raw.splitlines() if _plain(l).strip()]
    h1_line = next((l for l in lines if "标题标题" in l), "")
    lead = len(h1_line) - len(h1_line.lstrip())
    ck("H1 左对齐(lead==0,不再是居中)", lead == 0, "lead=%d %r" % (lead, h1_line[:20]))
except Exception as e:
    ck("H1 左对齐", False, repr(e))

# 4) H1 样式 = 加粗 + 强调色,且去掉下划线(默认是 bold underline)
try:
    segs, raw = _segments("# 标题\n")
    h1_styles = [st for tx, st in segs if "标题" in tx]
    h1_style = h1_styles[0] if h1_styles else ""
    ck("H1 加粗", "bold" in h1_style, repr(h1_style))
    ck("H1 不再下划线(脱离 rich 默认 bold underline)", "underline" not in h1_style, repr(h1_style))
except Exception as e:
    ck("H1 样式", False, repr(e))

# 5) 正文段落无样式(正常亮度,不被灰化)
try:
    segs, raw = _segments("正文段落文字\n")
    body_styles = [st for tx, st in segs if "正文" in tx]
    body_style = body_styles[0] if body_styles else "???"
    ck("正文不灰(style 为 none)", body_style in ("none", "None"), repr(body_style))
except Exception as e:
    ck("正文不灰", False, repr(e))

# 6) 行内代码:去掉默认的 bold + 黑底
try:
    segs, raw = _segments("前 `代码` 后\n")
    code_styles = [st for tx, st in segs if tx.strip() == "代码"]
    code_style = code_styles[0] if code_styles else ""
    ck("行内代码不加粗(去默认 bold)", "bold" not in code_style, repr(code_style))
    ck("行内代码无背景色(去默认 on black)", " on " not in (" " + code_style + " "), repr(code_style))
except Exception as e:
    ck("行内代码样式", False, repr(e))

# 7) 分隔线用 `─` 而非 ASCII `-`
try:
    segs, raw = _segments("上\n\n---\n\n下\n")
    plain = "\n".join(_plain(l) for l in raw.splitlines())
    ck("分隔线用 ─(非 ASCII -)", "─" in plain, repr([l for l in plain.splitlines() if l.strip()][:3]))
except Exception as e:
    ck("分隔线字符", False, repr(e))

# 8) 子进程 mdrender 端到端:把同一份 md 渲染出 ANSI(标题带样式的转义)
try:
    import subprocess
    mdrender = os.path.join(REPO, "wchat_py", "mdrender.py")
    p = subprocess.run([sys.executable, mdrender], input="# 标题\n\n正文\n",
                       capture_output=True, text=True, env=dict(os.environ, COLUMNS="60"))
    out = p.stdout or ""
    ck("mdrender 子进程 rc=0", p.returncode == 0, "rc=%s %s" % (p.returncode, (p.stderr or "")[:80]))
    ck("mdrender 输出含 ANSI 样式", "\x1b[" in out, repr(out[:40]))
    ck("mdrender 标题左对齐(输出行无前导空格)",
       any(_plain(l).strip().startswith("标题") and not _plain(l).startswith(" ")
           for l in out.splitlines()),
       repr([_plain(l) for l in out.splitlines() if "标题" in _plain(l)][:1]))
except Exception as e:
    ck("mdrender 子进程", False, repr(e))

print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
sys.exit(0 if F == 0 else 1)