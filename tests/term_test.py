#!/usr/bin/env python3
"""终端层地基自检：raw_mode 在正常/异常/非 tty 三种退出路径下都必须恢复 termios。

用 os.openpty 造一个真 tty 主从对，对从端进入 raw，检查退出后属性与进入前**逐项相同**。
末行 `RESULT total=N pass=N fail=0`，fail>0 退出码 1。
"""
import os
import sys
import termios

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wchat_py.term import raw_mode  # noqa: E402

p = f = 0
PENDIN = 0x20000000  # macOS 内核管理的瞬态位，tcsetattr 无法清，比较时掩掉


def norm(a):
    b = list(a)
    b[3] = b[3] & ~PENDIN
    return b


def ck(name, ok, detail=""):
    global p, f
    if ok:
        p += 1
        print("  ok   %s" % name)
    else:
        f += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


mfd, sfd = os.openpty()
try:
    # 1) 正常退出恢复
    before = termios.tcgetattr(sfd)
    with raw_mode(sfd):
        mid = termios.tcgetattr(sfd)
    after = termios.tcgetattr(sfd)
    ck("正常退出恢复", norm(before) == norm(after), "before!=after")
    ck("进入 raw 确实改了设置", mid != before, "raw 未生效")

    # 2) 异常退出恢复（最高风险路径）
    before = termios.tcgetattr(sfd)
    try:
        with raw_mode(sfd):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    after = termios.tcgetattr(sfd)
    ck("异常退出恢复", norm(before) == norm(after), "before!=after（终端会坏在 raw）")

    # 3) 非 tty：安全降级不抛
    r, w = os.pipe()
    try:
        with raw_mode(w):
            pass
        ck("非 tty 安全降级", True)
    except Exception as e:  # noqa: BLE001
        ck("非 tty 安全降级", False, repr(e))
    finally:
        os.close(r)
        os.close(w)
finally:
    os.close(mfd)
    os.close(sfd)

print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)