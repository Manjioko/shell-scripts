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
ck("带上收到的参数便于排查", "收到的参数" in out, repr(out)[:120])

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
ck("空命令横幅仍是红色强调", "\\033[1;31m" in b or "\x1b[1;31m" in b, repr(b))
b2 = cli._banner_text("run_shell_command", '{"command": "echo hi"}')
ck("正常命令横幅原样", "echo hi" in b2 and "命令为空" not in b2, repr(b2))
b3 = cli._banner_text("read_file", '{"path": "x"}')
ck("非 shell 工具横幅不受影响", "read_file" in b3, repr(b3))

print("=== 8. 正常参数必须仍然放行(别把功能拦死) ===")
out = cli._run_tool("run_shell_command", '{"command": "echo OK-123"}')
ck("正常命令能执行", "OK-123" in out and not out.startswith("错误:"), repr(out)[:120])
out = cli._run_tool("list_dir", '{"path": "."}')
ck("list_dir 无必填,空参也放行", not out.startswith("错误:必填"), repr(out)[:80])

print("=== 9. 结构锁:校验挂在 _run_tool 入口,所有工具都过 ===")
src = open(os.path.join(REPO, "wchat_py", "cli.py"), encoding="utf-8").read()
body = src[src.index("def _run_tool("):]
body = body[:body.index("\ndef ", 10)] if "\ndef " in body[10:] else body
ck("_run_tool 里有必填校验调用", "_missing_required(" in body)
ck("校验发生在任何工具分支之前",
   body.index("_missing_required(") < body.index('if name == "list_dir"'),
   "顺序不对:校验在分支之后等于漏掉一部分")

print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
sys.exit(0 if F == 0 else 1)

