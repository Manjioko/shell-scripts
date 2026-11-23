#!/usr/bin/env python3
"""Python 入口 e2e 探针。非 TUI 交互废弃后,交互只剩 TUI(由 tui_term_test 用真终端覆盖)。

本探针覆盖**可脚本化**的入口行为(用桩网关,不需真终端):
  A. 单轮 `-st "hi"`:能把一轮问答跑通,答案出现在 stdout,退出码 0;
  B. 非 TTY 且不带 -st(`python -m wchat_py.cli`,stdin 为管道)→ 不再进交互,
     打印"需要真终端 / 用 -st"的清晰提示,退出码 2。

要真跑一轮,必须用能 import openai 的解释器(同旧版)。
"""
import os
import socket
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _pick_python():
    """挑一个能 import openai 的解释器(系统 python3 没装时不是产品坏了)。"""
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


P = F = 0


def ck(name, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  ok   %s" % name)
    else:
        F += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


PY = _pick_python()
if PY is None:
    print("  NOTE ⚠ 没有能 import openai 的解释器,跳过本探针(需先装依赖;不假绿)")
    print("RESULT total=0 pass=0 fail=0")
    sys.exit(0)

d = tempfile.mkdtemp()
rp = os.path.join(d, "r.json")
open(rp, "w").write('[{"type":"text","text":"HELLO-PY"}]')
sk = socket.socket()
sk.bind(("127.0.0.1", 0))
port = sk.getsockname()[1]
sk.close()
stub = subprocess.Popen([PY, os.path.join(REPO, "tests/stub_gateway.py"), str(port)],
                        env=dict(os.environ, STUB_LOG=os.path.join(d, "log"), STUB_REPLY=rp,
                                 STUB_AUTH_DENY_IF="", STUB_FINAL_DENY_IF=""),
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(50):
    try:
        socket.create_connection(("127.0.0.1", port), 0.2).close()
        break
    except OSError:
        time.sleep(0.1)

env = dict(os.environ, BASE_URL="http://127.0.0.1:%d/v1/chat/completions" % port,
           WCHAT_API_KEY="sk-stub", WCHAT_NO_SETUP="1",
           CT_HOME=os.path.join(d, "ct"), WCHAT_HISTORY_DIR=os.path.join(d, "hist"),
           PYTHONPATH=REPO + ":" + os.environ.get("PYTHONPATH", ""), TERM="xterm-256color")
for k in ("AUTO_MODE", "WCHAT_AUTO_MODE", "MAX_TOOL_ROUNDS", "WCHAT_MODEL", "WCHAT_THINKING"):
    env.pop(k, None)

try:
    # A. 单轮 -st:能跑通一轮,答案上屏,退出码 0
    a = subprocess.run([PY, "-m", "wchat_py.cli", "-st", "hi"],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       env=env, cwd=d, timeout=60)
    ck("-st 单轮 rc=0", a.returncode == 0, "rc=%r err=%r" % (a.returncode, a.stderr[-200:]))
    ck("-st 单轮答案出现在 stdout", "HELLO-PY" in (a.stdout or ""), repr((a.stdout or "")[-200:]))

    # B. 非 TTY 且不带 -st:不再进交互,清晰报错 + rc=2
    b = subprocess.run([PY, "-m", "wchat_py.cli"],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       env=env, cwd=d, timeout=30)
    _err = (b.stderr or "") + (b.stdout or "")
    ck("非 TTY 交互 rc=2", b.returncode == 2, "rc=%r" % (b.returncode,))
    ck("非 TTY 交互提示要求真终端并指 -st", ("TTY" in _err and "-st" in _err), repr(_err[-200:]))
finally:
    stub.terminate()

print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
sys.exit(0 if F == 0 else 1)

