#!/usr/bin/env python3
"""MCP 入口引导:解析 -mcp、暴露 mcp_tools/mcp_schema/mcp_call、把 cli._run_tool
接到 mcp_bridge。由 wchat 入口在 `main()` 前调用 `install()`。

**两种启动模式**

  · 显式 `-mcp[=s1,s2]`:install() 当场探测。探测**之前**先打一行进度 ——
    旧版从敲下回车到探测结束终端一个字都没有(实测 8.9s 全静默;真实冷启动是分钟级),
    用户只会认为程序挂了。
  · 惰性(默认,只有此前成功用过 MCP 的机器才进入):只登记工具、**不碰网络**,
    首次 mcp_* 调用时才探测。见 `_lazy_armed()`。

**这一层只做三件事**,其余全交给 mcp_bridge 与上游 mcporter:

  1. **开关与状态机**:idle → busy → ready | failed | denied。failed/denied 是
     会话级终态,任何调用只返回同一句话 + 一条恢复命令,绝不重探 —— 这是"不无限重试"
     的唯一保证。
  2. **daemon 生命周期**:`owners` 引用计数(永远不阻塞谁,只决定"最后一个人走时要不要
     停 daemon")、SIGTERM/SIGHUP 清理、启动时收割 kill -9 留下的孤儿。
  3. **错误话术**:把上游原文交给模型,并明确说"不要重试"。

**曾经加过又删掉的东西(不要加回来)**

  · **按 server 占座**:daemon 是全机共享的,几条会话调 chrome 命中**同一条**连接。
    座位挡不住真并发,只会让第二个会话被完全拒绝;而且座位文件 kill -9 后残留 +
    pid 复用 ⇒ 永久锁死。
  · **连接数启发式 + 自动重启 daemon**:4 个 server 也可能有 5 条正常连接(实测),
    拿这个数当有病去重启是误判;重启还会关掉 MCP 托管浏览器里所有已开页面。
  · **出错后自动重启 daemon 重试**:没有次数上限时会变成"MCP 无限重启"(实测踩过),
    而且把用户已经打开的页面杀掉。连接复用的根因已在 `mcp_bridge.child_env()` 修掉,
    不需要任何运行期自愈。

判别标准很简单:**如果还需要这些自愈机制,说明根因没修掉。**
"""
import json
import os
import sys

_CALLS = [0]
_OWN = [False]
_OWNER_PID = [0]
# 启动时探测到的**可用** server 清单。以前每个 mcp_* 调用都重新跑一次
# `npx -y mcporter list --json`(热态白花 1~2s,冷态再挂一次超时);
# 而工具清单在一次会话里不会变,没理由重复枚举。
_BOOT_SRV = [None]
# 全量快照(含**失败**的 server 与上游原文)。旧版只留可用清单,失败原因只进 stderr —
# 模型侧只看到"server 未启用",于是它会换个名字再试一次。惰性化之后探测由模型触发,
# 失败原因必须回到模型手里,否则它不知道"是命令不存在还是依赖还在装"。
_SNAP = [None]

# 探测状态机:idle → busy → ready | failed | denied
#   failed/denied 都是**会话级终态**:不重探、不诱导改写参数重试。
#   为什么必须有终态:旧版探测只在启动跑一次,失败后 _BOOT_SRV 恒为 None,模型自然撞到
#   一句"不要重试";惰性化后 None 的语义变成"还没探",照抄旧判断会让**每一次**调用都
#   触发一次探测 —— 那才是真正的无限重启。
_ST = ["idle"]
_ERR = [""]        # 给人话的原因
_ERR_RAW = [""]    # 上游原文(判可恢复性 + 给用户恢复命令)
_PROBES = [0]      # 本会话真探测次数(硬上限,兜底:状态机写错也钉死在这里)
_PATCHED = [False]

# 三个通用 MCP 工具名。集中一处:执行层、schema 层、横幅层都必须用同一份,
# 分开写三份字面量正是上一轮漏登记的原因。
MCP_TOOL_NAMES = ("mcp_tools", "mcp_schema", "mcp_call")


# ------------------------------------------------------------------ 基础
def _ct_home():
    return os.environ.get("CT_HOME") or os.path.join(os.environ.get("HOME", ""), ".ct")


def _mcp_dir(*parts):
    return os.path.join(_ct_home(), "mcp", *parts)


def _alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False
    except (TypeError, ValueError):
        return False


def _now():
    import time
    return int(time.time())


def _atomic_write(path, text, mode=None):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    if mode is not None:
        try:
            os.chmod(tmp, mode)
        except OSError:
            pass
    os.replace(tmp, path)


# ------------------------------------------------------------------ daemon 引用计数
def _owners_path():
    return _mcp_dir("owners")


def _owners_read():
    """返回活着的 (pid, ts) 列表;陈旧行顺手丢掉。"""
    p = _owners_path()
    rows = []
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.strip().split("\t")
                pid = parts[0] if parts else ""
                ts = parts[1] if len(parts) > 1 and parts[1].isdigit() else "0"
                if pid.isdigit() and _alive(pid):
                    rows.append((int(pid), int(ts)))
    except OSError:
        pass
    return rows


