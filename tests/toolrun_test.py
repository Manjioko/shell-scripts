#!/usr/bin/env python3
"""run_shell_command 工具（Python 生产路径）自检：执行/硬墙拒/超时。末行 RESULT ...。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("WORK_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wchat_py import cli  # noqa: E402

p = f = 0


def ck(name, ok, detail=""):
    global p, f
    if ok:
        p += 1
        print("  ok   %s" % name)
    else:
        f += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


# 1) benign 执行
out = cli._run_tool("run_shell_command", json.dumps({"command": "printf 'hi\\nthere\\n'"}))
ck("执行输出含 hi/there", ("hi\n" in out and "there\n" in out), repr(out))

# 2) 硬墙拒绝（reboot -> H8），不执行
out = cli._run_tool("run_shell_command", json.dumps({"command": "reboot"}))
ck("硬墙拒绝 H8", out.startswith("[硬墙 H8]"), repr(out))

# 3) 超时（CMD_TIMEOUT=1, sleep 3）
os.environ["CMD_TIMEOUT"] = "1"
out = cli._run_tool("run_shell_command", json.dumps({"command": "sleep 3"}))
ck("超时提示", "超过单条上限" in out, repr(out))
os.environ.pop("CMD_TIMEOUT", None)

print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)

