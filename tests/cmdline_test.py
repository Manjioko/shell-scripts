#!/usr/bin/env python3
"""cmdline.classify 自检：核心元命令 → 动作映射 + 正文/前缀。末行 RESULT ...。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wchat_py.cmdline import classify  # noqa: E402

p = f = 0


def ck(name, ok, detail=""):
    global p, f
    if ok:
        p += 1
        print("  ok   %s" % name)
    else:
        f += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


cases = [
    ("-q", ("quit", "")), ("-l", ("list_history", "")), ("-msgs", ("show_msgs", "")),
    ("-c", ("compact", "")), ("-skill", ("skill_save", "")), ("-u", ("usage", "")),
    ("-h", ("help", "")), ("-n", ("new_session", "")), ("-cl", ("clear", "")),
    ("-stp", ("model_pro", "")), ("-stt", ("thinking_on", "")), ("-stf", ("model_flash", "")),
    ("-noauto", ("auto_off", "")), ("-fs", ("save_history", "")), ("-md", ("model_status", "")),
    ("-auto", ("auto_on", "")), ("-auto=full", ("auto_on", "full")),
    ("-img /tmp/x.png", ("image", "/tmp/x.png")), ("-img", ("image", "")),
    ("hello world", ("text", "hello world")), ("-dp", ("text", "-dp")),
]
for line, want in cases:
    got = classify(line)
    ck("%r -> %r" % (line, want), got == want, "got=%r" % (got,))

print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)

