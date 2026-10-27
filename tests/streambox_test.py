#!/usr/bin/env python3
"""流箱行为闸门:在"终端能力有缺陷"的屏幕上,流箱还画得对不对。

为什么要有这道闸门:流箱原来靠 `ESC[s`/`ESC[u` 存/取锚点、靠 `ESC[?7l` 关自动换行。
这两样都不是所有终端都认 —— 用户在 macOS 自带 Terminal.app 里看到"同一句话每行一遍、
每行缩进递增"的阶梯,同一份代码在 Ghostty 里正常。字节流只有一份,看不出差别,
所以这里用**屏幕模型**判结果:同一串字节喂给"全支持 / 不认存光标 / 不认关换行 /
两样都不认"四种终端,最终屏幕必须一模一样、只能有一份正文。

判据(每条都能真红):
  1. 正文只出现一次(阶梯形会把同一句重复多遍)
  2. 所有箱行都从第 0 列开始(阶梯形的标志是箱线右移)
  3. close() 后屏幕上不留箱内文字(擦不净会留残行)
  4. 四种终端能力的最终屏幕逐行一致
"""
import os
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wchat_py.render import StreamBox  # noqa: E402

ROWS, COLS = 24, 80
BAR_CH = "│"


def _w(ch):
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


class Screen:
    """够用的 VT 模型:只实现流箱和 rich 输出会用到的序列。

    三个可关的能力,对应现实中确实有终端不认的东西:
      csi_su  —— CSI s / CSI u (SCP/RCP 存/取光标);Terminal.app 系有不认的
      decawm  —— ESC[?7l 关自动换行
      onlcr   —— tty 层把 \\n 翻成 \\r\\n;raw 模式下不翻,是"阶梯"的经典成因
    """

    def __init__(self, csi_su=True, decawm=True, onlcr=True):
        self.g = [[" "] * COLS for _ in range(ROWS)]
        self.r = self.c = 0
        self.saved = (0, 0)
        self.csi_su = csi_su
        self.awm_ok = decawm      # 是否**认** ESC[?7l/?7h
        self.wrap = True          # 自动换行当前状态(默认开)
        self.onlcr = onlcr
        self.pending_wrap = False

    # --- 基础动作 ---
    def _scroll(self):
        self.g.pop(0)
        self.g.append([" "] * COLS)

    def _lf(self):
        self.r += 1
        if self.r >= ROWS:
            self._scroll()
            self.r = ROWS - 1

    def _put(self, ch):
        w = _w(ch)
        if w == 0:
            return
        if self.pending_wrap:
            self.pending_wrap = False
            if self.wrap:
                self.c = 0
                self._lf()
        if self.c + w > COLS:
            if not self.wrap:
                self.g[self.r][COLS - 1] = ch      # 不折行:盖在末列
                self.c = COLS - 1
                return
            self.c = 0
            self._lf()
        self.g[self.r][self.c] = ch
        if w == 2 and self.c + 1 < COLS:
            self.g[self.r][self.c + 1] = "\0"      # 宽字符占位,输出时丢掉
        self.c += w
        if self.c >= COLS:
            self.c = COLS - 1
            self.pending_wrap = True

    def feed(self, s):
        i = 0
        while i < len(s):
            ch = s[i]
            if ch == "\x1b":
                j = i + 1
                if j < len(s) and s[j] == "[":
                    k = j + 1
                    while k < len(s) and not ("@" <= s[k] <= "~"):
                        k += 1
                    if k >= len(s):
                        return
                    self._csi(s[j + 1:k], s[k])
                    i = k + 1
                    continue
                if j < len(s) and s[j] in "78":
                    if s[j] == "7":
                        self.saved = (self.r, self.c)
                    else:
                        self.r, self.c = self.saved
                        self.pending_wrap = False
                    i = j + 1
                    continue
                i = j + 1
                continue
            if ch == "\n":
                if self.onlcr:
                    self.c = 0
                self._lf()
                self.pending_wrap = False
            elif ch == "\r":
                self.c = 0
                self.pending_wrap = False
            elif ch == "\b":
                self.c = max(0, self.c - 1)
            else:
                self._put(ch)
            i += 1

    def _csi(self, p, f):
        if f == "s":
            if self.csi_su:
                self.saved = (self.r, self.c)
        elif f == "u":
            if self.csi_su:
                self.r, self.c = self.saved
                self.pending_wrap = False
        elif f == "K":
            n = int(p or 0)
            if n == 2:
                self.g[self.r] = [" "] * COLS
            elif n == 0:
                for c in range(self.c, COLS):
                    self.g[self.r][c] = " "
            elif n == 1:
                for c in range(0, min(self.c + 1, COLS)):
                    self.g[self.r][c] = " "
        elif f in "ABCD":
            n = int(p or 1)
            if f == "A":
                self.r = max(0, self.r - n)
            elif f == "B":
                self.r = min(ROWS - 1, self.r + n)
            elif f == "C":
                self.c = min(COLS - 1, self.c + n)
            else:
                self.c = max(0, self.c - n)
            self.pending_wrap = False
        elif f in "Hf":
            parts = (p or "1;1").split(";")
            self.r = max(0, min(ROWS - 1, int(parts[0] or 1) - 1))
            self.c = max(0, min(COLS - 1, int(parts[1] or 1) - 1))
            self.pending_wrap = False
        elif f in "hl" and p.startswith("?"):
            if "7" in p and self.awm_ok:
                self.wrap = (f == "h")
            if f == "h":
                self.pending_wrap = False

    # --- 读结果 ---
    def lines(self):
        return ["".join(row).replace("\0", "").rstrip() for row in self.g]

    def text(self):
        return "\n".join(self.lines())


