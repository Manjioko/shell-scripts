#!/usr/bin/env python3
"""B 阶段 1c：流式渲染（流箱：环形缓冲 / 残行 / 整窗重绘）。

**与 bash 版的有意分歧（2026-10 修）**：bash 的 `stream_box_*` 用
`ESC[s` 存锚点、每次 paint 用 `ESC[u` 跳回箱顶，并靠 `ESC[?7l` 关自动换行让行数可数。
这两样都**不是所有终端都认**，而 macOS 自带 Terminal.app 恰好是第一项不认的那类。

实测（`tests/termcap_probe.py`，在 Terminal.app 里跑，`TERM=xterm-256color`）：
    存/取光标 `CSI s` / `CSI u`  → **不认**（光标不回位，后续逐行右移 = 阶梯）
    `ESC 7` / `ESC 8`            → 认
    `ESC[?7l`（关自动换行）      → 认
    裸 LF 隐含回车                → 认
同一份字节流在 Ghostty 里一切正常 —— 这就是为什么光看代码/字节流发现不了，必须看
"终端把它画成了什么"。存光标一旦不认，paint 就从上次的落点继续写字、close 也擦不回
箱顶，整块内容在屏幕上越走越偏。

**为什么不改用 `ESC 7/8`**：它在 Terminal.app 上确实能用，但它和 `CSI s/u` 存的是
**绝对行号** —— 屏幕一滚就失效（流箱画在屏幕底部时必然滚）。相对移动没有这个毛病，
所以这里不换成"另一个存光标"，而是彻底不存。

所以这里改成**只依赖最古老的三个动作**：
  * `\\r`（回车）、`\\n`（换行）、`ESC[nA`（光标上移）—— 三者任何 VT 终端都支持；
  * 位置由**我们自己记账**：`drawn` = 上一帧画了几行，回箱顶 = `\\r` + `ESC[drawn-1A`。
    相对移动天然跟着滚动走，不会像"保存的绝对行号"那样在屏幕上滚之后失效。
  * 每行再按终端宽度**硬截断**（`_fit`）：即便终端不认 `?7l`、长行照旧自动折行，
    折行也不会发生，`drawn` 就始终等于真实占用行数。
  * `?7l/?25l` 仍然照发（锦上添花），但**不再是正确性的前提**。

节流（`STREAM_THROTTLE` + 墙钟）不在这里：重绘次数属调用方策略，本模块只回答
"给定缓冲状态，应该吐出什么字节"——这样它才能被确定性地单测与比对。
"""
import os
import re
import shutil
import subprocess
import sys
import unicodedata

BAR = "\033[2m│\033[0m "
BAR_W = 2                      # "│ " 的显示宽度，算可用列宽时要扣掉

