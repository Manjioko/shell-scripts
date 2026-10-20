#!/usr/bin/env python3
"""termwin.capture 自检：Python 跑命令 → clean_line → window_output，确定性验证。

用 printf 造 3 行；验证 full_output、display 窗口、clean_line 剥 ANSI。
末行 RESULT total=N pass=N fail=0。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wchat_py.termwin import capture  # noqa: E402

p = f = 0


def ck(name, ok, detail=""):
    global p, f
    if ok:
        p += 1
        print("  ok   %s" % name)
    else:
        f += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


# 1) 三行输出，head 足够大 -> full = 三行
disp, full, rc = capture(["/usr/bin/printf", "a\nb\nc\n"], win=2, head_n=50, buf_size=300)
ck("rc=0", rc == 0)
ck("full 三行", full == "a\nb\nc\n", repr(full))
ck("display 取最近 win=2 行", disp == ["b", "c"], repr(disp))

# 2) ANSI/退格被 clean_line 剥掉
disp, full, rc = capture(["/usr/bin/printf", "x\x1b[31mRED\x1b[0m\b!\n"], win=10)
ck("ANSI 剥离", full == "xRED!\n", repr(full))

print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)

