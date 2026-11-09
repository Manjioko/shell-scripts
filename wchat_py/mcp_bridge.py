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
import tempfile

PKG = os.environ.get("MCP_PKG", "mcporter@0.14.1")
LAUNCHER = os.environ.get("MCP_LAUNCHER", "npx")
SOCK_PATH = os.path.expanduser("~/.mcporter/daemon/user.sock")


def sock_path():
    """daemon socket 路径。**必须尊重上游的两个环境变量**。

    以前这里是写死的 `~/.mcporter/daemon/user.sock`。后果不是"报个错",
    而是 `sock_state()` 回答的是**另一个 daemon** 的状态:
      · 上游用 `MCPORTER_DAEMON_DIR` 换了目录(测试隔离、或用户自己指定)时,
        我们会一直说"daemon 在跑/不在跑",而真实那个是相反的;
      · `_atexit_stop()` 正是拿它当"要不要停 daemon"的闸门 —— 判断错了就会把
        别人的 daemon 停掉,或者该停的时候不停(留下孤儿)。
    2026-11 实测:隔离测试里 sock_state() 恒为 0,因为它连的其实是默认 daemon。
    """
    s = os.environ.get("MCPORTER_DAEMON_SOCKET")
    if s:
        return os.path.expanduser(s)
    d = os.environ.get("MCPORTER_DAEMON_DIR")
    if d:
        return os.path.join(os.path.expanduser(d), "daemon", "user.sock")
    return os.path.expanduser(os.path.join("~", ".mcporter", "daemon", "user.sock"))


# 兼容:老代码/测试里有直接引用这个常量的地方(旧名保留一个默认值)
SOCK_PATH = os.path.expanduser("~/.mcporter/daemon/user.sock")


