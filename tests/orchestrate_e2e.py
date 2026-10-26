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

# --- 缺省上限必须容得下长任务(本轮"每几轮就静默停下"的真因) ---
# 一句话任务常要 8–20 轮工具(读+改+测+迭代)。上限若是 5/25,会话会在中途**静默**
# 结束本轮,用户只能人工补"继续"接着跑——看上去就像 SDK 断了。这里用 8 轮工具
# 连发证明缺省值下能一次跑完(桩网关另起一个进程,因为它的请求游标是进程级的)。
rp2 = os.path.join(d, "r2.json")
_step = [{"type": "tool", "name": "list_dir", "args": {"path": "."}} for _ in range(8)]
_step.append({"type": "text", "text": "DONE8"})
open(rp2, "w").write(json.dumps(_step))
sk2 = socket.socket(); sk2.bind(("127.0.0.1", 0)); port2 = sk2.getsockname()[1]; sk2.close()
env2 = dict(env, STUB_REPLY=rp2, STUB_LOG=os.path.join(d, "log2.jsonl"))
stub2 = subprocess.Popen([sys.executable, os.path.join(REPO, "tests/stub_gateway.py"), str(port2)],
                         env=env2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(50):
    try:
        socket.create_connection(("127.0.0.1", port2), 0.2).close(); break
    except OSError:
        time.sleep(0.1)
_old_base = os.environ.get("BASE_URL")
os.environ["BASE_URL"] = "http://127.0.0.1:%d/v1/chat/completions" % port2
os.environ.pop("MAX_TOOL_ROUNDS", None)
try:
    s8 = Session()
    t8 = orchestrate(s8, "long", lambda m: cli._call_llm(m, ""), lambda n, a: cli._run_tool(n, a))
finally:
    if _old_base is not None:
        os.environ["BASE_URL"] = _old_base
    stub2.terminate()
ck("缺省上限下 8 轮工具一次跑完", t8 == "DONE8", repr(t8))
ck("8 轮工具计数=8", s8.tool_round == 8, str(s8.tool_round))
ck("请求数=9(8 次工具 + 1 次收尾)",
   len([1 for l in open(os.path.join(d, "log2.jsonl")) if l.strip()]) == 9)

# --- 轮次上限与审计缺省路径(本轮修复的回归锁) ---
# ① 上限缺省必须与 master 一致 250,不能被写成 5/25:一个正经任务(读+改+测+迭代)
#    远超 5 轮,上限过小会让会话在每几轮后静默停下,用户只能人工补"继续"。
_old_max = os.environ.pop("MAX_TOOL_ROUNDS", None)
s2 = Session()
_saved = cli._call_llm
_n = [0]


def _loopy(msgs):
    """永远要工具:用来撞上限(真实场景是模型陷入循环)。"""
    _n[0] += 1
    return ("", [{"id": "call_%d" % _n[0], "name": "list_dir",
                  "arguments": '{"path":"."}'}], None)


try:
    orchestrate(s2, "loop", _loopy, lambda n, a: "ok", max_rounds=3)
    ck("撞上限后返回(不抛)", True)
    ck("撞上限轮次计数=3", s2.tool_round == 3, str(s2.tool_round))
finally:
    if _old_max is not None:
        os.environ["MAX_TOOL_ROUNDS"] = _old_max

from wchat_py.session import Session as _S  # noqa: E402
ck("缺省 max_tool_rounds=250", _S().max_tool_rounds == 250, str(_S().max_tool_rounds))

# ② 审计缺省路径:测试会 env -u AUTO_AUDIT_LOG,AUTO_AUDIT_LOG 为空时 record 必须
#    落到 $CT_HOME/auto/audit.log,而不是静默丢行(拒绝最该留痕)。
from wchat_py import audit as _audit  # noqa: E402
_ct = os.path.join(d, "cthome")
_old_ct = os.environ.get("CT_HOME")
_old_al = os.environ.pop("AUTO_AUDIT_LOG", None)
os.environ["CT_HOME"] = _ct
try:
    _audit.record("", "final", verdict="deny", cmd="rm -rf /")
    _p = os.path.join(_ct, "auto", "audit.log")
    _txt = open(_p).read() if os.path.exists(_p) else ""
    ck("空 AUTO_AUDIT_LOG 落 CT_HOME", '"verdict":"deny"' in _txt, _txt[:80])
finally:
    if _old_ct is not None:
        os.environ["CT_HOME"] = _old_ct
    else:
        os.environ.pop("CT_HOME", None)
    if _old_al is not None:
        os.environ["AUTO_AUDIT_LOG"] = _old_al

print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)