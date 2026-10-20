#!/usr/bin/env python3
"""termpty.pty_run 自检：pty 下的输出捕获、超时进程组杀、rc 透传。

末行 RESULT total=N pass=N fail=0。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wchat_py.termpty import pty_run  # noqa: E402

p = f = 0


def ck(name, ok, detail=""):
    global p, f
    if ok:
        p += 1
        print("  ok   %s" % name)
    else:
        f += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


# 1) pty 下跑 printf，三行都抓到，rc=0，不超时
disp, full, rc, to = pty_run(["/usr/bin/printf", "a\nb\nc\n"], timeout=10)
ck("printf rc=0", rc == 0, "rc=%r" % rc)
ck("printf 未超时", to is False)
ck("三行都在", all(x in full for x in ("a\n", "b\n", "c\n")), repr(full))
ck("display 非空", len(disp) >= 1, repr(disp))

# 2) 超时：sleep 5，timeout=1 -> timed_out=True，且进程被整体杀掉（函数返回不卡）
t0 = time.monotonic()
disp, full, rc, to = pty_run(["/bin/sleep", "5"], timeout=1.0)
dt = time.monotonic() - t0
ck("sleep 超时命中", to is True, "to=%r" % to)
ck("超时后及时返回(<3s)", dt < 3.0, "%.2fs" % dt)

# 3) rc 透传：/bin/sh -c 'exit 7'
disp, full, rc, to = pty_run(["/bin/sh", "-c", "exit 7"], timeout=10)
ck("exit 7 透传 rc=7", rc == 7, "rc=%r" % rc)

print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)

