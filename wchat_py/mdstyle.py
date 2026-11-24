#!/usr/bin/env python3
"""共享 markdown 渲染样式(modern 主题):一处定义,两处复用。

为什么要独立成模块:同一份 markdown 有两条渲染路径 ——
  · 非 TUI:`render.render_markdown()` → 子进程跑 `mdrender.py`(整篇 → ANSI);
  · TUI  :`tui.py` 把 `rich.markdown.Markdown` 挂进内容区。
以前两条路各写各的:TUI 直接用 rich **默认样式**(H1 居中、品红/青色、粗体代码块),
和"现代、克制"的观感差得远,而且和非 TUI 的观感不一致。这个模块把 **主题(颜色/字形)**
和 **左对齐标题渲染器** 收成一份,`mdrender` 与 `tui` 都从这里取,保证两条路一致。

设计口径(极简灰阶,接近 claude code):
  · 标题左对齐,**只靠字重 + 淡灰分节线**区分,不用彩色(避免"一多就乱");H4+ 更弱;
  · 正文保持终端默认前景色(不灰化);次要元素(引用/项目符号/序号/分隔线)用灰阶弱化;
  · 行内代码用**淡灰 grey85** 区别于正文,去掉"粗体 + 黑底"重样式;
    代码块用 pygments `github-dark` 语法高亮(克制、贴编辑器深色);
  · 链接正文用蓝色、URL 蓝色下划线(Hover/点击仍交给终端)。

只影响 `markdown.*` 命名空间下的样式;正文段落无样式(即终端默认前景色),所以
"助手正文保持正常亮度、不被灰化"这条既有约定(见 toolui_test)不受影响。
"""

# 主题:覆盖 rich 默认的 markdown.* 样式。Theme(inherit=True) 只覆盖这里列出的名字,
# 其余仍回退到 rich 默认主题,不会把控制台里其它样式搞坏。
_STYLES = {
    # 极简灰阶(接近 claude code,C 版为后续微调基线):标题只靠**字重 + 淡灰分节线**区分层次,
    # 不用彩色(内容一多也不乱);正文保持**终端默认前景色**;次要元素(引用/符号/分隔线)降灰;
    # 链接保留蓝色作唯一强调;行内代码淡灰 grey85(无 bold/无底色)。代码块见 build_markdown 的 github-dark。
    "markdown.h1": "bold",
    "markdown.h1.border": "grey42",
    "markdown.h2": "bold",
    "markdown.h3": "bold",
    "markdown.h4": "bold",
    "markdown.h5": "dim",
    "markdown.h6": "dim italic",
    "markdown.h7": "dim italic",
    "markdown.code": "grey85",        # 行内代码:淡灰(区别正文但克制);无 bold / 无底色
    "markdown.code_block": "",        # 代码块:交给 rich 语法高亮(见 build_markdown 的 code_theme=github-dark)
    "markdown.block_quote": "grey54",
    "markdown.list": "",
    "markdown.item.bullet": "grey62",
    "markdown.item.number": "grey62",
    "markdown.link": "blue",
    "markdown.link_url": "blue underline",
    "markdown.table.border": "grey42",
    "markdown.table.header": "bold",
    "markdown.hr": "grey42",
    "markdown.strong": "bold",
    "markdown.em": "italic",
    "markdown.emph": "italic",
    "markdown.s": "strike",
    "markdown.kbd": "bold",
    "markdown.paragraph": "",         # 正文=终端默认前景色(不灰化/不强制色)
    "markdown.text": "",
}

_THEME = None
_MD_CLASS = None


def markdown_theme():
    """返回共享的 rich Theme(惰性构造并缓存;导出给 TUI push_theme 用)。"""
    global _THEME
    if _THEME is None:
        from rich.theme import Theme
        _THEME = Theme(_STYLES)
    return _THEME


def _build_markdown_class():
    """构造一个把标题改成"左对齐 + 现代收尾"的 rich Markdown 子类。

    rich 默认:**H1 居中**(在宽终端里离正文很远)、且 mdrender 旧实现给 H1 套
    一整圈 HEAVY 粗框。现代观感是左对齐 + 层级色,所以这里统一覆写 heading_open:
      · 全部标题 `justify="left"`;
      · H1 之后补一条**暗色细线**(不套框),给出清晰的分节而不过度占版面;
      · H2 之前留一个空行(与旧实现一致,块间距更舒展)。
    """
    from rich.markdown import Markdown as _Markdown
    from rich.markdown import Heading as _Heading
    from rich.markdown import HorizontalRule as _HorizontalRule
    from rich.text import Text
    from rich.rule import Rule

    class _ModernHeading(_Heading):
        def __rich_console__(self, console, options):
            self.text.justify = "left"
            if self.tag == "h1":
                yield self.text
                yield Rule(style="grey50", characters="─")
            else:
                if self.tag == "h2":
                    yield Text("")
                yield self.text

    class _ModernHR(_HorizontalRule):
        """分隔线统一用细横线 `─`(rich 默认是 ASCII `-`,和 H1 的下划线不一致)。"""

        def __rich_console__(self, console, options):
            style = console.get_style("markdown.hr", default="grey50")
            yield Rule(style=style, characters="─")
            yield Text()

    class _WchatMarkdown(_Markdown):
        elements = dict(_Markdown.elements)
        elements["heading_open"] = _ModernHeading
        elements["hr"] = _ModernHR

    return _WchatMarkdown


def build_markdown(text):
    """用共享的现代主题构造一个 rich Markdown 渲染对象(标题左对齐)。

    注意:它只是一个 rich **可渲染对象**,真正的颜色由**渲染它的 Console 主题**决定。
    mdrender 用 `make_console()`(已带本主题);TUI 需在 app 上 push 本主题(
    `markdown_theme()`),见 tui.on_mount。

    代码块语法高亮统一用 pygments 的 `github-dark`(比 monokai 更克制、更贴编辑器深色);
    行内代码不走语法高亮,颜色由 `markdown.code`(grey85)决定。
    """
    global _MD_CLASS
    if _MD_CLASS is None:
        _MD_CLASS = _build_markdown_class()
    return _MD_CLASS(text, code_theme="github-dark")


def make_console(**kwargs):
    """构造带本主题的 rich Console(给 mdrender 用;其余参数透传)。"""
    from rich.console import Console
    kwargs.setdefault("theme", markdown_theme())
    return Console(**kwargs)