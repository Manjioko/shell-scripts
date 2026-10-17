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


def _normalize_base_url(base_url: str) -> str:
    """把**完整端点**折成 SDK 要的"根"。

    本项目的历史约定是 `BASE_URL` = 完整端点(如 `https://api.deepseek.com/chat/completions`,
    见脚本「切换模型需要改这里」注释里列的三家地址),curl 就是 POST 这个整 URL。而
    SDK 的 `base_url` 语义是**根**,它会再拼 `/chat/completions` —— 直接透传会得到
    `.../chat/completions/chat/completions`,服务端回 **404**(2026-xx 用户实跑 `wchat -dp`
    复现)。所以这里剥掉已知的端点后缀;传进来的若是根(`.../v1`)则原样保留。
    """
    u = (base_url or "").rstrip("/")
    for suf in ("/chat/completions", "/completions"):
        if u.endswith(suf):
            return u[: -len(suf)]
    return u


def _make_client(base_url, api_key, api_key_header=""):
    """构造 SDK 客户端。缺依赖 → ImportError 由调用方转成 rc=2。"""
    from openai import OpenAI          # noqa: 延迟导入,便于"缺失时报清楚"
    headers = {}
    if api_key_header:                 # 如 MiMo 需要的兼容头 api-key
        headers[api_key_header] = api_key
    return OpenAI(base_url=_normalize_base_url(base_url), api_key=api_key,
                  default_headers=headers or None)


def _has_image(messages) -> bool:
    for m in messages:
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, list):
            for it in c:
                if isinstance(it, dict) and it.get("type") == _IMAGE_TYPE:
                    return True
    return False


def _thinking_type(v):
    """把 `thinking` 归一成**字符串**。

    它到 build_kwargs 时有两种形状:bash 建的请求体里已是 `{"type": "disabled"}`(curl 时代的
    形状,见 wchat.sh 请求体 jq 的 `thinking: {type: $thinking}`),也可能直接是字符串。
    **SDK 侧要字符串**(包成 extra_body 的那一层由这里负责)——漏了归一化就会双包成
    `{"type": {"type": "disabled"}}`,服务端 serde 把内层 map 的键名当枚举变体名去找,
    于是报 `thinking.type: unknown variant 'type', expected one of adaptive, enabled, disabled`
    (2026-xx 用户实跑 422 复现)。同一个漏归一化还会让下面 `thinking == "disabled"` 恒为假
    (对象 != 字符串),于是"带图 + 关思考"那条 reasoning_effort 规则**从未生效**。
    """
    if isinstance(v, dict):
        return v.get("type") or "disabled"
    return v or "disabled"


def build_kwargs(model, messages, tools, thinking):
    """把请求体各字段整理成 `client.chat.completions.create(**kwargs)`。

    `reasoning_effort` 的条件与旧 jq 一致:**带图且关思考**时取 `none`,否则 `high`
    (实测:qwen 网关"带图 + disabled + high"会 400)。
    """
    thinking = _thinking_type(thinking)
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


def _build_timeout(req):
    """给 SDK 的 timeout:停滞兜底走 **httpx 的 read timeout**。

    等价于 curl 时代的 `--speed-time "$SSE_STALL_TIMEOUT" --speed-limit 1`(连接建立后
    平均速率 <1B/s 持续该秒数即断开并报错),把"服务端静默挂死、读循环无限 pending"
    降级成可恢复的报错。**不能用总超时顶替**:总超时会把健康的长流一起砍掉。
    bash 侧由 `SSE_STALL_TIMEOUT` 派生 `export WCHAT_LLM_STALL`(单一出处仍在 bash)。
    """
    import httpx
    stall = req.get("stall") or os.environ.get("WCHAT_LLM_STALL", "")
    if stall:
        try:
            return httpx.Timeout(connect=15.0, read=float(stall),
                                 write=15.0, pool=15.0)
        except ValueError:
            pass
    total = req.get("timeout") or os.environ.get("WCHAT_LLM_TIMEOUT", "")
    if total:
        try:
            return float(total)
        except ValueError:
            pass
    return None


def _resolve_inputs(req):
    """把"请求输入"解析成 (messages, tools)。

    - `messages_file`：大 messages 走**文件**（ARG_MAX 防护），与 `messages` 互斥，文件优先；
    - `no_tools`：置真时 tools 强制 `[]`（与 jq 版 `no_tools ? "[]" : $(tools)` 同义）。

    这是 W12-4b-1 切换的前置：bash 侧只要交"输入"（model/thinking/no_tools/messages_file），
    不必再造整份请求体 —— 建体归 Python 一处，形状契约就只有一份。
    """
    msgs = req.get("messages", [])
    if req.get("messages_file"):
        with open(req["messages_file"], encoding="utf-8") as f:
            msgs = json.load(f)
    tools = req.get("tools", [])
    if req.get("no_tools"):
        tools = []
    return msgs, tools


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
    except Exception as e:
        # 凭据缺失/URL 非法等在**构造客户端**阶段就抛(SDK 不联网先校验 key)。
        # 必须在这里转成 error 事件:否则异常冒到 stderr,读循环把它当 api_error 拼进
        # "⚠️ API 请求失败: ..." 里,用户看到的是**一整段 Python Traceback**(用户实跑见过),
        # 既看不懂也暴露内部路径。
        _emit({"t": "error", "v": "客户端初始化失败(检查 API_KEY/BASE_URL): %s" % e})
        return _contract.EXIT_FAIL
    try:
        msgs, tools = _resolve_inputs(req)
    except Exception as e:
        _emit({"t": "error", "v": "读取 messages_file 失败: %s" % e})
        return _contract.EXIT_FAIL
    kwargs = build_kwargs(req.get("model", ""), msgs, tools,
                          req.get("thinking", "disabled"))
    to = _build_timeout(req)
    if to is not None:
        kwargs["timeout"] = to
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