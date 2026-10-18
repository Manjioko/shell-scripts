"""msg —— 向 messages 追加一条消息(迁移自 wchat.sh 的 `add_message`)。

`messages` 经 stdin 传入(大字符串绝不走 argv);正文与思考全文各经**临时文件**传入
(与 bash 原实现同款 ARG_MAX 防护);新 messages 由 stdout 回传。

退出码(见 `_contract`):
  0  成功,新 messages JSON 写 stdout
  1  未知子命令

用法:
  python3 -m wchat_py.msg add <content_file> <role> <tool_call_id> <tool_call_name> <tool_call_args> <reasoning_file> <thinking>
    # stdin = 当前 messages JSON
  python3 -m wchat_py.msg auth_add <content_file> <role> <keep>
    # stdin = 当前 auth_message JSON
"""

from __future__ import annotations

import json
import sys

from . import _contract


def _read_text(path: str) -> str:
    if not path:
        return ""
    try:
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


def build(messages_json: str, addctx: str, role: str, tool_call_id: str,
          tool_call_name: str, tool_call_args: str, reasoning: str, thinking: str):
    """按分支拼一条消息并追加。分支口径与 bash 旧实现逐条对齐。"""
    try:
        msgs = json.loads(messages_json)
    except Exception:
        msgs = []
    if role == "":
        # 默认 user 消息
        msgs.append({"role": "user", "content": addctx})
    elif role == "assistant" and tool_call_name:
        # assistant 发起工具调用:必须记 tool_calls;本轮已有正文也要写进 content,
        # 否则工具调用前的正文会丢失。thinking 模式下必须带 reasoning_content 字段
        # (哪怕空串)—— 省略字段会让下一轮请求 400。
        tc = {"id": tool_call_id, "type": "function",
              "function": {"name": tool_call_name, "arguments": tool_call_args}}
        if addctx == "":
            obj = {"role": "assistant", "content": None, "tool_calls": [tc]}
        else:
            obj = {"role": "assistant", "content": addctx, "tool_calls": [tc]}
        if reasoning != "" or thinking == "enabled":
            obj["reasoning_content"] = reasoning
        msgs.append(obj)
    elif tool_call_id:
        # tool 角色:工具执行结果,带 tool_call_id
        msgs.append({"role": role, "content": addctx, "tool_call_id": tool_call_id})
    else:
        obj = {"role": role, "content": addctx}
        if reasoning != "" or thinking == "enabled":
            obj["reasoning_content"] = reasoning
        msgs.append(obj)
    return msgs


def cmd_add(argv) -> int:
    content_file = argv[0] if len(argv) > 0 else ""
    role = argv[1] if len(argv) > 1 else ""
    tool_call_id = argv[2] if len(argv) > 2 else ""
    tool_call_name = argv[3] if len(argv) > 3 else ""
    tool_call_args = argv[4] if len(argv) > 4 else ""
    rsn_file = argv[5] if len(argv) > 5 else ""
    thinking = argv[6] if len(argv) > 6 else ""
    msgs = build(_contract.read_stdin_text(), _read_text(content_file), role,
                 tool_call_id, tool_call_name, tool_call_args,
                 _read_text(rsn_file), thinking)
    _contract.write_text(json.dumps(msgs, ensure_ascii=False))
    return _contract.EXIT_OK


def auth_add(auth_json: str, addctx: str, role: str, keep) -> str:
    """向审批链 auth_message 追加 `{role,content}`,再按 keep 裁剪。

    裁剪:保留 system(.[0]) + 最近 keep 组问答(每组 user+assistant 两条)。不裁剪会让
    审批链无上限增长——越判越慢越贵,更危险的是历史裁定会**锚化**后续判断。
    裁剪阈值/切片与 bash 旧实现逐条对齐(jq `.[(-2*keep):]`,keep=0 时即整段,刻意不改)。
    """
    try:
        msgs = json.loads(auth_json)
    except Exception:
        msgs = []
    if role == "":
        role = "user"
    try:
        k = int(keep)
    except (TypeError, ValueError):
        k = 0
    msgs.append({"role": role, "content": addctx})
    if len(msgs) > 1 + 2 * k:
        msgs = msgs[0:1] + msgs[-2 * k:]
    return json.dumps(msgs, ensure_ascii=False)


def cmd_auth_add(argv) -> int:
    content_file = argv[0] if len(argv) > 0 else ""
    role = argv[1] if len(argv) > 1 else "user"
    keep = argv[2] if len(argv) > 2 else "0"
    _contract.write_text(auth_add(_contract.read_stdin_text(), _read_text(content_file), role, keep))
    return _contract.EXIT_OK


