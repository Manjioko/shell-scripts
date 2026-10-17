#!/usr/bin/env python3
"""W12-4b：请求体构建（唯一出处，取代 wchat.sh 里 jq 版的 `make_request_body`）。

**字段顺序 = jq 的插入顺序**（model / messages / thinking / reasoning_effort / stream /
tools / stream_options）：parity 期要求与 jq 输出**逐字节相同**，而 `jq -n` 是按插入顺序
输出的，所以顺序本身就是契约的一部分（改了顺序 = 改了字节 = parity 红）。

只做**纯构建**：不读文件、不联网、不打日志。图片引用展开、刀1 观测（落盘状态）属于
W12-4b-2，见 docs §8.5。
"""
import json
import sys

IMAGE_TYPE = "image_url"


def has_image(messages) -> bool:
    """与 jq 侧 `_JQ_HAS_IMAGE` 同义：messages 里有没有 image_url 部件。"""
    for m in messages:
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, list):
            for it in c:
                if isinstance(it, dict) and it.get("type") == IMAGE_TYPE:
                    return True
    return False


def thinking_type(v):
    """`thinking` 可能以两种形状到达：对象 `{"type": ...}`（bash 建的请求体）或字符串。"""
    if isinstance(v, dict):
        return v.get("type") or "disabled"
    return v or "disabled"


def build_body(model, messages, thinking, tools, with_image=None):
    """构建请求体。`with_image` 仅测试用（不传则由 messages 推导）。

    注意两条**必须与 jq 侧逐字对齐**的判据：
      1. `thinking` 无条件包成 `{"type": <字符串>}`（多包一层会被服务端 serde 当枚举变体，422）；
      2. `reasoning_effort`：**带图且关思考** ⇒ `none`，否则 `high`
         （qwen 网关"带图 + disabled + high"会 400；实测见原 make_request_body 注释）。
    """
    t = thinking_type(thinking)
    img = has_image(messages) if with_image is None else bool(with_image)
    return {
        "model": model,
        "messages": messages,
        "thinking": {"type": t},
        "reasoning_effort": "none" if (t == "disabled" and img) else "high",
        "stream": True,
        "tools": tools or [],
        "stream_options": {"include_usage": True},
    }


def cmd_body(argv) -> int:
    """stdin 收 `{"model","messages","thinking","tools"}`，stdout 出请求体 JSON。

    与 jq 版一样：**stdout 只出正文**（调用方是 `$( )`），任何诊断走 stderr。
    """
    try:
        req = json.loads(sys.stdin.read())
    except Exception as e:
        sys.stderr.write("request: 输入 JSON 解析失败: %s\n" % e)
        return 2
    body = build_body(req.get("model", ""), req.get("messages", []),
                      req.get("thinking", "disabled"), req.get("tools", []))
    sys.stdout.write(json.dumps(body, ensure_ascii=False, indent=2))
    return 0


_COMMANDS = {"body": cmd_body}


def main(argv) -> int:
    if not argv or argv[0] not in _COMMANDS:
        sys.stderr.write("request: 未知子命令 %r\n" % (argv[0] if argv else ""))
        return 2
    return _COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

