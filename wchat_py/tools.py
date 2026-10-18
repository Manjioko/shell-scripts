#!/usr/bin/env python3
"""工具注册表的**唯一出处**（取代 wchat.sh 的 `tools()`，W12-5 地基）。

设计要点（实测得出，别改）：
1. **无变换分支必须"原样输出资源文件"**，不能 parse 再 dump —— bash 的 `tools()` 在那条
   分支上是把 170 行 JSON 字面量**按原文**吐出来的（多行、带缩进）。若这里重新序列化，
   字节就变了；而 `tools=` 的字节会进请求体，进而影响 token 计数与**前缀缓存命中**
   （前缀一变，那之后的 token 从打折变全价）。实测：资源文件 11891 字节 == bash 输出。
2. 两个变换是**独立**的（不是 if/else）：① 接口不支持原生联网 ⇒ 剔除 `web_search`；
   ② `MCP_ENABLE=1` 且 MCP 清单非空 ⇒ 追加 MCP 工具。写成 if/else 会造出
   "provider=none 且 -mcp 时 MCP 被一起丢掉" 的组合漏洞（本地桩网关测试的默认形状）。
3. 变换分支的输出形状对齐 `jq -c`：紧凑（`,` `:` 无空格）+ **保持键序** + 中文不转义。
"""
import json
import os
import sys

_SPEC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools_spec.json")


def spec_text() -> str:
    """静态 schema 的**原文**（无变换分支直接用它，保证逐字节相同）。"""
    with open(_SPEC, encoding="utf-8") as f:
        return f.read()


def _compact(obj) -> str:
    """对齐 `jq -c`：紧凑 + 不转义中文 + 保持插入顺序。"""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def build(provider="", mcp_enable=0, mcp_tools=None):
    """返回工具清单 JSON 文本。"""
    text = spec_text()
    need_filter = (provider == "none")
    need_mcp = bool(mcp_enable) and bool(mcp_tools) and mcp_tools != []
    if not need_filter and not need_mcp:
        return text                      # 原样字节（见模块头第 1 点）
    tools = json.loads(text)
    if need_filter:
        tools = [t for t in tools
                 if (t.get("function") or {}).get("name") != "web_search"]
    if need_mcp:
        tools = tools + list(mcp_tools)
    return _compact(tools)


def cmd_build(argv) -> int:
    """argv: provider mcp_enable mcp_tools_json_file(可空)。"""
    provider = argv[0] if len(argv) > 0 else ""
    mcp_enable = int(argv[1]) if len(argv) > 1 and argv[1] else 0
    mcp_tools = None
    if len(argv) > 2 and argv[2]:
        try:
            with open(argv[2], encoding="utf-8") as f:
                mcp_tools = json.load(f)
        except FileNotFoundError:
            mcp_tools = None
        except Exception as e:
            sys.stderr.write("tools: MCP 清单解析失败: %s\n" % e)
            return 2
    sys.stdout.write(build(provider, mcp_enable, mcp_tools))
    return 0


_COMMANDS = {"build": cmd_build}


def main(argv) -> int:
    if not argv or argv[0] not in _COMMANDS:
        sys.stderr.write("tools: 未知子命令 %r(可用: %s)\n"
                         % (argv[0] if argv else "", ", ".join(sorted(_COMMANDS))))
        return 2
    return _COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

