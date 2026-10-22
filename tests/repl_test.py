#!/usr/bin/env python3
"""repl.run_repl 自检：读行→classify→分发（不联网，用假 stream/handler）。末行 RESULT ...。"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wchat_py.repl import run_repl  # noqa: E402

p = f = 0


def ck(name, ok, detail=""):
    global p, f
    if ok:
        p += 1
        print("  ok   %s" % name)
    else:
        f += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


# 1) text 走 on_text，元命令走 on_meta，空行忽略，-q 退出
texts, metas = [], []
stream = io.StringIO("hi there\n\n-u\nfoo\n-q\nnever\n")
rc = run_repl(stream, lambda t: texts.append(t), lambda a, g: metas.append(a) or True)
ck("rc=0", rc == 0)
ck("text 分发顺序", texts == ["hi there", "foo"], repr(texts))
ck("元命令分发", metas == ["usage"], repr(metas))
ck("-q 后不再处理", "never" not in texts)

# 2) on_meta 返回 False 立即停止
texts2 = []
stream2 = io.StringIO("a\n-l\nb\n")
rc2 = run_repl(stream2, lambda t: texts2.append(t),
               lambda a, g: False)
ck("meta 返回False即停", texts2 == ["a"], repr(texts2))

# 3) 无 handler 时默认占位不崩
out = io.StringIO()
old = sys.stdout
sys.stdout = out
try:
    run_repl(io.StringIO("-l\n"), lambda t: None)
finally:
    sys.stdout = old
ck("默认 meta 打占位", "list_history" in out.getvalue(), repr(out.getvalue()))

# 4) -h 默认 handler 调 cli.help_user（真打印，非占位）
out2 = io.StringIO()
old = sys.stdout
sys.stdout = out2
try:
    run_repl(io.StringIO("-h\n"), lambda t: None)
finally:
    sys.stdout = old
_hv = out2.getvalue()
ck("-h 打印 help(非占位)", len(_hv) > 50 and "尚未在 Python 主循环实现" not in _hv, repr(_hv[:60]))

print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)