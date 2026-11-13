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


# ── 思考文本的展示档位(`-tf=on` / `-tf=off`)──────────────────────────────────
# 默认**只展示两行**:第一行是灰色三角,第二行是思考的第一个非空行,其余用 `…` 省略。
# 完整的思考链照常累积、照常进 messages 回传给模型 —— 这里改的只是**屏幕占用**,
# 不是"少想"。`-tf=on`(或 WCHAT_THINK_FULL=1)回到整段渲染。
_THINK_ON = ("1", "on", "true", "yes", "y")
_THINK_OFF = ("0", "off", "false", "no", "n")


def think_full(value=None):
    """思考是否整段展示。`value=None` 时读 WCHAT_THINK_FULL(未设 = 两行)。"""
    v = os.environ.get("WCHAT_THINK_FULL", "") if value is None else value
    return str(v if v is not None else "").strip().lower() in _THINK_ON


def set_think_full(value):
    """把 on/off 写进环境变量；返回新的布尔状态,值不认识返回 None(不改动)。

    真值表只在这里定义一份:启动参数 `-tf=`、会话内 `-tf=`、`-md` 的显示都走它。
    分开写三份的下场是"on 的别名各认各的"——那种不一致没有任何报错。
    """
    v = str(value if value is not None else "").strip().lower()
    if v in _THINK_ON:
        os.environ["WCHAT_THINK_FULL"] = "1"
        return True
    if v in _THINK_OFF:
        os.environ["WCHAT_THINK_FULL"] = "0"
        return False
    return None


# ── shell 输出箱:**跟随 `-tf`**,不另设开关 ──────────────────────────────────
# 2026-12 用户明确要求:"不要给同一件事两个开关"。`-tf=off`(默认)时 shell 输出箱
# 只留收尾行(正文照常进上下文,只是不上屏);`-tf=on` 时多行展开。
# 所以这里**没有** shell_full():判断一律用 `think_full()`,真值表也只有那一份。


def _reason_brief(text):
    """两行档:灰色 ▸ + 第一个非空行(压成一行,超出宽度或还有内容都补 `…`)。

    取**原文**首行而不是"先渲染 markdown 再取第一行":rich 会给每行套 SGR,
    按显示宽度截断时一旦切在转义序列中间,颜色会漏到后面的正文上(踩过同款)。
    """
    first, more = "", False
    for ln in (text or "").splitlines():
        s = " ".join(ln.split())
        if not s:
            continue
        if not first:
            first = s
        else:
            more = True
            break
    if not first:
        return ""            # 只有空白:不画一个孤零零的三角
    limit = room_for("  ")   # 行首两格缩进
    line = first + ("…" if more else "")
    if display_width(line) > limit:
        line = _fit(line, max(1, limit - 1)) + "…"
    return "\033[2m▸\033[0m\n  \033[2m" + line + "\033[0m\n\n"


def render_reason(text, full=None):
    """思考块渲染。`full=None` 时按当前档位(见 think_full)。

    · 两行档(默认) → 灰色 ▸ + 首行,其余 `…` 省略;
    · 整段档(-tf=on) → 旧行为:逐行两空格缩进、markdown 渲染后整体灰。
    """
    if full is None:
        full = think_full()
    if not full:
        return _reason_brief(text)
    body = render_markdown(text or "")
    body = body.replace("\033[0m", "\033[0m\033[2m")   # 保持整段灰,rich 内部 reset 后补 dim
    lines = body.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    out = "\033[2m▸\033[0m\n"
    out += "".join("  \033[2m" + ln + "\n" for ln in lines)
    out += "\033[0m\n"
    return out


# ── 非正文输出的统一灰化(2026-11 用户报"画面杂乱") ────────────────────────────
# 约定:**只有助手正文用正常亮度**;工具横幅/工具输出/状态行/审批与拒绝提示一律 dim,
# 与"思考内容"同一个灰(`\033[2m`)。这样一屏里"我要读的东西"只有正文与提问,
# 其余都是背景信息。工具**名字**保留原来的蓝色(`\033[1;36m`)不动 —— 那是方法色。
GREY = "\033[2m"
ZERO = "\033[0m"

