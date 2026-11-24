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
    ("-tf", ("think_full", "on")), ("-tf=on", ("think_full", "on")),
    ("-tf=off", ("think_full", "off")), ("-tf=YES", ("think_full", "YES")),
    ("-auto", ("auto_on", "")), ("-auto=full", ("auto_on", "full")),
    ("-img /tmp/x.png", ("image", "/tmp/x.png")), ("-img", ("image", "")),
    # `/` 前缀与 `-` **同一套命令**(归一化复用):两种都能用。
    ("/q", ("quit", "")), ("/n", ("new_session", "")), ("/cl", ("clear", "")),
    ("/tf=0", ("think_full", "0")), ("/auto=full", ("auto_on", "full")),
    ("/img /tmp/a.png", ("image", "/tmp/a.png")), ("/mcp", ("mcp_on", "")),
    ("/mcp=off", ("mcp_off", "")), ("/mcp=git,figma", ("mcp_on", "git,figma")),
    # 以 / 开头的普通正文必须**原样保留**,不能被归一化改写。
    ("/usr/bin/ls", ("text", "/usr/bin/ls")), ("8/9", ("text", "8/9")),
    ("hello /q", ("text", "hello /q")), ("/", ("text", "/")),
    ("hello world", ("text", "hello world")), ("-dp", ("text", "-dp")),
]
for line, want in cases:
    got = classify(line)
    ck("%r -> %r" % (line, want), got == want, "got=%r" % (got,))

# 命令目录(SLASH_COMMANDS)与 classify 必须同源不漂移:run/枚举/file 各自能解析成非 text。
from wchat_py.cmdline import SLASH_COMMANDS  # noqa: E402
for name, _desc, kind, opts in SLASH_COMMANDS:
    act, _arg = classify(name)
    if kind == "run":
        ck("目录 run %s 解析为非 text" % name, act != "text", "act=%r" % act)
    elif kind == "enum":
        ck("目录 enum %s 解析为非 text" % name, act != "text", "act=%r" % act)
        ck("目录 enum %s 有选项" % name, bool(opts), repr(opts))
        for val, _lab, _d in opts:
            a2, g2 = classify("%s=%s" % (name, val))
            ck("  %s=%s 解析为带参动作" % (name, val), a2 != "text" and g2 == val,
               "got=%r" % ((a2, g2),))
    elif kind == "file":
        ck("目录 file %s 解析为 image" % name, act == "image", "act=%r" % act)

print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)