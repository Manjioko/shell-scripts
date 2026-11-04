#!/usr/bin/env python3
"""输入框形状闸门:上下横线必须**紧贴**输入行,提示行只活在框内。

为什么要有这道闸门:输入框改成 Claude Code 风格(横线 + `❯` + 提示行)之后,
`bottom_toolbar` 的落点是 prompt_toolkit 的默认高度策略决定的 —— 输入窗拿到的是裸
`Dimension()`(= 可延伸,吃光剩余屏幕),于是底部工具条被推到**屏幕最后一行**。
24 行终端里实测:输入行在第 2 行、底部横线在第 24 行,中间 21 行空白。
这个故障**不报错、不影响交回文本**,只在真终端里靠肉眼才看得见 —— 正是本项目
反复踩的"静默视觉缺陷",所以必须用"屏幕坐标"判,不能用字节流判。

判据(全部在真 pty 里跑一次输入助手,把自己渲染的帧喂给一个最小 VT 屏):
  1. 顶部横线宽度 = 终端列数 - 1(留 1 列,末列写字会折行);
  2. 顶部横线**下一行**就是 `❯ abc`(不能被空行隔开);
  3. 底部横线**紧贴**输入行下一行(这条就是上面那个故障的回归锁);
  4. 快捷键提示紧贴底部横线下一行,且只在活跃输入时出现;
  5. 提交后:两条横线之间只剩 `❯ hello`,提示行**不再重复**(历史里不刷屏);
  6. 提交后:结果文本出现在框下方;
  7. 超宽输入软折行后,底部横线仍紧跟折行(= 框不倒、不越界)。

用法:`python3 tests/inputbox_layout_test.py`(需要 /dev/ptmx;不联网、不依赖内容)
       `HELPER=/path/to/inputbox.py` 可换被测文件(变异验证用)
"""
import fcntl
import os
import re
import select
import struct
import sys
import termios
import time
import pty

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HELPER = os.environ.get("HELPER") or os.path.join(REPO, "wchat_py", "inputbox.py")
COLS, ROWS = 80, 24
RULE = "─"

p = f = 0


def ck(name, ok, detail=""):
    global p, f
    if ok:
        p += 1
        print("  ok   %s" % name)
    else:
        f += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


class Screen:
    """只实现输入框真会用到的那些动作(CSI 移动/擦除 + CR/LF);够用即可,不求完整 VT。"""

    def __init__(self, cols=COLS, rows=ROWS):
        self.cols, self.rows = cols, rows
        self.g = [[" "] * cols for _ in range(rows)]
        self.y = self.x = 0

    def _put(self, ch):
        if self.x >= self.cols:
            self.x = 0
            self.y = min(self.rows - 1, self.y + 1)
        self.g[self.y][self.x] = ch
        self.x += 1

    def feed(self, data):
        i = 0
        while i < len(data):
            c = data[i]
            if c == "\x1b":
                m = re.match(r"\x1b\[([0-9;?]*)([ -/]*)([@-~])", data[i:])
                if m:
                    prm, fch = m.group(1), m.group(3)
                    n = int(prm.split(";")[0]) if prm and prm.split(";")[0].isdigit() else 0
                    if fch == "A":
                        self.y = max(0, self.y - max(1, n))
                    elif fch == "B":
                        self.y = min(self.rows - 1, self.y + max(1, n))
                    elif fch == "C":
                        self.x = min(self.cols - 1, self.x + max(1, n))
                    elif fch == "D":
                        self.x = max(0, self.x - max(1, n))
                    elif fch == "G":
                        self.x = max(0, min(self.cols - 1, (n or 1) - 1))
                    elif fch in "Hf":
                        v = [int(t) if t.isdigit() else 1
                             for t in (prm.split(";") + ["1", "1"])[:2]]
                        self.y = max(0, min(self.rows - 1, v[0] - 1))
                        self.x = max(0, min(self.cols - 1, v[1] - 1))
                    elif fch == "J":
                        if n == 2:
                            self.g = [[" "] * self.cols for _ in range(self.rows)]
                            self.y = self.x = 0
                        elif n == 0:
                            for xx in range(self.x, self.cols):
                                self.g[self.y][xx] = " "
                            for yy in range(self.y + 1, self.rows):
                                self.g[yy] = [" "] * self.cols
                    elif fch == "K":
                        if n == 2:
                            self.g[self.y] = [" "] * self.cols
                        elif n == 0:
                            for xx in range(self.x, self.cols):
                                self.g[self.y][xx] = " "
                        elif n == 1:
                            for xx in range(0, self.x + 1):
                                self.g[self.y][xx] = " "
                    # 注意是 `i += m.end()`:m 是在 `data[i:]` 上匹配的,写成 `i = m.end()`
                    # 会在每个转义序列上原地打转(死循环,实测把闸门挂死)。
                    i += m.end()
                    continue
                m = re.match(r"\x1b[()][0-9A-Za-z]|\x1b[=>78]", data[i:])
                i += m.end() if m else 1
                continue
            if c == "\r":
                self.x = 0
            elif c == "\n":
                self.y = min(self.rows - 1, self.y + 1)
            elif c == "\b":
                self.x = max(0, self.x - 1)
            elif c >= " ":
                self._put(c)
            i += 1

    def lines(self):
        out = ["".join(row).rstrip() for row in self.g]
        while out and not out[-1]:
            out.pop()
        return out


