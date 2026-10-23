#!/usr/bin/env python3
"""auto 终审 _auto_judge 单测：Y/N/缺省/异常四路径（fail-closed）。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("WCHAT_AUTO_ASSUME_YES", None)
import wchat_py.cli as c

T = F = 0
def ck(n, ok):
    global T, F; T += 1
    if ok: print("ok -", n)
    else: print("FAIL -", n); F += 1

ck("Y 放行", c._auto_judge("ls", call=lambda x: "Y 安全")[0] == "Y")
ck("N 拒绝", c._auto_judge("rm", call=lambda x: "N 危险")[0] == "N")
ck("无 call -> None(fail-closed)", c._auto_judge("ls", call=None) == (None, None))
def bad(x): raise RuntimeError("x")
ck("异常 -> None(fail-closed)", c._auto_judge("ls", call=bad) == (None, None))
os.environ["WCHAT_AUTO_ASSUME_YES"] = "1"
ck("ASSUME_YES 直接 Y", c._auto_judge("ls")[0] == "Y")
os.environ.pop("WCHAT_AUTO_ASSUME_YES", None)
print("RESULT total=%d pass=%d fail=%d" % (T, T - F, F))
sys.exit(0 if F == 0 else 1)

