#!/usr/bin/env python3
"""终端窗口的 pty 外壳 + 信号语义（B 阶段第 3 步 3b-3/3b-4）。

对应 bash run_with_window 的 pty 分支 + Ctrl+C 语义：
- pty 给子命令真 tty（行缓冲、实时输出），master 端 select 轮询读；
- 超时按**进程组**整组杀；
- SIGINT（用户 Ctrl+C）：**第一次**向子进程组发 SIGTERM（优雅中断），**第二次**发 SIGKILL
  并放弃本轮（interrupted>=2），读循环立即收尾 —— 等价 bash 的"一次中断/两次急停"。
- 返回 (display, full, rc, timed_out, interrupted)。

无头环境用「线程定时向自身发 SIGINT」模拟按键，可自动化验证信号语义与"终端不被搞坏"。
"""
import os
import pty
import select
import signal
import subprocess
import time

from wchat_py.termwin import clean_line, window_output


def pty_run(argv, timeout=20.0, win=10, head_n=50, buf_size=300, poll=0.1, handle_sigint=True):
    master, slave = pty.openpty()
    try:
        p = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave,
                             start_new_session=True, close_fds=True)
    except Exception:
        os.close(master)
        os.close(slave)
        raise
    os.close(slave)

    state = {"int": 0}
    old_handler = None

    def _on_sigint(signum, frame):
        state["int"] += 1
        try:
            sig = signal.SIGTERM if state["int"] == 1 else signal.SIGKILL
            os.killpg(os.getpgid(p.pid), sig)
        except OSError:
            pass

    if handle_sigint:
        old_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, _on_sigint)

    chunks = []
    deadline = time.monotonic() + timeout
    timed_out = False
    try:
        while True:
            if state["int"] >= 2:          # 第二次 Ctrl+C：放弃本轮
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            try:
                r, _, _ = select.select([master], [], [], min(poll, remaining))
            except OSError:
                break
            if master in r:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                if not data:
                    break
                chunks.append(data)
    finally:
        if timed_out and state["int"] == 0:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except OSError:
                pass
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except OSError:
                pass
            try:
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        os.close(master)
        if handle_sigint and old_handler is not None:
            signal.signal(signal.SIGINT, old_handler)

    text = b"".join(chunks).decode("utf-8", "replace")
    lines = [clean_line(ln) for ln in text.splitlines()]
    display = lines[-win:] if len(lines) > win else lines
    return display, window_output(lines, head_n, buf_size), p.returncode, timed_out, state["int"]

