#!/usr/bin/env python3
"""人工确认链行为自检(硬墙/审批器/会话记忆)。末行 RESULT ...。

锁四件事(每一条都对应一个真实发生过的现象):
  1. 硬墙逐字 yes 之后**直接执行**,不再弹第二道"是否执行?"(实测:每次都问两遍);
  2. 同一条普通命令人批过一次后,本会话再执行**不再问**(实测:一晚被问 4 次);
  3. 路径授权要**记账**:同一目录第二次访问不再问(旧代码此处 NameError,永远记不上);
  4. 硬墙命令**不**进会话缓存 —— 不可逆动作每次都要人点头。
"""
import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# ⚠ 必须**无条件**钉住,不能用 setdefault:本测试第 4 节在 `$HOME` 下建目录来测"越界
# 授权记账",而父进程若已带 WORK_ROOT=$HOME(从家目录启动 wchat 时就是这样),setdefault
# 会保留它 → 那个目录变成"工作根内" → 直接免审放行、不记账 → 断言红。
# 实测:干净 HEAD 上只要 WORK_ROOT=$HOME 就必红,和业务代码无关。
os.environ["WORK_ROOT"] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.pop("AUTO_MODE", None)      # auto 档会自动放行,同样测不到"记账"
from wchat_py import cli          # noqa: E402
from wchat_py import hardwall     # noqa: E402

p = f = 0


def ck(name, ok, detail=""):
    global p, f
    if ok:
        p += 1
        print("  ok   %s" % name)
    else:
        f += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


class Stdin(object):
    """假 stdin:isatty 可控,readline 按脚本吐行。"""

    def __init__(self, lines=(), tty=True):
        self._it = iter(lines)
        self._tty = tty

    def isatty(self):
        return self._tty

    def readline(self):
        try:
            return next(self._it)
        except StopIteration:
            return ""


def run(cmd, stdin_lines=(), tty=True):
    """跑一次 shell 工具,返回 (工具返回文本, 屏幕输出)。"""
    old_in, old_out = sys.stdin, sys.stdout
    buf = io.StringIO()
    sys.stdin = Stdin(stdin_lines, tty)
    sys.stdout = buf
    try:
        out = cli._run_tool("run_shell_command", json.dumps({"command": cmd}))
    finally:
        sys.stdin, sys.stdout = old_in, old_out
    return out, buf.getvalue()


# ---- 公共环境:非 auto、无显式放行 ----
for _k in ("WCHAT_AUTO_ASSUME_YES", "AUTO_MODE", "WCHAT_AUTO_MODE"):
    os.environ.pop(_k, None)
os.environ["WCHAT_TRACE"] = "0"
_alog = os.path.join(tempfile.mkdtemp(), "audit.log")
os.environ["AUTO_AUDIT_LOG"] = _alog
_calls = {"auth": 0}


def _fake_auth(cmd):
    _calls["auth"] += 1
    return ("N", "test-judge-says-no")


cli._auth_think = _fake_auth

# ---- 1) 硬墙 yes 之后不再问第二道 ----
_real_detail = hardwall.detail
hardwall.detail = lambda c, auto=False: ("H7", "", "unit-test:假装命中强推规则")
try:
    out, screen = run("printf 'HDRUN\\n'", ["yes\n"])
finally:
    hardwall.detail = _real_detail
ck("硬墙 yes 后命令真的执行", "HDRUN" in out, repr(out[:120]))
ck("硬墙 yes 只问一次(出现严格确认提示)", screen.count("确需执行?必须输入 yes") == 1,
   repr(screen[-200:]))
ck("硬墙 yes 后**没有**第二道普通确认", "是否执行?" not in screen, repr(screen[-200:]))
ck("硬墙 yes 后不再调审批器", _calls["auth"] == 0, str(_calls["auth"]))

# ---- 2) 硬墙不进会话缓存:同一条再来一次仍要问 ----
hardwall.detail = lambda c, auto=False: ("H7", "", "unit-test:假装命中强推规则")
try:
    out2, screen2 = run("printf 'HDRUN2\\n'", [])
finally:
    hardwall.detail = _real_detail
ck("硬墙命令第二次仍需人确认(不缓存)", "确需执行?必须输入 yes" in screen2, repr(screen2[-160:]))
ck("未确认则拒绝(回传里写明未执行)",
   out2.startswith("用户拒绝执行高危命令"), repr(out2[:60]))

# ---- 3) 普通命令批过一次后不再问 ----
_calls["auth"] = 0
cmd = "printf 'ONE\\n'"
out3, screen3 = run(cmd, ["y\n"])
ck("普通命令人工批准后执行", "ONE" in out3, repr(out3[:80]))
ck("首次批准时确实问了", "是否执行?" in screen3, repr(screen3[-160:]))
_n_first = _calls["auth"]
out4, screen4 = run(cmd, [])           # 没有可读输入:若再问就等于拒绝
ck("同一命令第二次不再问人", "是否执行?" not in screen4, repr(screen4[-200:]))
ck("同一命令第二次直接执行", "ONE" in out4, repr(out4[:80]))
ck("第二次省掉了审批请求", _calls["auth"] == _n_first, "%d -> %d" % (_n_first, _calls["auth"]))
ck("屏幕提示命中会话记忆", "本会话已批准过同一命令" in screen4, repr(screen4[-160:]))

# ---- 4) 路径授权记账(旧代码此处 NameError) ----
# 注意:不能用 TMPDIR 下的路径 —— 临时目录本来就在放行名单里,`_path_guard` 会
# 直接返回 None,根本不走确认,也就测不到"记账"。这里用 $HOME 下的普通目录。
_outside = tempfile.mkdtemp(prefix="authflow_out_", dir=os.path.expanduser("~"))
_target = os.path.join(_outside, "f.txt")
open(_target, "w").write("x")
old_in = sys.stdin
try:
    sys.stdin = Stdin(["y\n"])
    _r1 = cli._path_guard(_target, "r")
    sys.stdin = Stdin([])               # 第二次没有任何输入:能放行说明记账生效
    _r2 = cli._path_guard(_target, "r")
finally:
    sys.stdin = old_in
ck("路径第一次放行", _r1 is None, repr(_r1))
ck("同一路径第二次自动放行(记账生效)", _r2 is None, repr(_r2))
ck("授权名单里确实有该路径",
   any(os.path.realpath(b) == os.path.realpath(_target) or
       os.path.realpath(_target).startswith(os.path.realpath(b) + "/")
       for _m, b in cli._GRANTED_PATHS), str(sorted(cli._GRANTED_PATHS)))

import shutil
shutil.rmtree(_outside, ignore_errors=True)

print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)