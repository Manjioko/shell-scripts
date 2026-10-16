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


_COMMANDS = {
    "add": cmd_add,
    "auth_add": cmd_auth_add,
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