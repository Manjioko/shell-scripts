#!/usr/bin/env python3
"""shell 输出箱展示闸门:**跟着 `-tf` 走**,不给第二个开关。

契约(2026-12 用户定的):
  1. **默认折叠**:输出箱里的内容一个字都不上屏,只留一行 `└─ 完成 · 共 N 行`
     —— 折叠档**没有箱子**,所以不收口(`─┘` 只属于展开档);
  2. **`-tf=on` 才展开**:多行内容都上屏、带 `│` 边框、末行才写 `└─ … ─┘`;
  3. 两档**回给模型都是全量**(防为了屏幕干净而 `return ""`);
  4. **没有 `-sf`**:`parse`/`classify` 里都不该再出现 `shell_full`,
     启动参数与会话内元命令都只认 `-tf`(同一件事两个开关 = 两套真值表,
     迟早出现"改了 tf 没改 sf"的形状,而它不报错);
  5. 无输出时仍有 `共 0 行` 汇总(防静默成功),与档位无关;
  6. `-md` 不再单列 shell 档位(既然跟 `-tf` 走,再列一行等于把同一事实说两遍)。

第 1/2 条各跑两遍不同命令,是因为"折叠档没上屏"这种断言最容易写成
"我只检查了那三行没出现"—— 换个命令就假绿。
"""
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from wchat_py.render import set_think_full, think_full  # noqa: E402

P = F = 0


