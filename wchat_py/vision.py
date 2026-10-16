"""vision —— 视觉转述请求/响应的纯逻辑(迁移自 wchat.sh 的 vision_*)。

· `build_body`  —— 组装转述请求体(model + parts 文件 + 提示词 + 可选 system);
· `parse_resp`  —— 响应取文归一:多数网关 content 是字符串,部分(Anthropic 风格)拆成
                   parts 数组,不归一会把对象字面量当转述文本喂给模型。

退出码(见 `_contract`):0 成功 / 1 未知子命令。

用法:
  python3 -m wchat_py.vision build_body <model> <parts_file> <prompt> [sys]
  python3 -m wchat_py.vision parse_resp   # stdin=响应 JSON -> stdout=转述文本
"""

from __future__ import annotations

import json
import sys

from . import _contract


def build_body(model: str, parts_file: str, prompt: str, sys_text: str) -> dict:
    try:
        with open(parts_file, "rb") as fh:
            parts = json.loads(fh.read().decode("utf-8", "replace"))
    except Exception:
        parts = []
    messages = []
    if sys_text != "":
        messages.append({"role": "system", "content": sys_text})
    messages.append({"role": "user",
                     "content": parts + [{"type": "text", "text": prompt}]})
    # extra=$(vision_extra_fields) 恒为 `, thinking: {type:"disabled"}`
    return {"model": model, "messages": messages,
            "thinking": {"type": "disabled"}, "stream": False}


def _tostring(x) -> str:
    """对齐 jq `tostring`:字符串原样,其它按 JSON 编码(null/true/false/数字/对象)。"""
    if isinstance(x, str):
        return x
    if x is None:
        return "null"
    if x is True:
        return "true"
    if x is False:
        return "false"
    return json.dumps(x, ensure_ascii=False)


def parse_resp(resp_json: str) -> str:
    """响应取文归一。与 jq 旧实现对齐:解析失败/无内容 → 空串。"""
    try:
        r = json.loads(resp_json)
    except Exception:
        return ""
    try:
        c = r["choices"][0]["message"]["content"]
    except Exception:
        c = None
    if isinstance(c, list):
        parts = []
        for it in c:
            if isinstance(it, dict):
                t = it.get("text")
                parts.append(t if isinstance(t, str) else "")
            else:
                parts.append(_tostring(it))
        res = "".join(parts)
    elif c is None:
        res = None
    else:
        res = c
    if res is None or res is False:      # jq `// ""` 对 null/false 取空
        return ""
    return res if isinstance(res, str) else _tostring(res)


def cmd_build_body(argv) -> int:
    model = argv[0] if len(argv) > 0 else ""
    parts_file = argv[1] if len(argv) > 1 else ""
    prompt = argv[2] if len(argv) > 2 else ""
    sys_text = argv[3] if len(argv) > 3 else ""
    _contract.write_text(json.dumps(build_body(model, parts_file, prompt, sys_text),
                                    ensure_ascii=False))
    return _contract.EXIT_OK


def cmd_parse_resp(argv) -> int:
    _contract.write_text(parse_resp(_contract.read_stdin_text()))
    return _contract.EXIT_OK


_COMMANDS = {
    "build_body": cmd_build_body,
    "parse_resp": cmd_parse_resp,
}


def main(argv) -> int:
    if not argv or argv[0] not in _COMMANDS:
        sys.stderr.write(
            "vision: 未知子命令 %r(可用: %s)\n"
            % (argv[0] if argv else "", ", ".join(sorted(_COMMANDS)))
        )
        return _contract.EXIT_FAIL
    return _COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