def inject(messages, mark: str, text: str):
    """往 **system(content)里**注入一段带标记的清单，幂等。

    等价于现有 jq（skills/MCP/auto 三处共用同一形状）：
      `.[0].content = ((.[0].content | split($mark)[0]) + "\\n\\n" + $mark + "\\n" + $list)`
    `split($mark)[0]` 取的是"第一个标记之前的部分"，所以重复调用不会叠加（先剥旧段再贴新段）。
    """
    out = list(messages)
    if not out:
        return out
    m0 = dict(out[0])
    cur = m0.get("content")
    if not isinstance(cur, str):
        cur = "" if cur is None else str(cur)
    m0["content"] = cur.split(mark)[0] + "\n\n" + mark + "\n" + text
    out[0] = m0
    return out


def init(system_text: str = "You are a helpful assistant."):
    """新会话的初始 messages（等价 `jq -n '[{role:"system",content:"..."}]'`）。"""
    return [{"role": "system", "content": system_text}]


def cmd_inject(argv) -> int:
    mark = argv[0] if len(argv) > 0 else ""
    list_file = argv[1] if len(argv) > 1 else ""
    msgs = json.loads(_contract.read_stdin_text())
    _contract.write_text(json.dumps(inject(msgs, mark, _read_text(list_file)),
                                   ensure_ascii=False, indent=2) + "\n")
    return _contract.EXIT_OK


def cmd_init(argv) -> int:
    text = argv[0] if argv and argv[0] else "You are a helpful assistant."
    _contract.write_text(json.dumps(init(text), ensure_ascii=False, indent=2) + "\n")
    return _contract.EXIT_OK


def _content_text(m):
    """等价 jq 片段 `_JQ_CONTENT_TEXT`：数组内容拼 text 部件 + **图片只留计数**。

    "图片本体不进文本转写，但留一句'此处原有 N 张图'"——否则摘要/提炼会把'用户看过图'
    这件事整个抹掉。口径必须与 jq 侧逐字一致，否则同一段对话在压缩与提炼两条路上
    会给出不同文本（历史上正是这种漂移导致摘要后半段丢失）。
    """
    c = m.get("content")
    if isinstance(c, list):
        txt = "\n".join((it.get("text") or "") for it in c
                        if isinstance(it, dict) and it.get("type") == "text")
        n = sum(1 for it in c if isinstance(it, dict) and it.get("type") == "image_url")
        if n > 0:
            txt += "\n(此处原有 %d 张原图,图片本体不进文本转写)" % n
        return txt
    return c if isinstance(c, str) else ("" if c is None else str(c))


def render_for_summary(messages, start=1, cut=None):
    """把 messages 转成"人读的对话文本"（压缩摘要 / 技能提炼**共用同一口径**）。

    等价 jq：`.[$start:$cut][] | if .tool_calls != null then "[assistant → 工具调用] " +
    (.tool_calls|tostring) else "[" + .role + "] " + <_JQ_CONTENT_TEXT> end`

    两处调用原本各写一份（`[$start:$cut]` 与 `[1:]`），只是范围不同 —— 合并成一个函数，
    避免"两份格式化逻辑漂移"（那种漂移曾导致摘要凭空少一段，且不报错）。
    """
    seg = messages[start:cut] if cut is not None else messages[start:]
    lines = []
    for m in seg:
        if not isinstance(m, dict):
            continue
        tc = m.get("tool_calls")
        if tc is not None:
            lines.append("[assistant → 工具调用] "
                         + json.dumps(tc, ensure_ascii=False, separators=(",", ":")))
        else:
            lines.append("[%s] %s" % (m.get("role"), _content_text(m)))
    return "\n".join(lines)


def cmd_render_summary(argv) -> int:
    """stdin = messages JSON；argv[0]=start(默认1)、argv[1]=cut(空=到末尾)。"""
    start = int(argv[0]) if len(argv) > 0 and argv[0] != "" else 1
    cut = int(argv[1]) if len(argv) > 1 and argv[1] != "" else None
    msgs = json.loads(_contract.read_stdin_text())
    _contract.write_text(render_for_summary(msgs, start, cut) + "\n")
    return _contract.EXIT_OK


def append_user(messages, content):
    """追加一条 `{"role":"user","content":…}`。等价 jq `. + [{role:"user",content:$c}]`。

    注意这里**只**追加一条纯文本消息，不走 `build()`（不加 reasoning_content、不动
    tool 相关字段）—— 调用点是"让模型调 set_title 工具生成标题"，不需要那些。
    """
    return list(messages) + [{"role": "user", "content": content}]


def cmd_append_user(argv) -> int:
    content = argv[0] if argv else ""
    msgs = json.loads(_contract.read_stdin_text())
    _contract.write_text(json.dumps(append_user(msgs, content), ensure_ascii=False))
    return _contract.EXIT_OK


_COMMANDS = {
    "add": cmd_add,
    "auth_add": cmd_auth_add,
    "inject": cmd_inject,
    "init": cmd_init,
    "render_summary": cmd_render_summary,
    "append_user": cmd_append_user,
}


def main(argv) -> int:
    if not argv or argv[0] not in _COMMANDS:
        sys.stderr.write(
            "msg: 未知子命令 %r(可用: %s)\n"
            % (argv[0] if argv else "", ", ".join(sorted(_COMMANDS)))
        )
        return _contract.EXIT_FAIL
    return _COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))