#!/usr/bin/env python3
"""问终端自己:几个 wchat 流箱依赖的能力到底支不支持。

为什么需要它:流箱用 `ESC[s`/`ESC[u` 存/取锚点、`ESC[?7l` 关自动换行。用户在
Terminal.app 里看到阶梯式重复、在 Ghostty 里正常,但**光看字节流分不清是哪一项**。
这里不猜:直接发 DSR(`ESC[6n`)让终端回报光标行列,再用它验证"存/取光标"是否真的
回到了原处。

用法(在**目标终端**里跑,一次一个):
    python3 tests/termcap_probe.py

判读:每行 PASS/FAIL;末尾一行 `VERDICT ...` 直接复制给人看即可。
"""
import fcntl
import os
import struct
import sys
import termios
import time

CSI = "\033["
DECSC, DECRC = "\0337", "\0338"


class Tty:
    def __init__(self):
        self.fd = os.open("/dev/tty", os.O_RDWR)
        self.old = termios.tcgetattr(self.fd)
        new = termios.tcgetattr(self.fd)
        new[3] &= ~(termios.ICANON | termios.ECHO | termios.ISIG)
        new[6][termios.VMIN] = 0
        new[6][termios.VTIME] = 3
        termios.tcsetattr(self.fd, termios.TCSANOW, new)

    def restore(self):
        termios.tcsetattr(self.fd, termios.TCSANOW, self.old)

    def write(self, s):
        os.write(self.fd, s.encode())

    def size(self):
        try:
            r, c, _, _ = struct.unpack("HHHH", fcntl.ioctl(
                self.fd, termios.TIOCGWINSZ, b"\0" * 8))
            return (r or 24, c or 80)
        except OSError:
            return (24, 80)

    def read_reply(self) -> str:
        buf = b""
        end = time.time() + 1.5
        while time.time() < end:
            try:
                chunk = os.read(self.fd, 64)
            except OSError:
                break
            if not chunk:
                continue
            buf += chunk
            if buf.endswith(b"R"):
                break
        return buf.decode("latin-1", "replace")

    def pos(self):
        """DSR: 回报 (行, 列),1 基。无应答返回 None。"""
        self.write(CSI + "6n")
        rep = self.read_reply()
        i = rep.find(CSI)
        if i < 0 or "R" not in rep[i:]:
            return None
        body = rep[i + 2:rep.index("R", i)]
        try:
            r, c = body.split(";")
            return (int(r), int(c))
        except ValueError:
            return None


def main():
    t = Tty()
    rows, cols = t.size()
    res = {}
    try:
        t.write(CSI + "2J" + CSI + "H")          # 干净一屏
        t.write("wchat 终端能力探针  (%dx%d)  批量开关见末尾 VERDICT 行\r\n" % (rows, cols))

        # 1) CSI s / CSI u (SCP/RCP)
        t.write("\r\n测试 A: CSI s / CSI u(存/取光标)\r\n")
        t.write(CSI + "4;1H")                    # 停到第 4 行行首
        p0 = t.pos()
        t.write(CSI + "s" + "XXXX" + CSI + "u")
        p1 = t.pos()
        res["CSI s/u"] = bool(p0 and p1 and p0 == p1)
        t.write(CSI + "0K")

        # 2) DECSC / DECRC (ESC 7 / ESC 8)
        t.write("\r\n测试 B: ESC 7 / ESC 8(DECSC/DECRC)\r\n")
        t.write(CSI + "6;1H")
        q0 = t.pos()
        t.write(DECSC + "XXXX" + DECRC)
        q1 = t.pos()
        res["ESC 7/8"] = bool(q0 and q1 and q0 == q1)
        t.write(CSI + "0K")

        # 3) ESC[2K 是"整行擦除"还是只擦一半
        #    写法:第 8 行写 AAAA,回行首写 BB,然后光标移到第 3 列发 2K;
        #    若整行擦除,光标位置不变但该行内容全空——位置查不出来,只能靠下面第 4 项
        #    间接佐证,这里只记录终端是否接受该序列(不应答也算接受)。
        # 4) ESC[?7l 关自动换行
        t.write("\r\n测试 C: ESC[?7l(关自动换行)\r\n")
        t.write(CSI + "10;1H" + CSI + "?7l")
        t.write("a" * cols)                      # 正好写满一行
        r, c = (t.pos() or (0, 0))
        res["?7l"] = (r == 10 and c == cols)     # 没折行 = 光标停在末列
        t.write(CSI + "?7h" + CSI + "2K")

        # 5) 折行行为(?7h 下写满一行应折到下一行)
        t.write("\r\n测试 D: 正常自动换行\r\n")
        t.write(CSI + "12;1H")
        t.write("b" * (cols + 1))
        r, c = (t.pos() or (0, 0))
        res["autowrap"] = (r == 13)
        t.write(CSI + "2K")

        # 汇总:画在屏幕底部,方便截图
        t.write(CSI + "15;1H")
        t.write("──── 结果 ────\r\n")
        for k, v in res.items():
            t.write("  %-12s %s\r\n" % (k, "PASS" if v else "FAIL"))
        bad = [k for k, v in res.items() if not v]
        t.write("\r\nVERDICT rows=%d cols=%d %s\r\n"
                % (rows, cols, " ".join("%s=%s" % (k.replace(" ", "_"), "P" if v else "F")
                                        for k, v in res.items())))
        t.write("（缺哪项就是这项不认）\r\n" if bad else "全部支持。\r\n")
        t.write("\r\n按回车结束…")
        end = time.time() + 60
        while time.time() < end:
            try:
                chunk = os.read(t.fd, 8)
            except OSError:
                break
            if chunk:                          # 回车/任意键
                break
            if os.isatty(t.fd) is False:
                break
            time.sleep(0.1)
    finally:
        t.restore()
        t.write(CSI + "2J" + CSI + "H")
        os.close(t.fd)


if __name__ == "__main__":
    main()