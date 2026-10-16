"""llm —— W12-4:主/子请求的 HTTP/SSE 走**官方 OpenAI SDK**,不照搬 curl(见 docs §8.5)。

设计要点:
  · **依赖**:`openai` 是必需依赖(缺失 → 明确报错,不回退 curl);
  · **接口缝(seam)**:本模块把每个流事件编码成**一行 JSON** 写 stdout,由 bash 侧读去做
    终端渲染与累积。这样 HTTP/SSE 一次性收进 Python,终端层留在 bash(B 阶段)。
  · 事件形状:{"t":"text"|"reasoning"|"tool_calls"|"usage"|"done", "v":...}
  · 只做"发请求 + 解析流",不做任何终端绘制。

退出码(见 `_contract`):
  0  成功
  1  业务性失败(请求异常,stdout 里给 {"t":"error"} 一行)
  2  环境异常(缺 `openai` 依赖 / 入参非法)

用法:
  python3 -m wchat_py.llm check
  python3 -m wchat_py.llm stream        # stdin=请求 JSON -> stdout=事件 JSON-lines
"""

from __future__ import annotations

import json
import os
import sys

from . import _contract

DEP = "openai"
_IMAGE_TYPE = "image_url"


def _dep_error() -> str:
    return ("缺少 %s 依赖:请 `pip3 install %s`(W12-4 的 HTTP/SSE 层已改用官方 SDK,"
            "不再回退 curl)。" % (DEP, DEP))


def _make_client(base_url, api_key, api_key_header=""):
    """构造 SDK 客户端。缺依赖 → ImportError 由调用方转成 rc=2。"""
    from openai import OpenAI          # noqa: 延迟导入,便于"缺失时报清楚"
    headers = {}
    if api_key_header:                 # 如 MiMo 需要的兼容头 api-key
        headers[api_key_header] = api_key
    return OpenAI(base_url=base_url, api_key=api_key, default_headers=headers or None)


def _has_image(messages) -> bool:
    for m in messages:
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, list):
            for it in c:
                if isinstance(it, dict) and it.get("type") == _IMAGE_TYPE:
                    return True
    return False


def build_kwargs(model, messages, tools, thinking):
    """把请求体各字段整理成 `client.chat.completions.create(**kwargs)`。

    `reasoning_effort` 的条件与旧 jq 一致:**带图且关思考**时取 `none`,否则 `high`
    (实测:qwen 网关"带图 + disabled + high"会 400)。
    """
    effort = "none" if (thinking == "disabled" and _has_image(messages)) else "high"
    kw = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "reasoning_effort": effort,
        "extra_body": {"thinking": {"type": thinking}},
    }
    if tools:
        kw["tools"] = tools
    return kw


def _delta_reasoning(delta):
    """`reasoning_content` 是厂商额外字段:SDK 可能放在属性上,也可能在 model_extra。"""
    rc = getattr(delta, "reasoning_content", None)
    if rc is None:
        extra = getattr(delta, "model_extra", None) or {}
        rc = extra.get("reasoning_content")
    return rc


def _tool_delta(t):
    fn = getattr(t, "function", None)
    return {
        "index": getattr(t, "index", None),
        "id": getattr(t, "id", None),
        "name": getattr(fn, "name", None),
        "arguments": getattr(fn, "arguments", None),
    }


def encode_chunk(chunk) -> "list":
    """把 SDK 的一个流 chunk 编码成事件列表(纯函数,便于无依赖测试)。"""
    evs = []
    u = getattr(chunk, "usage", None)
    if u is not None:
        d = getattr(u, "prompt_tokens_details", None)
        evs.append({"t": "usage", "v": {
            "prompt": getattr(u, "prompt_tokens", None),
            "completion": getattr(u, "completion_tokens", None),
            "total": getattr(u, "total_tokens", None),
            # 刀1:缓存命中与图片计费(某些端点缺字段 → 0,不沿用上一轮)
            "cached": getattr(d, "cached_tokens", 0) if d is not None else 0,
            "image": getattr(d, "image_tokens", 0) if d is not None else 0}})
    for ch in (getattr(chunk, "choices", None) or []):
        d = getattr(ch, "delta", None)
        if d is not None:
            c = getattr(d, "content", None)
            if c:
                evs.append({"t": "text", "v": c})
            rc = _delta_reasoning(d)
            if rc:
                evs.append({"t": "reasoning", "v": rc})
            tc = getattr(d, "tool_calls", None)
            if tc:
                evs.append({"t": "tool_calls", "v": [_tool_delta(t) for t in tc]})
        fr = getattr(ch, "finish_reason", None)
        if fr:
            evs.append({"t": "done", "v": fr})
    return evs


def _emit(ev) -> None:
    sys.stdout.write(json.dumps(ev, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def cmd_check(argv) -> int:
    try:
        import openai  # noqa: F401
    except ImportError:
        sys.stderr.write(_dep_error() + "\n")
        return _contract.EXIT_ENV
    sys.stderr.write("openai 可用\n")
    return _contract.EXIT_OK


def cmd_stream(argv) -> int:
    try:
        req = json.loads(_contract.read_stdin_text())
    except Exception as e:
        sys.stderr.write("llm: 请求 JSON 解析失败: %s\n" % e)
        return _contract.EXIT_ENV
    # base_url / api_key / 兼容头:JSON 未给则取环境(bash 侧已 export BASE_URL/API_KEY)
    base_url = req.get("base_url") or os.environ.get("BASE_URL", "")
    api_key = (req.get("api_key") or os.environ.get("WCHAT_API_KEY")
               or os.environ.get("API_KEY", ""))
    hdr = req.get("api_key_header", "api-key")
    try:
        client = _make_client(base_url, api_key, hdr)
    except ImportError:
        sys.stderr.write(_dep_error() + "\n")
        return _contract.EXIT_ENV
    kwargs = build_kwargs(req.get("model", ""), req.get("messages", []),
                          req.get("tools", []), req.get("thinking", "disabled"))
    to = req.get("timeout") or os.environ.get("WCHAT_LLM_TIMEOUT", "")
    if to:
        try:
            kwargs["timeout"] = float(to)
        except ValueError:
            pass
    try:
        stream = client.chat.completions.create(**kwargs)
        for chunk in stream:
            for ev in encode_chunk(chunk):
                _emit(ev)
    except Exception as e:
        _emit({"t": "error", "v": str(e)})
        return _contract.EXIT_FAIL
    return _contract.EXIT_OK


_COMMANDS = {"check": cmd_check, "stream": cmd_stream}


def main(argv) -> int:
    if not argv or argv[0] not in _COMMANDS:
        sys.stderr.write("llm: 未知子命令 %r\n" % (argv[0] if argv else ""))
        return _contract.EXIT_ENV
    return _COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))