def _owners_write(rows):
    p = _owners_path()
    text = "".join("%d\t%d\n" % (pid, ts) for pid, ts in rows)
    try:
        if text:
            _atomic_write(p, text, 0o600)
        elif os.path.isfile(p):
            os.unlink(p)
        return True
    except OSError:
        return False


def _claim_daemon():
    """登记"本会话也在用 daemon"。**永远成功**,不阻塞别的会话。

    返回值保留 True 是为了兼容旧调用点;语义已从"抢独占"变成"记账"。
    """
    me = os.getpid()
    rows = [r for r in _owners_read() if r[0] != me]
    rows.append((me, _now()))
    if not _owners_write(rows):
        return True   # 账本写不出来:不阻塞使用,只是最后可能没人停 daemon
    _OWNER_PID[0] = me
    return True


def _release_daemon():
    me = os.getpid()
    rows = [r for r in _owners_read() if r[0] != me]
    _owners_write(rows)
    return rows


# 旧名保留:外部(以及历史笔记)提到过这两个名字。
_lock_claim = _claim_daemon
_lock_release = _release_daemon

# ------------------------------------------------------------------ 惰性开关
def _lazy_marker():
    return _mcp_dir(".enabled")


def mark_enabled():
    """记住"这台机器确实要用 MCP"。由成功探测 / `wchat mcp add` 写入。

    为什么要有这个标记:惰性模式如果对所有装了 wchat 的人默认开,每个请求都会多出
    约 300 tokens 的 mcp_* 工具定义,而绝大多数人根本不用 MCP。标记的语义是
    "你已经表达过要用了",此后不再需要每次带 -mcp。
    """
    try:
        _atomic_write(_lazy_marker(), "%d\n" % _now(), 0o600)
        return True
    except OSError:
        return False


def _lazy_armed():
    if os.environ.get("MCP_DISABLE") == "1":
        return False
    if os.environ.get("MCP_LAZY") == "0":
        return False
    if os.environ.get("MCP_LAZY") == "1":
        return True
    return os.path.isfile(_lazy_marker())


def _arg_auto(argv):
    for a in argv:
        if a == "-auto" or a.startswith("-auto="):
            return True
    return False


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


def _arg_no_mcp(argv):
    return "-no-mcp" in argv


# 纯查询型子命令:`wchat mcp` / `skills` / `memory` / `model` … 跑完就退,
# 既不对话也不调工具。`wchat repl` / `run-st` 是真正的对话入口,**不在**这个名单里。
_SUBCMDS = ("mcp", "skills", "memory", "model", "ctmodel", "usage", "apilog")
_HELP_ARGS = ("-h", "--help", "-help", "help")


def _skip_mcp(argv):
    """这条命令根本不需要 MCP 时返回 True(子命令 / 帮助)。

    为什么必须显式跳过:install() 在 cli.main 之前跑,对 `wchat mcp add` 也会照常
    登记工具、打一行"按需模式"横幅、并(探测成功时)写下 `.enabled` 标记 ——
    用户在 `wchat mcp add` 里按 Ctrl+C 取消,居然被记成"这台机器要用 MCP"。
    那是噪音,外加一个不该产生的副作用。
    """
    rest = list(argv[1:])
    if any(a in _HELP_ARGS for a in rest):
        return True
    for a in rest:
        if a.startswith("-"):
            continue
        return a in _SUBCMDS
    return False


# ------------------------------------------------------------------ 探测
def _usable(srv):
    return [s for s in (srv or [])
            if s.get("status") == "ok" and (s.get("tools") or [])]


def _why_line(s):
    """把一个 server 的失败原因压成一行人话(上游原文截断)。"""
    st = s.get("status") or "?"
    issue = s.get("issue") or {}
    raw = str(issue.get("rawMessage") or s.get("error") or "").replace("\n", " ")[:120]
    return "status=%s%s" % (st, (" · " + raw) if raw else "")


def _recoverable(raw):
    """只有"超时/依赖还在装"值得再试一次。命令写错、参数错、上游 4xx 重试一万次也一样。"""
    t = (raw or "").lower()
    return ("timeout" in t or "未在" in (raw or "") or "timed out" in t
            or "still installing" in t or "exited unexpectedly" in t)


def _probe(explicit=False):
    """探测一次。返回 (ok, err)。**只做探测**,状态迁移由调用方负责。"""
    from wchat_py import mcp_bridge as mb
    _PROBES[0] += 1
    if explicit:
        tmo = None          # 显式:沿用 _discovery_budget()(按 server 数自适应)
    else:
        try:
            tmo = int(os.environ.get("MCP_LAZY_TIMEOUT") or 45)
        except ValueError:
            tmo = 45
        tmo = max(5, tmo)
    srv, err = mb.list_json(timeout=tmo)
    if srv is None:
        _SNAP[0] = []
        _BOOT_SRV[0] = None
        return False, (err or "全部 server 探测失败或没有可用工具")
    usable = []
    for s in _usable(srv):
        # 探测**不占座**:占座的含义是"我正在操作这个 server",而探测只是读工具清单,
        # 两个会话各摸一遍互不影响。早先版本在探测时就把全部 server 预定了,结果第一个
        # 会话一启动,第二个会话又变成整机失能 —— 换了形状的同一个 bug。
        usable.append(s)
    _SNAP[0] = srv
    _BOOT_SRV[0] = usable
    if not usable:
        det = ";".join("%s %s" % (s.get("name"), _why_line(s)) for s in (srv or []))
        return False, "全部 server 探测器都没拿到可用工具: %s" % (det or "清单为空")
    return True, ""