def sock_state():
    """2=无 socket;0=可连接(daemon 在跑);1=陈旧连不上。"""
    import socket as _s
    p = sock_path()
    if not os.path.exists(p):
        return 2
    s = _s.socket(_s.AF_UNIX, _s.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        s.connect(p)
        s.close()
        return 0
    except Exception:
        return 1


def daemon_stop(timeout=20):
    out, err = _run([LAUNCHER, "-y", PKG, "daemon", "stop"], timeout=timeout)
    return out is not None

ROUTE_HINT = (
    "用 **mcp_call** 的场景:答案取决于**某个外部程序自己的运行态**——它界面渲染出来的结果、"
    "它内部的数据、它此刻的状态,或以工具为单位提供的任何\"只有那个程序里才知道\"的东西。\n"
    "与内置工具的分工:read_file/grep 读的是磁盘上的静态文本,web_fetch 取的是静态 HTML;"
    "**只要答案依赖 JS 执行后或程序运行后才成立的状态,内置工具一律给不出**——这时用 mcp_call,"
    "不要拿前两者凑一个看起来像的答案。\n"
    "每个 server 具体能做什么,以下面清单里它自己那行的说明为准(写\"未提供用途说明\"的表示信息缺失,"
    "先 mcp_tools 看它的工具名单,别猜)。"
)


def _base_specs():
    """三个通用 MCP 工具的定义。**描述里不放目录**(目录是可变的,见 tool_specs)。"""
    return [
        {"type": "function", "function": {
            "name": "mcp_tools",
            "description": "列出 MCP server 的工具名+一行摘要。"
                           "不传 server = 看全部 server 及它们此刻是否可用(连不上的会带原因)。",
            "parameters": {"type": "object",
                           "properties": {"server": {"type": "string"}},
                           "required": []}}},
        {"type": "function", "function": {
            "name": "mcp_schema",
            "description": "取单个 MCP 工具的完整参数结构(参数报错后用一次)。",
            "parameters": {"type": "object",
                           "properties": {"server": {"type": "string"},
                                          "tool": {"type": "string"}},
                           "required": ["server", "tool"]}}},
        {"type": "function", "function": {
            "name": "mcp_call",
            "description": "调用一个 MCP 工具并取回结果。args 须符合该工具参数结构。"
                           "截图/渲染类工具返回的图片会直接作为图片交给你的眼睛(占 image token);"
                           "其他工具若返回图片,只落盘并给出确切路径,需要看时用 read_image 读它。",
            "parameters": {"type": "object",
                           "properties": {"server": {"type": "string"},
                                          "tool": {"type": "string"},
                                          "args": {"type": "object", "additionalProperties": True}},
                           "required": ["server", "tool"]}}},
    ]


TOOL_SPECS = _base_specs()


def tool_specs(snap=None):
    """工具定义 + 目录 + 路由提示(都写进 description)。

    为什么这些不放 system 消息:惰性模式下探测发生在会话中途,而 system 是 messages[0],
    开场就固化了 —— 想更新它就得重建整条 system,等于把 prompt 前缀缓存全废掉。
    工具 schema 是**每轮**重新构建的(`cli._call_llm` 每次都调 tools.build),挂在这里
    天然跟着状态走,还不多花一个字节。

    ⚠ `ROUTE_HINT` 必须在这里出现。它原来由 `manifest()` 注入 system;改成惰性后
    `MCP_MANIFEST` 置空,这块就两头落空了 —— 结果是模型手里有一把 mcp_* 工具却
    **不知道什么时候该用**,只能靠用户提醒。实测确认过这个回退,断言在
    `py_mcp_boot_test.py` 里钉着。
    """
    specs = _base_specs()
    # 路由提示挂在 mcp_call 上(模型是"想调工具"时才读它的描述)。
    for _s in specs:
        if _s["function"]["name"] == "mcp_call":
            _s["function"]["description"] = (
                "调用一个 MCP 工具并取回结果。args 须符合该工具参数结构。\n" + ROUTE_HINT)
    if not snap:
        return specs
    lines = []
    usable = [s.get("name") for s in snap
              if s.get("status") == "ok" and (s.get("tools") or [])]
    for s in snap:
        n = s.get("name") or "?"
        tools_n = len(s.get("tools") or [])
        if s.get("status") == "ok" and tools_n:
            lines.append("%s(%d 个工具)" % (n, tools_n))
        else:
            issue = s.get("issue") or {}
            raw = str(issue.get("rawMessage") or s.get("error") or "").replace("\n", " ")[:60]
            lines.append("%s(不可用%s)" % (n, (" · " + raw) if raw else ""))
    if lines:
        specs[0]["function"]["description"] += (
            "\n本次会话可用的 server:%s。"
            "清单里标『不可用』的不要再试,原因已附在你看到的括号里。"
            % ";".join(lines))
        _ = usable
    return specs


def catalog_text(snap, usable):
    """`mcp_tools` 不传 server 时的输出:全貌 + 失败原因。"""
    by_ok = {s.get("name") for s in (usable or [])}
    out = ["MCP 全貌(本次会话):"]
    for s in snap or []:
        n = s.get("name") or "?"
        tools_n = len(s.get("tools") or [])
        if n in by_ok:
            names = ", ".join(t.get("name", "") for t in (s.get("tools") or []))
            out.append("- %s · 可用 · %d 个工具: %s" % (n, tools_n, names))
        elif s.get("status") == "ok" and tools_n:
            out.append("- %s · 连上了但**本会话不提供**(被另一个 wchat 会话占用)" % n)
        else:
            issue = s.get("issue") or {}
            raw = str(issue.get("rawMessage") or s.get("error") or "?").replace("\n", " ")[:160]
            out.append("- %s · 不可用 · status=%s · 原因(上游原文): %s"
                       % (n, s.get("status") or "?", raw))
    if len(out) == 1:
        out.append("(清单为空:没有任何 server 被声明)")
    out.append("先用 mcp_schema 取参数结构再 mcp_call;标了不可用的不要重试。")
    return "\n".join(out)



# ---------------------------------------------------------------- 子进程环境
# 只传这些:它们决定"能不能跑起来"(找得到解释器、找得到家目录、编码正常),
# 而且**跨调用稳定**。
_ENV_KEEP = ("PATH", "HOME", "TMPDIR", "USER", "LOGNAME", "SHELL",
             "LANG", "LANGUAGE", "TERM", "TZ")
_ENV_PREFIX_KEEP = ("LC_", "MCPORTER_", "XDG_")


def _manifest_env_names():
    """清单里通过 `${VAR}` / `$env:VAR` 引用到的变量名。

    这些**必须**原样传给子进程,否则 `${GITHUB_TOKEN}` 之类会在 server 端变成空串,
    症状是"server 能连上但所有调用都 401",而报错完全不指向这个真因。
    """
    import re
    p = config_path()
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return set()
    names = set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)", text))
    names |= set(re.findall(r"\$env:([A-Za-z_][A-Za-z0-9_]*)", text))
    return names