def scenario(csi_su=True, decawm=True, onlcr=True):
    """一次真实用法:思考先流式到达(同一句被反复重绘),再给正文。"""
    sc = Screen(csi_su=csi_su, decawm=decawm, onlcr=onlcr)
    sc.feed("$ wchat\n")
    box = StreamBox(win=6)
    parts = ["The user says 你好 (hello). ", "Just respond warmly ",
             "and briefly, no tools needed.", "\n",
             # 超过一屏宽度的一行:终端若不认 ?7l 就会折行,行数记账随之错位
             "这一行故意写得很长" * 8, "\n", "你好！有什么可以帮你的吗？"]
    for p in parts:
        box.append(p)
        sc.feed(box.paint_full())
    sc.feed(box.close())
    sc.feed("\n你好！有什么可以帮你的吗？\n")
    return sc


def count(text, needle):
    n, i = 0, 0
    while True:
        i = text.find(needle, i)
        if i < 0:
            return n
        n += 1
        i += len(needle)


def main():
    p = f = 0

    def ck(name, ok, detail=""):
        nonlocal p, f
        if ok:
            p += 1
            print("  ok   %s" % name)
        else:
            f += 1
            print("  FAIL %s%s" % (name, ("  ← " + detail) if detail else ""))

    profiles = {
        "全支持(Ghostty)": dict(),
        "不认 CSI s/u": dict(csi_su=False),
        "不认 ESC[?7l": dict(decawm=False),
        "两样都不认(Terminal.app 形状)": dict(csi_su=False, decawm=False),
    }
    shots = {}
    for name, kw in profiles.items():
        sc = scenario(**kw)
        shots[name] = sc.lines()
        t = sc.text()
        ck("%s: 正文只出现一次" % name, count(t, "你好！有什么可以帮你的吗？") == 1,
           "出现 %d 次" % count(t, "你好！有什么可以帮你的吗？"))
        ck("%s: 思考句不重复" % name, count(t, "no tools needed") <= 1,
           "出现 %d 次" % count(t, "no tools needed"))
        # 阶梯的两个可观测特征:箱线右移,以及"行首出现空白"(落点漂了)
        drift = [ln for ln in sc.lines() if ln.strip() and ln != ln.lstrip()]
        ck("%s: 无右移行" % name, not drift,
           "缩进行数 %d,例如 %r" % (len(drift), drift[0][:50] if drift else ""))
        ck("%s: 关闭后不留箱内残字" % name,
           "no tools needed" not in "\n".join(sc.lines()[-4:]),
           "箱内容疑似残留")
    base = shots["全支持(Ghostty)"]
    for name, shot in shots.items():
        if name == "全支持(Ghostty)":
            continue
        ck("%s: 屏幕与全支持逐行一致" % name, shot == base,
           "首个不同行 %r vs %r" % next(((a, b) for a, b in zip(shot, base) if a != b),
                                        ("", "")))

    print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
    if f:
        print("---- 全支持屏幕 ----")
        for i, ln in enumerate(shots["全支持(Ghostty)"]):
            if ln:
                print("%2d|%s" % (i, ln))
        print("---- 两样都不认屏幕 ----")
        for i, ln in enumerate(shots["两样都不认(Terminal.app 形状)"]):
            if ln:
                print("%2d|%s" % (i, ln))
    return 0 if f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

