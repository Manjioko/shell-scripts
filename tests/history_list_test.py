#!/usr/bin/env python3
"""history_list 自检（空目录/多文件/排序/过滤非 json）。末行 RESULT ...。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wchat_py.session import history_list  # noqa: E402

p = f = 0


def ck(name, ok, detail=""):
    global p, f
    if ok:
        p += 1
        print("  ok   %s" % name)
    else:
        f += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


d = tempfile.mkdtemp()
ck("空目录 -> []", history_list(d) == [])
open(os.path.join(d, "b.json"), "w").write("[]")
open(os.path.join(d, "a.json"), "w").write("[]")
open(os.path.join(d, "notes.txt"), "w").write("x")
open(os.path.join(d, "c.json"), "w").write("[]")
ck("排序且只含 json", history_list(d) == ["a.json", "b.json", "c.json"], str(history_list(d)))
ck("不存在目录 -> []", history_list(os.path.join(d, "nope")) == [])
ck("空串 -> []", history_list("") == [])

print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)