def child_env():
    """给 mcporter 子进程的**最小稳定环境**。

    ⚠ 这是本项目最贵的一课(2026-11 用户实测三轮后定位):

    mcporter 把**子进程的整套环境变量**算进 daemon 的连接身份 —— 实测同一 cwd、
    同一份清单,只改一个环境变量,连接数就整套翻倍且永不回收:

        list --json                    → 4 条
        FOO=1 list --json              → 8 条
        BAR=2 list --json              → 12 条
        WCHAT_DEBUG=1 list --json      → 16 条

    而 wchat 原来传的是 `dict(os.environ)`,并且**自己在会话里不断改它**
    (`MCP_STATE` / `MCP_USABLE` / `MCP_GRANTED` / `MCP_ENABLE` / `MCP_SAVE_IMAGES` /
    `WCHAT_LAST_*_TOKENS` …)。于是每调一次 chrome 就多一条连接;chrome 类连接又
    永不自动回收,而浏览器 profile 是全局独占锁 —— 最后一定撞
    `The browser is already running for …/chrome-profile`。

    只传"能跑起来 + 跨调用稳定"的那几项,身份就恒定了。实测:env 怎么抖,
    连接数都恒等于 server 数,零冲突。

    `MCPORTER_*` 原样保留:那是上游自己的开关(`MCPORTER_CONFIG` / `DAEMON_DIR` /
    `DISABLE_CHROME_DEVTOOLS_RELAY` …),用户显式设了就该生效。
    注意 `BROKER_ENV`(上游自己也说是 broker 控制而非子进程输入)已在 identity
    计算里被排除,所以这部分不会反过来制造分裂。
    """
    env = {}
    for k, v in os.environ.items():
        if k in _ENV_KEEP or k.startswith(_ENV_PREFIX_KEEP):
            env[k] = v
    for k in _manifest_env_names():
        if k in os.environ and k not in env:
            env[k] = os.environ[k]
    p = config_path()
    if os.path.isfile(p):
        # 只在清单**确实存在**时才钉:上游对"指向不存在的文件"是直接 ENOENT 报错、
        # 不做回落,钉一个不存在的路径会把"没配"变成"报错"。
        env["MCPORTER_CONFIG"] = p
    # wchat 托管浏览器的**独立 profile**(B 计划)。
    # 为什么独立:chrome-devtools-mcp 默认用 `~/.cache/chrome-devtools-mcp/chrome-profile`,
    # 那是**全机独占锁** —— 任何别的程序(Claude Desktop、残留 daemon、你自己开的
    # chrome-devtools-mcp)抢到它,wchat 这边就全体 `The browser is already running`。
    # 换成 wchat 自己的目录后,谁在外面抢都与我们无关。
    # 用 ${VAR} 占位(而不是把绝对路径写进清单)是为了:①清单可跨机器复制;
    # ②用户想换 profile 位置只改这一个环境变量。
    if not env.get("WCHAT_MCP_CHROME_PROFILE"):
        ct = os.environ.get("CT_HOME") or os.path.join(os.environ.get("HOME") or "", ".ct")
        env["WCHAT_MCP_CHROME_PROFILE"] = os.path.join(ct, "mcp", "chrome-profile")
    # 子进程不该继承交互式提示:任何等待输入的 server 都会挂到超时
    env.setdefault("CI", "1")
    return env


