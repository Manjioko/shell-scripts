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
SOCK_PATH = os.path.expanduser("~/.mcporter/daemon/user.sock")


def sock_state():
    """2=无 socket;0=可连接(daemon 在跑);1=陈旧连不上。"""
    import socket as _s
    if not os.path.exists(SOCK_PATH):
        return 2
    s = _s.socket(_s.AF_UNIX, _s.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        s.connect(SOCK_PATH)
        s.close()
        return 0
    except Exception:
        return 1


def daemon_stop(timeout=20):
    out, err = _run([LAUNCHER, "-y", PKG, "daemon", "stop"], timeout=timeout)
    return out is not None

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


def _child_env():
    """给 mcporter 子进程的环境:钉死 MCPORTER_CONFIG。

    ⚠ 这一行在 Python 移植时丢过一次(bash 版 wchat_ref:500 有)。后果不是报错,
    而是**语义漂移**:不钉的话 mcporter 会按自己的规则把 `<cwd>/config/mcporter.json`
    也合并进来,于是 `-mcp chrome` 能不能用取决于你在哪个目录启动 wchat ——
    在 A 目录能用、在 B 目录报"server 不存在",且报错不会指向这个真因。

    只在清单文件**确实存在**时才钉:上游对"指向不存在的文件"是直接 ENOENT 报错、
    不做回落,钉一个不存在的路径会把"没配"变成"报错"。
    """
    env = dict(os.environ)
    p = config_path()
    if os.path.isfile(p):
        env["MCPORTER_CONFIG"] = p
    return env


def _run(argv, timeout=60):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           env=_child_env())
    except FileNotFoundError:
        return None, "找不到启动器 %s" % argv[0]
    except subprocess.TimeoutExpired:
        return None, "mcporter 超时(结果未知,严禁原样重发)"
    return p.stdout, (p.stderr if p.returncode != 0 else None)


LAST_COUNTS = {}


def config_path():
    """清单路径:**统一收在 ~/.ct/ 下**(与 config/history/skills 同处)。

    为什么不再沿用上游的默认位:那个目录里还有 daemon 的 socket、连接密钥与 OAuth
    vault,全是**运行时状态**。把"我们的清单"和它们混在一起,既不好审计(一个是配置、
    几个是凭据),也说不清"哪些该备份"。`~/.ct/` 是 wchat 自己的家,清单放这里。

    优先级:环境变量 MCP_CONFIG > ~/.ct/mcporter.json(CT_HOME 优先)。
    """
    if os.environ.get("MCP_CONFIG"):
        return os.environ["MCP_CONFIG"]
    ct = os.environ.get("CT_HOME") or os.path.join(os.environ.get("HOME") or "", ".ct")
    return os.path.join(ct, "mcporter.json")


def config_servers():
    """按配置声明顺序返回 server 名(只读 keys,不读值/凭据)。"""
    p = config_path()
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            d = json.load(f)
        return list((d.get("mcpServers") or {}).keys())
    except Exception:
        return []


def list_json(timeout=60):
    """返回 (servers 列表, err)。servers = [{name,description,tools:[{name,description}]}]"""
    global LAST_COUNTS
    if not shutil.which(LAUNCHER):
        return None, "找不到启动器 %s(本次按无 MCP 运行)" % LAUNCHER
    out, err = _run([LAUNCHER, "-y", PKG, "list", "--json"], timeout=timeout)
    if out is None:
        return None, err
    try:
        d = json.loads(out)
    except ValueError:
        return None, "mcporter list 输出非 JSON: %s" % (err or out[:120])
    LAST_COUNTS = d.get("counts") or {}
    srv = d.get("servers") or []
    _cm = config_map()
    for _s in srv:
        _al = (_cm.get(_s.get("name")) or {}).get("allowedTools")
        if isinstance(_al, list) and _al:
            _allow = set(str(x) for x in _al)
            _s["tools"] = [t for t in (_s.get("tools") or [])
                           if t.get("name") in _allow]
    only = [x for x in (os.environ.get("MCP_ONLY", "") or "").replace(",", " ").split() if x]
    if only:
        srv = [s for s in srv if s.get("name") in only]
    return srv, None


