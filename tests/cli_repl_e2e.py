#!/usr/bin/env python3
"""cli `repl` 子命令 e2e：stdin 两轮 -> orchestrate -> 桩网关(每请求一条 text 回复)。

断言：两轮回复都出现、stub 收到 2 个请求。末行 RESULT ...。
"""
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
open(rp, "w").write('[{"type":"text","text":"R1"},{"type":"text","text":"R2"}]')

sk = socket.socket(); sk.bind(("127.0.0.1", 0)); port = sk.getsockname()[1]; sk.close()
logp = os.path.join(d, "log.jsonl")
env0 = dict(os.environ, STUB_LOG=logp, STUB_REPLY=rp, STUB_AUTH_DENY_IF="", STUB_FINAL_DENY_IF="")
stub = subprocess.Popen([sys.executable, os.path.join(REPO, "tests/stub_gateway.py"), str(port)],
                        env=env0, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(50):
    try:
        socket.create_connection(("127.0.0.1", port), 0.2).close(); break
    except OSError:
        time.sleep(0.1)

env = dict(os.environ, BASE_URL="http://127.0.0.1:%d/v1/chat/completions" % port,
           WCHAT_API_KEY="sk-stub", PYTHONPATH=REPO + ":" + os.environ.get("PYTHONPATH", ""))
try:
    r = subprocess.run([sys.executable, "-m", "wchat_py.cli", "repl"], input="x\ny\n",
                       capture_output=True, text=True, env=env, timeout=60, cwd=d)
finally:
    stub.terminate()

out = r.stdout
p = f = 0


def ck(name, ok, detail=""):
    global p, f
    if ok:
        p += 1
        print("  ok   %s" % name)
    else:
        f += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


ck("rc=0", r.returncode == 0, "rc=%r stderr=%r" % (r.returncode, r.stderr[:120]))
ck("回复 R1", "R1" in out, repr(out[:80]))
ck("回复 R2", "R2" in out, repr(out[:80]))
ck("请求数=2", len([1 for l in open(logp) if l.strip()]) == 2)
_first = json.loads(open(logp).readline())
ck("请求含 tools(schema)", _first.get("has_tools") is True, str(_first.get("has_tools")))

print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)