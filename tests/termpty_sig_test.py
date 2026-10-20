#!/usr/bin/env python3
"""termpty 信号语义自检：一次 SIGINT 优雅中断、两次 SIGINT 急停。

无头环境用线程定时向自身发 SIGINT 模拟 Ctrl+C。末行 RESULT ...。
"""
import os
import signal
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wchat_py.termpty import pty_run  # noqa: E402

# run_gates 环境下进程可能继承了被 block 的 SIGINT；显式 unblock，否则自发的 SIGINT
# 会一直 pending，handler 永不触发（单独跑没事、进闸门偶发失败）。
try:
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGINT})
except (AttributeError, OSError):
    pass

p = f = 0


def ck(name, ok, detail=""):
    global p, f
    if ok:
        p += 1
        print("  ok   %s" % name)
    else:
        f += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


def fire(delay, n=1):
    def go():
        time.sleep(delay)
        for _ in range(n):
            os.kill(os.getpid(), signal.SIGINT)
            time.sleep(0.05)
    t = threading.Thread(target=go, daemon=True)
    t.start()
    return t


# 1) 一次 Ctrl+C：sleep 5 被 SIGTERM 优雅中断，interrupted==1，及时返回
t0 = time.monotonic()
fire(0.3, 1)
disp, full, rc, to, intr = pty_run(["/bin/sleep", "5"], timeout=10)
dt = time.monotonic() - t0
ck("单次 SIGINT interrupted==1", intr == 1, "intr=%r" % intr)
ck("单次 SIGINT 及时返回(<4s)", dt < 4.0, "%.2fs" % dt)
ck("单次 SIGINT 不算超时", to is False)

# 2) 两次 Ctrl+C：第二次放弃本轮，interrupted>=2，更快返回
t0 = time.monotonic()
fire(0.3, 2)
disp, full, rc, to, intr = pty_run(["/bin/sh", "-c", "trap '' TERM; sleep 5"], timeout=10)
dt = time.monotonic() - t0
ck("两次 SIGINT interrupted>=2", intr >= 2, "intr=%r" % intr)
ck("两次 SIGINT 及时返回(<4s)", dt < 4.0, "%.2fs" % dt)

# 3) 信号处理后主进程仍正常（handler 已恢复，未留 KeyboardInterrupt 隐患）
try:
    os.kill(os.getpid(), signal.SIGINT)
    ck("SIGINT handler 已恢复(默认)", False, "未恢复:未抛 KeyboardInterrupt")
except KeyboardInterrupt:
    ck("SIGINT handler 已恢复(默认)", True)

print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)