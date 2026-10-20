#!/usr/bin/env python3
"""终端窗口的 pty 外壳（B 阶段第 3 步 3b-3/3b-4 核心，先做数据通路 + 超时/进程组）。

对应 bash run_with_window 的 pty 分支：给子命令一个真 tty（行缓冲、实时输出），
master 端 select 轮询读，超时按**进程组**整组杀（等价 kill_tree 的粗粒度兜底），
逐行 clean_line → 环形缓冲 → 回传 (display, full, rc, timed_out)。

不含 Ctrl+C 交互语义（那需真终端按键）；此处先把 pty/超时/进程组做对并用子进程验证。
"""
import os
import pty
import select
import signal
import subprocess
import time

from wchat_py.termwin import clean_line, window_output


def pty_run(argv, timeout=20.0, win=10, head_n=50, buf_size=300, poll=0.1):
    """在 pty 里跑 argv，返回 (display, full, rc, timed_out)。

    - start_new_session=True：子命令进独立会话/进程组，超时用 killpg 整组杀。
    - 超时命中：SIGKILL 进程组，timed_out=True（rc 由 wait 兜）。
    """
    master, slave = pty.openpty()
    try:
        p = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave,
                             start_new_session=True, close_fds=True)
    except Exception:
        os.close(master)
        os.close(slave)
        raise
    os.close(slave)

    chunks = []
    deadline = time.monotonic() + timeout
    timed_out = False
    try:
        while True:
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
                except OSError:      # Linux 子进程退出后 master 读报 EIO
                    break
                if not data:
                    break
                chunks.append(data)
    finally:
        if timed_out:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
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

    text = b"".join(chunks).decode("utf-8", "replace")
    lines = [clean_line(ln) for ln in text.splitlines()]
    display = lines[-win:] if len(lines) > win else lines
    return display, window_output(lines, head_n, buf_size), p.returncode, timed_out

