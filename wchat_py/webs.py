"""webs —— 联网搜索工具的纯逻辑(迁移自 wchat.sh 的 web_search_build_body)。

按 provider 出原生搜索的**请求体**;新增一家接口 = 这里加一个分支。
不认识/未启用的 provider 返回 rc=1(业务性结果,调用方回退通用 web_fetch)。

退出码(见 `_contract`):
  0  成功,请求体 JSON 写 stdout
  1  该 provider 不支持(不回退 bash,调用方据此换路)

用法:
  python3 -m wchat_py.webs build_body <provider> <query> <model>
  python3 -m wchat_py.webs fetch_error <http_code> <url>   # -> 归因文案
"""

from __future__ import annotations

import json
import sys

from . import _contract


def build_body(provider: str, query: str, model: str):
    if provider == "mimo":
        # mimo 私有:tools 数组声明 web_search;force_search 强制必搜(否则模型可能凭记忆作答)
        return {
            "model": model,
            "messages": [{"role": "user", "content": query}],
            "tools": [{
                "type": "web_search",
                "search_result": True,
                "keyword": True,
                "max_keyword": 5,
                "force_search": True,
                "limit": 10,
            }],
            "thinking": {"type": "disabled"},
            "reasoning_effort": "high",
            "stream": False,
        }
    if provider == "qwen":
        # dashscope/qwen:顶层 enable_search 开联网,forced_search 强制必搜;
        # enable_thinking:false 关思考链(实测同查询开着要白烧 1429 reasoning token)。
        return {
            "model": model,
            "messages": [{"role": "user", "content": query}],
            "enable_search": True,
            "enable_thinking": False,
            "search_options": {"forced_search": True, "enable_source": False},
            "stream": False,
        }
    return None


def fetch_error(code: str, url: str) -> str:
    """抓取失败时按 HTTP 状态码细分原因,让模型正确归因(网络/站点问题,换 shell 命令不会改善)。"""
    if code == "000":
        return ("错误:无法连接 %s(HTTP 000:网络不可达或超时。换用 shell 命令不会改善,"
                "请更换站点或搜索引擎,或向用户说明网络限制)" % url)
    if code == "403" or code == "429":
        return ("错误:%s 返回 HTTP %s(被站点拒绝/限流,可能是反爬。"
                "换用 shell 命令不会改善,请更换站点)" % (url, code))
    if code[:1] in ("4", "5"):
        return "错误:%s 返回 HTTP %s(站点拒绝或服务器错误,请更换站点)" % (url, code)
    return "错误:无法获取 %s(HTTP %s,空响应)" % (url, code)


def cmd_build_body(argv) -> int:
    provider = argv[0] if len(argv) > 0 else ""
    query = argv[1] if len(argv) > 1 else ""
    model = argv[2] if len(argv) > 2 else ""
    body = build_body(provider, query, model)
    if body is None:
        return _contract.EXIT_FAIL
    _contract.write_text(json.dumps(body, ensure_ascii=False))
    return _contract.EXIT_OK


def cmd_fetch_error(argv) -> int:
    code = argv[0] if len(argv) > 0 else ""
    url = argv[1] if len(argv) > 1 else ""
    _contract.write_text(fetch_error(code, url))
    return _contract.EXIT_OK


_COMMANDS = {
    "build_body": cmd_build_body,
    "fetch_error": cmd_fetch_error,
}


def main(argv) -> int:
    if not argv or argv[0] not in _COMMANDS:
        sys.stderr.write(
            "webs: 未知子命令 %r(可用: %s)\n"
            % (argv[0] if argv else "", ", ".join(sorted(_COMMANDS)))
        )
        return _contract.EXIT_FAIL
    return _COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))