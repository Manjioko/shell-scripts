#!/usr/bin/env python3
"""MCP 桥接(Python 入口):复用外部 mcporter CLI,不自己实现 daemon/传输。

bash 侧对应 mcp_boot + mcp_tools/mcp_schema/mcp_call。这里只做:
  list_json()       -> `mcporter list --json` 的 servers/tools 清单
  schema(server,tool) -> `mcporter list s.t --schema --json`
  call(server,tool,args) -> `mcporter call s.t --args ... --output json`
外部进程一律 subprocess 列表调用(不拼接 shell),超时即杀。
"""
import json
import os
import shutil
import subprocess

PKG = os.environ.get("MCP_PKG", "mcporter@0.14.1")
LAUNCHER = os.environ.get("MCP_LAUNCHER", "npx")

TOOL_SPECS = [
    {"type": "function", "function": {
        "name": "mcp_tools",
        "description": "列出某个已启用 MCP server 的工具名+一行摘要。",
        "parameters": {"type": "object",
                       "properties": {"server": {"type": "string"}},
                       "required": ["server"]}}},
    {"type": "function", "function": {
        "name": "mcp_schema",
        "description": "取单个 MCP 工具的完整参数结构(参数报错后用一次)。",
        "parameters": {"type": "object",
                       "properties": {"server": {"type": "string"},
                                      "tool": {"type": "string"}},
                       "required": ["server", "tool"]}}},
    {"type": "function", "function": {
        "name": "mcp_call",
        "description": "调用一个 MCP 工具并取回结果。args 须符合该工具参数结构。",
        "parameters": {"type": "object",
                       "properties": {"server": {"type": "string"},
                                      "tool": {"type": "string"},
                                      "args": {"type": "object", "additionalProperties": True}},
                       "required": ["server", "tool"]}}},
]


def _run(argv, timeout=60):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return None, "找不到启动器 %s" % argv[0]
    except subprocess.TimeoutExpired:
        return None, "mcporter 超时(结果未知,严禁原样重发)"
    return p.stdout, (p.stderr if p.returncode != 0 else None)


def list_json(timeout=60):
    """返回 (servers 列表, err)。servers = [{name,description,tools:[{name,description}]}]"""
    if not shutil.which(LAUNCHER):
        return None, "找不到启动器 %s(本次按无 MCP 运行)" % LAUNCHER
    out, err = _run([LAUNCHER, "-y", PKG, "list", "--json"], timeout=timeout)
    if out is None:
        return None, err
    try:
        d = json.loads(out)
    except ValueError:
        return None, "mcporter list 输出非 JSON: %s" % (err or out[:120])
    srv = d.get("servers") or []
    only = [x for x in (os.environ.get("MCP_ONLY", "") or "").replace(",", " ").split() if x]
    if only:
        srv = [s for s in srv if s.get("name") in only]
    return srv, None


def manifest(srv, max_tools=40):
    """系统提示用的一行摘要。"""
    lines = ["## 可用外部能力(MCP)"]
    for s in srv or []:
        names = [t.get("name") for t in (s.get("tools") or [])][:max_tools]
        lines.append("- %s: %s。工具: %s" % (s.get("name"), s.get("description") or "",
                                             ", ".join(names) or "(无)"))
    return "\n".join(lines)


def tools_text(srv, server):
    for s in srv or []:
        if s.get("name") == server:
            return "\n".join("- %s: %s" % (t.get("name"), t.get("description") or "")
                             for t in (s.get("tools") or [])) or "(该 server 无工具)"
    return "错误:server '%s' 未启用。可用: %s" % (server, ", ".join(x.get("name") for x in srv or []))


def schema(server, tool, timeout=60):
    out, err = _run([LAUNCHER, "-y", PKG, "list", "%s.%s" % (server, tool),
                     "--schema", "--json"], timeout=timeout)
    if out is None:
        return "错误:取 schema 失败: %s" % err
    return out.strip() or (err or "(无 schema 输出)")


def call(server, tool, args, timeout=120):
    aj = json.dumps(args or {}, ensure_ascii=False)
    out, err = _run([LAUNCHER, "-y", PKG, "call", "%s.%s" % (server, tool),
                     "--args", aj, "--output", "json",
                     "--timeout", str(int(timeout * 1000))], timeout=timeout + 15)
    if out is None:
        return "错误:MCP 调用失败: %s" % err
    try:
        d = json.loads(out)
    except ValueError:
        return out.strip() or (err or "(无输出)")
    texts = [c.get("text", "") for c in (d.get("content") or []) if c.get("type") == "text"]
    res = "\n".join(texts) if texts else json.dumps(d, ensure_ascii=False)
    if d.get("isError"):
        return "错误(MCP isError): %s" % res
    return res