def _child_env():
    """兼容旧名。"""
    return child_env()


def _mcp_root():
    """所有 mcporter 子进程的**固定工作目录**。返回路径,建不出来时返回 None。

    ⚠ 这是本项目最容易漏的一处,2026-11 用户实测踩到(现象是"每调一次 MCP 就多一个
    `chrome-devtools-mcp` 进程,第二个起全部报 profile 被占用")。

    根因(实测,不是推测):mcporter 把**调用方进程的 cwd** 也算进 daemon 的连接身份 ——
    同一个 daemon、同一份清单,从 projA 调一次建连接 1,从 projB 调一次又建连接 2,
    各自 spawn 一个 server 进程。对照实验(同一 daemon,3 个不同 cwd):
        cwd=projA → servers=1 procs=1 rc=0
        cwd=projB → servers=2 procs=2 rc=1(The browser is already running…)
        cwd=projC → servers=3 procs=3 rc=1
    固定 cwd 之后:4 个不同目录 → servers 恒为 1、procs 恒为 1、全部 rc=0。
    已排除的变量:`--root` 无效、显式 `--config` 无效、chrome-devtools relay 开关无关、
    探测超时不会堆积、"一次冲突后雪崩"也不成立(失败会复用那个坏连接,不新建)。

    为什么这对 wchat 是致命的:wchat 每个会话都在**用户的项目目录**里启动,
    所以每换一个项目就多一个 server 实例。对有状态的 server(chrome 抢同一个
    profile 锁)来说,第一个赢,其余全废 —— 而用户看到的只是"第一次能开网页,
    之后就不行了"。

    顺带把 `_child_env` 里那条注释的隐患也彻底关掉:mcporter 再也不可能按 cwd
    去找 `<cwd>/config/mcporter.json` 并把它合并进来。
    """
    ct = os.environ.get("CT_HOME") or os.path.join(os.environ.get("HOME") or "", ".ct")
    d = os.path.join(ct, "mcp", "root")
    try:
        os.makedirs(d, exist_ok=True)
        return d
    except OSError:
        return None      # 建不出来就不固定,退回旧行为(不阻塞使用)


def _run(argv, timeout=60):
    try:
        kw = {"capture_output": True, "text": True, "timeout": timeout,
              "env": _child_env()}
        _r = _mcp_root()
        if _r:
            kw["cwd"] = _r
        p = subprocess.run(argv, **kw)
    except FileNotFoundError:
        return None, "找不到启动器 %s" % argv[0]
    except subprocess.TimeoutExpired:
        # 必须说清"没返回"与"没做"是两件事:超时只杀了 npx 这个直接子进程,
        # daemon 是独立进程、会继续把依赖装完。实测:16:11 那次超时后,daemon 在
        # 16:12-16:14 把 uvx 的 wheel 下完了,下一次启动直接复用 —— 用户看到的是
        # "第二次突然就好了",而没人知道第一次其实干了一半活。
        return None, ("mcporter 未在 %ss 内返回(结果未知,严禁原样重发;"
                      "daemon 侧的安装/连接可能仍在继续)" % timeout)
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


