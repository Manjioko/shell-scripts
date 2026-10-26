#!/usr/bin/env python3
"""终端窗口的 pty 外壳 + 信号语义（B 阶段第 3 步 3b-3/3b-4）。

- pty 给子命令真 tty；master 端 select 轮询读；超时按进程组整组杀。
- SIGINT（Ctrl+C）：第一次 → 子进程组 SIGTERM（优雅中断）；第二次 → SIGKILL + 放弃本轮。
  **关键**：SIGINT handler 必须在 spawn 之前装好，否则"spawn 与装 handler 之间"的窗口里
  到达的 SIGINT 会走默认处置（KeyboardInterrupt），在负载高时偶发失败（闸门实测踩到）。
- 返回 (display, full, rc, timed_out, interrupted)。
"""
import os
import pty
import select
import signal
import subprocess
import time

from wchat_py.termwin import clean_line, window_output


def pty_run(argv, timeout=20.0, win=10, head_n=50, buf_size=300, poll=0.1,
            handle_sigint=True, env=None):
    state = {"int": 0}
    proc = {"p": None}
    old_handler = None

    def _on_sigint(signum, frame):
        state["int"] += 1
        p = proc["p"]
        if p is None:
            return
        try:
            sig = signal.SIGTERM if state["int"] == 1 else signal.SIGKILL
            os.killpg(os.getpgid(p.pid), sig)
        except OSError:
            pass

    # 先装 handler 再 spawn，堵住竞态窗口
    if handle_sigint:
        old_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, _on_sigint)

    master = slave = None
    try:
        master, slave = pty.openpty()
        p = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave,
                             start_new_session=True, close_fds=True, env=env)
        proc["p"] = p
    except Exception:
        for fd in (master, slave):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        if handle_sigint and old_handler is not None:
            signal.signal(signal.SIGINT, old_handler)
        raise
    os.close(slave)

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