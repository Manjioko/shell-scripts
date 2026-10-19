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
import re

BAR = "\033[2m│\033[0m "


class StreamBox:
    """环形缓冲 + 残行 + 整窗重绘。`paint()` 是**纯函数**（无 IO、不看时钟）。"""

    def __init__(self, win=10):
        self.win = int(win)
        self.buf = []
        self.line = ""          # 残行（未遇到换行的部分）

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
        return "".join(out)