def _discovery_budget():
    """返回 (per_server_ms, outer_s):单 server 发现超时 与 外框安全网。

    两件事必须分开:
      · mcporter 的 `--timeout` 是 **per-server** 的:某个 server 卡住时它自己标
        status=error 并**继续返回其余 server**,这是我们要的降级粒度;
      · 外框 subprocess 超时是**一刀切**的:到点连成功的那几个一起丢,这就是
        2026-10-30 那次"4 个 server 声明了、一个都没起来"的直接原因。
    所以:把判定交给 per-server,外框只按 server 数量留足安全网。
    """
    try:
        per = int(os.environ.get("MCP_DISCOVERY_TIMEOUT_MS") or 20000)
    except ValueError:
        per = 20000
    if per <= 0:
        per = 20000
    n = max(1, len(config_servers()))
    # 每个 server 一个发现窗口 + 20s 固定开销(npx 自身解析、daemon 握手、JSON 解析)。
    # 上限 600s:真到这一步已经该去看具体 server 了,再长只是让人干等。
    outer = min(600, max(30, (per * n) // 1000 + 20))
    return per, outer


def list_json(timeout=None):
    """返回 (servers 列表, err)。servers = [{name,description,tools:[{name,description}]}]

    timeout=None 表示按 _discovery_budget() 自适应;显式传值只影响外框,per-server 仍走预算。
    """
    global LAST_COUNTS
    if not shutil.which(LAUNCHER):
        return None, "找不到启动器 %s(本次按无 MCP 运行)" % LAUNCHER
    per_ms, outer = _discovery_budget()
    if timeout is not None:
        outer = timeout
    out, err = _run([LAUNCHER, "-y", PKG, "list", "--json", "--timeout", str(per_ms)],
                    timeout=outer)
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


# ---------------------------------------------------------------- 图片块落地/交给模型
# 为什么要有这一段(2026-11 用户实测):
#   chrome 的 take_screenshot 返回的是 MCP `image` 内容块(base64)。旧实现只挑
#   `type == "text"` 的块,**image 块只数个数、内容整个扔掉** —— 于是模型对截图是瞎的:
#   不占一个 image token,却也看不到任何画面,只会照着文字说"截图成功捕获"。
#   更糟的是那句话自相矛盾:"已保存到 (未配置 MCP_SAVE_IMAGES,图片未落盘)"。
#
# 现在:图片块**落盘**(拿到确切路径,模型不会再靠猜文件名)+ 按策略决定是否**直通**给模型。
# 直通走的是与 `-img` / `read_image` **完全同一条**通道(`imgstate.register_image_url`
#   → `[[IMGREF:ref]]` → `request.expand_refs` 展开成 image_url),所以图片回收/♻ 记账
#   也自动复用,不需要第二套逻辑。
_SCREENSHOT_HINTS = ("screenshot", "capture", "render")


def _want_inline_images(tool):
    """这一轮要不要把图片块真的交给模型(直通 = 花 image token)。

    `MCP_IMAGES`:`auto`(默认)/ `on` / `off`。
    默认 auto 只对**截屏/渲染类**工具直通:浏览器自动化循环里每步都回图会烧掉巨额
    token,而模型通常只需要 a11y 文本;它真要看图会明说。
    """
    mode = (os.environ.get("MCP_IMAGES") or "auto").strip().lower()
    if mode in ("0", "off", "no", "never", "false"):
        return False
    if mode in ("1", "on", "yes", "always", "true"):
        return True
    name = (tool or "").lower()
    return any(h in name for h in _SCREENSHOT_HINTS)


def _img_dir_for_write():
    """图片落盘目录:`MCP_SAVE_IMAGES` 优先,否则本进程的临时目录(会话内可复读)。

    为什么给默认目录而不是"没配就不落":不落盘 = 模型事后想看也没有东西可读,
    而用户看到的只是"没落盘"。临时目录由系统回收,不需要我们清理。
    """
    d = os.environ.get("MCP_SAVE_IMAGES") or ""
    if not d:
        d = os.path.join(tempfile.gettempdir(), "wchat-mcp-images", str(os.getpid()))
    os.makedirs(d, exist_ok=True)
    return d


def _ext_for_mime(mime):
    m = (mime or "").split(";", 1)[0].strip().lower()
    return {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg",
            "image/webp": ".webp", "image/gif": ".gif", "image/bmp": ".bmp"}.get(m, ".bin")


def _write_image_block(block, out_dir, index, ts):
    """把一个 MCP image 块写到磁盘,返回路径;失败返回空串。"""
    import base64
    data = block.get("data")
    if not data:
        return ""
    try:
        raw = base64.b64decode(data)
    except Exception:
        return ""
    ext = _ext_for_mime(block.get("mimeType"))
    base = os.path.join(out_dir, "mcp-image-%d-%d" % (ts, index))
    path = base + ext
    n = 2
    while os.path.exists(path):
        path = "%s-%d%s" % (base, n, ext)
        n += 1
    try:
        with open(path, "wb") as f:
            f.write(raw)
    except OSError:
        return ""
    return path


def _attach_images(server, tool, blocks):
    """落盘 + (按策略)登记为可直通图片。返回 (直通的 ref 列表, 落盘路径列表, 尺寸说明)。"""
    from wchat_py import img as _img
    from wchat_py.imgstate import register_image_url
    try:
        maxside = int(os.environ.get("IMG_INLINE_MAX_SIDE") or 2048)
    except ValueError:
        maxside = 2048
    try:
        cap = int(os.environ.get("MCP_IMAGES_MAX") or 4)
    except ValueError:
        cap = 4
    inline = _want_inline_images(tool)
    paths, refs, notes = [], [], []
    import time as _t
    ts = int(_t.time())
    for i, b in enumerate(blocks, 1):
        if inline and len(refs) >= cap:
            notes.append("超出单次上限 %d,其余未直通" % cap)
            inline = False
        path = _write_image_block(b, _img_dir_for_write(), i, ts)
        if not path:
            notes.append("第 %d 张写盘失败" % i)
            continue
        paths.append(path)
        if not inline:
            continue
        try:
            url, _mime, orig, actual = _img.load_data_url(path, maxside)
        except Exception:
            url, orig, actual = path, 0, 0
        if not url.startswith("data:"):
            notes.append("第 %d 张读成图片失败(只有路径)" % i)
            continue
        refs.append(register_image_url(path, url, actual))
        if orig and actual and actual < orig:
            notes.append("第 %d 张已缩到 %dpx" % (i, actual))
    return refs, paths, notes


def call(server, tool, args, timeout=120):
    aj = json.dumps(args or {}, ensure_ascii=False)
    argv = [LAUNCHER, "-y", PKG, "call", "%s.%s" % (server, tool),
            "--args", aj, "--output", "json", "--timeout", str(int(timeout * 1000))]
    out, err = _run(argv, timeout=timeout + 15)
    if out is None:
        return "错误:MCP 调用失败: %s" % err
    try:
        d = json.loads(out)
    except ValueError:
        return out.strip() or (err or "(无输出)")
    content = d.get("content") or []
    texts = [c.get("text", "") for c in content if c.get("type") == "text"]
    res = "\n".join(texts) if texts else json.dumps(d, ensure_ascii=False)
    blocks = [c for c in content if c.get("type") == "image"]
    if blocks:
        refs, paths, notes = _attach_images(server, tool, blocks)
        if refs:
            from wchat_py.imgstate import imgjson_parts  # noqa: F401  (保持同一处出口)
            _txt = "%s\n(截图已交给主模型:%d 张%s)" % (
                res, len(refs), (";" + ";".join(notes)) if notes else "")
            if d.get("isError"):
                return "错误(MCP isError): %s" % _txt
            return "IMGJSON:" + json.dumps(
                {"refs": refs, "text": _txt}, ensure_ascii=False)
        # 没直通:如实说清"有几张、在哪",别再把它说成已保存又没落盘。
        # 路径**单独成行且不带任何后缀**:模型要把它原样交给 read_image,紧跟一个 `)`
        # 就会变成不存在的路径(实测被自己的格式坑了一次)。
        res += ("\n(另有 %d 个图片块已落盘,但**未**随本次结果交给模型"
                "(要看得花 image token)。路径:\n%s%s\n"
                "  · 要看:直接说\"看图\",我用 read_image 读上面路径;"
                "或设 MCP_IMAGES=on 让截图类结果自动回图)"
                % (len(blocks),
                   "\n".join("  " + p for p in paths) if paths else "  (写盘失败)",
                   ("\n  附注:" + ";".join(notes)) if notes else ""))
    if d.get("isError"):
        return "错误(MCP isError): %s" % res
    return res