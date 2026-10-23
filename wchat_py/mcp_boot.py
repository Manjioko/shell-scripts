#!/usr/bin/env python3
"""MCP 入口引导(不改 cli.py):解析 -mcp、暴露 mcp_tools/mcp_schema/mcp_call、
包装 cli._run_tool 分发到 mcp_bridge。由 wchat 入口在 main() 前调用 install()。"""
import json
import os
import sys


def _arg_mcp(argv):
    enable = False
    only = ""
    for a in argv:
        if a == "-mcp":
            enable = True
        elif a.startswith("-mcp="):
            enable = True
            only = a.split("=", 1)[1].replace(",", " ").strip()
    return enable, only


def _dispatch(name, args_json):
    from wchat_py import mcp_bridge as mb
    from wchat_py import audit
    try:
        a = json.loads(args_json or "{}")
    except ValueError:
        a = {}
    srv, err = mb.list_json()
    if srv is None:
        return "错误:MCP 不可用: %s" % err
    if name == "mcp_tools":
        return mb.tools_text(srv, a.get("server") or "")
    if name == "mcp_schema":
        return mb.schema(a.get("server") or "", a.get("tool") or "")
    if name == "mcp_call":
        server, tool = a.get("server") or "", a.get("tool") or ""
        names = {s.get("name") for s in srv}
        if server not in names:
            return "错误:server '%s' 未启用。可用: %s" % (server, ", ".join(sorted(names)))
        # 非 auto 首次使用该 server 需人工确认(会话内一次)
        granted = set((os.environ.get("MCP_GRANTED") or "").split())
        if os.environ.get("AUTO_MODE") not in ("full", "sandbox") and server not in granted:
            if sys.stdin.isatty():
                sys.stdout.write("⚠ 首次使用 MCP server『%s』,授权本会话? [y/N] " % server)
                sys.stdout.flush()
                try:
                    ans = (sys.stdin.readline() or "").strip().lower()
                except Exception:
                    ans = ""
                sys.stdout.write("\n"); sys.stdout.flush()
                if ans not in ("y", "yes"):
                    audit.record(os.environ.get("AUTO_AUDIT_LOG", ""), "mcp",
                                 server=server, tool=tool, decision="deny", reason="human_no")
                    return "用户未授权 MCP server '%s'" % server
            os.environ["MCP_GRANTED"] = (os.environ.get("MCP_GRANTED", "") + " " + server).strip()
        out = mb.call(server, tool, a.get("args") or {})
        audit.record(os.environ.get("AUTO_AUDIT_LOG", ""), "mcp",
                     server=server, tool=tool,
                     decision=("deny" if out.startswith("错误") else "allow"))
        return out
    return "错误:未知 MCP 工具 %s" % name


def install(argv=None):
    argv = list(argv if argv is not None else sys.argv)
    enable, only = _arg_mcp(argv)
    if not enable:
        return
    os.environ["MCP_ENABLE"] = "1"
    if only:
        os.environ["MCP_ONLY"] = only
    import wchat_py.cli as cli
    _orig = cli._run_tool

    def _rt(name, args_json):
        if name in ("mcp_tools", "mcp_schema", "mcp_call"):
            return _dispatch(name, args_json)
        return _orig(name, args_json)

    cli._run_tool = _rt
    # 把三个通用 MCP 工具并入工具 schema
    import wchat_py.tools as tools
    _ob = tools.build

    def _build(*a, **k):
        s = _ob(*a, **k)
        try:
            data = json.loads(s) if isinstance(s, str) else s
            data = list(data) + __import__("wchat_py.mcp_bridge", fromlist=["TOOL_SPECS"]).TOOL_SPECS
            return json.dumps(data, ensure_ascii=False) if isinstance(s, str) else data
        except Exception:
            return s

    tools.build = _build
    from wchat_py import mcp_bridge
    srv, err = mcp_bridge.list_json()
    if srv is None:
        sys.stderr.write("⚠ -mcp 已请求,但 MCP 不可用: %s\n" % err)
    else:
        os.environ["MCP_MANIFEST"] = mcp_bridge.manifest(srv)
        sys.stderr.write("🛡 MCP 已启用: %d 个 server(%s)\n"
                         % (len(srv), ", ".join(s.get("name") for s in srv)))

