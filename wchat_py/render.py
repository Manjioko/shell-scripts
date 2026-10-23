#!/usr/bin/env python3
"""B 阶段 1c：流式渲染（**精确复刻** bash 的 stream_box_* 三件套）。

为什么单独成模块：1c 里其余部分都是把已有 Python 件串起来，**只有渲染需要逐字节复刻**，
而且它有三段（环形缓冲 / 残行 / 整窗重绘），出错点密集。

bash 原文（`wchat.sh` 3087–3139）：
```bash
stream_box_push()  { stream_buf+=("$1"); (( ${#stream_buf[@]} > STREAM_WIN )) && stream_buf=("${stream_buf[@]:1}"); }
stream_box_paint() {
    (( stream_open )) || return 0
    (( stream_anchor )) || stream_box_reserve
    printf '\\033[u'
    for i in "${stream_buf[@]}"; do printf '\\033[2K\\033[2m│\\033[0m %s\\r\\033[1B' "$i"; done
    stream_drawn=${#stream_buf[@]}
    if [[ -n "$stream_line_buf" ]]; then
        printf '\\033[2K\\033[2m│\\033[0m %s' "$stream_line_buf"
    fi
}
stream_box_append() {
    stream_line_buf+="$1"
    while [[ "$stream_line_buf" == *$'\\n'* ]]; do 切行 -> push; done
    节流: now - last_paint > STREAM_THROTTLE => paint
}
```

**节流**（`STREAM_THROTTLE` + `EPOCHREALTIME`）**不在本模块**：重绘次数依赖墙钟，属调用方的策略；
本模块只负责"给定缓冲状态，paint 应该吐出什么字节"——这样它才能被确定性地单测与比对。
"""
import os
import re
import subprocess
import sys

BAR = "\033[2m│\033[0m "


class StreamBox:
    """环形缓冲 + 残行 + 整窗重绘。`paint()` 是**纯函数**（无 IO、不看时钟）。"""

    def __init__(self, win=10):
        self.win = int(win)
        self.buf = []
        self.line = ""          # 残行（未遇到换行的部分）
        self.anchor = False
        self.drawn = 0

    def reserve(self):
        """首次真正上屏前预留箱区(等价 stream_box_reserve):在内容下方留 win 行,
        光标上移回箱顶并保存锚点 \\033[s。缺了它 paint 的 \\033[u 会跳到未知位置,
        表现为"回复把屏幕上原有内容清空/覆盖"(历史现象)。"""
        if self.anchor:
            return ""
        self.anchor = True
        # 关自动换行 + 隐光标(等价 stream_box_open):长行不折行,行数才可数;
        # 否则终端自动折行 -> drawn 计错 -> 重画重复、close 擦不净(用户实测)。
        return ("\033[?25l\033[?7l" + ("\n" * self.win)
                + ("\033[%dA" % (self.win - 1)) + "\033[s")

    def paint_full(self):
        """reserve + paint:调用方想"真的画到终端"时用它(单测仍用纯 paint)。"""
        return self.reserve() + self.paint()

    def close(self):
        """关箱(等价 stream_box_close):回锚点,逐行 2K 擦除,再回锚点,
        让后续 markdown 从箱顶接续;恢复光标。返回应写出的字节。"""
        if not self.anchor:
            return ""
        out = ["\033[u"]
        for _ in range(self.drawn):
            out.append("\033[2K\033[1B")
        out.append("\033[u\033[?25h\033[?7h")
        self.buf = []
        self.line = ""
        self.drawn = 0
        self.anchor = False
        return "".join(out)

    def push(self, line):
        """等价 stream_box_push：入队，超窗口丢最旧。"""
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
        """等价 stream_box_paint：整窗重绘，末尾带残行（若有）。返回应写出的字节。"""
        out = ["\033[u"]
        for ln in self.buf:
            out.append("\033[2K" + BAR + ln + "\r\033[1B")
        if self.line:
            out.append("\033[2K" + BAR + self.line)
        self.drawn = len(self.buf) + (1 if self.line else 0)
        return "".join(out)



def render_markdown(text):
    """整篇 markdown -> rich(ANSI)：调用项目既有 render_helper.py(与 bash 同一组件)。
    缺 rich/文件时回退原文，不阻断输出。"""
    helper = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "render_helper.py")
    try:
        p = subprocess.run([sys.executable, helper], input=(text or ""),
                           capture_output=True, text=True)
        if p.returncode == 0 and p.stdout:
            return p.stdout
    except OSError:
        pass
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