def _install_patches():
    """把 mcp_* 接进 cli._run_tool / tools.build / toolrun.known_tools。重复调用不会叠加。

    ⚠ **三处必须同时登记**,少一处就会出现"能执行但界面说没有这个工具"的分裂:
      ① `cli._run_tool`   —— 执行层(真正跑去 dispatch)
      ② `tools.build`     —— schema 层(模型看得见)
      ③ `toolrun.known_tools` —— 横幅/校验层(界面与"编造名字"判定)
    2026-11 用户实测:只登记了 ①②,于是屏幕上打 `⏺ mcp_tools · ⚠ 未知工具名,不执行`,
    而它其实是能跑的 —— 模型据此去猜别的名字,连开个网页都试了五次。
    """
    if _PATCHED[0]:
        return
    import wchat_py.cli as cli
    _orig = cli._run_tool

    def _rt(name, args_json):
        if name in MCP_TOOL_NAMES:
            return _dispatch(name, args_json)
        return _orig(name, args_json)

    cli._run_tool = _rt
    import wchat_py.tools as tools
    _ob = tools.build

    def _build(*a, **k):
        s = _ob(*a, **k)
        # ⚠ 必须在这里再判一次 MCP_ENABLE:探测失败/用户关掉时,上面已经把 MCP_ENABLE
        #   关回 0,但本函数是**无条件**往 schema 里塞 TOOL_SPECS 的。不判的结果是
        #   "banner 说不可用、模型每轮都看得见 mcp_*、每次调用都踩空",比不注册更糟 ——
        #   模型会把失败归因到自己的参数上,反复换着试。
        if os.environ.get("MCP_ENABLE") != "1":
            return s
        try:
            from wchat_py import mcp_bridge as _mb
            data = json.loads(s) if isinstance(s, str) else s
            data = list(data) + _mb.tool_specs(_SNAP[0])
            return json.dumps(data, ensure_ascii=False) if isinstance(s, str) else data
        except Exception:
            return s

    tools.build = _build
    # ③ 横幅/校验层:不登记的话,模型能跑却被告知"未知工具名,不执行"(2026-11 用户实测)
    from wchat_py import toolrun as _tr
    _tr.register_known(MCP_TOOL_NAMES)
    _PATCHED[0] = True


# ------------------------------------------------------------------ 状态机入口
BUSY_TEXT = ("错误:MCP 正在连接中(本次会话的第 %d 次尝试),已触发,不要重复调用、"
             "不要换参数重试。等这一步返回结果,或改用其他方式回答。")


def _mark_ready():
    """探测成功后的统一收尾(两条路径必须一致,不然"可用比例"会一边对一边错)。"""
    _ST[0] = "ready"
    _ERR[0] = ""
    _ERR_RAW[0] = ""
    n_ok, n_all = len(_BOOT_SRV[0] or []), len(_SNAP[0] or [])
    os.environ["MCP_STATE"] = "ok" if n_ok == n_all else "degraded"
    os.environ["MCP_USABLE"] = "%d/%d" % (n_ok, n_all)
    # 目录不再走 system(会话中途才探测,而 system 早已固化)。改由 mcp_tools 的
    # description 携带 —— 工具 schema 每轮重建,天然跟着走,且不动 prompt 前缀缓存。
    os.environ["MCP_MANIFEST"] = ""
    mark_enabled()


def ensure_ready():
    """惰性入口:保证"探测过一次"这件事已经发生,并且状态是终态之一。

    返回 (ok, 给模型的错误文本)。
    """
    st = _ST[0]
    if st == "ready":
        return True, ""
    if st == "failed":
        return False, FAILED_TEXT % (_ERR[0] or "未知原因", _recover_cmd())
    if st == "denied":
        return False, DENIED_TEXT
    if st == "busy":
        return False, BUSY_TEXT % _PROBES[0]
    _ST[0] = "busy"
    from wchat_py import mcp_bridge as _mb0
    _before = _mb0.sock_state()
    _claim_daemon()      # 让别的 wchat 会话知道"我也在用",退出时不会互相掐死
    try:
        ok, err = _probe(explicit=False)
    except KeyboardInterrupt:
        # Ctrl+C 打断探测:必须落到终态,不能停在 busy(否则之后每次调用都说"正在探测中");
        # 也必须把工具撤下 —— 留着一个每次调用都失败的 mcp_*,模型会反复踩空。
        _ERR[0] = "探测被用户中断(Ctrl+C)"
        _ERR_RAW[0] = ""
        _ST[0] = "failed"
        _BOOT_SRV[0] = None
        os.environ["MCP_STATE"] = "off"
        os.environ["MCP_ENABLE"] = "0"
        return False, FAILED_TEXT % (_ERR[0], _recover_cmd())
    if ok:
        _mark_ready()
        # 惰性模式下 daemon 是**第一次探测时**被拉起的,这里必须认领 own,
        # 否则 _atexit_stop 会因 _OWN=False 直接返回,daemon 永远不停(同一类漏洞的第二处)。
        _OWN[0] = (_before != 0 and _mb0.sock_state() == 0)
        if _OWN[0]:
            _remember_daemon_owner(daemon_pid())
        return True, ""
    _ERR[0] = err or "未知原因"
    _ERR_RAW[0] = err or ""
    # 可恢复(超时/依赖在装)且还有预算 → 自动再探一次。上限 2 次,写死在这里:
    # 会话中途用户在等,不像启动那样可以"再启一次就好"。
    try:
        cap = int(os.environ.get("MCP_MAX_PROBES") or 2)
    except ValueError:
        cap = 2
    if _recoverable(_ERR_RAW[0]) and _PROBES[0] < cap:
        ok2, err2 = _probe(explicit=False)
        if ok2:
            _mark_ready()
            return True, ""
        _ERR[0] = err2 or _ERR[0]
        _ERR_RAW[0] = err2 or _ERR_RAW[0]
    _ST[0] = "failed"
    os.environ["MCP_STATE"] = "off"
    os.environ["MCP_USABLE"] = "0/0"
    # 失败当场收摊:工具不再注册。账本(引用计数)等 atexit 收,
    # 因为同一进程后续可能被用户显式 -mcp 救回来。
    os.environ["MCP_ENABLE"] = "0"
    return False, FAILED_TEXT % (_ERR[0], _recover_cmd())


