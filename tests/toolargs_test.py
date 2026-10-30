#!/usr/bin/env python3
"""工具参数校验闸门:空/缺失的必填参数**不许**被当成合法输入执行。

这条闸门来自一次真实困惑:用户看到界面上

    ⏺ shell ·
    └─ 完成 · 共 0 行 ┘

两次,于是"命令输出为空很奇怪,让我用 read_file 看看" —— 白烧一轮工具调用。
真因不是输出问题,是**这次调用根本没带 command 参数**:
`run_shell_command` 拿到空串 → `bash -c ""` → 0 行输出 → 显示"完成" → 返回空字符串。
一串"成功"的假象,只有"内容为空"这一个可疑线索。

同类形状还有:`read_file {"path": ""}` 会读到工作目录本身、`write_file` 缺 content
会写空文件、`grep {}` 会扫全树。它们都不报错。

所以判据是:**必填参数缺失/为空时,必须返回以"错误:"开头的明确说明**,
不透传去执行;并且横幅上不许留一片空白。必填清单以 `tools_spec.json` 为唯一真源。

后来用户又报"AI 调了一条空命令"(`⏺ shell · (命令为空 —— 本次调用未带 command 参数)`),
查历史发现参数**一个字没少**,是模型把键名写成了 `cmd`(见 §7b/7c/10)。这说明
"参数为空"这条判据还不够:归因必须区分**没带参数**和**键名不对**,否则横幅会把
"模型写错键"报成"wchat 丢了参数" —— 人和模型都会照着错的线索排查。
"""
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

os.environ["WCHAT_API_LOG"] = "0"
os.environ["WCHAT_NO_SETUP"] = "1"

import wchat_py.cli as cli  # noqa: E402

P = F = 0


