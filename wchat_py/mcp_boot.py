#!/usr/bin/env python3
"""MCP 入口引导(不改 cli.py):解析 -mcp、暴露 mcp_tools/mcp_schema/mcp_call、
包装 cli._run_tool 分发到 mcp_bridge。由 wchat 入口在 main() 前调用 install()。"""
import json
import os
import sys

_CALLS = [0]
_OWN = [False]
_LOCK_KEEP = [False]


def _lock_file():
    ct = os.environ.get("CT_HOME") or os.path.join(os.environ.get("HOME", ""), ".ct")
    return os.path.join(ct, "mcp", "owners")


def _lock_claim():
    f = _lock_file()
    try:
        os.makedirs(os.path.dirname(f), exist_ok=True)
        try:
            os.chmod(os.path.dirname(f), 0o700)
        except OSError:
            pass
        keep = []
        if os.path.isfile(f):
            for line in open(f, encoding="utf-8", errors="replace"):
                pid = line.strip().split("\t")[0]
                if not pid.isdigit():
                    continue
                try:
                    os.kill(int(pid), 0)
                    keep.append(pid)
                except OSError:
                    pass
        if keep:
            sys.stderr.write("⚠ 已有 wchat 会话独占 MCP(pid %s),本次不启用 MCP。\n" % keep[0])
            return False
        with open(f, "w", encoding="utf-8") as fp:
            fp.write("%d\n" % os.getpid())
        _LOCK_KEEP[0] = True
        return True
    except OSError:
        return True   # 锁目录建不出来:不阻塞,只是失去独占


def _lock_release():
    if not _LOCK_KEEP[0]:
        return
    f = _lock_file()
    try:
        if os.path.isfile(f):
            keep = [ln for ln in open(f, encoding="utf-8", errors="replace")
                    if ln.strip().split("\t")[0] != str(os.getpid())]
            with open(f, "w", encoding="utf-8") as fp:
                fp.writelines(keep)
    except OSError:
        pass


def _atexit_stop():
    from wchat_py import mcp_bridge as mb
    _lock_release()
    if not _OWN[0]:
        return
    if mb.sock_state() != 0:
        return
    if mb.daemon_stop():
        sys.stderr.write("  [mcp] daemon 已随本会话优雅停止(它忙时会自己拒绝,那种情况会保留)\n")
    else:
        sys.stderr.write("  [mcp] daemon 未停止(可能仍有活跃调用)。手工: npx -y %s daemon stop\n"
                         % mb.PKG)
    sys.stderr.flush()


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
        try:
            maxc = int(os.environ.get("AUTO_MAX_MCP_CALLS") or 0)
        except ValueError:
            maxc = 0
        if maxc > 0 and _CALLS[0] >= maxc:
            audit.record(os.environ.get("AUTO_AUDIT_LOG", ""), "mcp",
                         server=server, tool=tool, decision="deny", reason="quota")
            return ("错误:MCP 调用次数已达本会话上限(%d)。这是进度刹车,不是权限判定;"
                    "停止重试,把结果与卡点告诉用户。" % maxc)
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
        _CALLS[0] += 1
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
    if not _lock_claim():
        os.environ["MCP_ENABLE"] = "0"
        return
    import atexit
    atexit.register(_atexit_stop)
    _ct = os.environ.get("CT_HOME") or os.path.join(os.environ.get("HOME", ""), ".ct")
    os.environ.setdefault("MCP_SAVE_IMAGES", os.path.join(_ct, "mcp", "images"))
    _before = mcp_bridge.sock_state()
    srv, err = mcp_bridge.list_json()
    _OWN[0] = (_before != 0 and mcp_bridge.sock_state() == 0)
    if srv is None:
        sys.stderr.write("⚠ -mcp 已请求,但 MCP 不可用: %s\n" % err)
    else:
        os.environ["MCP_MANIFEST"] = mcp_bridge.manifest(srv)
        sys.stderr.write("🛡 MCP 已启用: %d 个 server(%s)\n"
                         % (len(srv), ", ".join(s.get("name") for s in srv)))