FAILED_TEXT = (
    "错误:MCP 本次会话连接失败(原因:%s)。\n"
    "**本会话已停止尝试,不要再次调用 mcp_* 工具、不要改写 server 名或参数重试** ——"
    "重试不会成功,只会把同一段失败反复塞进上下文。\n"
    "请如实告诉用户 MCP 不可用、并给出这一条恢复办法:退出 wchat 后执行\n"
    "  %s\n"
    "看到 counts 里 error=0 就说明预热好了,重开会话即可。"
)

DENIED_TEXT = ("用户已在本次会话中关闭 MCP(-mcp=off 或 -no-mcp)。不要重试;"
               "需要 MCP 请让用户重开会话并保持开启。")


def _recover_cmd():
    from wchat_py import mcp_bridge as mb
    return "MCPORTER_CONFIG=%s npx -y %s list --json" % (mb.config_path(), mb.PKG)

# ------------------------------------------------------------------ 分发
def _dispatch(name, args_json):
    from wchat_py import mcp_bridge as mb
    from wchat_py import audit
    try:
        a = json.loads(args_json or "{}")
    except ValueError:
        a = {}

    # 惰性:第一次进来才探测。ready/failed/denied 都是幂等的,不会重复探测。
    ok, why = ensure_ready()
    if not ok:
        return why

    srv = _BOOT_SRV[0]
    if srv is None:
        return FAILED_TEXT % (_ERR[0] or "MCP 未就绪", _recover_cmd())

    if name == "mcp_tools":
        # 不传 server = 看**全貌**(含连不上的那些与原因)。这是惰性模式下模型的入口:
        # 它得先知道有哪些能力、哪些此刻不可用,才会去调对的东西。
        s = (a.get("server") or "").strip()
        if not s:
            return mb.catalog_text(_SNAP[0] or srv, _BOOT_SRV[0] or [])
        return mb.tools_text(srv, s)
    if name == "mcp_schema":
        server, tool = a.get("server") or "", a.get("tool") or ""
        names = {s.get("name") for s in srv}
        if server not in names:
            # 先校验再问上游:旧版对不可用/写错的 server 也照样去跑一条
            # `mcporter list s.t --schema`,白等一次超时,还给模型一种"再试一次也许行"的错觉。
            return "错误:server '%s' 未启用,取不到它的 schema。可用: %s" % (
                server, ", ".join(sorted(names)))
        return mb.schema(server, tool)
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
        # 这里**不要**加"按 server 互斥":daemon 是全机共享的,几条会话会命中同一条
        # 连接,互斥挡不住真并发、只会让第二个会话完全不能用(加过又删掉,见文件头)。
        # 非 auto 首次使用该 server 需人工确认(会话内一次)
        granted = set((os.environ.get("MCP_GRANTED") or "").split())
        if os.environ.get("AUTO_MODE") not in ("full", "sandbox") and server not in granted:
            if os.environ.get("MCP_GRANT_MODE") == "ask" and sys.stdin.isatty():
                from wchat_py.render import confirm_block
                if not confirm_block("首次使用 MCP server『%s』,授权本会话" % server,
                                     ask="是否授权"):
                    audit.record(os.environ.get("AUTO_AUDIT_LOG", ""), "mcp",
                                 server=server, tool=tool, decision="deny", reason="human_no")
                    return "用户未授权 MCP server '%s'" % server
            os.environ["MCP_GRANTED"] = (os.environ.get("MCP_GRANTED", "") + " " + server).strip()
        _CALLS[0] += 1
        out = mb.call(server, tool, a.get("args") or {})
        # 结果字节帽(与 master MCP_RESULT_MAX_BYTES 同源,默认 16384):
        # MCP 返回值常是**单行 JSON**,按行/按列截都骗得过去(一行就是全部),
        # 必须单独按字节封顶,否则一次调用就能把上下文顶到几万字符。
        try:
            _cap = int(os.environ.get("MCP_RESULT_MAX_BYTES")
                       or os.environ.get("MCP_RESULT_MAX") or 16384)
        except ValueError:
            _cap = 16384
        if _cap > 0 and isinstance(out, str) and len(out.encode("utf-8")) > _cap:
            _raw = out.encode("utf-8")
            _head = _raw[:_cap // 2].decode("utf-8", "replace")
            _tail = _raw[-_cap // 2:].decode("utf-8", "replace")
            out = ("%s\n...(MCP 结果过长已截断:原 %d 字节,保留头尾各 %d)...\n%s"
                   % (_head, len(_raw), _cap // 2, _tail))
        audit.record(os.environ.get("AUTO_AUDIT_LOG", ""), "mcp",
                     server=server, tool=tool,
                     decision=("deny" if out.startswith("错误") else "allow"))
        return out
    return "错误:未知 MCP 工具 %s" % name


def _surface(srv, err=None):
    """复刻 bash mcp_show_surface 的启动暴露面文案。err: list_json 的外框级错误(可空)。"""
    from wchat_py import mcp_bridge as mb
    by = {s.get("name"): s for s in (srv or [])}
    order = mb.config_servers() or [s.get("name") for s in (srv or [])]
    order = sorted(order)                       # bash 用 jq keys[],字典序
    exposed = [n for n in order
               if n in by and by[n].get("status") == "ok" and (by[n].get("tools") or [])]
    exposed_line = [s.get("name") for s in (srv or [])
                    if s.get("status") == "ok" and (s.get("tools") or [])]
    dim, rst, grn, ylw, red = "\033[2m", "\033[0m", "\033[32m", "\033[33m", "\033[31m"
    sys.stderr.write("  %s[mcp] 暴露面(命令行归你,wchat 不解释也不替你改):%s\n" % (dim, rst))
    okn = badn = 0
    for n in order:
        s = by.get(n)
        if s is None:
            badn += 1
            sys.stderr.write("    %s✗%s %s · 上游清单里**根本没有这个 server**(status=缺席)\n" % (red, rst, n))
            sys.stderr.write("      含义:它被配置声明了,但 mcporter 没报回来。先跑 list --status 看它怎么说。\n")
        elif n in exposed:
            okn += 1
            sys.stderr.write("    %s✓%s %s · %d 个工具 · %s\n"
                             % (grn, rst, n, len(s.get("tools") or []), s.get("transport") or "?"))
        elif s.get("status") == "ok":
            badn += 1
            sys.stderr.write("    %s⚠%s %s · 在线但没提供工具(status=ok,tools=0)· %s\n"
                             % (ylw, rst, n, s.get("transport") or "?"))
            sys.stderr.write("      含义:连上了,只是它不暴露任何工具(可能全被自己的开关裁掉了)。本次不注册它。\n")
        else:
            badn += 1
            st = s.get("status") or "?"
            issue = s.get("issue") or {}
            rs = str(issue.get("rawMessage") or s.get("error") or "?").replace("\n", " ")[:160]
            ms = s.get("durationMs")
            extra = (" · 耗时 %sms" % ms) if ms else ""
            sys.stderr.write("    %s✗%s %s · status=%s · 原因(上游原文): %s%s\n"
                             % (red, rst, n, st, rs, extra))
            if s.get("transport"):
                sys.stderr.write("      命令行: %s\n" % s.get("transport"))
    counts = " ".join("%s=%s" % (k, v) for k, v in (mb.LAST_COUNTS or {}).items()) or "无"
    sys.stderr.write("  %s[mcp] 本次共声明 %d 个:可用 %d,未启用 %d;上游 counts: %s%s\n"
                     % (dim, len(order), okn, badn, counts, rst))
    if badn > 0:
        sys.stderr.write("  %s  · 耗时长的失败(数秒~数十秒)多为冷启动或端口/实例被占,再启一次即可;"
                         "几十毫秒就失败的多半是命令或参数本身不对。%s\n" % (dim, rst))
    if okn == 0 and badn > 0:
        # 两种病因完全不同,不能共用一句"daemon 有阻塞标记"把它糊掉:
        #   · daemon 异常退出留下的标记:stop/start 都清不掉,要前台带日志起;
        #   · 冷启动/命令写错:重试或改配置,跟 daemon 状态无关。
        # 以前只有一个分支,用户被指向 daemon stop 白折腾 —— 而真因就写在上面的
        # 『原因(上游原文)』里。没有证据就不许编病因。
        _e = str(err or "")
        if "exited unexpectedly" in _e or "no replacement" in _e:
            sys.stderr.write("  %s⚠ MCP 全部不可用:daemon 上次异常退出后留下了阻塞标记,"
                             "stop/start 都清不掉。%s\n" % (ylw, rst))
            sys.stderr.write("  %s  恢复: npx -y %s daemon start --foreground --log"
                             "(前台起,日志直接看得见)%s\n" % (dim, mb.PKG, rst))
        else:
            sys.stderr.write("  %s⚠ MCP 全部不可用:真因就是上面每条的『原因(上游原文)』,"
                             "别猜也别去 stop daemon。%s\n" % (ylw, rst))
            sys.stderr.write("  %s  · 几十毫秒就失败 → 命令/参数本身不对,重试无效;"
                             "数秒~数十秒 → 冷启动,再启一次即可(daemon 会继续把依赖装完)。%s\n"
                             % (dim, rst))
    sys.stderr.write("  %s  · 提醒:MCP 结果会进对话上下文并发到 API 端;要关遥测/脱敏头,"
                     "请按上面 server 的文档自己加到它的参数里。%s\n" % (dim, rst))
    sys.stderr.write("  %s[mcp] 已暴露: %s %s\n" % (dim, " ".join(exposed_line), rst))
    sys.stderr.flush()


_STOPPED = [False]


def _atexit_stop():
    """清理本会话的痕迹:账本、必要时停 daemon。

    ⚠ **只在正常退出/未捕获异常时会被调到**(atexit 的语义)。三条路径不走这里:
      · `kill -TERM`(含关终端时的 SIGHUP)→ 由 `_install_signal_handlers()` 兜;
      · `kill -9` / OOM / 崩溃 → **什么都拦不住**,只能靠下次启动时 `reap_orphan_daemon()`;
      · 机器重启 → 进程自然没了,但 `~/.mcporter/daemon/` 会留下陈旧 socket 与账本,
        `sock_state()` 判为"陈旧连不上"(返回 1),不会被当成活 daemon。
    """
    if _STOPPED[0]:
        return
    _STOPPED[0] = True
    from wchat_py import mcp_bridge as mb
    rows = _release_daemon()
    if not _OWN[0]:
        return
    if rows:
        return          # 还有别的 wchat 会话在用:绝不停(旧版就是这里把别人的 MCP 掐死的)
    if mb.sock_state() != 0:
        _forget_daemon_owner()
        return
    if mb.daemon_stop():
        _forget_daemon_owner()
        sys.stderr.write("  [mcp] daemon 已随本会话优雅停止(它忙时会自己拒绝,那种情况会保留)\n")
    else:
        sys.stderr.write("  [mcp] daemon 未停止(可能仍有活跃调用)。手工: npx -y %s daemon stop\n"
                         % mb.PKG)
    sys.stderr.flush()


# ------------------------------------------------------------------ 孤儿 daemon 治理
def _daemon_owner_path():
    return _mcp_dir("daemon.owner")


def _remember_daemon_owner(pid, ts=None):
    """记下"这个 daemon 是 wchat 拉起来的"。

    为什么需要:daemon socket 是**全机一个**(`~/.mcporter/daemon/user.sock`),
    Claude Desktop 等别家程序也可能拉起它。如果没有这层归属标记,"收割孤儿"
    就会误杀别人正在用的 daemon。写的是 (pid, 时间戳),pid 变了就说明不是我们那个。
    """
    if not pid:
        return
    try:
        _atomic_write(_daemon_owner_path(), "%d\t%d\n" % (int(pid), int(ts or _now())), 0o600)
    except (OSError, TypeError, ValueError):
        pass


def _read_daemon_owner():
    try:
        with open(_daemon_owner_path(), encoding="utf-8", errors="replace") as f:
            parts = (f.readline() or "").strip().split("\t")
        pid = int(parts[0]) if parts and parts[0].isdigit() else 0
        ts = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        return pid, ts
    except (OSError, ValueError, IndexError):
        return 0, 0


def _forget_daemon_owner():
    try:
        os.unlink(_daemon_owner_path())
    except OSError:
        pass


def daemon_status_json():
    """取 daemon 状态(不拉起)。拿不到返回 None。"""
    from wchat_py import mcp_bridge as mb
    try:
        cap = int(os.environ.get("MCP_DAEMON_STATUS_TIMEOUT") or 15)
    except ValueError:
        cap = 15
    out, _ = mb._run([mb.LAUNCHER, "-y", mb.PKG, "daemon", "status", "--json"], timeout=cap)
    if not out:
        return None
    try:
        d = json.loads(out)
        return d if isinstance(d, dict) else None
    except ValueError:
        return None

def daemon_pid():
    """当前 daemon 的 pid。拿不到返回 0。

    注意:`daemon status` **不会**顺手拉起 daemon(实测),所以可以安全地用它探测。
    """
    d = daemon_status_json()
    try:
        return int((d or {}).get("pid") or 0)
    except (TypeError, ValueError):
        return 0


def reap_orphan_daemon():
    """启动时收割:上一次 wchat 被 kill -9 / 崩溃留下的 daemon。

    必须三条同时成立才动手(缺一条都可能误伤):
      1. 有归属标记,且标记里的 pid **就是**当前活着那个 daemon(pid 变了说明
         是别家程序或新实例拉起的,不归我们管);
      2. `owners` 账本里**没有**活着的 wchat 会话(否则那是别人的 daemon);
      3. socket 真的可连(陈旧 socket 不算)。

    为什么只能在启动时做:daemon 自己**永远不会**因为空闲而退出 —— 实测确认
    `daemon status` 里 `idleTimeoutMs=None`,而它本该来自配置的 `daemonIdleTimeoutMs`,
    但上游 `canonicalUserConfiguration()` 只认它自己的 canonical 路径、**忽略
    MCPORTER_CONFIG**,所以对我们的清单永远读不到。于是 kill -9 之后那个 daemon
    会一直活着,直到机器重启。
    """
    if os.environ.get("MCP_NO_REAP") == "1":
        return ("skip", "MCP_NO_REAP=1")
    opid, _ots = _read_daemon_owner()
    if not opid:
        return ("none", "没有归属标记")
    if _owners_read():
        return ("busy", "还有别的 wchat 会话在用")
    from wchat_py import mcp_bridge as mb
    if mb.sock_state() != 0:
        _forget_daemon_owner()
        return ("none", "daemon 已不在")
    cur = daemon_pid()
    if not cur:
        return ("unknown", "拿不到 daemon pid")
    if cur != opid:
        # daemon 换过:标记过期,别动(可能是别家拉起的)
        _forget_daemon_owner()
        return ("stale", "daemon 已换实例(%s→%s),标记作废" % (opid, cur))
    if not _alive(opid):
        _forget_daemon_owner()
        return ("none", "记录的 daemon 已退出")
    ok = mb.daemon_stop()
    _forget_daemon_owner()
    return ("reaped" if ok else "failed", "daemon %d" % opid)


def _reap_and_report():
    """跑一次孤儿收割,失败了也不许挡住正常使用(最坏情况只是留个孤儿)。"""
    try:
        st, why = reap_orphan_daemon()
        if st == "reaped":
            sys.stderr.write("  [mcp] 清掉了一个上次异常退出留下的 daemon(%s)"
                             ";它本来会一直活着\n" % why)
            sys.stderr.flush()
    except Exception:
        pass


def _install_signal_handlers():
    """让 `kill`(SIGTERM)与关终端(SIGHUP)也走清理。

    为什么必须补这道:atexit 只覆盖"进程自己正常结束"。终端一关,macOS 发 SIGHUP,
    Python 默认直接终止,atexit **不会**跑 —— 于是 daemon 变孤儿,而它又永不自退。
    实测:`kill -TERM` 与 `kill -9` 都会留下孤儿 daemon。

    只接管 SIGTERM/SIGHUP:SIGINT 归 REPL/流式输出那套自己管(它有自己的语义),
    在这里抢会破坏 Ctrl+C 打断输出的行为。
    """
    import signal as _sig
    for s in (_sig.SIGTERM, _sig.SIGHUP):
        try:
            _sig.signal(s, _on_term)
        except (ValueError, OSError):
            pass      # 非主线程/平台不支持:忽略,不影响功能


def _on_term(signum, frame):
    # 先把**前台 shell 命令**收掉:否则用户 kill 掉 wchat,那条命令还在跑。
    # 实测只有"pty master 关闭 → SIGHUP"这一道时,命令 `trap '' HUP` 就整组活下来
    # (残留 bash + sleep,ppid=1)。这里主动 SIGKILL 整组,不给它忽略的机会。
    try:
        from wchat_py import termpty as _tp
        _tp.kill_active_group()
    except Exception:
        pass
    try:
        _atexit_stop()
    except Exception:
        pass
    finally:
        # 用 _exit 而不是 sys.exit:别让清理期间的异常盖掉退出码,也别再跑一遍 atexit
        os._exit(128 + int(signum))


# ------------------------------------------------------------------ 显式启用(会话内也可调用)
def enable_now(only="", surface=True):
    """当场探测并暴露面。返回 (ok, 给用户的一句话)。

    会话内 `-mcp` 与命令行 `-mcp` 走同一条路 —— 两套实现必然漂移,这里只留一套。
    `surface=False` 时不打那块"暴露面"详表(会话内由 repl 用一行回执概括,
    否则详表会和输入框抢屏幕)。
    """
    from wchat_py import mcp_bridge as mb
    os.environ["MCP_ENABLE"] = "1"
    os.environ["MCP_REQUESTED"] = "1"
    if only:
        os.environ["MCP_ONLY"] = only.replace(",", " ").strip()
    _install_patches()

    names = mb.config_servers()
    if not names:
        os.environ["MCP_ENABLE"] = "0"
        os.environ["MCP_STATE"] = "off"
        return False, "清单里没有任何 server(%s),先 `wchat mcp add` 或编辑该文件。" % mb.config_path()

    # 探测前先把"要开始了"说出来:旧版从敲回车到探测结束终端一个字都没有,
    # 8.9s 全静默(真实冷启动是分钟级),用户只会认为程序挂了。
    sys.stderr.write("  [mcp] 正在连接 %d 个 server:%s\n"
                     "        (最多等 %s;可 Ctrl+C 中断,中断后本次会话不再尝试 MCP)\n"
                     % (len(names), ", ".join(names),
                        "按 server 数自适应" if not only else only))
    sys.stderr.flush()
    _claim_daemon()
    _before = mb.sock_state()
    _ST[0] = "busy"
    try:
        ok, err = _probe(explicit=True)
    except KeyboardInterrupt:
        _ERR[0] = "用户中断"
        _ST[0] = "failed"
        os.environ["MCP_ENABLE"] = "0"
        os.environ["MCP_STATE"] = "off"
        return False, "已中断(本次会话不再尝试 MCP)"
    _OWN[0] = (_before != 0 and mb.sock_state() == 0)
    if _OWN[0]:
        # 记下"这个 daemon 是 wchat 拉起来的":进程被 kill -9 时没有机会写它,
        # 所以只能在这里(确认 ownership 的那一刻)落盘,供下次启动时收割。
        _remember_daemon_owner(daemon_pid())
    if not ok:
        # —— 探测即不可用:四件善后,一件都不能少 ——
        # 1) 关回 MCP_ENABLE:_build 据此不注册 mcp_*,模型不再对着死工具反复试;
        # 2) 立刻退账本:否则下一次会被误判成"还有人在用";
        # 3) 不认领 daemon:冷启动失败时它可能正在后台把依赖装完,stop 掉等于把
        #    下一次启动重新拖回超时。让它自己按 idleTimeout 退。
        _ERR[0] = err or "全部 server 探测失败或没有可用工具"
        _ERR_RAW[0] = _ERR[0]
        _ST[0] = "failed"
        os.environ["MCP_ENABLE"] = "0"
        os.environ["MCP_STATE"] = "off"
        os.environ.pop("MCP_MANIFEST", None)
        _release_daemon()
        _OWN[0] = False
        sys.stderr.write("⚠ -mcp 已请求,但 MCP 不可用: %s\n" % _ERR[0])
        sys.stderr.write("  → 本次**不注册** mcp_* 工具(模型不会看到,也不会反复踩空)。\n"
                         "  → 先手动预热一次再重开(超时也不要紧,daemon 会继续把依赖装完;\n"
                         "     看到 counts 里 error=0 就说明好了):\n"
                         "      %s\n" % _recover_cmd())
        sys.stderr.flush()
        return False, "MCP 不可用(原因见上方)"
    n_ok, n_all = len(_BOOT_SRV[0] or []), len(_SNAP[0] or [])
    _ST[0] = "ready"
    os.environ["MCP_STATE"] = "ok" if n_ok == n_all else "degraded"
    os.environ["MCP_USABLE"] = "%d/%d" % (n_ok, n_all)
    mark_enabled()
    if surface:
        _surface(_SNAP[0], None)
    return True, "MCP 已启用(%d/%d 可用): %s" % (
        n_ok, n_all, ", ".join(s.get("name") or "?" for s in (_BOOT_SRV[0] or [])))


def turn_off():
    """会话内关掉(-mcp=off / -no-mcp)。工具立即从 schema 消失,不再问、不再探。

    连**横幅/校验层**也要撤干净:否则关掉之后模型瞎调 mcp_call 时,界面会打
    "未知工具名"之外的另一种话 —— 两种说法不一致,排查时最费时间的是这种。
    """
    os.environ["MCP_ENABLE"] = "0"
    os.environ["MCP_STATE"] = "off"
    os.environ["MCP_DISABLE"] = "1"
    _ST[0] = "denied"
    try:
        from wchat_py import toolrun as _tr
        _tr.unregister_known(MCP_TOOL_NAMES)
    except Exception:
        pass
    return True


def _ensure_manifest_and_migrate():
    """清单就位 + 一次性补 chrome 的独立 profile。

    两个都幂等,失败都不阻止启动(最坏情况是继续用全局 profile)。
    """
    try:
        from wchat_py import seed as _seed_boot
        _seed_boot.ensure_manifest()
    except Exception:
        pass
    try:
        from wchat_py import seed as _seed_boot
        done, why = _seed_boot.migrate_chrome_profile()
        if done:
            sys.stderr.write("  [mcp] %s(避免与其它程序抢同一个浏览器 profile)\n" % why)
            sys.stderr.flush()
    except Exception:
        pass


def install(argv=None):
    argv = list(argv if argv is not None else sys.argv)
    if _skip_mcp(argv):
        return
    if _arg_no_mcp(argv):
        os.environ["MCP_DISABLE"] = "1"
        return
    enable, only = _arg_mcp(argv)
    if enable:
        # 清单必须**先**就位:本函数在 cli.main 之前执行(ensure_manifest 却在 cli.main 里),
        # 于是全新机器第一次跑 `wchat -mcp` 会带着"没有 ~/.ct/mcporter.json"去探测,
        # 必然一个 server 也回不来 —— 用户看到的是"全部 server 探测失败",而真因是顺序。
        _ensure_manifest_and_migrate()
        # 先收割上次留下的孤儿,再登记自己 —— 顺序不能反:先登记就把自己算成"有人在用",
        # 收割永远不生效。
        _reap_and_report()
        import atexit
        atexit.register(_atexit_stop)
        _install_signal_handlers()
        enable_now(only)
        return

    # ---------------- 惰性(默认) ----------------
    # 三条不进入惰性的理由,各有一次现场:
    #   · 用户明确 -no-mcp;
    #   · auto 档:无人值守时不允许模型自行拉起外部能力并把内容发出去
    #     (与 -auto 的"白名单启动时一次批好,模型不能半路自己加"同一条纪律);
    #   · 这台机器从没成功用过 MCP(无标记):白送三个每轮占 ~300 tokens 的工具,
    #     而绝大多数人根本不碰 MCP。
    if os.environ.get("MCP_DISABLE") == "1":
        return
    if _arg_auto(argv):
        return
    if not _lazy_armed():
        return
    _ensure_manifest_and_migrate()
    from wchat_py import mcp_bridge as mb
    names = mb.config_servers()
    if not names:
        return
    os.environ["MCP_ENABLE"] = "1"
    os.environ["MCP_STATE"] = "lazy"
    _install_patches()
    _reap_and_report()
    import atexit
    atexit.register(_atexit_stop)
    _install_signal_handlers()
    # 这里**不再打印"按需模式"横幅**。用户明确反馈:每次启动都刷一行 MCP 提示是噪音,
    # 而他关心的是"能不能用",不是"我们有几级开关"。状态仍可查:
    #   `wchat mcp` / 会话内 `-md` / MCP 真出错时的告警(那些只在出问题时出现)。