# 非正文状态行的统一前缀(2026-12 用户指定为**常置样式**)。
# 与 shell 输出箱的收尾行 `└─ 完成 · 共 N 行` 同一形状:一眼看出"这个工具块到这儿收口"。
# 它只在 `render.py` 定义一份 —— 文案散在多处各自手写前缀,迟早出现"有的带有的不带"。
#
# 缩进 3 列:横幅是 `⏺ ` 前缀(1 空格 + ⏺ + 1 空格),`└` 必须**对齐 ⏺ 之后第一个字母**,
# 也就是行列 3。用户给的样例:
#      ⏺ read_file · {…}
#        └─ 已读取 /path(2 行)
STATUS_PREFIX = "   └─ "

# 展开档(输出箱有 `│` 边框)的正文前缀:与 `└─` 同缩进,否则框的左沿会从第 0 列开始,
# 看起来像从上一段输出里"漏"出来的。
BOX_PREFIX = "   │ "


def room_for(prefix, slack=1):
    """`prefix` 之后还剩多少列可写(留 `slack` 列,避免在最后一列写字触发折行)。

    **不要再用"减一个常数"来估可用宽度。** 这里踩过一次:横幅前缀 ` ⏺ shell · `
    实际 11 列,而 `ellipsis` 里写的是 `term_cols() - 8` —— 于是每一条横幅都比终端
    宽 3 列,终端自动折行,`…` 落到**第二行**开头。看起来像"省略号位置不对",
    根因是宽度算错;而且它不报错、不崩,只有在真终端里用肉眼看才发现。
    判据必须由**实测前缀宽度**推出。
    """
    return max(8, term_cols() - display_width(prefix) - slack)


def ellipsis(text, limit=None):
    """压到一行并按显示宽度截断(超出补 `…`)。`limit=None` 取"带 1 列缩进的整行"。"""
    s = " ".join(str(text or "").split())
    if limit is None:
        limit = room_for(" ", slack=1)       # 行首 1 个空格 + 1 列余量
    if display_width(s) <= limit:
        return s
    return _fit(s, max(1, limit - 1)) + "…"


def dim(text, limit=None):
    """灰化一段非正文输出(压成一行;`limit=0` 表示**不截断**,允许终端自然折行)。

    为什么留 `limit=0`:拒绝**理由**是屏幕上唯一需要看清的东西,截一半等于没给
    (实测 80 列终端下 "提权命令(sudo/doas/su)在任何模式下都不自动执行。替代:把命令
    原样告诉用户…" 的处置建议正好被切掉)。工具输出/横幅预览则必须截 —— 那些可能几百行。
    """
    s = " ".join(str(text or "").split())
    if not s:
        return ""
    if limit != 0:
        s = ellipsis(s, limit)
    return GREY + s + ZERO


def dim_after(prefix, text, slack=1):
    """灰化 `text`,宽度正好塞进 `prefix` 之后的剩余列 —— 保证整行不折。

    用它替代 `prefix + dim(x)` 这种写法:后者不知道 prefix 有多宽,只能估,
    估错就是"折行 + 省略号跑到第二行"(见 `room_for` 的注释)。
    """
    return dim(text, room_for(prefix, slack))



def tool_banner(name, preview):
    """工具横幅:` ⏺ <方法名> · <一行灰预览>`。

    方法名保留原来的蓝色(`\\033[1;36m`,用户说"方法的蓝色不动");**预览一律灰化并压成
    一行**,宽度按**实测前缀宽度**算出来(见 `room_for`) —— 早期用"减 8"估算,
    结果每条横幅都比终端宽 3 列,终端折行,`…` 掉到第二行开头。
    完整命令仍在上下文里(进 messages 的是 arguments 原文,不是这行预览)。
    """
    head = " \033[36m⏺\033[0m \033[1;36m%s\033[0m \033[2m·\033[0m " % name
    return head + dim_after(head, preview) + "\n"