ROUTE_HINT = (
    "用 **mcp_call** 的场景:答案取决于**某个外部程序自己的运行态**——它界面渲染出来的结果、"
    "它内部的数据、它此刻的状态,或以工具为单位提供的任何\"只有那个程序里才知道\"的东西。\n"
    "与内置工具的分工:read_file/grep 读的是磁盘上的静态文本,web_fetch 取的是静态 HTML;"
    "**只要答案依赖 JS 执行后或程序运行后才成立的状态,内置工具一律给不出**——这时用 mcp_call,"
    "不要拿前两者凑一个看起来像的答案。\n"
    "每个 server 具体能做什么,以下面清单里它自己那行的说明为准(写\"未提供用途说明\"的表示信息缺失,"
    "先 mcp_tools 看它的工具名单,别猜)。"
)


def config_map():
    """配置里的 mcpServers 字典(只用结构,如 allowedTools;不输出凭据值)。"""
    p = config_path()
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            d = json.load(f)
        return d.get("mcpServers") or {}
    except Exception:
        return {}


def manifest(srv, max_bytes=None):
    """复刻 bash mcp_catalog_text:路由提示 + 每 server 工具名/说明,超预算折叠。"""
    try:
        cap = int(os.environ.get("MCP_CATALOG_MAX_BYTES") or 6144)
    except ValueError:
        cap = 6144
    if max_bytes is not None:
        try:
            cap = int(max_bytes)
        except ValueError:
            pass
    out = ROUTE_HINT + "\n"
    for s in srv or []:
        names = ", ".join(t.get("name", "") for t in (s.get("tools") or []))
        desc = s.get("description") or ""
        sd = ("  —— " + desc) if desc else \
            "  —— (未提供用途说明:先 mcp_tools 看它的工具名单,别凭名字猜它能做什么)"
        out += "- %s: %s%s\n" % (s.get("name"), names, sd)
    if len(out.encode("utf-8")) > cap:
        out = ROUTE_HINT + "\n"
        for s in srv or []:
            out += "- %s: %d 个工具(清单超限已折叠,用 mcp_tools 现查)\n" % (
                s.get("name"), len(s.get("tools") or []))
        out += "\n⚠ 外部能力过多已折叠,不要凭记忆猜工具名;先 mcp_tools <server> 再调。"
    return out


def tools_text(srv, server):
    for s in srv or []:
        if s.get("name") == server:
            body = "\n".join("- %s: %s" % (t.get("name"),
                                           (t.get("description") or "").replace("\n", " ")[:200])
                             for t in (s.get("tools") or [])) or "(该 server 无工具)"
            return ("MCP server '%s' 的工具(名称+一行摘要,不含参数结构;要结构用 mcp_schema):\n%s"
                    % (server, body))
    return "错误:server '%s' 未启用。可用: %s" % (server, ", ".join(x.get("name") for x in srv or []))


def schema(server, tool, timeout=60):
    out, err = _run([LAUNCHER, "-y", PKG, "list", "%s.%s" % (server, tool),
                     "--schema", "--json"], timeout=timeout)
    if out is None:
        return "错误:取 schema 失败: %s" % err
    return out.strip() or (err or "(无 schema 输出)")


def call(server, tool, args, timeout=120):
    aj = json.dumps(args or {}, ensure_ascii=False)
    argv = [LAUNCHER, "-y", PKG, "call", "%s.%s" % (server, tool),
            "--args", aj, "--output", "json", "--timeout", str(int(timeout * 1000))]
    _img_dir = os.environ.get("MCP_SAVE_IMAGES", "")
    if _img_dir:
        try:
            os.makedirs(_img_dir, exist_ok=True)
            argv += ["--save-images", _img_dir]
        except OSError:
            pass
    out, err = _run(argv, timeout=timeout + 15)
    if out is None:
        return "错误:MCP 调用失败: %s" % err
    try:
        d = json.loads(out)
    except ValueError:
        return out.strip() or (err or "(无输出)")
    texts = [c.get("text", "") for c in (d.get("content") or []) if c.get("type") == "text"]
    res = "\n".join(texts) if texts else json.dumps(d, ensure_ascii=False)
    nimg = len([c for c in (d.get("content") or []) if c.get("type") == "image"])
    if nimg:
        res += "\n(返回含 %d 个图片块,已保存到 %s;要看图请调用 read_image 读该目录下路径)" % (
            nimg, _img_dir or "(未配置 MCP_SAVE_IMAGES,图片未落盘)")
    if d.get("isError"):
        return "错误(MCP isError): %s" % res
    return res