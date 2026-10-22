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
d = tempfile.mkdtemp()
rp = os.path.join(d, "r.json")
open(rp, "w").write('[{"type":"text","text":"HELLO-PY"}]')
sk = socket.socket(); sk.bind(("127.0.0.1", 0)); port = sk.getsockname()[1]; sk.close()
stub = subprocess.Popen([sys.executable, os.path.join(REPO, "tests/stub_gateway.py"), str(port)],
                        env=dict(os.environ, STUB_LOG=os.path.join(d, "log"), STUB_REPLY=rp,
                                 STUB_AUTH_DENY_IF="", STUB_FINAL_DENY_IF=""),
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(50):
    try:
        socket.create_connection(("127.0.0.1", port), 0.2).close(); break
    except OSError:
        time.sleep(0.1)

env = dict(os.environ, BASE_URL="http://127.0.0.1:%d/v1/chat/completions" % port,
           WCHAT_API_KEY="sk-stub", PYTHONPATH=REPO + ":" + os.environ.get("PYTHONPATH", ""),
           TERM="xterm-256color")
master, slave = pty.openpty()
p = subprocess.Popen([sys.executable, "-m", "wchat_py.cli", "repl"],
                     stdin=slave, stdout=slave, stderr=slave, env=env, cwd=d)
os.close(slave)
out = b""
try:
    time.sleep(0.3)
    os.write(master, b"hi\n")
    deadline = time.time() + 20
    sent_q = False
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

