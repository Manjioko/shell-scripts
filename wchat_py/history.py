"""history —— 历史存档里的**纯 JSON 变换**(迁移自 wchat.sh 的 save_history/get_history)。

只摘"消息结构的变形"这两段,上屏/文件名清洗/全局赋值/文件 IO 全留 bash:
  · `pack`              —— 存盘前把 messages 包成存档对象,并**剥掉 system 里的注入段**
                           (技能/auto/MCP 清单不随历史存档,加载时重新注入最新列表);
  · `drop_orphan_tools` —— 加载时剔除"孤儿 tool"(前一条不是含其 tool_call_id 的
                           assistant),否则 API 400 且无输出。

退出码(见 `_contract`):
  0  成功,结果写 stdout
  1  未知子命令

用法:
  python3 -m wchat_py.history pack <model> <thinking> <mark> <mark2> <mark3>   # stdin=messages
  python3 -m wchat_py.history drop_orphan_tools                                # stdin=messages
"""

from __future__ import annotations

import json
import sys

from . import _contract


def pack(messages_json: str, model: str, thinking: str,
         mark: str, mark2: str, mark3: str) -> str:
    """存盘对象:`{model, messages(已剥注入段), thinking:{type}, reasoning_effort, stream}`。

    剥离 = `.content | split($mark)[0] | split($mark2)[0] | split($mark3)[0]`:
    每刀取第一次出现**之前**的部分(标记不在则原样)。auto 注入段必须剥,否则下次普通模式
    加载这份历史,system 里还写着"当前处于 auto 模式"。
    """
    msgs = json.loads(messages_json)
    if msgs:
        c = msgs[0].get("content")
        if isinstance(c, str):
            for mk in (mark, mark2, mark3):
                if mk:
                    c = c.split(mk)[0]
            msgs[0]["content"] = c
    obj = {
        "model": model,
        "messages": msgs,
        "thinking": {"type": thinking},
        "reasoning_effort": "high",
        "stream": True,
    }
    return json.dumps(obj, ensure_ascii=False)


def drop_orphan_tools(messages_json: str):
    """剔除孤儿 tool 消息(前一条 assistant 不含其 tool_call_id 的,一律丢)。"""
    try:
        msgs = json.loads(messages_json)
    except Exception:
        msgs = []
    out = []
    for m in msgs:
        if m.get("role") == "tool":
            ok = False
            if out and out[-1].get("role") == "assistant":
                tcs = out[-1].get("tool_calls") or []
                ok = any(tc.get("id") == m.get("tool_call_id") for tc in tcs)
            if ok:
                out.append(m)
            # else: 丢掉(孤儿)
        else:
            out.append(m)
    return out


def cmd_pack(argv) -> int:
    model = argv[0] if len(argv) > 0 else ""
    thinking = argv[1] if len(argv) > 1 else ""
    mark = argv[2] if len(argv) > 2 else ""
    mark2 = argv[3] if len(argv) > 3 else ""
    mark3 = argv[4] if len(argv) > 4 else ""
    _contract.write_text(pack(_contract.read_stdin_text(), model, thinking, mark, mark2, mark3))
    return _contract.EXIT_OK


def cmd_drop_orphan_tools(argv) -> int:
    _contract.write_text(json.dumps(drop_orphan_tools(_contract.read_stdin_text()),
                                    ensure_ascii=False))
    return _contract.EXIT_OK


_COMMANDS = {
    "pack": cmd_pack,
    "drop_orphan_tools": cmd_drop_orphan_tools,
}


def main(argv) -> int:
    if not argv or argv[0] not in _COMMANDS:
        sys.stderr.write(
            "history: 未知子命令 %r(可用: %s)\n"
            % (argv[0] if argv else "", ", ".join(sorted(_COMMANDS)))
        )
        return _contract.EXIT_FAIL
    return _COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