def ck(name, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  ok   %s" % name)
    else:
        F += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


def spec_required():
    with open(os.path.join(REPO, "wchat_py", "tools_spec.json"), encoding="utf-8") as f:
        out = {}
        for t in json.load(f) or []:
            fn = (t or {}).get("function") or {}
            out[fn.get("name")] = list((fn.get("parameters") or {}).get("required") or [])
        return out


SPEC = spec_required()

print("=== 1. 必填清单来自 spec(不是抄的) ===")
ck("spec 里 run_shell_command 必填 command",
   SPEC.get("run_shell_command") == ["command"], repr(SPEC.get("run_shell_command")))
ck("_required_of 与 spec 一致(抽查三个)",
   cli._required_of("read_file") == SPEC.get("read_file")
   and cli._required_of("write_file") == SPEC.get("write_file")
   and cli._required_of("run_shell_command") == SPEC.get("run_shell_command"),
   repr([cli._required_of(x) for x in ("read_file", "write_file", "run_shell_command")]))

print("=== 2. 空命令不许执行(现场那个 bug) ===")
for label, args in (("command 空串", '{"command": ""}'),
                    ("完全没有参数", "{}"),
                    ("arguments 是空字符串", ""),
                    ("command 只有空白", '{"command": "   \\n  "}')):
    out = cli._run_tool("run_shell_command", args)
    ck("%s -> 明确报错" % label,
       isinstance(out, str) and out.startswith("错误:") and "必填参数" in out,
       repr(out)[:120])

print("=== 3. 其它工具的必填也守住了 ===")
for name, args in (("read_file", '{"path": ""}'),
                   ("read_file", "{}"),
                   ("write_file", '{"path": "x"}'),
                   ("grep", "{}"),
                   ("web_fetch", "{}"),
                   ("web_search", "{}"),
                   ("read_skill", "{}"),
                   ("read_image", "{}"),
                   ("edit_file", '{"path": "x", "old_string": "a"}')):
    out = cli._run_tool(name, args)
    ck("%s %s -> 明确报错" % (name, args),
       isinstance(out, str) and out.startswith("错误:"), repr(out)[:120])

print("=== 4. 报错要能指导下一步(不是只说'错了') ===")
out = cli._run_tool("run_shell_command", "{}")
ck("说明缺哪个参数", "command" in out, repr(out)[:120])
ck("要求重新发起调用", "重新发起" in out, repr(out)[:120])
ck("回显收到的参数便于排查", "(空)" in out or "{}" in out, repr(out)[:140])

print("=== 4b. 与 master 的措辞对齐(排障搜索锚点) ===")
# master(bash)在 run_shell_command 分支里是:
#   content="错误:命令参数为空,工具调用参数解析失败: ${fn_args:-\"(空)\"}"
# 迁移到 Python 时这一段**漏掉了**,才让空命令被当成合法输入执行。
# 这条断言钉住"master 那句话仍然在",既是行为契约也是排障锚点。
ck("shell 空参数沿用 master 原话",
   out.startswith("错误:命令参数为空,工具调用参数解析失败:"), repr(out)[:120])
ck("非 shell 工具不硬套'命令参数'",
   cli._run_tool("read_file", "{}").startswith("错误:参数为空,"),
   repr(cli._run_tool("read_file", "{}"))[:120])
ck("master 的兜底里有这句(防它被删)",
   "命令参数为空,工具调用参数解析失败" in
   open(os.path.join(REPO, "tests", "legacy_wchat.sh"), encoding="utf-8").read())

print("=== 5. 坏 JSON 要回显原文片段 ===")
out = cli._run_tool("run_shell_command", '{"command": "cd /tmp && gr')
ck("报错说不是合法 JSON", "不是合法 JSON" in out, repr(out)[:100])
ck("回显收到的原始参数", "/tmp && gr" in out, repr(out)[:140])

print("=== 6. 非对象 JSON 也不许过 ===")
out = cli._run_tool("run_shell_command", '["a"]')
ck("数组参数被拒", out.startswith("错误:"), repr(out)[:100])

print("=== 7. 横幅:空命令必须显式标出,不许留白 ===")
b = cli._banner_text("run_shell_command", '{"command": ""}')
ck("空命令横幅不含'空白即完成'的误导", "命令为空" in b, repr(b))
# 2026-11:非正文统一灰化(与思考同色),红色不再用于横幅 —— 可辨识度改由 `⚠` +
# `未执行` 承担。这条断言从"必须是红色"改成"必须是灰 + 有警示标",防止把
# "灰"误当成"没标出"(两者在纯文本里都看不见,只有断言能区分)。
ck("空命令横幅是灰色(非正文统一色)", "\x1b[2m" in b, repr(b))
ck("空命令横幅不再用红色(与'非正文一律灰'冲突)", "\x1b[1;31m" not in b, repr(b))
ck("空命令横幅带警示标且标明未执行", "⚠" in b and "未执行" in b, repr(b))
b2 = cli._banner_text("run_shell_command", '{"command": "echo hi"}')
ck("正常命令横幅原样", "echo hi" in b2 and "命令为空" not in b2, repr(b2))
b3 = cli._banner_text("read_file", '{"path": "x"}')
ck("非 shell 工具横幅不受影响", "read_file" in b3, repr(b3))

print("=== 7b. 参数不是空、而是键名不对:横幅不许瞎归因 ===")
# 真实事故(用户报"AI 调了一条空命令"):历史里那条调用是
#   run_shell_command {"cmd": "cd ... && grep ...", "reason": "..."}
# 参数一个字没少,只是键名写成 cmd。老横幅一律打
#   "(命令为空 —— 本次调用未带 command 参数)"
# 把"模型写错键"说成"wchat 丢了参数",人和模型都被带偏。分档说清原因:
b = cli._banner_text("run_shell_command", '{"cmd": "ls -la", "reason": "看目录"}')
ck("不再谎称'未带 command 参数'", "未带 command 参数" not in b, repr(b))
ck("点名实际收到的键", "cmd" in b, repr(b))
ck("明确标'未执行'", "未执行" in b, repr(b))
b = cli._banner_text("run_shell_command", '{"path": "/x", "pattern": "y"}')
ck("另一种错名形状也点名", "path" in b and "pattern" in b, repr(b))
ck("带空 command 时说'命令为空'(区分于缺键)",
   "命令为空" in cli._banner_text("run_shell_command", '{"command": "  "}'),
   repr(cli._banner_text("run_shell_command", '{"command": "  "}')))
ck("一个参数都没有时明说",
   "没带任何参数" in cli._banner_text("run_shell_command", "{}"),
   repr(cli._banner_text("run_shell_command", "{}")))

print("=== 7c. 报错要把'收到了哪些键'告诉模型(否则它会原样再发一遍) ===")
out = cli._run_tool("run_shell_command", '{"cmd": "ls"}')
ck("回显收到的参数键", "本次收到的参数键:cmd" in out, repr(out)[:200])
ck("点出键名疑似写错", "cmd 应为 command" in out, repr(out)[:200])
ck("master 原话仍在(搜索锚点)", out.startswith("错误:命令参数为空"), repr(out)[:120])
_marker = os.path.join(REPO, ".scratch", "toolargs_should_not_exist")
try:
    os.unlink(_marker)
except OSError:
    pass
cli._run_tool("run_shell_command", json.dumps({"cmd": "touch %s" % _marker}))
ck("键名不对时绝不执行(命令里的副作用没有发生)", not os.path.exists(_marker))


out = cli._run_tool("run_shell_command", '{"command": "echo OK-123"}')
ck("正常命令能执行", "OK-123" in out and not out.startswith("错误:"), repr(out)[:120])
out = cli._run_tool("list_dir", '{"path": "."}')
ck("list_dir 无必填,空参也放行", not out.startswith("错误:必填"), repr(out)[:80])

print("=== 9. 结构锁:校验挂在 _run_tool 入口,所有工具都过 ===")
# 用 inspect.getsource 取**函数本体**,而不是按文件路径切字符串:
# 实现已经搬到 wchat_py/toolrun.py(分层重组),而"校验必须在所有分支之前"这条
# 契约与它住在哪个文件无关。写死路径会让下次搬文件变成一次假红。
import inspect

body = inspect.getsource(cli._run_tool)
ck("_run_tool 里有必填校验调用", "_missing_required(" in body)
ck("校验发生在任何工具分支之前",
   body.index("_missing_required(") < body.index('if name == "list_dir"'),
   "顺序不对:校验在分支之后等于漏掉一部分")

print("=== 10. 端到端:这条错参数在真实工具循环里也不执行、且屏幕上说真话 ===")
# 单测只证明 _banner_text/_run_tool;这里走真正的 run_turn 循环(打桩 _call_llm),
# 钉住"用户看到的那一行"——事故里用户看到的就是这一行。
import io as _io
import re as _re

_marker2 = os.path.join(REPO, ".scratch", "toolargs_loop_should_not_exist")
try:
    os.unlink(_marker2)
except OSError:
    pass
_rounds = {"n": 0}
_orig_call = cli._call_llm


def _fake_call(msgs, model, on_text=None, on_reason=None):
    _rounds["n"] += 1
    if _rounds["n"] == 1:
        return ("", [{"_i": 0, "id": "c1", "name": "run_shell_command",
                      "arguments": json.dumps({"cmd": "touch %s" % _marker2,
                                               "reason": "看代码"}, ensure_ascii=False)}], None)
    return ("收到,改用 command。", [], None)


_buf = _io.StringIO()
_old_out = sys.stdout
cli._call_llm = _fake_call
sys.stdout = _buf
try:
    cli.run_turn("测试", model="x", max_rounds=3)
finally:
    sys.stdout = _old_out
    cli._call_llm = _orig_call
_screen = _re.sub(r"\x1b\[[0-9;]*m", "", _buf.getvalue())
ck("屏幕上不再出现'未带 command 参数'", "未带 command 参数" not in _screen, _screen[:200])
ck("屏幕上点名收到的键", "cmd" in _screen, _screen[:200])
ck("循环把错误回传给模型(有第二轮)", _rounds["n"] >= 2, repr(_rounds))
ck("错参数的副作用没有发生", not os.path.exists(_marker2))

print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
sys.exit(0 if F == 0 else 1)