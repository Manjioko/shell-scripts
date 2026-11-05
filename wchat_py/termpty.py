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

# 当前正在跑的**前台**进程组。0 = 没有。
#
# 为什么需要它:wchat 被 `kill`(SIGTERM)或关终端(SIGHUP)时,以前唯一的清理是
# "pty master 关闭 → 内核给前台进程组发 SIGHUP"。实测这条**不够**:
#   · 命令自己 `trap '' HUP` → 整组活着(实测残留 bash + sleep 共 2 个,ppid=1);
#   · 命令内部 `setsid` 脱离会话 → 更收不到;
#   · `nohup … &` → 同上。
# 前两种是"用户杀了 wchat,却还有命令在跑",必须清;第三种(nohup/setsid)是显式
# 要求脱离,清了反而违背意图,所以这里只处理**仍在原进程组里**的那些。
_ACTIVE = {"pgid": 0}


def kill_active_group():
    """杀掉当前前台进程组(SIGKILL)。给信号处理器用,幂等。返回是否真发过信号。

    用 SIGKILL 而不是 SIGTERM:这里是"用户要退出 wchat"的路径,他不想再等一个
    温柔退出的宽限期;而且实测忽略 TERM 的命令很常见(`trap '' TERM`)。
    """
    pgid = _ACTIVE["pgid"]
    if not pgid:
        return False
    try:
        os.killpg(pgid, signal.SIGKILL)
        return True
    except OSError:
        return False


def install_signal_cleanup(extra=None):
    """把 SIGTERM/SIGHUP 变成本模块的清理入口。幂等,可多次调用。

    只在**真要用 shell** 之前装(toolrun 在调 pty_run 前调一次),因为它的唯一职责是
    "别让前台命令变成孤儿",没有前台命令时没必要改变进程的退出语义。

    `extra`:额外要跑的清理(例如 MCP 的 daemon 收尾)。这样两套清理不会互相覆盖
    —— 谁后装谁生效的坑就在这里:MCP 装自己的 handler 会悄悄把这条顶掉。
    """
    import signal as _sig

    def _on_term(signum, frame):
        kill_active_group()
        if extra is not None:
            try:
                extra()
            except Exception:
                pass
        os._exit(128 + int(signum))

    for s in (_sig.SIGTERM, _sig.SIGHUP):
        try:
            _sig.signal(s, _on_term)
        except (ValueError, OSError):
            pass
    return _on_term


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
        # 登记前台进程组:start_new_session=True 让它成为新会话/新进程组组长,
        # 所以 pgid == pid。wchat 被 kill 时靠这个值主动收尾(见 kill_active_group)。
        try:
            _ACTIVE["pgid"] = os.getpgid(p.pid)
        except OSError:
            _ACTIVE["pgid"] = p.pid
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
        _ACTIVE["pgid"] = 0      # 已经不归我们管了:别再让信号处理器去杀这个 pgid
                                 # (pgid 会被系统复用,留着会误杀下一条命令)
        os.close(master)
        if handle_sigint and old_handler is not None:
            signal.signal(signal.SIGINT, old_handler)

    text = b"".join(chunks).decode("utf-8", "replace")
    lines = [clean_line(ln) for ln in text.splitlines()]
    display = lines[-win:] if len(lines) > win else lines
    return display, window_output(lines, head_n, buf_size), p.returncode, timed_out, state["int"]