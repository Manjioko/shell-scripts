#!/usr/bin/env python3
"""Ctrl+C 打断流式输出的 e2e(pty 驱动真实入口 + 桩网关慢速流)。

用户报的故障:AI 输出中途按 Ctrl+C,master 是"取消这次输出、回输入框等着";
迁移版直接抛 KeyboardInterrupt traceback 退出会话。

断言(全部行为化,不看源码):
  1. 中断后进程**还活着**(不是 rc≠0 退出);
  2. 输出里有"已中断"提示,且**没有 Traceback**(父子两侧都不许刷栈);
  3. 中断后还能继续用:再发一句能得到新回复;
  4. 被打断的那一轮**没有把 assistant 消息写进上下文**(用户那句保留);
  5. -q 能正常退出 rc=0。

⚠ 必须用 `pty.fork()`:Ctrl+C 不是"往 master 写一个 \x03 字节"就行 —— tty 行规程
只对**该 tty 的前台进程组**发 SIGINT,而 `subprocess.Popen(stdin=slave)` 起的子进程
没有把 slave 设成控制终端(没有 setsid+TIOCSCTTY),于是 \x03 只是被当成普通字节
读走,进程照跑到流结束(第一版测试就栽在这:断言"没中断"其实是测试没发出信号)。
"""
import json
import os
import pty
import select
import socket
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
d = tempfile.mkdtemp()
rp = os.path.join(d, "r.json")
open(rp, "w").write(json.dumps([
    {"type": "text", "text": "SLOWCHUNK-" * 12},
    {"type": "text", "text": "SECOND-OK"},
], ensure_ascii=False))

sk = socket.socket(); sk.bind(("127.0.0.1", 0)); port = sk.getsockname()[1]; sk.close()
log = os.path.join(d, "log.jsonl")
stub = subprocess.Popen(
    [sys.executable, os.path.join(REPO, "tests/stub_gateway.py"), str(port)],
    env=dict(os.environ, STUB_LOG=log, STUB_REPLY=rp, STUB_SLOW_MS="8000",
             STUB_AUTH_DENY_IF="", STUB_FINAL_DENY_IF=""),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(50):
    try:
        socket.create_connection(("127.0.0.1", port), 0.2).close(); break
    except OSError:
        time.sleep(0.1)

env = dict(os.environ,
           BASE_URL="http://127.0.0.1:%d/v1/chat/completions" % port,
           WCHAT_API_KEY="sk-stub",
           PYTHONPATH=REPO + ":" + os.environ.get("PYTHONPATH", ""),
           CT_HOME=os.path.join(d, "ct"),
           WCHAT_HISTORY_DIR=os.path.join(d, "hist"),
           TERM="xterm-256color")
for k in ("AUTO_MODE", "WCHAT_AUTO_MODE", "MAX_TOOL_ROUNDS", "WCHAT_MODEL", "WCHAT_THINKING"):
    env.pop(k, None)

p = f = 0


def ck(name, ok, detail=""):
    global p, f
    if ok:
        p += 1
        print("  ok   %s" % name)
    else:
        f += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


pid, master = pty.fork()
if pid == 0:                                  # 子进程:控制终端就是这块 pty
    try:
        os.chdir(d)
        os.execvpe(sys.executable, [sys.executable, os.path.join(REPO, "wchat"), "repl"], env)
    finally:
        os._exit(127)

_status = {"rc": None}


def poll():
    """非阻塞收尸:返回 rc / None(仍在跑)。"""
    if _status["rc"] is not None:
        return _status["rc"]
    wpid, st = os.waitpid(pid, os.WNOHANG)
    if wpid == pid:
        _status["rc"] = (os.WEXITSTATUS(st) if os.WIFEXITED(st)
                         else -os.WTERMSIG(st))
        return _status["rc"]
    return None


def drain(until, deadline_s, want=None, collect=True):
    """读到 until(b"") 出现 / 超时 / 进程退出;返回这段时间的输出。"""
    global out
    buf = b""
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        r, _, _ = select.select([master], [], [], 0.2)
        if master in r:
            try:
                data = os.read(master, 65536)
            except OSError:
                break
            if not data:
                break
            buf += data
            if collect:
                out += data
            if want is not None and want in buf:
                break
        elif poll() is not None:
            break
    return buf


out = b""
try:
    drain(b"", 3.0)                                   # 等 REPL 起来
    os.write(master, b"hi\n")
    pre = drain(b"", 25, want=b"SLOWCHUNK")
    ck("中断前已开始流式输出", b"SLOWCHUNK" in pre, repr(pre[-120:]))

    os.write(master, b"\x03")                         # 真 Ctrl+C(SIGINT)
    tail = drain(b"", 15, want="已中断".encode())

    ck("进程仍存活(未因 KeyboardInterrupt 退出)", poll() is None,
       "rc=%r tail=%r" % (poll(), tail[-200:]))
    ck("有'已中断'提示", "已中断".encode() in tail, repr(tail[-300:]))
    txt = out.decode("utf-8", "replace")
    ck("没有 Traceback(父子都不刷栈)", "Traceback" not in txt, repr(txt[-300:]))
    ck("没有 KeyboardInterrupt 裸栈", "KeyboardInterrupt" not in txt)

    os.write(master, b"again\n")
    got = drain(b"", 25, want=b"SECOND-OK")
    ck("中断后仍可继续对话(得到新回复)", b"SECOND-OK" in got,
       repr(got[-300:]))

    # 输入行上按 Ctrl+C(没有流):master 是清掉这行回提示符,同样不许退出
    time.sleep(0.5)
    os.write(master, b"\x03")
    drain(b"", 3.0)
    ck("空提示符 Ctrl+C 不退出", poll() is None, "rc=%r" % (poll(),))
    os.write(master, b"third\n")
    got3 = drain(b"", 25)          # 桩脚本第 3 条是缺省回复(结束)
    ck("空提示符 Ctrl+C 后仍能对话", "结束".encode() in got3, repr(got3[-200:]))

    os.write(master, b"-q\n")
    # -q 之后会问"退出前保存对话? [Y/n]"(tty 下与 master 一致),答 n 再退
    ask = drain(b"", 8, want="保存对话".encode())
    if "保存对话".encode() in ask:
        os.write(master, b"n\n")
    for _ in range(100):
        if poll() is not None:
            break
        drain(b"", 0.2)
    ck("-q 正常退出(rc=0)", poll() == 0, "rc=%r" % (poll(),))
finally:
    if poll() is None:
        try:
            os.kill(pid, 9)
        except OSError:
            pass
    try:
        os.close(master)
    except OSError:
        pass

recs = []
try:
    with open(log) as fh:
        recs = [json.loads(x) for x in fh if x.strip()]
except OSError:
    pass
mains = [r for r in recs if r.get("kind") == "main"]
ck("两次请求都到达网关", len(mains) >= 2, str(len(mains)))
if len(mains) >= 2:
    # 中断那轮的 assistant 若不丢弃,第二轮会多带一条 → +2;正确应只有 +1(用户第二句)
    ck("被打断的 assistant 未写入上下文",
       mains[1]["n_messages"] == mains[0]["n_messages"] + 1,
       "first=%s second=%s" % (mains[0]["n_messages"], mains[1]["n_messages"]))
    ck("每轮无孤儿 tool 消息", all(r.get("orphan_tools", 0) == 0 for r in mains),
       str([r.get("orphan_tools") for r in mains]))

stub.terminate()
print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)