def shot(keys, settle=1.2, cols=COLS, resize_to=None):
    """在真 pty 里起一次输入助手,写 keys,把渲染帧还原成屏幕文本。"""
    m, s = pty.openpty()
    fcntl.ioctl(s, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, cols, 0, 0))
    pid = os.fork()
    if pid == 0:                                   # 子进程:pty 当控制终端,吞掉 stdout
        os.close(m)
        os.setsid()
        fcntl.ioctl(s, termios.TIOCSCTTY, 1)
        for fd in (0, 1, 2):
            os.dup2(s, fd)
        os.chdir(REPO)
        os.execvpe(sys.executable,
                   [sys.executable, HELPER, "--no-commands", "--history", "",
                    "--clip-dir", ""],
                   dict(os.environ, TERM="xterm-ghostty", COLUMNS=str(cols),
                        LINES=str(ROWS), PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8"))
        os._exit(127)
    os.close(s)
    # 屏幕缓冲按**两种宽度里较宽的那个**建:否则 resize 变宽之后画出来的长横线
    # 会被模拟器自己折行,看起来像"残留半截"(我曾据此误判了一次)。
    scr = Screen(cols=max(cols, resize_to or cols))
    frames = bytearray()

    def pump(dur):
        end = time.time() + dur
        while time.time() < end:
            r, _, _ = select.select([m], [], [], 0.05)
            if not r:
                continue
            try:
                data = os.read(m, 65536)
            except OSError:
                return
            if not data:
                return
            frames.extend(data)
            scr.feed(data.decode("utf-8", "replace"))
            # 不答 CPR 的话 prompt_toolkit 会打警告并重排(见 tty_osc_input_test.sh 的注释)
            for _ in range(data.count(b"\x1b[6n")):
                os.write(m, b"\x1b[2;1R")

    try:
        pump(1.0)
        if keys:
            os.write(m, keys.encode())
        pump(settle)
        if resize_to:
            # 真改窗口尺寸:TIOCSWINSZ 会让子进程收到 SIGWINCH,
            # prompt_toolkit 走它自己的 erase → 请求 CPR → 重绘 那条路。
            fcntl.ioctl(m, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, resize_to, 0, 0))
            pump(1.5)
    finally:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(pid, 0)
        except OSError:
            pass
        try:
            os.close(m)
        except OSError:
            pass
    return scr.lines(), frames.decode("utf-8", "replace")


def is_rule(ln, cols=COLS):
    return bool(ln) and set(ln) == {RULE} and len(ln) == cols - 1


def find_rules(lines):
    return [i for i, ln in enumerate(lines) if is_rule(ln)]


def row_of(lines, needle):
    for i, ln in enumerate(lines):
        if needle in ln:
            return i
    return -1


print("=== 1. 活跃输入:两条横线夹住输入行,提示紧贴其下 ===")
typing, typing_raw = shot("abc")
rules = find_rules(typing)
ck("有两条横线(顶 + 底)", len(rules) == 2, repr(rules) + " / " + repr(typing))
if len(rules) == 2:
    top, bot = rules
    ck("顶部横线宽度 = 终端列数 - 1(末列不写字)",
       len(typing[top]) == COLS - 1, "%d 列" % len(typing[top]))
    ck("顶部横线下一行就是输入行", bot - top == 2, "间距 %d" % (bot - top))
    ck("输入行是 `❯ abc`", typing[top + 1].strip() == "❯ abc", repr(typing[top + 1]))
    hint_i = row_of(typing, "回车发送")
    ck("提示行紧贴底部横线下一行", hint_i == bot + 1, "hint@%d rule@%d" % (hint_i, bot))
    ck("提示行讲的是发送/续行/选文件",
       all(k in typing[hint_i] for k in ("回车发送", "续行", "选文件")),
       repr(typing[hint_i] if hint_i >= 0 else ""))

print("=== 2. 提交后:框闭合、提示不再重复、结果在框下 ===")
done, done_raw = shot("hello\r", settle=1.5)
drules = find_rules(done)
ck("提交后仍留下完整的框(两条横线)", len(drules) == 2, repr(drules) + " / " + repr(done))
if len(drules) == 2:
    t, b = drules
    ck("两条横线之间只剩 `❯ hello`", b - t == 2 and done[t + 1].strip() == "❯ hello",
       repr(done[t + 1] if b - t == 2 else done))
    ck("框下方能看到交回的文本", any("hello" in ln for ln in done[b + 1:]),
       repr(done[b + 1:]))
ck("提示行不重复进历史(历史的框里只有两条横线,没有 hint)",
   sum(1 for ln in done if "回车发送" in ln) == 0,
   repr([ln for ln in done if "回车发送" in ln]))

print("=== 3. 超宽输入:软折行后横线仍紧跟,框不倒 ===")
wide, _wide_raw = shot("y" * 120)
wrules = find_rules(wide)
ck("超宽输入下仍是两条横线", len(wrules) == 2, repr(wrules) + " / " + repr(wide))
if len(wrules) == 2:
    t, b = wrules
    ym = [i for i, ln in enumerate(wide) if "y" in ln]
    ck("折行内容都在两横线之间", bool(ym) and all(t < i < b for i in ym), repr(ym))
    ck("折行占两行(120 列 / 79 列可用)", len(ym) == 2, repr(ym))
    ck("折行没有把横线挤出行外(底部横线紧跟最后一行正文)", b == ym[-1] + 1,
       "rule@%d last@%d" % (b, ym[-1]))

def _last_sgr_before(raw, idx):
    """取 idx 之前**最后一个带属性**的 SGR(如 `\x1b[0;2m`),用来判"这段字是什么颜色"。

    只看最近的 `\x1b[<...>m`:屏幕清理/光标移动那类 CSI 不改变颜色,跳过它们。
    见 2026-12 用户报的两个配色 bug —— 框线/提示符不是灰的、回答完之后下边框变白;
    两者都只有看字节里的 SGR 才能判,字节流里"看起来"完全正常。
    """
    best = ""
    for mt in re.finditer(r"\x1b\[([0-9;]*)m", raw[:idx]):
        best = mt.group(1)
    return best


def _is_dim(sgr):
    """SGR 参数里带 2 = dim(灰)。`0;2` / `2` 都算;`0` 或 `0;38;5;…` 不算。"""
    return "2" in [x for x in sgr.split(";") if x]


print("=== 4. 配色:框线 / `❯` / 占位提示一律 dim(= 思考内容那一档灰) ===")
# 用户 2026-12 报的四条:横线不是灰、占位提示不是灰、`❯` 是蓝的、AI 回答完之后
# **下边框变白**(那条线是 `_close_box` 手写的,样式表管不到 → 裸写就是终端默认前景)。
_rules = [mt.start() for mt in re.finditer("─{20,}", typing_raw)]
ck("活跃输入:抓到多条横线(顶+底,含重绘)", len(_rules) >= 2, str(len(_rules)))
_bad = [i for i in _rules if not _is_dim(_last_sgr_before(typing_raw, i))]
ck("活跃输入:每条横线前面都是 dim(没有裸写的默认色)",
   not _bad, "第 %d 条前面的 SGR = %r" % (len(_rules) - len(_bad) + 1,
                                          _last_sgr_before(typing_raw, _bad[0])) if _bad else "")
_prompts = [mt.start() for mt in re.finditer("❯", typing_raw)]
ck("`❯` 也是 dim(不是蓝色)", bool(_prompts)
   and all(_is_dim(_last_sgr_before(typing_raw, i)) for i in _prompts),
   repr([_last_sgr_before(typing_raw, i) for i in _prompts]))
# 占位提示:`❯` 之后紧跟的那段字必须与 `❯` 同一个属性(样式没被另起一段改成默认色)
_i = typing_raw.find("问点什么")
ck("占位提示是 dim(与 `❯` 同属性,深色主题下不再是白的)",
   _i > 0 and _is_dim(_last_sgr_before(typing_raw, _i)),
   repr(_last_sgr_before(typing_raw, _i)) if _i > 0 else "placeholder 没上屏")
_drules = [mt.start() for mt in re.finditer("─{20,}", done_raw)]
_bad2 = [i for i in _drules if not _is_dim(_last_sgr_before(done_raw, i))]
ck("提交后补画的那条横线也是 dim(`_close_box` 的回归锁:裸写=白)",
   bool(_drules) and not _bad2,
   "全 %d 条,裸写的 %r" % (len(_drules), _last_sgr_before(done_raw, _bad2[0])) if _bad2 else "")

print("=== 5. 横线宽度必须取自**本帧**:不是活查询(deterministic,不靠 resize) ===")
# resize 残影的根因就这一条:横线宽度若来自 `get_app().output.get_size()` 这种活查询,
# 拖拽改变窗口大小时它会在**同一帧内**变化,而这一帧是按 prompt_toolkit 缓存的 size
# 渲染的 —— 宽出去那截擦不掉(终端只擦到当前宽度),回流后就是"不规则残影"。
# 实测现场:一次 resize 里上下两条横线宽度不同(约 200 列 vs 170 列)。
# 这里直接在 80 列的终端里用**别的宽度**调控件,断言它老老实实按给定宽度画:
sys.path.insert(0, REPO)
import wchat_py.inputbox as IB  # noqa: E402

for w in (37, 53, 120):
    ctl = IB._width_control(lambda width: [("class:border", IB._rule(width))])
    ctl.create_content(w, 1)
    _txt = ctl.text[0][1]
    ck("width=%d → 横线正好 %d 列(与本机终端 %d 列无关)" % (w, w - 1, COLS),
       _txt == "─" * (w - 1), "len=%d" % len(_txt))
ck("_rule(1) 不产生空串(极窄终端也不塌)",
   IB._rule(1) == "─" and IB._rule(0) == "─", repr((IB._rule(1), IB._rule(0))))
# 结构锁:活查询那条路只许留在"框外补底边"(`_close_box`)一处。
# 数全文件出现次数:1 个定义 + 1 个 `_close_box` 调用 = 2。多出来的必然是新加的
# 框内调用 —— 那正是残影的根因,必须挡住(做变异时它逃过一版只查 `_install_rules`
# 的锁,因为回退写在 `_top()` 里)。
# ⚠ 读**被测文件**(HELPER),不是硬编码的正式文件 —— 否则换 HELPER 跑变异时,
# 这条锁永远在检查没被改过的那份,变异一律"活着"(实测踩过)。
_src = open(HELPER, encoding="utf-8").read()
_n = _src.count("_rule_text(")
ck("活查询(_rule_text)只留给框外补底边,框内一律按帧宽",
   _n == 2, "出现 %d 次:%r" % (_n, [ln.strip() for ln in _src.splitlines()
                                    if "_rule_text(" in ln]))

print("=== 6. 窄终端(40 列)同样不裂 ===")
narrow, _narrow_raw = shot("abc", cols=40)
nrules = [i for i, ln in enumerate(narrow) if is_rule(ln, cols=40)]
ck("40 列下横线宽度 = 39", all(len(narrow[i]) == 39 for i in nrules),
   repr([narrow[i] for i in nrules]))
ck("40 列下仍是紧贴的两条横线", len(nrules) == 2 and (nrules[1] - nrules[0]) == 2,
   repr(nrules) + " / " + repr(narrow))

print("=== 7. 改窗口尺寸后:两条横线同宽且等于新宽度,不留残影 ===")
# 用户实报:"resize 终端后输入框上部出现不规则样式残影"。可证的那一半是**上下两条
# 横线宽度不一致**(实测 200 列 vs 170 列)—— 只可能来自"同一帧里两次活查询拿到
# 不同宽度";宽出去那截终端擦不到(ESC[K 只擦到当前宽度),叠加回流就成了残影。
# 这里真改 pty 的窗口尺寸(子进程收 SIGWINCH),再判最终屏幕。
for (c0, c1) in ((100, 60), (60, 100)):
    lines, _raw = shot("hi", settle=0.8, cols=c0, resize_to=c1)
    rs = [i for i, ln in enumerate(lines) if is_rule(ln, cols=c1)]
    ck("resize %d→%d:仍是两条横线,且都等于新宽度 %d" % (c0, c1, c1 - 1),
       len(rs) == 2, repr([(i, len(lines[i])) for i, ln in enumerate(lines) if ln.startswith("─")]))
    ck("resize %d→%d:上下两条同宽(不许上宽下窄)" % (c0, c1),
       len({len(lines[i]) for i in rs}) <= 1, repr([len(lines[i]) for i in rs]))
    # 旧宽度那截必须被清掉:整屏不许出现比当前宽度更长的"─"串
    over = [(i, len(ln)) for i, ln in enumerate(lines)
            if ln and set(ln.strip()) == {RULE} and len(ln) > c1 - 1]
    ck("resize %d→%d:没有超出新宽度的旧横线残留" % (c0, c1), not over, repr(over))
    ck("resize %d→%d:输入行与提示行都还在(框没被 resize 弄丢)" % (c0, c1),
       any("❯" in ln for ln in lines) and any("回车发送" in ln for ln in lines), repr(lines))

print("=== 8. resize 残影的补偿量(_reflow_extra)与接线 ===")
# 第二个根因:终端把**旧**的长横线按新宽度重排 → 箱子实际变高,而 prompt_toolkit
# 记的是上一帧的逻辑行数,擦屏时上移不够 → 上方剩一条擦不到的旧横线。
# 这里先钉"算出来的补偿行数",再钉"这个补偿真的接到了 renderer.erase 上"。
from types import SimpleNamespace  # noqa: E402

ck("100→60、光标在 `❯` 行:多折出 1 行", IB._reflow_extra([99, 3, 99, 40], 1, 60) == 1,
   repr(IB._reflow_extra([99, 3, 99, 40], 1, 60)))
ck("100→40:多折出 2 行", IB._reflow_extra([99, 3, 99, 40], 1, 40) == 2,
   repr(IB._reflow_extra([99, 3, 99, 40], 1, 40)))
ck("变宽(60→100)不需要补偿", IB._reflow_extra([59, 3, 59, 40], 1, 100) == 0,
   repr(IB._reflow_extra([59, 3, 59, 40], 1, 100)))
ck("空行按 1 行算,不会算成 0", IB._reflow_extra([0], 0, 60) == 0,
   repr(IB._reflow_extra([0], 0, 60)))
ck("不合法输入不炸(宽度 0)", IB._reflow_extra([99], 0, 0) >= 0, "ok")


class _FakeCell:
    def __init__(self, ch=" "):
        self.char = ch


class _FakeScreen:
    def __init__(self, lens):
        self.height = len(lens)
        self.data_buffer = [{x: _FakeCell("x") for x in range(n)} if n else {} for n in lens]


class _FakeOut:
    def __init__(self, cols):
        self.cols = cols
        self.moves = []

    def get_size(self):
        return SimpleNamespace(columns=self.cols)

    def cursor_up(self, n):
        self.moves.append(n)

    def flush(self):
        pass


class _FakeRenderer:
    def __init__(self, cols, old_cols, lens, y=1):
        self.output = _FakeOut(cols)
        self._last_size = SimpleNamespace(columns=old_cols)
        self._last_screen = _FakeScreen(lens)
        self._cursor_pos = SimpleNamespace(y=y)
        self.erased = 0

    def erase(self, leave_alternate_screen=True):
        self.erased += 1


class _FakeSession:
    def __init__(self, r):
        self.app = SimpleNamespace(renderer=r)


_r = _FakeRenderer(60, 100, [99, 3, 99, 40])
ck("_resize_guard 接得上", IB._resize_guard(_FakeSession(_r)) is True)
_r.erase()
ck("erase 前先多上移 1 行(残影那一截)", _r.output.moves == [1], repr(_r.output.moves))
ck("原 erase 仍然被调用", _r.erased == 1)
_r2 = _FakeRenderer(100, 60, [59, 3, 59, 40])
IB._resize_guard(_FakeSession(_r2))
_r2.erase()
ck("变宽时不多上移(不吃历史)", _r2.output.moves == [], repr(_r2.output.moves))

print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)