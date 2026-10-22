#!/usr/bin/env python3
"""cli._call_llm on_text 增量回调：delta 拼接 == 返回 text。末行 RESULT ...。"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
d = tempfile.mkdtemp()
rp = os.path.join(d, "r.json")
open(rp, "w").write('[{"type":"text","text":"hello world"}]')
sk = socket.socket(); sk.bind(("127.0.0.1", 0)); port = sk.getsockname()[1]; sk.close()
env0 = dict(os.environ, STUB_LOG=os.path.join(d, "log"), STUB_REPLY=rp, STUB_AUTH_DENY_IF="", STUB_FINAL_DENY_IF="")
stub = subprocess.Popen([sys.executable, os.path.join(REPO, "tests/stub_gateway.py"), str(port)],
                        env=env0, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(50):
    try:
        socket.create_connection(("127.0.0.1", port), 0.2).close(); break
    except OSError:
        time.sleep(0.1)
os.environ["BASE_URL"] = "http://127.0.0.1:%d/v1/chat/completions" % port
os.environ["WCHAT_API_KEY"] = "sk-stub"
sys.path.insert(0, REPO)
from wchat_py import cli  # noqa: E402

parts = []
try:
    text, tcs, err = cli._call_llm([{"role": "user", "content": "x"}], "", on_text=lambda s: parts.append(s))
finally:
    stub.terminate()

p = f = 0


def ck(name, ok, detail=""):
    global p, f
    if ok:
        p += 1
        print("  ok   %s" % name)
    else:
        f += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


ck("无错", err is None, repr(err))
ck("返回 text", text == "hello world", repr(text))
ck("delta 拼接 == text", "".join(parts) == text, repr("".join(parts)))
ck("有增量(>1 段或=1)", len(parts) >= 1, str(len(parts)))

print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)