def ck(name, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  ok   %s" % name)
    else:
        F += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
GREY = "\x1b[2m"


def plain(s):
    return ANSI.sub("", s)


def text_lines(s):
    return [ln for ln in plain(s).split("\n") if ln.strip()]


class _Env:
    def __init__(self, **kv):
        self.kv = kv
        self.old = {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.old[k] = os.environ.get(k)
            os.environ[k] = v
        return self

    def __exit__(self, *a):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


os.environ.pop("WCHAT_THINK_FULL", None)

print("=== 1. 档位真值表(只有 -tf 一套) ===")
ck("默认 = 两行 / shell 折叠", think_full() is False, repr(think_full()))
for v in ("on", "1", "true", "yes", "y", "ON"):
    ck("set(%r) → True" % v, set_think_full(v) is True and think_full() is True)
for v in ("off", "0", "false", "no", "n", "OFF"):
    ck("set(%r) → False" % v, set_think_full(v) is False and think_full() is False)
ck("非法值返回 None 且不改动状态", set_think_full("maybe") is None and think_full() is False)

print("=== 2. toolrun:折叠只留收尾行;`-tf=on` 展开;回给模型都是全量 ===")
import wchat_py.toolrun as TR  # noqa: E402

AUDIT = os.path.join(REPO, ".scratch", "shell_view_audit.jsonl")


def run_shell(cmd, **envkv):
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    args = {"command": cmd, "reason": "shell-view-gate"}
    try:
        with _Env(**envkv):
            out = TR._run_tool("run_shell_command", json.dumps(args, ensure_ascii=False))
    finally:
        sys.stdout = old
    return out, buf.getvalue()


with _Env(AUTO_MODE="full", WCHAT_AUTO_ASSUME_YES="1", AUTO_AUDIT_LOG=AUDIT):
    out, screen = run_shell("printf 'A\\nB\\nC\\n'", WCHAT_THINK_FULL="0")
vis = text_lines(screen)
ck("折叠:三行内容一个字不上屏",
   not any(x in plain(screen) for x in ("A\n", "B\n", "C\n")) and "A" not in plain(screen)
   and "B" not in plain(screen) and "C" not in plain(screen), repr(vis)[:200])
ck("折叠:只留一条收尾行", len(vis) == 1, repr(vis)[:200])
ck("折叠:收尾行恰好是 `└─ 完成 · 共 3 行`(不收口)", vis[0].strip() == "└─ 完成 · 共 3 行",
   repr(vis)[:200])
ck("折叠:不出现 `─┘`(没有箱子就不该有箱底)", "─┘" not in plain(screen), repr(vis)[:200])
ck("折叠:回给模型仍是全量", all(x in out for x in ("A", "B", "C")), repr(out)[:120])
ck("折叠:收尾行是灰的", GREY in screen, repr(screen[:80]))

with _Env(AUTO_MODE="full", WCHAT_AUTO_ASSUME_YES="1", AUTO_AUDIT_LOG=AUDIT):
    out, screen = run_shell("printf 'A\\nB\\nC\\n'", WCHAT_THINK_FULL="1")
vis = text_lines(screen)
ck("-tf=on:三行内容都上屏",
   sum(1 for ln in vis if plain(ln).strip().lstrip("│ ").strip() in ("A", "B", "C")) == 3,
   repr(vis)[:200])
ck("-tf=on:带 │ 边框前缀",
   all("│" in ln for ln in vis
       if plain(ln).strip().lstrip("│ ").strip() in ("A", "B", "C")), repr(vis)[:160])
ck("-tf=on:回给模型仍是全量", all(x in out for x in ("A", "B", "C")), repr(out)[:120])
ck("-tf=on:末行是 `└─ 完成 · 共 3 行 ─┘`(展开档才收口)",
   vis and vis[-1].strip() == "└─ 完成 · 共 3 行 ─┘", repr(vis)[-2:])
for ln in [x for x in screen.split("\n") if plain(x).strip().endswith(("A", "B"))]:
    payload = ln.split("│", 1)[1] if "│" in ln else ln
    payload = payload.replace("\x1b[0m", "").lstrip()
    ck("展开档内容本身被灰包住", payload.startswith(GREY), repr(payload)[:100])

# 换一条命令再验一遍折叠:上面写的是"那三行没出现",换个输出就是假绿的常见来源
with _Env(AUTO_MODE="full", WCHAT_AUTO_ASSUME_YES="1", AUTO_AUDIT_LOG=AUDIT):
    out2, screen2 = run_shell("printf 'x1\\nx2\\n'", WCHAT_THINK_FULL="0")
ck("折叠(换命令):内容仍一个字不上屏",
   "x1" not in plain(screen2) and "x2" not in plain(screen2), repr(text_lines(screen2))[:160])
ck("折叠(换命令):收尾行计数正确且不收口",
   text_lines(screen2)[0].strip() == "└─ 完成 · 共 2 行", repr(text_lines(screen2))[:160])

# 无输出也必须有一条汇总行(防静默成功),与档位无关。
with _Env(AUTO_MODE="full", WCHAT_AUTO_ASSUME_YES="1", AUTO_AUDIT_LOG=AUDIT):
    _o, _s = run_shell("true", WCHAT_THINK_FULL="0")
_sum = [ln for ln in text_lines(_s) if "└─" in ln and "共 0 行" in ln]
ck("无输出:仍有一行『共 0 行』汇总", len(_sum) == 1, repr(_s)[:200])

print("=== 3. `-sf` 已删除:解析层不该再有这个动作 ===")
from wchat_py.cli import parse  # noqa: E402
from wchat_py.cmdline import classify  # noqa: E402

ck("parse 结果里没有 shell_full 键", "shell_full" not in parse([]), repr(parse([]).keys()))
ck("`-sf=on` 不再是展示开关",
   parse(["-sf=on"]).get("think_full") == "", repr(parse(["-sf=on"])))
ck("classify('-sf=on') 走'已废弃'动作(明说,不当正文发给模型)",
   classify("-sf=on") == ("deprecated_sf", "on"), repr(classify("-sf=on")))
from wchat_py.repl import _default_meta  # noqa: E402
from wchat_py.session import Session  # noqa: E402

_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    _default_meta("deprecated_sf", "on", Session.from_env({}))
ck("废弃动作有回执、并指到 -tf",
   "已并入" in _buf.getvalue() and "-tf" in _buf.getvalue(), repr(_buf.getvalue()))
ck("-tf=on → on(唯一入口)", parse(["-tf=on"])["think_full"] == "on", repr(parse(["-tf=on"])))
ck("裸 -tf → on", parse(["-tf"])["think_full"] == "on")
ck("默认留空(=两行 + shell 折叠)", parse([])["think_full"] == "")
ck("-tf=on 不吞正文", parse(["-tf=on", "你好"])["content"] == "你好",
   repr(parse(["-tf=on", "你好"])["content"]))

print("=== 4. 启动参数真的落到环境变量(走 main 的接线) ===")
_d = tempfile.mkdtemp(prefix="shellview_")
_env = dict(os.environ, HOME=_d, CT_HOME=os.path.join(_d, ".ct"), WCHAT_NO_SETUP="1")
_env.pop("WCHAT_THINK_FULL", None)
_code = ("import os,sys\n"
         "sys.path.insert(0, %r)\n"
         "from wchat_py import cli\n"
         "rc = cli.main(['-tf=%s', '-u'])\n"
         "print('ENV=', os.environ.get('WCHAT_THINK_FULL'), 'RC=', rc)\n")


def _run(flag):
    r = subprocess.run([sys.executable, "-c", _code % (REPO, flag)],
                       capture_output=True, text=True, env=_env)
    return r.stdout + r.stderr


_o = _run("on")
ck("-tf=on → WCHAT_THINK_FULL=1", "ENV= 1" in _o, _o[-200:])
_o = _run("off")
ck("-tf=off → WCHAT_THINK_FULL=0", "ENV= 0" in _o, _o[-200:])
_bad = _run("maybe")
ck("非法 -tf 值明说并按默认(两行 + 折叠)走",
   "-tf 只接受 on/off" in _bad and "ENV= None" in _bad, _bad[-300:])

print("=== 5. 会话内 -tf=(交互模式同一套,回执里说清 shell 同档) ===")

ck("-tf=on 被识别为元命令", classify("-tf=on") == ("think_full", "on"), repr(classify("-tf=on")))
ck("裸 -tf 按 on", classify("-tf") == ("think_full", "on"))
ck("-tf=on 不会被当正文发给模型", classify("-tf=on")[0] != "text")

set_think_full("off")
_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    _default_meta("think_full", "on", Session())
ck("会话内 -tf=on 生效", os.environ.get("WCHAT_THINK_FULL") == "1", repr(_buf.getvalue()))
ck("会话内 -tf=on 回执提到 shell 也展开", "shell" in _buf.getvalue() and "展开" in _buf.getvalue(),
   repr(_buf.getvalue()))
_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    _default_meta("think_full", "off", Session())
ck("会话内 -tf=off 生效", os.environ.get("WCHAT_THINK_FULL") == "0")
ck("会话内 -tf=off 回执提到 shell 只留收尾行", "shell" in _buf.getvalue(),
   repr(_buf.getvalue()))
_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    _default_meta("think_full", "whatever", Session())
ck("会话内非法值给用法、不改状态",
   "用法" in _buf.getvalue() and os.environ.get("WCHAT_THINK_FULL") == "0",
   repr(_buf.getvalue()))

print("=== 6. -md 不再单列 shell 档位(同一事实不说两遍) ===")
from wchat_py.session import model_status_lines  # noqa: E402
_j = "\n".join(model_status_lines(Session.from_env({"WCHAT_THINK_FULL": "1"})))
ck("-md 显示整段", "思考显示: 整段(-tf=on)" in _j, _j)
ck("-md 不另起一行讲 shell", "shell输出" not in _j, _j)
_j = "\n".join(model_status_lines(Session.from_env({"WCHAT_THINK_FULL": "0"})))
ck("-md 显示两行", "思考显示: 两行(默认;-tf=on 展开)" in _j, _j)
os.environ.pop("WCHAT_THINK_FULL", None)

print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
sys.exit(0 if F == 0 else 1)