# 一次性吞掉一个转义序列（SGR / 光标移动 / 私有模式），用来算"显示宽度"
_ESC = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[()#][0-9A-Za-z]|\x1b[78]")


def _ch_width(ch):
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def display_width(s):
    """不算转义序列的显示宽度（CJK 记 2）。"""
    return sum(_ch_width(c) for c in _ESC.sub("", s))


def _fit(s, limit):
    """把一行截到 limit 列以内：转义序列整段保留、绝不在序列中间下刀。"""
    if limit <= 0:
        return ""
    out = []
    w = 0
    i = 0
    while i < len(s):
        m = _ESC.match(s, i)
        if m:
            out.append(m.group(0))
            i = m.end()
            continue
        cw = _ch_width(s[i])
        if w + cw > limit:
            break
        out.append(s[i])
        w += cw
        i += 1
    return "".join(out)


def term_cols(default=80):
    try:
        return shutil.get_terminal_size((default, 24)).columns or default
    except Exception:
        return default


class StreamBox:
    """环形缓冲 + 残行 + 整窗重绘。`paint()` 是**纯函数**（无 IO、不看时钟）。

    位置记账：
      * `reserve()` 在内容下方留 win 行，光标停在**箱顶**（第 0 行）；
      * `paint()` 先回到箱顶（`\\r` + 上移 drawn-1 行），再逐行重画，结束时停在**最后一行**；
      * `close()` 同样先回箱顶，逐行擦干净，再回到箱顶，让后续 markdown 从箱顶接续。
    """

    def __init__(self, win=10, cols=None):
        self.win = int(win)
        self.cols = cols                  # None = 每次现取终端宽度
        self.buf = []
        self.line = ""                    # 残行（未遇到换行的部分）
        self.anchor = False               # 已预留箱区（保留旧属性名，语义=已 reserve）
        self.drawn = 0

    # --- 内部 ---
    def _cols(self):
        return self.cols or term_cols()

    def _up(self, n):
        """从当前行回到箱顶所需的上移字节（n = 上一帧画的行数）。"""
        return "\033[%dA" % (n - 1) if n > 1 else ""

    def _room(self):
        """一行正文可用列宽。末尾留 1 列：某些终端在最末列写字符就折行。"""
        return max(8, self._cols() - BAR_W - 1)

    # --- 对外 ---
    def reserve(self):
        """首次真正上屏前预留箱区：内容下方留 win 行，光标上移回箱顶。

        不再保存光标位置（`ESC[s` 有终端不认），paint/close 靠 `drawn` 相对回位。"""
        if self.anchor:
            return ""
        self.anchor = True
        self.drawn = 0
        # 预留行一律写 `\r\n`:有终端的 LF 只下移不回列(xterm 的 LNM 关着时就是),
        # 只发 `\n` 会让后续内容逐行右移 —— 正是"阶梯"的形状。多写一个 CR 零成本。
        return ("\033[?25l\033[?7l" + "\r\n" * self.win
                + ("\033[%dA" % (self.win - 1) if self.win > 1 else ""))

    def paint_full(self):
        """reserve + paint：调用方想"真的画到终端"时用它（单测仍用纯 paint）。"""
        return self.reserve() + self.paint()

    def close(self):
        """关箱：回箱顶，逐行整行擦除，再回箱顶，恢复光标与自动换行。"""
        if not self.anchor:
            return ""
        n = self.drawn
        out = ["\r" + self._up(n)]
        for i in range(n):
            if i:
                out.append("\r\033[1B")
            out.append("\033[2K")
        out.append("\r" + self._up(n))
        out.append("\033[?25h\033[?7h")
        self.buf = []
        self.line = ""
        self.drawn = 0
        self.anchor = False
        return "".join(out)

    def push(self, line):
        """入队，超窗口丢最旧。"""
        self.buf.append(line)
        if len(self.buf) > self.win:
            self.buf = self.buf[1:]

    def append(self, text):
        """等价 stream_box_append 的**拆行部分**（不重画；重画由调用方按节流决定）。

        返回本次新入队的行数，供调用方决定是否 paint。
        """
        self.line += text
        n = 0
        while "\n" in self.line:
            head, self.line = self.line.split("\n", 1)
            self.push(head)
            n += 1
        return n

    def paint(self):
        """整窗重绘，末尾带残行（若有）。返回应写出的字节。"""
        lines = list(self.buf) + ([self.line] if self.line else [])
        room = self._room()
        out = ["\r" + self._up(self.drawn)]
        for i, ln in enumerate(lines):
            if i:
                out.append("\r\033[1B")
            out.append("\033[2K" + BAR + _fit(ln, room))
        self.drawn = len(lines)
        return "".join(out)


def render_markdown(text):
    """整篇 markdown -> rich(ANSI)：调用包内的 mdrender(与 bash 同一组件)。
    缺 rich/文件时回退原文，不阻断输出。"""
    helper = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "wchat_py", "mdrender.py")
    try:
        from wchat_py.runtime import _runtime_fallback
        _py = _runtime_fallback()
    except Exception:
        _py = sys.executable
    try:
        p = subprocess.run([_py, helper], input=(text or ""),
                           capture_output=True, text=True)
        if p.returncode == 0 and p.stdout:
            return p.stdout
    except OSError:
        pass          # helper 起不来就回退原文(渲染是装饰,不阻断输出)
    return text or ""


def render_reason(text):
    """复刻 bash flush_thinking:灰色 ▸ + 每行两空格缩进 + dim(markdown 渲染后整体灰)。"""
    body = render_markdown(text or "")
    body = body.replace("\033[0m", "\033[0m\033[2m")   # 保持整段灰,rich 内部 reset 后补 dim
    lines = body.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    out = "\033[2m▸\033[0m\n"
    out += "".join("  \033[2m" + ln + "\n" for ln in lines)
    out += "\033[0m\n"
    return out


def tool_banner(name, preview):
    """等价 bash `tool_banner`：`printf ' \033[36m⏺\033[0m \033[1;36m%s\033[0m \033[2m·\033[0m %s\n'`。"""
    return " \033[36m⏺\033[0m \033[1;36m%s\033[0m \033[2m·\033[0m %s\n" % (name, preview)


def args_preview(args_json):
    """等价 bash 的 `jq -c '.'` + null→(参数为空) + >160 字符截断加 `…`。"""
    import json
    try:
        obj = json.loads(args_json) if args_json else None
    except Exception:
        obj = None
    if obj is None:
        return "(参数为空)"
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    if len(s) > 160:
        s = s[:160] + "…"
    return s