# ── 交互确认块(2026-12 用户要求:与 shell 工具块统一)─────────────────────────
# 旧形状是**另起一套**:`⚠ 需要人工确认` + `    命令:<整条命令>` + 红字提问。
# 用户指出两处毛病:①和 `⏺ shell · …` / `└─ 完成 · …` 的块不是一个形状;
# ②命令正文又摊一遍("上面的 shell 已经写过了"),把画面弄乱。
# 再后来去掉了"原因"行 —— 用户原话:"原因描述明显是给 AI 模型看的,并不是给人看的"。
# 确实:硬墙那段话术是"替代:把配置写进工作目录内的文件,或把内容打印出来交给用户
# 自行放置",那是**给模型的指令**,不是人做决定需要的材料。人只需要知道
# "哪条规则 + 命中了什么",这两样都在标题里。话术照旧通过工具返回值交给模型。
# 现在统一成:
#      ⏺ shell · 限时120秒 · cp /tmp/a.json ~/.ct/mcporter.json …
#        └─ 高危命令(规则 H5),命中: cp
#        └─ 是否执行?(必须逐字 yes,n/回车 = 拒绝):
# 红色**只染标题词**;`└─`、命中、提问一律 GREY(与"思考"同色)。
RED = "\033[1;31m"

# 硬墙话术末尾自带 `(命中: cp)` / `(命中目标: /path)`。这里只取那个片段给人看,
# 话术正文不上屏。`_MARK_RE` 仍然需要 —— 它是"命中"的唯一来源,不是"原因"。
_MARK_RE = re.compile(r"\s*\(命中(?:目标)?:\s*(.*?)\)\s*$")


def mark_of(text):
    """从硬墙/拒绝话术里取出 `(命中: xxx)` 的 xxx;没有则返回空串。

    屏幕只显示这个片段;话术正文(`text`)由调用方通过返回值交给模型。
    """
    m = _MARK_RE.search(str(text or ""))
    return m.group(1).strip() if m else ""


def _titled_line(title, mark=""):
    """`   └─ <红标题>[,命中: <片段>]` —— 拒绝提示与确认块的标题行共用。

    `mark` 超宽时按**实测**剩余宽度截断:折行会把 `└─` 顶到第 0 列,
    看起来像从上一段输出里漏出来的(路径类 mark 很容易超宽)。
    """
    mark = " ".join(str(mark or "").split())
    head = "%s%s%s%s%s" % (GREY, STATUS_PREFIX, RED, title, ZERO)
    if not mark:
        return head
    seg = ",命中: "
    avail = max(8, term_cols() - display_width(STATUS_PREFIX)
                - display_width(title) - display_width(seg) - 1)
    if display_width(mark) > avail:
        mark = _fit(mark, max(1, avail - 1)) + "…"
    return head + "%s%s%s%s" % (GREY, seg, mark, ZERO)


