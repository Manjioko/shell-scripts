#!/usr/bin/env python3
"""思考文本展示闸门:`-tf=off`(默认)只占两行,`-tf=on` 整段。

守的是三件"坏了也不报错、只是屏幕变糟"的事:
  1. **默认两行**:灰色三角 + 思考第一个非空行,其余用 `…` 省略;
     中间那些行**一个字都不许漏到屏幕上**(否则用户又回到"一屏全是思考");
  2. **`-tf=on` 回到整段**(旧行为),且 `-tf=off` 能切回来 —— 两种档位都要真出内容,
     只测一种会得到"开了也看不见"或"关不掉"的单边假绿;
  3. **两条入口同一套真值表**:启动参数 `-tf=` 与会话内 `-tf=` 走同一个
     `render.set_think_full`,非法值必须明说,不许静默当 on/off。

为什么钉在**字节**上:这是纯展示层,没有返回值可供断言;而颜色泄漏(截断切在 SGR
中间)与"少的不是内容而是颜色"这类问题,只有看字节才看得出来。
"""
import contextlib
import io
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

P = F = 0


def ck(name, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  ok   %s" % name)
    else:
        F += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


def show(s):
    return repr(s)[:220]


def _plain(s):
    """去掉 SGR 后的可读文本(用于判断"这行到底有没有内容")。"""
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


os.environ.pop("WCHAT_THINK_FULL", None)
os.environ["COLUMNS"] = "50"          # 写死宽度:闸门不能随本地窗口漂
os.environ["LINES"] = "40"

from wchat_py.render import (render_reason, set_think_full, think_full,  # noqa: E402
                             display_width)

THINK3 = "第一行思考内容\n第二行不该出现\n第三行也不该出现"
LONG1 = "这是一行很长的思考内容" * 8

print("=== 1. 默认(两行):灰三角 + 首行 + … ===")
out = render_reason(THINK3)
lines = out.split("\n")
ck("默认:只有两行有内容(其余是分隔空行)",
   len([x for x in lines if x.strip()]) == 2 and lines[-1] == "", show(out))
ck("第一行是灰色三角", lines[0] == "\033[2m▸\033[0m", show(lines[0]))
ck("第二行两格缩进、灰、以 … 结尾", lines[1].startswith("  \033[2m")
   and lines[1].endswith("…\033[0m"), show(lines[1]))
ck("第二行是思考的第一行", "第一行思考内容" in lines[1], show(lines[1]))
ck("中间的思考行一个字都没上屏", "第二行" not in out and "第三行" not in out, show(out))
ck("颜色只开两份、也只关两份(SGR 没被截断)",
   out.count("\033[2m") == 2 and out.count("\033[0m") == 2, show(out))

print("=== 2. 只有一行:不补多余的 …;太长则按宽度截 ===")
one = render_reason("只有一行思考")
ck("单行不加 …", "只有一行思考" in one and "…" not in one, show(one))
long_out = render_reason(LONG1)
_long_line = long_out.split("\n")[1]
ck("超宽首行被截断且以 … 收尾", _long_line.endswith("…\033[0m"), show(_long_line))
ck("截断后不超过终端宽度(含两格缩进)",
   display_width(_long_line[:-len("\033[0m")]) <= 50, "%d 列" % display_width(_long_line))
ck("空白思考不画孤立三角", render_reason("  \n\n") == "", show(render_reason("  \n\n")))

print("=== 3. -tf=on:整段(旧行为);-tf=off 切回两行 ===")
ck("set on 返回 True", set_think_full("on") is True and think_full() is True)
full = render_reason(THINK3)
ck("整段:三行思考都在", all(k in full for k in ("第一行", "第二行", "第三行")), show(full))
_body = [x for x in full.split("\n")[1:] if _plain(x).strip()]
ck("整段:每条正文行都两格缩进且是灰的",
   bool(_body) and all(x.startswith("  \033[2m") for x in _body), show(full))
ck("set off 返回 False", set_think_full("off") is False and think_full() is False)
ck("切回两行", "第二行" not in render_reason(THINK3))
ck("非法值返回 None 且不改动状态", set_think_full("maybe") is None and think_full() is False)

print("=== 4. 启动参数 -tf=:解析 ===")
from wchat_py.cli import parse  # noqa: E402
ck("-tf=on → on", parse(["-tf=on"])["think_full"] == "on", repr(parse(["-tf=on"])))
ck("-tf=off → off", parse(["-tf=off"])["think_full"] == "off")
ck("裸 -tf → on", parse(["-tf"])["think_full"] == "on")
ck("-tf=on 不吞正文", parse(["-tf=on", "你好"])["content"] == "你好",
   repr(parse(["-tf=on", "你好"])["content"]))
ck("默认留空(=两行档)", parse([])["think_full"] == "")

print("=== 5. 启动参数真的落到环境变量(走 main 的接线,不是只测 parse)===")
_d = tempfile.mkdtemp(prefix="thinkview_")
_env = dict(os.environ, HOME=_d, CT_HOME=os.path.join(_d, ".ct"), WCHAT_NO_SETUP="1")
_env.pop("WCHAT_THINK_FULL", None)      # 子进程从"未设置"起步,才测得出 -tf 的接线
_code = ("import os,sys\n"
         "sys.path.insert(0, %r)\n"
         "from wchat_py import cli\n"
         "rc = cli.main(['-tf=%s', '-u'])\n"
         "print('ENV=', os.environ.get('WCHAT_THINK_FULL'), 'RC=', rc)\n")


def _run(flag):
    r = subprocess.run([sys.executable, "-c", _code % (REPO, flag)],
                       capture_output=True, text=True, env=_env)
    return r.stdout + r.stderr


ck("-tf=on → WCHAT_THINK_FULL=1", "ENV= 1" in _run("on"), _run("on")[-200:])
ck("-tf=off → WCHAT_THINK_FULL=0", "ENV= 0" in _run("off"), _run("off")[-200:])
_bad = _run("maybe")
ck("非法 -tf 值明说并按默认(两行)走",
   "-tf 只接受 on/off" in _bad and "ENV= None" in _bad, _bad[-300:])

print("=== 6. 会话内 -tf=(交互模式同一套) ===")
from wchat_py.cmdline import classify  # noqa: E402
from wchat_py.repl import _default_meta  # noqa: E402
from wchat_py.session import Session  # noqa: E402
from wchat_py.session import model_status_lines  # noqa: E402

ck("-tf=on 被识别为元命令", classify("-tf=on") == ("think_full", "on"), repr(classify("-tf=on")))
ck("-tf=off 被识别为元命令", classify("-tf=off") == ("think_full", "off"))
ck("裸 -tf 按 on", classify("-tf") == ("think_full", "on"))
ck("-tf=on 不会被当正文发给模型", classify("-tf=on")[0] != "text")

set_think_full("off")
_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    _default_meta("think_full", "on", Session())
ck("会话内 -tf=on 生效", os.environ.get("WCHAT_THINK_FULL") == "1", show(_buf.getvalue()))
ck("会话内 -tf=on 有回执", "整段" in _buf.getvalue(), show(_buf.getvalue()))
_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    _default_meta("think_full", "off", Session())
ck("会话内 -tf=off 生效", os.environ.get("WCHAT_THINK_FULL") == "0")
ck("会话内 -tf=off 有回执", "两行" in _buf.getvalue(), show(_buf.getvalue()))
_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    _default_meta("think_full", "whatever", Session())
ck("会话内非法值给用法、不改状态",
   "用法" in _buf.getvalue() and os.environ.get("WCHAT_THINK_FULL") == "0",
   show(_buf.getvalue()))

print("=== 7. -md 如实显示当前档位 ===")
_j = "\n".join(model_status_lines(Session.from_env({"WCHAT_THINK_FULL": "1"})))
ck("-md 显示整段", "思考显示: 整段(-tf=on)" in _j, _j)
_j = "\n".join(model_status_lines(Session.from_env({"WCHAT_THINK_FULL": "0"})))
ck("-md 显示两行", "思考显示: 两行(默认;-tf=on 展开)" in _j, _j)
os.environ.pop("WCHAT_THINK_FULL", None)

print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
sys.exit(0 if F == 0 else 1)