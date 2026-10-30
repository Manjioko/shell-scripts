#!/usr/bin/env python3
"""run_shell_command 工具自检（硬墙 + 审批闸 + 执行 + 超时）。末行 RESULT ...。"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("WORK_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wchat_py import cli  # noqa: E402

# 单测不联网: 初审器固定判 N(非 auto 走人工确认); auto/显式放行分支不受影响
cli._auth_think = lambda cmd: ("N", "unit-test")

p = f = 0


def ck(name, ok, detail=""):
    global p, f
    if ok:
        p += 1
        print("  ok   %s" % name)
    else:
        f += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


def run(cmd):
    return cli._run_tool("run_shell_command", json.dumps({"command": cmd}))


# 1) 无交互、未确认 => fail-closed 拒（不执行）
for _k in ("WCHAT_AUTO_ASSUME_YES", "AUTO_MODE", "WCHAT_AUTO_MODE"):
    os.environ.pop(_k, None)
out = run("printf 'hi\\n'")
ck("无 tty 未确认 => 拒", out.startswith("用户未批准"), repr(out))

# 2) 硬墙拒绝（reboot -> H8），先于审批
_alog = os.path.join(tempfile.mkdtemp(), "audit.log")
os.environ["AUTO_AUDIT_LOG"] = _alog
out = run("reboot")
ck("硬墙拒绝 H8", ("H8" in out and "拒绝" in out), repr(out))
ck("审计记硬墙拒绝", os.path.exists(_alog) and '"H8"' in open(_alog).read(), (open(_alog).read() if os.path.exists(_alog) else ""))
os.environ.pop("AUTO_AUDIT_LOG", None)

# 3) 显式放行 => 执行
os.environ["WCHAT_AUTO_ASSUME_YES"] = "1"
_alogy = os.path.join(tempfile.mkdtemp(), "a.log")
os.environ["AUTO_AUDIT_LOG"] = _alogy
out = run("printf 'hi\\n'")
# 结尾换行会被截断链吃掉 —— 与 master 一致:`content=$(truncate_output ...)`
# 的 `$( )` 本来就剥结尾换行。所以这里只断言内容,不断言行尾。
ck("显式放行执行", out.strip() == "hi", repr(out))
ck("审计记放行 allow", os.path.exists(_alogy) and '"allow"' in open(_alogy).read(), open(_alogy).read() if os.path.exists(_alogy) else "")
os.environ.pop("AUTO_AUDIT_LOG", None)

# 4) 超时（放行 + CMD_TIMEOUT=1）
# 文案从"超过单条上限"改成"超过本次上限":上限现在**按调用可变** ——
# 模型可以传 timeout 自己放宽(默认 120s,封顶 CMD_TIMEOUT_MAX=3600s)。
os.environ["CMD_TIMEOUT"] = "1"
out = run("sleep 3")
ck("超时提示", "超过本次上限" in out, repr(out))
ck("超时提示告诉模型可以带更大的 timeout 重试", "timeout" in out and "重试" in out, repr(out))
ck("超时提示点名 run_in_background 这条路", "run_in_background" in out, repr(out))
os.environ.pop("CMD_TIMEOUT", None)
os.environ.pop("WCHAT_AUTO_ASSUME_YES", None)


# 严格语义：非 auto 必逐字 yes（假 tty）
class _FakeIn:
    def __init__(self, ans):
        self.ans = ans

    def isatty(self):
        return True

    def readline(self):
        return self.ans + "\n"


_old = sys.stdin
os.environ.pop("AUTO_MODE", None)
try:
    for ans, want in [("yes", True), ("y", False), ("", False), ("YES", False), ("yes ", True)]:
        sys.stdin = _FakeIn(ans)
        got = cli._shell_approved("echo")
        ck("逐字yes %r -> %s" % (ans, want), got is want, str(got))
finally:
    sys.stdin = _old

print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)