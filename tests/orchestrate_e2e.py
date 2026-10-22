#!/usr/bin/env python3
"""orchestrate 真实链路 e2e：cli._call_llm + cli._run_tool + 桩网关（先 tool 后 text）。

断言：最终文本、消息角色序列、tool_call_id 配对（无孤儿）、msg_count。末行 RESULT ...。
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from wchat_py.session import Session, orchestrate, messages_list  # noqa: E402
from wchat_py import cli  # noqa: E402

d = tempfile.mkdtemp()
rp = os.path.join(d, "r.json")
open(rp, "w").write('[{"type":"tool","name":"list_dir","args":{"path":"."}},{"type":"text","text":"DONE"}]')
proj = os.path.join(d, "proj"); os.makedirs(proj)
open(os.path.join(proj, "f.txt"), "w").write("x")

sk = socket.socket(); sk.bind(("127.0.0.1", 0)); port = sk.getsockname()[1]; sk.close()
env = dict(os.environ, STUB_LOG=os.path.join(d, "log.jsonl"), STUB_REPLY=rp,
           STUB_AUTH_DENY_IF="", STUB_FINAL_DENY_IF="")
stub = subprocess.Popen([sys.executable, os.path.join(REPO, "tests/stub_gateway.py"), str(port)],
                        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(50):
    try:
        socket.create_connection(("127.0.0.1", port), 0.2).close(); break
    except OSError:
        time.sleep(0.1)

os.environ["BASE_URL"] = "http://127.0.0.1:%d/v1/chat/completions" % port
os.environ["WCHAT_API_KEY"] = "sk-stub"
os.environ["WORK_ROOT"] = proj
old = os.getcwd(); os.chdir(proj)

p = f = 0


def ck(name, ok, detail=""):
    global p, f
    if ok:
        p += 1
        print("  ok   %s" % name)
    else:
        f += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


try:
    s = Session()
    text = orchestrate(s, "list", lambda m: cli._call_llm(m, ""), lambda n, a: cli._run_tool(n, a))
finally:
    os.chdir(old)
    stub.terminate()

ml = messages_list(s.messages_json)
ck("最终文本 DONE", text == "DONE", repr(text))
ck("角色序列", [m["role"] for m in ml] == ["user", "assistant", "tool", "assistant"],
   str([m["role"] for m in ml]))
ck("msg_count=4", s.msg_count == 4, str(s.msg_count))
if len(ml) >= 3 and ml[1].get("tool_calls"):
    ck("tool_call_id 配对", ml[2].get("tool_call_id") == ml[1]["tool_calls"][0].get("id"))
    ck("tool 结果非空", bool(ml[2].get("content")), repr(ml[2].get("content"))[:60])
else:
    ck("tool_call_id 配对", False, "第2条无 tool_calls")
ck("请求数=2", len([1 for l in open(os.path.join(d, "log.jsonl")) if l.strip()]) == 2)

print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)