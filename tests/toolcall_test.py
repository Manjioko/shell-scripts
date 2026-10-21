#!/usr/bin/env python3
"""toolcall CLI 自检：JSON {status,content} 分离 + 各工具可用。末行 RESULT ...。"""
import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
d = tempfile.mkdtemp()
open(os.path.join(d, "a.txt"), "w").write("alpha\nbeta\n")

p = f = 0


def ck(name, ok, detail=""):
    global p, f
    if ok:
        p += 1
        print("  ok   %s" % name)
    else:
        f += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


def call(name, args):
    env = dict(os.environ, PYTHONPATH=REPO, WORK_ROOT=d)
    out = subprocess.run([sys.executable, "-m", "wchat_py.toolcall", name],
                         input=json.dumps(args), capture_output=True, text=True, env=env, cwd=d)
    return json.loads(out.stdout)


j = call("list_dir", {"path": "."})
ck("list_dir content 含 a.txt", "a.txt" in j["content"], repr(j["content"]))
ck("list_dir status 是 ' 已列出'", j["status"].startswith(" 已列出"), repr(j["status"]))
ck("status 未混入 content", " 已列出" not in j["content"])

j = call("read_file", {"path": "a.txt"})
ck("read_file content 含 alpha", "alpha" in j["content"])
ck("read_file status ' 已读取'", j["status"].startswith(" 已读取"), repr(j["status"]))

j = call("grep", {"pattern": "alpha"})
ck("grep content 含 a.txt", "a.txt" in j["content"], repr(j["content"]))

j = call("write_file", {"path": "b.txt", "content": "hi"})
ck("write_file status ' 已写入'", j["status"].startswith(" 已写入"), repr(j["status"]))
ck("write_file content 含 ✅", "✅" in j["content"])

print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)

