#!/usr/bin/env python3
"""Python 入口 e2e 探针：pty 驱动 `python -m wchat_py.cli repl`，喂 hi / -q，桩网关。

断言：交互入口能起来、回复出现、-q 退出 rc=0。这是把 wchat 切到 Python 入口前的必要条件；
完整的"vs bash 逐场景差异集"是后续（本探针只证 Python 入口在 pty 下可交互运行）。
"""
import os
import pty
import select
import socket
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 本探针要真跑一轮对话,所以**必须用能 import openai 的解释器**。
# 系统 python3 没有 openai 时不是产品坏了,而是"这个解释器跑不了 wchat 的 LLM 子进程"
# —— 直接红会让人以为入口有问题(实测:报的是"缺少 openai 依赖",看着像产品缺陷)。
# 依次试:自己 → brew 装的 wchat venv → PATH 上的 python3.x。都不可用才明示跳过。
def _pick_python():
    import glob
    import shutil
    cands = [sys.executable]
    for pat in ("/opt/homebrew/Cellar/wchat/*/libexec/venv/bin/python",
                "/home/linuxbrew/.linuxbrew/Cellar/wchat/*/libexec/venv/bin/python",
                "/usr/local/Cellar/wchat/*/libexec/venv/bin/python"):
        cands.extend(sorted(glob.glob(pat), reverse=True))
    for v in ("3.13", "3.12", "3.11", "3.10", "3.9"):
        cands.append(shutil.which("python" + v))
    seen = set()
    for c in cands:
        if not c or c in seen:
            continue
        seen.add(c)
        try:
            r = subprocess.run([c, "-c", "import openai"], capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0:
            return c
    return None


PY = _pick_python()
if PY is None:
    print("  NOTE ⚠ 没有能 import openai 的解释器,跳过本探针(需先装依赖;不假绿)")
    print("RESULT total=0 pass=0 fail=0")
    sys.exit(0)

d = tempfile.mkdtemp()
rp = os.path.join(d, "r.json")
open(rp, "w").write('[{"type":"text","text":"HELLO-PY"}]')
sk = socket.socket(); sk.bind(("127.0.0.1", 0)); port = sk.getsockname()[1]; sk.close()
stub = subprocess.Popen([PY, os.path.join(REPO, "tests/stub_gateway.py"), str(port)],
                        env=dict(os.environ, STUB_LOG=os.path.join(d, "log"), STUB_REPLY=rp,
                                 STUB_AUTH_DENY_IF="", STUB_FINAL_DENY_IF=""),
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(50):
    try:
        socket.create_connection(("127.0.0.1", port), 0.2).close(); break
    except OSError:
        time.sleep(0.1)

env = dict(os.environ, BASE_URL="http://127.0.0.1:%d/v1/chat/completions" % port,
           WCHAT_API_KEY="sk-stub", WCHAT_TUI="0",
           PYTHONPATH=REPO + ":" + os.environ.get("PYTHONPATH", ""), TERM="xterm-256color")
master, slave = pty.openpty()
p = subprocess.Popen([PY, "-m", "wchat_py.cli", "repl"],
                     stdin=slave, stdout=slave, stderr=slave, env=env, cwd=d)
os.close(slave)
out = b""
try:
    time.sleep(0.3)
    os.write(master, b"hi\n")
    deadline = time.time() + 20
    sent_q = False
    sent_n = False
    while time.time() < deadline:
        r, _, _ = select.select([master], [], [], 0.2)
        if master in r:
            try:
                data = os.read(master, 65536)
            except OSError:
                break
            if not data:
                break
            out += data
            if b"HELLO-PY" in out and not sent_q:
                os.write(master, b"-q\n")
                sent_q = True
            # -q 之后会问"退出前保存对话? [Y/n]"(tty 下与 master 一致)。
            # 不答这一问,进程会**停在那里等输入**,测试只能 terminate 它,
            # 拿到的 rc 是 -15(SIGTERM)—— 看起来像"repl 退不出去"的产品缺陷,
            # 实际是探针漏了一步。对照 tests/interrupt_e2e.py 的同名处理。
            if sent_q and not sent_n and b"\xe4\xbf\x9d\xe5\xad\x98\xe5\xaf\xb9\xe8\xaf\x9d" in out:
                os.write(master, b"n\n")
                sent_n = True
        if p.poll() is not None:
            break
finally:
    if p.poll() is None:
        p.terminate()
    try:
        p.wait(timeout=3)
    except subprocess.TimeoutExpired:
        p.kill()
    os.close(master)
    stub.terminate()

text = out.decode("utf-8", "replace")
rr = 0 if ("HELLO-PY" in text and p.returncode == 0) else 1
print("rc=%r 含回复=%s" % (p.returncode, "HELLO-PY" in text))
if rr:
    print("out tail:", repr(text[-300:]))
print("RESULT total=1 pass=%d fail=%d" % (1 if rr == 0 else 0, rr))
sys.exit(rr)