def reject_line(title, mark=""):
    """打一行拒绝提示(**没有提问**,程序已直接拒,不等人回答)。

    auto 档的硬墙拒绝、路径越界拒绝走这里。形状与 `confirm_block` 的标题行
    完全一样 —— 用户无论在人机对话还是无人值守下,看到的是同一种提示。
    **不印处置建议**:那是给模型的,走工具返回值。
    """
    sys.stdout.write("\033[2K" + _titled_line(title, mark) + "\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# 确认钩子:所有 yes/no 提问都经 confirm_block。在**全屏 TUI** 下 stdio 的 readline 不可用
# (stdin 归 Textual 事件循环),由 TUI 装一个 hook 改成弹 ModalScreen。非 TUI 时保持 None,
# 走原来的 readline(零改动)。
# ---------------------------------------------------------------------------
_CONFIRM_HOOK = [None]


def set_confirm_hook(fn):
    """装/卸(传 None)全屏 TUI 的确认钩子。
    fn(title, mark, question) -> bool;返回 True=批准。"""
    _CONFIRM_HOOK[0] = fn


# 单行文本输入钩子(用于需要输入**具体字符串**的交互,如开启 auto 的 "AUTO" 签字)。
# TUI 下 stdio 的 readline 不可用(stdin 归事件循环),由 TUI 装一个 InputModal;非 TUI 走 readline。
_LINE_HOOK = [None]


def set_line_hook(fn):
    _LINE_HOOK[0] = fn


def read_line(question="", choices=None):
    """读一行文本:hook 优先(TUI 弹输入框),否则退回到 sys.stdin.readline(非 TUI)。
    返回**不含行尾换行**的字符串(与原 readline 调用处 `(...readline()...).strip()` 兼容)。

    可选 `choices`(字符串列表):给出时 TUI 弹**分页列表选择框**(如加载历史编号),
    把候选条目列在输入框上方、支持 l/n 翻页;非 TUI 下忽略它(调用方照旧把列表打到 stdout)。"""
    if _LINE_HOOK[0] is not None:
        try:
            if choices is not None:
                return _LINE_HOOK[0](question, choices) or ""
            return _LINE_HOOK[0](question) or ""
        except Exception:
            return ""
    try:
        return (sys.stdin.readline() or "").rstrip("\n")
    except (KeyboardInterrupt, EOFError):
        return ""
    except Exception:
        return ""


def confirm_block(title, mark="", strict=False, ask="是否执行",
                  default_yes=False, no_word="拒绝"):
    """打印一条交互确认块并读入答案,返回是否批准。

    **所有 yes/no 提问都走这一份**(普通审批 / 硬墙升级 / read 档转人工 /
    高危路径 / 工作目录外访问 / 保存对话 / 保存技能 / MCP 授权 / 装技能 / 写清单)。
    以前是五套语法(`[y/N]`、`[Y/n]`、`(y/N)`、`(y 或 yes,n/回车 = 取消)`、`⚠ …?`),
    同一句"要不要"在不同地方长得不一样,用户没法形成肌肉记忆。

    `mark` 是"命中了什么"(规则片段 / 目标路径)。**没有"原因"参数**:
    给人看的是"哪条规则 + 命中什么",处置建议是给模型的,走返回值。
    路径类确认要把路径传进 `mark` —— 它没有上一行 `⏺` 横幅可参照,
    不给出路径人就不知道该放行哪个文件。

    回答口径由参数决定,**提问行如实写出**,不为了文案整齐而说谎:
      · `strict=True`   —— 必须**逐字** `yes`(硬墙口径:输 y 不算)
      · `default_yes=True` —— 回车即"是"(`Y/n` 语义,如"退出前保存对话")
      · 默认            —— `y`/`yes` 批准,回车是"否"
    `no_word` 只是把"否"换个更贴切的说法(取消 / 不保存),不改判定。
    """
    mark = " ".join(str(mark or "").split())
    sys.stdout.write("\033[2K" + _titled_line(title, mark) + "\n")

    if strict:
        tail = "(必须逐字 yes,n/回车 = %s): " % no_word
    elif default_yes:
        tail = "(Y 或 yes,n/回车 = 是): "
    else:
        tail = "(y 或 yes,n/回车 = %s): " % no_word
    # 动词统一收口:调用方写 "是否放行?" 或 "是否放行" 都得到同一个形状。
    ask = str(ask or "是否执行").strip().rstrip("?？")
    # 全屏 TUI:用 ModalScreen 弹窗取答案(stdin 归事件循环,不能 readline)。
    _hook = _CONFIRM_HOOK[0]
    if _hook is not None:
        sys.stdout.flush()
        try:
            return bool(_hook(title, mark, ask))
        except Exception:
            return False
    # 提问文字灰,但**行末就复位**:否则用户敲的 yes 会继承灰色回显。
    sys.stdout.write("%s%s%s%s%s" % (GREY, STATUS_PREFIX, ask, tail, ZERO))
    sys.stdout.flush()
    try:
        ans = (sys.stdin.readline() or "").strip()
    except (KeyboardInterrupt, EOFError):
        # Ctrl+C / 关掉 stdin 一律当"拒绝",不能让它冒泡成 traceback
        # (项目踩过:交互向导里 Ctrl+C 吐 traceback)。注意 KeyboardInterrupt
        # 继承 BaseException,**不是** Exception,单写 `except Exception` 捕不到。
        ans = ""
    except Exception:
        ans = ""
    sys.stdout.write("\n"); sys.stdout.flush()
    if strict:
        return ans == "yes"
    if default_yes:
        # 回车(=空)算"是";明确打 n/no 才算"否"。
        return ans == "" or ans.lower() in ("y", "yes")
    return ans.lower() in ("y", "yes")


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