#!/usr/bin/env python3
"""B 阶段 1a：Python 入口 + `-h` 帮助输出（逐字节等价 bash 的 `help_user`）。

为什么从 `-h` 开始：它**不依赖 API、不依赖终端交互**，却覆盖了"入口 + 参数分派 + 输出格式"
三件事，而且已有 `tests/replay/help.json` 这个 **bytes 级**基线可判读。

`inp_msg` 等价于 bash 的：
    printf '%s\\n' "$*" | sed $'s/^/\\033[2m│\\033[0m /'
即每行前缀 `\\033[2m│\\033[0m `（暗色竖线 + 空格）。

⚠ 插值项（`MCP_CONFIG` / `SKILLS_DIR` / `SKILLS_DIR_DEFAULT`）必须与 bash 侧**同源**：
优先取环境变量，其次取这里的默认值。任何一处不一致，`help` 基线的 bytes 比对会立刻红。
"""
import os
import sys

MC_DIRNAME = ".mcporter"


def inp_msg(msg=""):
    """等价 bash 的 inp_msg：每行加暗色 `│ ` 前缀。"""
    sys.stdout.write("\033[2m│\033[0m " + msg + "\n")


def _skills_dir_default():
    return os.environ.get("SKILLS_DIR_DEFAULT") or os.path.join(
        os.environ.get("HOME", ""), "." + "c" + "t", "skills")


def help_user():
    # 实测: bash 侧 MCP_CONFIG 默认指向用户目录下的 mcporter 配置(不是 .ct/mcp.json);
    # 猜错会让 help 基线的 bytes 比对立刻红 —— 这正是 bytes 级判据的价值。
    mcp_cfg = os.environ.get("MCP_CONFIG") or os.path.join(
        os.environ.get("HOME", ""), MC_DIRNAME, "mcporter.json")
    skills_dir = os.environ.get("SKILLS_DIR") or _skills_dir_default()
    sd_default = _skills_dir_default()

    inp_msg("使用方法: ct [选项] [内容]")
    inp_msg("选项:")
    inp_msg("  -t    启用思考模式")
    inp_msg("  -p    使用 deepseek-v4-pro 模型")
    inp_msg("  -dp   启用思考模式并使用 deepseek-v4-pro 模型")
    inp_msg("  -l    查看历史记录并选择加载")
    inp_msg("  -f    忽略历史记录")
    inp_msg("  -tr   翻译任务")
    inp_msg("  -to   词源词根解释任务")
    inp_msg("  -st   单轮模式:执行一轮对话后退出,不进交互循环(内容为空时在提示符输入一轮)")
    inp_msg("  -auto  敏感权限全自动(免逐次确认)。可接档位: -auto / -auto=read|sandbox|full")
    inp_msg("         read=只自动放行越界读取 | sandbox(默认)=工作目录内全自动 | full=再加 --allow-write 名单")
    inp_msg("         硬墙(提权/系统路径/凭据/越界写/强推/下载即执行)任何档位都不放行;启动需输入 AUTO 签字")
    inp_msg("  --allow 路径 / --allow-write 路径  越界只读/可写白名单(可多次,配合 -auto)")
    inp_msg("  --allow-install [包名,逗号]  允许 auto 安装这些包(配额 AUTO_MAX_INSTALLS,默认 5);不带包名 = 全开并警告。换源一律拒")
    inp_msg("  -mcp [服务器,逗号]  启用 MCP 外部能力(mcp_tools/mcp_schema/mcp_call 三条通道)")
    inp_msg("         清单来自 %s;**必须在该文件里写 \"imports\": []**,否则会带进" % mcp_cfg)
    inp_msg("         Codex/VS Code 等其它程序声明的 server(wchat 会主动报出并排除,但别依赖这个)")
    inp_msg("         ⚠ 这是**能力开关,不是安全边界**:不带 -mcp 只表示我们不提供这三条通道,")
    inp_msg("           模型仍可用 run_shell_command 自行调用 mcporter。无人值守 + auto 时必须点名")
    inp_msg("         启动时把每个 server **实际拿到的命令行原样摊开**;我们不解释也不替你改第三方参数")
    inp_msg("         (第三方参数会随版本改名,写死在我们代码里只会静默失效)。遥测、脱敏头这类")
    inp_msg("         开关请按各 server 自己的文档设;启动输出里那行就是它实际拿到的参数。")
    inp_msg("  -img  发送图片(后接图片路径,自动转述为文字)")
    inp_msg("  -skill 从当前对话提炼技能,确认后保存到本次生效的技能目录(见下)")
    inp_msg("         当前技能目录: %s(未设 SKILLS_DIR 时为 %s;外置值须过校验)" % (skills_dir, sd_default))
    inp_msg("  -c    手动压缩对话历史")
    inp_msg("  -u    查看当前上下文 token 用量")
    inp_msg("  -q    退出程序(Ctrl+C 不会退出,这是唯一退出方式)")
    inp_msg("  -h    显示帮助信息")
    inp_msg()
    inp_msg("内容交互时，也可使用以下选项:")
    inp_msg("  -msgs 显示当前消息记录")
    inp_msg("  -l    查看历史记录并选择加载")
    inp_msg("  -skill 从当前对话提炼技能,确认后保存到 %s" % skills_dir)
    inp_msg("  -c    手动压缩对话历史")
    inp_msg("  -n    新对话(清空历史并清屏)")
    inp_msg("  -cl   清屏(保留对话历史)")
    inp_msg("  -stp  切换到 pro 模型")
    inp_msg("  -auto[=read|sandbox|full]  会话中途开启/切换 auto 档位(需重新签字)")
    inp_msg("  -noauto  关闭 auto,恢复逐次人工确认")
    inp_msg("  -stt  开启思考模式")
    inp_msg("  -stf  恢复默认(flash 模型,关闭思考)")
    inp_msg("  -fs   立即保存当前对话历史(-f 模式下静默跳过)")
    inp_msg("  -md   查看当前模型/思考/标题信息")
    inp_msg("  -u    查看当前上下文 token 用量")
    inp_msg("  -img <路径> 发送图片(自动转述为文字)")
    inp_msg("  Ctrl+F 选择文件(图片自动转述;文本传路径,由 AI 用工具按需读取)")
    inp_msg("  Ctrl+G/Ctrl+V 粘贴剪贴板图片(截图后直接附加,自动转述;部分终端下直接粘贴亦可)")
    inp_msg("  -q    退出程序(询问是否保存,默认保存;N/n 不保存;-f 不询问)")
    inp_msg("  -h    显示帮助信息")
    inp_msg()
    inp_msg("输入规则:")
    inp_msg("  - 回车 = 发送整段(缓冲区为空时不动)")
    inp_msg("  - 想换行:把行尾写成 \\ 再回车(反斜杠会被吃掉,不发送,继续输入)")
    inp_msg("  - 粘贴多行文本会自动折叠成一行占位,回车发送时原样展开")
    inp_msg("  - -x 命令仅在单行输入时生效;多行/续行中输入 -x 按正文处理")
    inp_msg("  - Ctrl+C 清空当前行,Ctrl+R 整段清空本轮输入")


def parse(argv):
    """解析命令行 → 状态字典（**纯函数**，不设全局）。

    为什么返回 dict 而不是直接改全局：bash 侧的状态仍归 bash（B 第 4 步才谈所有权）。
    这里用 shellio.assign 输出赋值语句，由 bash `eval` 回去 —— 与硬墙那条回传通道同一套。
    """
    content = ""
    model = ""
    thinking = "disabled"
    forget_history = 0
    quick_mode = 0
    seen_help = 0
    action = ""
    auto_mode = ""
    mcp_enable = 0
    mcp_only = ""
    unknown = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if not a.startswith("-"):
            # ⚠ 注意: bash 是 `content+="${!i}"` —— **无分隔符直接拼接**,
            # 所以 `-st hello world` 得到 `helloworld`。不要"顺手"加空格。
            content += a
        elif a == "-h":
            seen_help = 1
        elif a == "-f":
            forget_history = 1
        elif a == "-t":
            thinking = "enabled"
        elif a == "-p":
            model = os.environ.get("PRO_MODEL", "")
        elif a == "-dp":
            thinking = "enabled"
            model = os.environ.get("PRO_MODEL", "")
        elif a == "-c":
            # ⚠ -c/-u 不是标志, 是"解析期立即执行 + break":
            #   bash 里是 `compact_messages; break` / `print_usage; break`。
            #   所以这里只记 action 并**终止解析**(后面的参数不会被消费) ——
            #   若不 break, `-c x` 里的 x 会被当正文, 静默改变行为。
            action = "compact"
            break
        elif a == "-u":
            action = "usage"
            break
        elif a == "-auto" or a.startswith("-auto="):
            auto_mode = a.split("=", 1)[1] if "=" in a else "sandbox"
        elif a == "-st" or a == "-to":
            quick_mode = 1
            forget_history = 1
        else:
            unknown.append(a)
        i += 1
    return {"content": content, "model": model, "thinking": thinking,
            "forget_history": str(forget_history),
            "quick_mode": str(quick_mode), "seen_help": str(seen_help), "action": action,
            "auto_mode": auto_mode, "unknown": " ".join(unknown)}


def cmd_parse(argv):
    from wchat_py.shellio import assign
    st = parse(argv)
    sys.stdout.write(assign(st))
    return 0


def run_single_turn(content, model=""):
    """单轮：组消息 → 调 `llm stream` → 用 StreamBox 渲染 → 返回退出码。

    复用现有 Python 件(msg/llm), 不重造; 事件流经同一条 JSON-lines seam(与 bash 一致)。
    """
    import json as _json
    import subprocess
    from wchat_py import msg as msgmod
    from wchat_py.render import StreamBox

    msgs = msgmod.append_user(msgmod.init(_system_prompt_text()), content)
    env_req = {"model": model or os.environ.get("FLASH_MODEL", ""),
               "messages": msgs, "thinking": "disabled", "tools": []}
    box = StreamBox(int(os.environ.get("STREAM_WIN", "10")))
    text_all = []
    p = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "wchat_py", "_run.py"), "llm", "stream"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
    p.stdin.write(_json.dumps(env_req, ensure_ascii=False))
    p.stdin.close()
    for line in p.stdout:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = _json.loads(line)
        except Exception:
            continue
        t = ev.get("t")
        if t == "text":
            box.append(ev.get("v") or "")
            sys.stdout.write(box.paint())
            sys.stdout.flush()
        elif t == "error":
            sys.stderr.write("API 请求失败: %s\n" % ev.get("v"))
    rc = p.wait()
    return 0 if rc == 0 else 1


def _call_llm(msgs, model, on_text=None, on_reason=None):
    """调一次 llm stream, 返回 (text, tool_calls, error)。事件走同一条 JSON-lines seam。

    on_text(delta) 若给: 每个 text 增量实时回调(供流式渲染); text 仍被累积返回。
    on_reason(delta) 若给: 思考增量回调(-dp/思考模式); 未给但有 on_text 时暗色直出。
    """
    import json as _json, subprocess
    from wchat_py import tools as _tools
    try:
        _tb = _tools.build(provider=os.environ.get("WEB_SEARCH_PROVIDER", ""),
                           mcp_enable=(1 if os.environ.get("MCP_ENABLE") == "1" else 0),
                           mcp_tools=None)
        _tlist = _json.loads(_tb) if isinstance(_tb, str) else _tb
    except Exception:
        _tlist = []
    _model = model or os.environ.get("WCHAT_MODEL") or os.environ.get("FLASH_MODEL", "")
    _think = os.environ.get("WCHAT_THINKING", "disabled") or "disabled"
    req = {"model": _model, "messages": msgs,
           "thinking": _think, "tools": _tlist}
    _ref_tmp = None
    if _IMG_URLS:
        import tempfile as _tf
        _fd, _ref_tmp = _tf.mkstemp(prefix="ct_refmap_")
        with os.fdopen(_fd, "w", encoding="utf-8") as _rf:
            _json.dump({k: {"url": v} for k, v in _IMG_URLS.items()}, _rf)
        req["ref_map_file"] = _ref_tmp
    if os.environ.get("WCHAT_TRACE") == "1":
        sys.stderr.write("TRACE llm model=%r thinking=%r\n" % (_model, _think)); sys.stderr.flush()
    p = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "wchat_py", "_run.py"), "llm", "stream"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
    p.stdin.write(_json.dumps(req, ensure_ascii=False)); p.stdin.close()
    text, tcs, err = [], [], None
    for line in p.stdout:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = _json.loads(line)
        except Exception:
            continue
        t = ev.get("t")
        if t == "text":
            _d = ev.get("v") or ""
            text.append(_d)
            if on_text is not None and _d:
                on_text(_d)
        elif t == "reasoning":
            _r = ev.get("v") or ""
            if _r and on_reason is not None:
                on_reason(_r)
        elif t == "tool_calls":
            # 网关按增量分片下发:首片带 name+args 前段,后续片只补 args。必须按 index
            # 累积合并,不能覆盖(覆盖会让 name 变空、args 只剩尾巴 -> 工具永不执行)。
            for _d in (ev.get("v") or []):
                _idx = _d.get("index")
                if _idx is None:
                    _idx = len(tcs)
                _slot = None
                for _s in tcs:
                    if _s.get("_i") == _idx:
                        _slot = _s; break
                if _slot is None:
                    _slot = {"_i": _idx, "id": "", "name": "", "arguments": ""}
                    tcs.append(_slot)
                if _d.get("id"):
                    _slot["id"] = _d["id"]
                if _d.get("name"):
                    _slot["name"] = _d["name"]
                if _d.get("arguments"):
                    _slot["arguments"] += _d["arguments"]
        elif t == "usage":
            _u = ev.get("v") or {}
            try:
                os.environ["WCHAT_LAST_PROMPT_TOKENS"] = str(int(_u.get("prompt") or 0))
                os.environ["WCHAT_LAST_CACHED_TOKENS"] = str(int(_u.get("cached") or 0))
                os.environ["WCHAT_LAST_IMAGE_TOKENS"] = str(int(_u.get("image") or 0))
            except (TypeError, ValueError):
                pass
        elif t == "error":
            err = ev.get("v")
    p.wait()
    if _ref_tmp:
        try:
            os.unlink(_ref_tmp)
        except OSError:
            pass
    for _s in tcs:
        _s.pop("_i", None)
    return "".join(text), tcs, err


def _shell_approved(cmd, strict=True):
    """shell 工具人工审批：auto=full 由硬墙裁决(此处放行)；显式 ASSUME_YES 放行；
    有 tty 弹确认；否则 fail-closed 拒（无头环境绝不静默执行）。
    strict=True 必须逐字 yes；strict=False 接受 y/yes(普通确认,同 master)。"""
    if os.environ.get("AUTO_MODE") == "full":
        return True
    if os.environ.get("WCHAT_AUTO_ASSUME_YES") == "1":
        return True
    try:
        if not sys.stdin.isatty():
            return False
    except Exception:
        return False
    sys.stdout.write("\033[2K \033[1;31m⚠ 需要人工确认\033[0m\n")
    sys.stdout.write(" \033[2m   命令:%s\033[0m\n" % cmd)
    if strict:
        sys.stdout.write(" \033[1;31m确需执行?必须输入 yes(n/回车/Ctrl+C = 拒绝): \033[0m")
    else:
        sys.stdout.write(" \033[1;31m是否执行?(y 或 yes,n/回车 = 拒绝): \033[0m")
    sys.stdout.flush()
    ans = (sys.stdin.readline() or "").strip()
    sys.stdout.write("\n"); sys.stdout.flush()
    return ans == "yes" if strict else (ans.lower() in ("y", "yes"))


from wchat_py.prompts import AUTO_FINAL_SYS as _AFS, AUTH_HEAD as _AH, AUTH_TAIL as _AT
AUTO_FINAL_SYS = _AFS
AUTH_SYS = _AH + "{workdir}" + _AT


def _auth_think(cmd):
    """初审：Y 放行 / N(带原因) 交终审。异常返回 (None,None) 供调用方 fail-closed。"""
    model = os.environ.get("AUTH_MODEL") or os.environ.get("PRO_MODEL") or ""
    _sys = AUTH_SYS.replace("{workdir}", os.environ.get("WORK_ROOT") or os.getcwd())
    try:
        raw = _nonstream([{"role": "system", "content": _sys},
                          {"role": "user", "content": cmd}], model)
    except Exception:
        return None, None
    line = (raw or "").split("\n", 1)[0].strip()
    if line[:1] in ("Y", "y"):
        return "Y", ""
    if line[:1] in ("N", "n"):
        return "N", line[1:].strip()
    return None, None


def _nonstream(msgs, model):
    """非流式单次调用(审批器/终审器用),返回 content；失败抛异常由调用方处理。"""
    from wchat_py.llm import _make_client
    cli = _make_client(os.environ.get("BASE_URL", ""),
                       os.environ.get("API_KEY") or os.environ.get("WCHAT_API_KEY") or "",
                       os.environ.get("API_KEY_HEADER", "api-key"))
    resp = cli.chat.completions.create(model=(model or "default"), messages=msgs,
                                       stream=False,
                                       extra_body={"thinking": {"type": "disabled"}})
    return resp.choices[0].message.content or ""


def _nonstream_msg(msgs, model, tools=None):
    """非流式单次调用,返回 message 对象(支持 tools/function calling)。"""
    from wchat_py.llm import _make_client
    cli = _make_client(os.environ.get("BASE_URL", ""),
                       os.environ.get("API_KEY") or os.environ.get("WCHAT_API_KEY") or "",
                       os.environ.get("API_KEY_HEADER", "api-key"))
    kw = dict(model=(model or "default"), messages=msgs, stream=False,
              extra_body={"thinking": {"type": "disabled"}})
    if tools:
        kw["tools"] = tools
    resp = cli.chat.completions.create(**kw)
    return resp.choices[0].message


SAVE_SKILL_TOOL = {"type": "function", "function": {
    "name": "save_skill",
    "description": "保存从对话中提炼出的技能草稿",
    "parameters": {"type": "object", "properties": {
        "name": {"type": "string", "description": "技能名:简短英文小写标识,连字符分隔,不超过 30 字符"},
        "description": {"type": "string", "description": "1-2 句话说明什么场景下使用该技能(检索词,要具体)"},
        "body": {"type": "string", "description": "技能正文,markdown 格式,包含操作步骤/常用命令/注意事项/用户偏好,不超过 800 字"}},
        "required": ["name", "description", "body"]}}}


def _skill_draft(conv):
    """复刻 master skill_save:用 save_skill 工具拿结构化草稿(最多两次,带自纠)。"""
    import json as _json
    from wchat_py.prompts import SKILL_SYS, SKILL_RETRY_SYS
    model = os.environ.get("FLASH_MODEL", "")
    user = conv
    prev = ""
    for attempt in (1, 2):
        if attempt == 1:
            msgs = [{"role": "system", "content": SKILL_SYS},
                    {"role": "user", "content": user}]
        else:
            msgs = [{"role": "system", "content": SKILL_RETRY_SYS},
                    {"role": "user", "content": user + "\n\n(你上次的输出不符合要求,原文如下,"
                     "请改为调用 save_skill 工具,把内容填进工具参数:)\n" + prev[:500]}]
        try:
            msg = _nonstream_msg(msgs, model, tools=[SAVE_SKILL_TOOL])
        except Exception:
            return None
        tcs = getattr(msg, "tool_calls", None) or []
        if tcs:
            try:
                args = _json.loads(getattr(tcs[0].function, "arguments", "") or "{}")
            except (ValueError, AttributeError):
                args = {}
            if args.get("name") and args.get("description") and args.get("body"):
                return args
        prev = getattr(msg, "content", "") or ""
        if not prev:
            break
    return None


def _default_final_call(cmd, reason=""):
    model = os.environ.get("AUTH_MODEL") or os.environ.get("PRO_MODEL") or ""
    msgs = [{"role": "system", "content": AUTO_FINAL_SYS},
            {"role": "user", "content": "命令: %s\n执行理由: %s\n初审结论: N\n" % (cmd, reason)}]
    try:
        from wchat_py.llm import _make_client
        _base = os.environ.get("BASE_URL", "")
        _key = os.environ.get("API_KEY") or os.environ.get("WCHAT_API_KEY") or ""
        _hdr = os.environ.get("API_KEY_HEADER", "api-key")
        _cli = _make_client(_base, _key, _hdr)
        _resp = _cli.chat.completions.create(model=(model or "default"),
                                             messages=msgs, stream=False,
                                             extra_body={"thinking": {"type": "disabled"}})
        text = (_resp.choices[0].message.content or "")
        err = None
    except Exception as _e:
        if os.environ.get("WCHAT_TRACE") == "1":
            sys.stderr.write("TRACE final EXC %r\n" % (_e,)); sys.stderr.flush()
        return ""
    if os.environ.get("WCHAT_TRACE") == "1":
        sys.stderr.write("TRACE final model=%r text=%.50r err=%r\n"
                         % (model, text, err)); sys.stderr.flush()
    if err:
        return ""
    return text or ""


def _auto_judge(cmd, call=None, reason=""):
    """auto 终审：call(cmd)->raw 文本，判 ALLOW/DENY(或 Y/N)。

    返回 (verdict, reason)；call 缺省走真实终审 LLM(_default_final_call)；异常/无判定 →
    (None,None) 供调用方 fail-closed（拒绝）。WCHAT_AUTO_ASSUME_YES=1 直接放行（测试用）。
    """
    if os.environ.get("WCHAT_AUTO_ASSUME_YES") == "1":
        return "Y", "assume-yes"
    if os.environ.get("WCHAT_TRACE") == "1":
        sys.stderr.write("TRACE judge enter call_none=%r reason=%r\n"
                         % (call is None, reason)); sys.stderr.flush()
    if call is None:
        call = lambda c: _default_final_call(c, reason)
    try:
        raw = call(cmd)
    except Exception:
        return None, None
    if not raw:
        return None, None
    line = str(raw).split("\n", 1)[0].replace("\r", "").strip()
    tok = line.split(" ", 1)[0].strip().upper()
    reason = line[len(line.split(" ", 1)[0]):].strip()
    if tok in ("Y", "YES", "ALLOW"):
        return "Y", reason
    if tok in ("N", "NO", "DENY"):
        return "N", reason
    return None, None


_IMG_REFS = {}
_IMG_URLS = {}
_IMG_SEQ = [0]
_GRANTED_PATHS = set()


def _register_image_url(path, url, side=0):
    """登记一张待挂载图片,返回引用号(供 messages 里存 [[IMGREF:id]])。"""
    _IMG_SEQ[0] += 1
    ref = "r%d%d" % (_IMG_SEQ[0], os.getpid())
    _IMG_URLS[ref] = url
    try:
        _IMG_REFS[os.path.realpath(path)] = {
            "id": _IMG_SEQ[0], "ref": ref, "gone": False,
            "recheck": False, "side": side}
    except Exception:
        pass
    return ref


def _reset_image_refs():
    """-n 新会话:历史被清空,图片引用登记也必须清零(否则首次读图会被误判'已在上下文')。"""
    _IMG_REFS.clear()
    _IMG_URLS.clear()
    _IMG_SEQ[0] = 0


def _path_guard(path, mode):
    """工作目录外访问闸(对应 bash path_access):返回 None=放行, 字符串=拒绝原因。"""
    if not path:
        return None
    p = os.path.realpath(os.path.expanduser(path))
    root = os.path.realpath(os.environ.get("WORK_ROOT") or os.getcwd())
    home = os.path.realpath(os.environ.get("HOME") or "")
    try:
        import tempfile as _tf
        tmp = os.path.realpath(_tf.gettempdir())
    except Exception:
        tmp = "/tmp"
    _tmps = {tmp, "/tmp", "/private/tmp"}
    _envtmp = os.environ.get("TMPDIR", "")
    if _envtmp:
        _tmps.add(os.path.realpath(_envtmp))

    def under(x, r):
        r = r.rstrip("/")
        return bool(r) and (x == r or x.startswith(r + "/"))

    if under(p, root) or any(under(p, t) for t in _tmps if t):
        return None
    _creds = (".ct/config", ".ct/history", ".ssh", ".aws", ".kube", ".docker",
              ".netrc", ".npmrc", ".mcporter")
    is_cred = home and any(under(p, os.path.join(home, x)) for x in _creds)
    auto = os.environ.get("AUTO_MODE") in ("full", "sandbox")
    _act = "写" if mode == "rw" else "读"

    def _deny_status(rule):
        from wchat_py import audit as _au
        _au.record(os.environ.get("AUTO_AUDIT_LOG", ""), "path", rule=rule,
                   decision="auto_deny", path=path)
        sys.stdout.write(" ⛔ 越界%s被硬墙拒绝(%s):%s\n" % (_act, rule, path))
        sys.stdout.flush()

    if is_cred:
        if auto:
            _deny_status("P1")
            return "错误:%s被拒绝[硬墙 P1]:凭据/会话数据路径不可访问。这不是用户拒绝,是代码级红线,换方案继续,不要改写路径重试同一目标。" % _act
        sys.stdout.write("\033[2K \033[1;31m⛔ 高危路径\033[0m \033[2m%s\033[0m\n\n" % path)
        sys.stdout.write(" \033[1;31m确需放行?必须输入 yes(n/回车 = 拒绝): \033[0m")
        sys.stdout.flush()
        try:
            ans = (sys.stdin.readline() or "").strip()
        except Exception:
            ans = ""
        sys.stdout.write("\n"); sys.stdout.flush()
        return None if ans == "yes" else "用户拒绝访问: %s" % path
    if auto:
        if mode == "r":
            return None
        _deny_status("P2")
        return "错误:%s被拒绝[硬墙 P2]:工作目录外写入。这不是用户拒绝,是代码级红线,换方案继续,不要改写路径重试同一目标。" % _act
    key = (mode, p)
    if key in _GRANTED_PATHS:
        return None
    sys.stdout.write("\033[2K \033[1;31m⚠ AI 请求访问工作目录外文件\033[0m \033[2m%s\033[0m\n\n" % path)
    sys.stdout.write(" \033[1;31m是否放行?(y 或 yes,n/回车 = 拒绝): \033[0m")
    sys.stdout.flush()
    try:
        ans = (sys.stdin.readline() or "").strip().lower()
    except Exception:
        ans = ""
    sys.stdout.write("\n"); sys.stdout.flush()
    if ans in ("y", "yes"):
        _GRANTED_PATHS.add(key)
        return None
    sys.stdout.write(" ⛔ 用户拒绝%s: %s\n" % (_act, path)); sys.stdout.flush()
    return "用户拒绝访问工作目录外文件: %s" % path


def _banner_text(name, args_json):
    """master 的工具横幅:shell 显示 'shell · 命令原文',其余 'name · args 预览'。"""
    from wchat_py.render import tool_banner, args_preview
    if name == "run_shell_command":
        try:
            import json as _j
            cmd = (_j.loads(args_json or "{}") or {}).get("command") or ""
        except ValueError:
            cmd = args_json or ""
        return tool_banner("shell", cmd)
    return tool_banner(name or "", args_preview(args_json or ""))


def _auto_backup(path):
    """auto 档下写文件前做一次会话备份(首次落备份,不覆盖);返回是否新增。"""
    if not (os.environ.get("AUTO_MODE") or os.environ.get("WCHAT_AUTO_MODE")):
        return False
    if not os.path.isfile(path):
        return False
    try:
        dest = os.environ.get("WCHAT_AUTO_BACKUP_DIR") or ""
        if not dest:
            return False
        os.makedirs(dest, exist_ok=True)
        rel = path.replace("+", "%2b").replace("/", "+")
        bak = os.path.join(dest, rel + ".bak")
        if os.path.exists(bak):
            return False
        import shutil as _sh
        _sh.copy2(path, bak)
        try:
            n = int(os.environ.get("WCHAT_AUTO_BACKUPS") or 0)
        except ValueError:
            n = 0
        os.environ["WCHAT_AUTO_BACKUPS"] = str(n + 1)
        return True
    except OSError:
        return False


def _run_tool(name, args_json):
    """执行一个工具。目前只接 list_dir(1d 的第一个);其余**明示未迁移**, 不静默成功。"""
    import json as _json
    from wchat_py.tools_run import list_dir, read_file, grep, write_file, edit_file
    try:
        a = _json.loads(args_json or "{}")
    except Exception:
        return "错误:工具参数不是合法 JSON"
    if name == "list_dir":
        path = a.get("path") or "."
        # 授权(resolve_path/path_access)在 bash 侧;Python 版这里只处理工作根内的相对/绝对路径
        resolved = os.path.expanduser(path)
        if not os.path.isabs(resolved):
            resolved = os.path.join(os.getcwd(), resolved)
        _deny = _path_guard(resolved, "r")
        if _deny:
            return _deny
        content, status = list_dir(resolved, path)
        if status:
            sys.stdout.write(status + "\n")
        return content
    if name == "read_file":
        path = a.get("path") or ""
        resolved = os.path.expanduser(path)
        if not os.path.isabs(resolved):
            resolved = os.path.join(os.getcwd(), resolved)
        _deny = _path_guard(resolved, "r")
        if _deny:
            return _deny
        work_root = os.environ.get("WORK_ROOT") or os.getcwd()
        content, status = read_file(resolved, path, a.get("offset"), a.get("limit"),
                                    os.environ.get("READ_MAX_CHARS", "30000"), work_root)
        if status:
            sys.stdout.write(status + "\n")
        return content
    if name == "grep":
        work_root = os.environ.get("WORK_ROOT") or os.getcwd()
        _gp = os.path.expanduser(a.get("path") or "")
        if _gp:
            _deny = _path_guard(_gp, "r")
            if _deny:
                return _deny
        content, status = grep(a.get("pattern") or "", _gp,
                               int(os.environ.get("GREP_MAX_MATCHES", "200")),
                               int(os.environ.get("GREP_MAX_COL", "500")), work_root)
        if status:
            sys.stdout.write(status + "\n")
        return content
    if name == "write_file":
        path = a.get("path") or ""
        work_root = os.environ.get("WORK_ROOT") or os.getcwd()
        resolved = os.path.expanduser(path)
        if not os.path.isabs(resolved):
            resolved = os.path.join(work_root, resolved)
        _deny = _path_guard(resolved, "rw")
        if _deny:
            return _deny
        _auto_backup(resolved)
        content, status = write_file(resolved, path, a.get("content") or "", work_root)
        if status:
            sys.stdout.write(status + "\n")
        return content
    if name == "edit_file":
        path = a.get("path") or ""
        work_root = os.environ.get("WORK_ROOT") or os.getcwd()
        resolved = os.path.expanduser(path)
        if not os.path.isabs(resolved):
            resolved = os.path.join(work_root, resolved)
        _deny = _path_guard(resolved, "rw")
        if _deny:
            return _deny
        _auto_backup(resolved)
        content, status = edit_file(resolved, path, a.get("old_string") or "",
                                    a.get("new_string") or "", work_root)
        if status:
            sys.stdout.write(status + "\n")
        return content
    if name == "read_image":
        import base64 as _b64
        import json as _json
        import mimetypes as _mt
        _p = os.path.expanduser(a.get("path") or "")
        if not os.path.isabs(_p):
            _p = os.path.join(os.environ.get("WORK_ROOT") or os.getcwd(), _p)
        if not os.path.isfile(_p):
            return "错误:文件不存在: %s" % (a.get("path") or "")
        _ext = os.path.splitext(_p)[1].lower()
        if _ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
            return "错误:不是支持的图片格式(仅 JPEG/PNG/GIF/WebP/BMP): %s" % a.get("path")
        _deny = _path_guard(_p, "r")
        if _deny:
            return _deny
        _rp = os.path.realpath(_p)
        _st = _IMG_REFS.get(_rp)
        if _st is not None:
            # 刀2 同图去重:已挂过就不重挂;模型坚持"看不到"再给它一次(两次原则)
            if not _st.get("gone"):
                if not _st.get("recheck"):
                    _st["recheck"] = True
                    _rid = _st.get("ref") or ("r%d" % _st.get("id", 0))
                    sys.stdout.write(" 🖼 同一张图已在上下文(引用号 %s),未重复挂载\n" % _rid)
                    sys.stdout.flush()
                    return ("read_image: %s —— 这张图本会话已经给过你了(见引用号 %s 所在的那条消息),"
                            "本次不再重复挂载。请回看那条消息里的画面作答;若那里已看不到图片本体"
                            "(例如它被回收过),再调用一次本工具,我会收回旧引用并重新挂载。"
                            % (a.get("path") or _p, _rid))
                _st["gone"] = True
                print(" ♻ 收回旧引用 #%d 并重新挂载(两次原则)" % _st["id"], flush=True)
                sys.stdout.flush()
        else:
            _IMG_SEQ[0] += 1
            _st = {"id": _IMG_SEQ[0], "ref": "r%d%d" % (_IMG_SEQ[0], os.getpid()),
                   "gone": False, "recheck": False, "side": 0}
            _IMG_REFS[_rp] = _st
        # 刀1 inline 降档:超过 IMG_INLINE_MAX_SIDE 的图缩到该边长(控 token);
        # 无 Pillow 时保持原图(降级但不崩)
        _mime = _mt.guess_type(_p)[0] or "image/png"
        _data = None
        _side = 0
        _ow = _oh = _w = _h = 0
        try:
            from PIL import Image as _Image
            _im = _Image.open(_p)
            _ow, _oh = _im.size
            _w, _h = _ow, _oh
            _side = max(_im.size)
            _lim = int(os.environ.get("IMG_INLINE_MAX_SIDE", "2048"))
            if _lim > 0 and _side > _lim:
                _im.thumbnail((_lim, _lim))
                _w, _h = _im.size
                import io as _io
                _buf = _io.BytesIO()
                _im.convert("RGB").save(_buf, format="PNG")
                _data = _buf.getvalue()
                _mime = "image/png"
                _side = max(_w, _h)
        except Exception:
            _data = None
            _side = 0
        if _data is None:
            # Pillow 不可用时的降档兜底:macOS sips(自带)
            try:
                import shutil as _sh
                _lim = int(os.environ.get("IMG_INLINE_MAX_SIDE", "2048"))
                if _lim > 0 and _sh.which("sips"):
                    import subprocess as _sp
                    _g = _sp.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", _p],
                                 capture_output=True, text=True)
                    _nums = [int(x.split(":")[1]) for x in _g.stdout.splitlines()
                             if "pixel" in x and x.split(":")[1].strip().isdigit()]
                    if _nums:
                        _side = max(_nums)
                        _ow, _oh = _nums[0], _nums[1]
                        _w, _h = _ow, _oh
                    if _nums and max(_nums) > _lim:
                        import tempfile as _tf
                        _tmp = _tf.mktemp(suffix=".png")
                        _r2 = _sp.run(["sips", "-Z", str(_lim), _p, "--out", _tmp],
                                      capture_output=True, text=True)
                        if _r2.returncode == 0 and os.path.isfile(_tmp):
                            with open(_tmp, "rb") as _f:
                                _data = _f.read()
                            os.unlink(_tmp)
                            _mime = "image/png"
                            _side = _lim
                            if _ow and _oh:
                                _ratio = float(_lim) / max(_ow, _oh)
                                _w = max(1, round(_ow * _ratio))
                                _h = max(1, round(_oh * _ratio))
            except Exception:
                _data = None
        if _data is None:
            try:
                with open(_p, "rb") as _f:
                    _data = _f.read()
            except OSError as _e:
                return "错误:读取图片失败: %s" % _e
        _st["side"] = _side
        _disp = a.get("path") or _p
        if _ow and _oh and (_w < _ow or _h < _oh):
            sys.stdout.write(" 🖼 原图已交给主模型 %s(已缩到 %spx)\n" % (_disp, _side))
        else:
            _dw = _w or _ow or _side
            _dh = _h or _oh or _side
            sys.stdout.write(" 🖼 原图已交给主模型 %s(交付 %dx%d,与原文件同尺寸)\n"
                             % (_disp, _dw, _dh))
        sys.stdout.flush()
        _url = "data:%s;base64,%s" % (_mime, _b64.b64encode(_data).decode())
        _IMG_URLS[_st["ref"]] = _url
        return "IMGJSON:" + _json.dumps(
            {"url": _url, "path": _p, "ref": _st["ref"],
             "text": "已读取图片 #%d: %s(档位 %d px)" % (_st["id"], _p, _side)},
            ensure_ascii=False)
    if name == "read_skill":
        from wchat_py import skill as _sk
        _sd = os.environ.get("SKILLS_DIR_DEFAULT") or os.path.join(
            os.environ.get("CT_HOME") or os.path.join(os.environ.get("HOME", ""), ".ct"), "skills")
        _r = _sk.read_skill(a.get("name") or "", _sd,
                            os.environ.get("SKILL_READ_MAX", "20000"),
                            os.environ.get("SKILLS_DIR_EXTERNAL", "0"))
        return _r.get("content", "")
    if name == "web_fetch":
        import re as _re
        import urllib.request as _ur
        import urllib.error as _ue
        from wchat_py import webs as _webs
        _url = a.get("url") or ""
        if not (_url.startswith("http://") or _url.startswith("https://")):
            return "错误:url 必须以 http:// 或 https:// 开头: %s" % _url
        _req = _ur.Request(_url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36"})
        try:
            with _ur.urlopen(_req, timeout=30) as _r:
                _raw = _r.read().decode("utf-8", "replace")
        except _ue.HTTPError as _e:
            return _webs.fetch_error(str(_e.code), _url)
        except Exception:
            return _webs.fetch_error("000", _url)
        # 纯 Python 渲染(替代 w3m):不再要求用户安装外部浏览器/渲染器
        _txt = _webs.html_to_text(_raw)
        return _txt
    if name == "web_search":
        import json as _json
        import urllib.request as _ur
        from wchat_py import webs as _webs
        _prov = os.environ.get("WEB_SEARCH_PROVIDER", "none")
        if _prov in ("", "none"):
            return "错误:当前接口未启用原生联网(web_search)。请改用 web_fetch 抓指定网页。"
        _q = a.get("query") or a.get("keyword") or ""
        _body = _webs.build_body(_prov, _q, os.environ.get("FLASH_MODEL", ""))
        if not _body:
            return "错误:联网参数构建失败(provider=%s)" % _prov
        _key = os.environ.get("API_KEY") or os.environ.get("WCHAT_API_KEY") or ""
        _hdrs = {"Content-Type": "application/json", "Authorization": "Bearer " + _key,
                 "api-key": _key}
        _req = _ur.Request(os.environ.get("BASE_URL", ""),
                           data=_json.dumps(_body).encode("utf-8"), headers=_hdrs)
        try:
            with _ur.urlopen(_req, timeout=int(os.environ.get("WEB_SEARCH_TIMEOUT", "90"))) as _r:
                _resp = _json.loads(_r.read().decode("utf-8", "replace"))
        except Exception as _e:
            return "错误:联网搜索失败: %s" % _e
        try:
            return _resp["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError, TypeError):
            return "错误:联网搜索返回格式异常"
    if name == "run_shell_command":
        from wchat_py import audit as _audit
        cmd = a.get("command") or ""
        from wchat_py.hardwall import detail as _hd_detail
        _auto = os.environ.get("AUTO_MODE") in ("full", "sandbox")
        if os.environ.get("WCHAT_TRACE") == "1":
            sys.stderr.write("TRACE shell auto=%r mode=%r cmd=%.20s\n"
                             % (_auto, os.environ.get("AUTO_MODE"), cmd)); sys.stderr.flush()
        _r, _s, _m = _hd_detail(cmd, _auto)
        if os.environ.get("WCHAT_TRACE") == "1":
            sys.stderr.write("TRACE hd r=%r s=%r m=%.30r\n" % (_r, _s, _m)); sys.stderr.flush()
        if _r:
            if _auto:
                _one = (cmd or "").replace("\n", " ")
                sys.stdout.write(" ⛔ 硬墙拒绝(%s):%s\n" % (_r, _one))
                _audit.record(os.environ.get("AUTO_AUDIT_LOG", ""), "shell",
                              rule=_r, verdict="auto_deny", cmd=cmd)
                return ("[硬墙 %s] %s" % (_r, _m))
            # 非 auto:master 保留人的最终裁量权——升级为严格确认(逐字 yes),
            # 而不是代码层直接拒(否则与 master 行为不一致)。
            sys.stdout.write("\033[2K \033[1;31m⛔ 高危命令(规则 %s),任何模式都不自动执行\033[0m\n" % _r)
            sys.stdout.write(" \033[2m   命令:%s\033[0m\n" % cmd)
            if _m:
                sys.stdout.write(" \033[2m   原因:%s\033[0m\n" % _m)
            sys.stdout.write(" \033[1;31m确需执行?必须输入 yes(n/回车/Ctrl+C = 拒绝): \033[0m")
            sys.stdout.flush()
            try:
                _ans = (sys.stdin.readline() or "").strip()
            except Exception:
                _ans = ""
            sys.stdout.write("\n"); sys.stdout.flush()
            if _ans != "yes":
                _audit.record(os.environ.get("AUTO_AUDIT_LOG", ""), "shell",
                              rule=_r, verdict="human_no", cmd=cmd)
                return ("用户拒绝执行高危命令(规则 %s): %s\n请换方案,或把命令告知用户请他手动执行。"
                        % (_r, cmd))
            # 用户已逐字 yes:放行,继续后面的执行流程
        if _auto:
            _av, _ars = _auth_think(cmd)
            if _av != "Y":
                _v, _rs = _auto_judge(cmd, reason=(a.get("reason", "") if isinstance(a, dict) else ""))
                if _v == "Y":
                    _bump_auto()
                    sys.stdout.write("auto 终审放行: %s\n" % cmd)
                    _audit.record(os.environ.get("AUTO_AUDIT_LOG", ""), "final",
                                  rule="final", verdict="auto_allow", cmd=cmd)
                elif _v == "N":
                    sys.stdout.write("auto 终审拒绝: %s\n" % cmd)
                    _audit.record(os.environ.get("AUTO_AUDIT_LOG", ""), "final",
                                  rule="final", verdict="auto_deny", reason=_rs or "", cmd=cmd)
                    return "auto 终审拒绝(%s): %s" % (_rs or "no-reason", cmd)
                else:
                    _audit.record(os.environ.get("AUTO_AUDIT_LOG", ""), "final",
                                  rule="final", verdict="auto_deny", reason="judge-unavailable", cmd=cmd)
                    return "auto 终审不可用(无判定),已拒(安全兜底): %s" % cmd
            else:
                _bump_auto()
        if not _auto:
            _av2, _ars2 = _auth_think(cmd)
            if _av2 != "Y":
                if os.environ.get("AUTO_MODE") == "read":
                    sys.stdout.write("该命令需人工确认: %s [y/N] " % cmd); sys.stdout.flush()
                    try:
                        _ans = (sys.stdin.readline() or "").strip().lower()
                    except Exception:
                        _ans = ""
                    if _ans not in ("y", "yes"):
                        _audit.record(os.environ.get("AUTO_AUDIT_LOG", ""), "shell",
                                      decision="deny", reason="human-n", cmd=cmd)
                        return "用户未批准(转人工): %s" % cmd
                elif not _shell_approved(cmd, strict=False):
                    _audit.record(os.environ.get("AUTO_AUDIT_LOG", ""), "shell",
                                  decision="deny", reason="no-approval", cmd=cmd)
                    return ("用户未批准执行命令: %s\n(非 auto 档需人工确认;"
                            "当前无交互 tty 或未确认,已拒)" % cmd)
        from wchat_py import audit as _audit
        _audit.record(os.environ.get("AUTO_AUDIT_LOG", ""), "shell", decision="allow", cmd=cmd)
        _bg = a.get("run_in_background")
        if _bg is True or str(_bg).lower() == "true":
            # 真后台(对齐 master):nohup 语义 + stdin 断开 + 输出到日志,立即返回 PID。
            import subprocess as _sp
            import tempfile as _tf
            _fd, _log = _tf.mkstemp(prefix="ct_bg_")
            os.close(_fd)
            with open(_log, "ab") as _lf:
                _p = _sp.Popen(["bash", "-c", cmd], stdin=_sp.DEVNULL,
                               stdout=_lf, stderr=_sp.STDOUT, start_new_session=True)
            _msg = ("✅ 服务已在后台启动:PID %d\n日志: %s\n"
                    "查看输出: 用 shell 工具执行 tail -f %s\n"
                    "停止服务: kill %d(若有残留子进程,先 pkill -P %d 再 kill %d)"
                    % (_p.pid, _log, _log, _p.pid, _p.pid, _p.pid))
            sys.stdout.write(" 后台启动 PID %s · 日志 %s\n" % (_p.pid, _log)); sys.stdout.flush()
            return _msg
        from wchat_py.termpty import pty_run
        try:
            _timeout = float(os.environ.get("CMD_TIMEOUT", "120"))
        except ValueError:
            _timeout = 120.0
        _disp, _full, _rc, _to, _intr = pty_run(
            ["bash", "-c", cmd], timeout=_timeout, win=int(os.environ.get("WIN", "10")))
        # 与 master run_with_window 一致:输出逐行 dim │ 前缀上屏,底部一行 dim 汇总。
        _n = len(_full.splitlines())
        for _ln in _disp:
            sys.stdout.write("\033[2K\033[2m│\033[0m %s\r\n" % _ln)
        if _to:
            sys.stdout.write("\033[2m└─ 超过单命令上限 %ss,已终止 · 共 %d 行 ─┘\033[0m\n"
                             % (os.environ.get("CMD_TIMEOUT", "120"), _n))
            _full += "\n(命令超过单条上限 %ss,已终止 · rc=%s)" % (_timeout, _rc)
        elif _intr:
            sys.stdout.write("\033[2m└─ 已中断 · 共 %d 行 ─┘\033[0m\n" % _n)
        else:
            sys.stdout.write("\033[2m└─ 完成 · 共 %d 行 ─┘\033[0m\n" % _n)
        sys.stdout.flush()
        return _full
    return "错误:工具 %s 尚未迁移到 Python 入口(1d 进行中)" % name


def _system_prompt_text():
    """单轮(-st)与 REPL 共用的 system:基础句 + 技能清单 + MCP 清单(与 master 一致)。"""
    base = "You are a helpful assistant."
    try:
        from wchat_py import skill as _sk
        _sd = os.environ.get("SKILLS_DIR_DEFAULT") or os.path.join(
            os.environ.get("CT_HOME") or os.path.join(os.environ.get("HOME", ""), ".ct"), "skills")
        _extra = _sk.scan_skills(_sd,
                                 os.environ.get("SKILL_MAX_INJECT", "30"),
                                 os.environ.get("SKILL_DESC_MAX", "200"),
                                 os.environ.get("SKILLS_DIR_EXTERNAL", "0"))
        if _extra:
            base += "\n\n" + _extra
    except Exception:
        pass
    _mcp = os.environ.get("MCP_MANIFEST", "")
    if _mcp:
        base += "\n\n" + _mcp
    return base


def run_turn(content, model="", max_rounds=5):
    """单轮内的工具链闭环: 请求 -> 有 tool_calls 就执行并续发 -> 无则结束。"""
    import json as _json
    from wchat_py import msg as msgmod
    from wchat_py.render import StreamBox, render_markdown, render_reason
    msgs = msgmod.append_user(msgmod.init(_system_prompt_text()), content)
    box = StreamBox(int(os.environ.get("STREAM_WIN", "10")))
    last_text = ""
    reason = []

    def _on_reason(d):
        reason.append(d)
        box.append(d)
        sys.stdout.write(box.paint_full())
        sys.stdout.flush()
    def _on_text(d):
        box.append(d)
        sys.stdout.write(box.paint_full())
        sys.stdout.flush()
    for _ in range(max_rounds):
        text, tcs, err = _call_llm(msgs, model, on_text=_on_text, on_reason=_on_reason)
        if os.environ.get("WCHAT_TRACE") == "1":
            sys.stderr.write("TRACE round tcs=%d names=%r err=%r\n"
                             % (len(tcs), [t.get("name") for t in tcs], err)); sys.stderr.flush()
        if text:
            last_text = text
        if err:
            sys.stdout.write(box.close()); sys.stdout.flush()
            sys.stderr.write("API 请求失败: %s\n" % err); return 1
        if not tcs:
            sys.stdout.write(box.close()); sys.stdout.flush()
            if reason:
                sys.stdout.write(render_reason("".join(reason)))
            sys.stdout.write(render_markdown(last_text)); sys.stdout.write("\n"); sys.stdout.flush()
            return 0
        # 工具执行前先关流箱并 flush 思考:否则权限提示/工具横幅会打在箱内光标处,
        # 下一帧 paint 的 \033[u 会把提示擦掉(用户实测:提示在箱上方且被滚掉)。
        sys.stdout.write(box.close()); sys.stdout.flush()
        if reason:
            sys.stdout.write(render_reason("".join(reason)))
            reason.clear()
        # 前导正文(assistant 在 tool_calls 之前说的话)必须在工具横幅前重新渲染:
        # 它刚才只在流箱里滚动过,close 已擦除;不渲染就会像"没说过话"。
        if last_text:
            sys.stdout.write(render_markdown(last_text)); sys.stdout.write("\n")
            sys.stdout.flush()
            last_text = ""
        # assistant 的 tool_calls 消息(正文也带上, 否则调用前的正文会丢)
        msgs.append({"role": "assistant", "content": text or None,
                     "tool_calls": [{"id": tc.get("id") or ("call_%d" % i), "type": "function",
                                     "function": {"name": tc.get("name") or "",
                                                  "arguments": tc.get("arguments") or "{}"}}
                                    for i, tc in enumerate(tcs)]})
        # 每个调用都必须有配对的 tool 结果, 否则下一轮 400(孤儿 tool_calls)
        for tc in tcs:
            sys.stdout.write(_banner_text(tc.get("name") or "", tc.get("arguments") or ""))
            sys.stdout.write("\n"); sys.stdout.flush()
            out = _run_tool(tc.get("name") or "", tc.get("arguments") or "{}")
            if isinstance(out, str) and out.startswith("IMGJSON:"):
                import json as _j
                try:
                    _d = _j.loads(out[len("IMGJSON:"):])
                except ValueError:
                    _d = None
                if _d:
                    _ref = _d.get("ref") or ""
                    _txt = _d.get("text", "图片已读取")
                    if _ref:
                        _txt += "\n[[IMGREF:%s]]" % _ref
                    msgs.append({"role": "tool", "content": _txt,
                                 "tool_call_id": tc.get("id") or ("call_%d" % tcs.index(tc))})
                    continue
            msgs.append({"role": "tool", "content": out,
                         "tool_call_id": tc.get("id") or ("call_%d" % tcs.index(tc))})
    sys.stdout.write(box.close()); sys.stdout.flush()
    if reason:
        sys.stdout.write(render_reason("".join(reason)))
    sys.stdout.write(render_markdown(last_text)); sys.stdout.write("\n"); sys.stdout.flush()
    sys.stdout.write("已达上限 %d 次:本轮助手工具调用过多,已停止自动执行\n" % max_rounds)
    return 0


def _bump_auto():
    try:
        n = int(os.environ.get("WCHAT_AUTO_APPROVALS") or 0)
    except ValueError:
        n = 0
    os.environ["WCHAT_AUTO_APPROVALS"] = str(n + 1)


def _auto_summary():
    """退出时的 auto 会话小结(与 master auto_report_exit 同形)。"""
    tier = os.environ.get("AUTO_MODE") or os.environ.get("WCHAT_AUTO_MODE") or ""
    if not tier:
        return
    ct = os.environ.get("CT_HOME") or os.path.join(os.environ.get("HOME", ""), ".ct")
    alog = os.environ.get("AUTO_AUDIT_LOG") or os.path.join(ct, "auto", "audit.log")
    try:
        n = int(os.environ.get("WCHAT_AUTO_APPROVALS") or 0)
    except ValueError:
        n = 0
    try:
        bn = int(os.environ.get("WCHAT_AUTO_BACKUPS") or 0)
    except ValueError:
        bn = 0
    try:
        import subprocess as _sp
        p = _sp.run(["git", "rev-parse", "--short", "HEAD"],
                    capture_output=True, text=True, timeout=5)
        rev = p.stdout.strip() if p.returncode == 0 else "(非 git 仓库)"
    except Exception:
        rev = "(非 git 仓库)"
    w = sys.stdout.write
    d, r = "\033[2m", "\033[0m"
    w("\n%s── auto 会话小结 ──%s\n" % (d, r))
    w("%s  自动放行 %d 次 · 备份 %d 份 · 档位 %s%s\n" % (d, n, bn, tier, r))
    w("%s  审计日志: %s%s\n" % (d, alog, r))
    w("%s  回滚: git diff %s 查看改动;git checkout %s -- . 复原%s\n" % (d, rev, rev, r))
    bdir = os.environ.get("WCHAT_AUTO_BACKUP_DIR", "")
    if bn > 0 and bdir:
        w("%s  备份目录: %s%s\n" % (d, bdir, r))
    try:
        from wchat_py import audit as _audit
        _audit.record(alog, "exit", tier=tier, verdict="finished",
                      approvals=n, backups=bn)
    except Exception:
        pass
    sys.stdout.flush()


def _load_ct_config():
    """源化 ~/.ct/config 到环境(与 bash `source` 同语义):只认 KEY=value 行,
    跳过注释/空行;行尾 ` # ...` 注释剥掉;值去引号。已存在的环境变量不覆盖。"""
    _ct = os.environ.get("CT_HOME") or os.path.join(os.environ.get("HOME", ""), ".ct")
    p = os.path.join(_ct, "config")
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                k = k.strip()
                if not k or not (k[0].isalpha() or k[0] == "_"):
                    continue
                if " #" in v:
                    v = v.split(" #", 1)[0]
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
                    v = v[1:-1]
                os.environ[k] = v
    except OSError:
        pass


def _detect_web_provider():
    """复刻 bash detect_web_search_provider:按 BASE_URL 与 flash 模型判断家在谁家。"""
    base = os.environ.get("BASE_URL", "")
    fm = os.environ.get("FLASH_MODEL", "") or os.environ.get("WCHAT_MODEL", "")
    if "xiaomimimo" in base or "mimo" in base or fm.startswith("mimo"):
        return "mimo"
    if any(k in base for k in ("maas.aliyuncs.com", "dashscope", "aliyuncs.com")) \
            or fm.startswith("qwen") or fm.startswith("qwq"):
        return "qwen"
    return "none"


def _apply_runtime_defaults():
    """补齐 bash 的派生默认值:窗口/压缩阈值/联网方式/超时。缺这些 -md/-u 显示全 0/空。"""
    os.environ.setdefault("CTX_WINDOW", "1000000")
    try:
        _ctx = int(os.environ.get("CTX_WINDOW") or 1000000)
    except ValueError:
        _ctx = 1000000
    os.environ.setdefault("TOKEN_THRESHOLD", str(_ctx * 3 // 4))
    os.environ.setdefault("WEB_SEARCH_TIMEOUT", "90")
    _wp = os.environ.get("WEB_SEARCH_PROVIDER", "")
    if _wp in ("", "auto"):
        os.environ["WEB_SEARCH_PROVIDER"] = _detect_web_provider()


TITLE_TOOL = {"type": "function", "function": {
    "name": "set_title",
    "description": "提交为本次对话总结的标题",
    "parameters": {"type": "object", "properties": {
        "title": {"type": "string", "description": "对话标题,简短精炼,不超过 30 字,不含特殊字符与换行"}},
        "required": ["title"]}}}


def _gen_title(msgs_json):
    """根据对话生成短标题(与 master 一致:调用 set_title 工具)。失败回退内容/空。"""
    import json as _json
    try:
        msgs = _json.loads(msgs_json)
        msgs = list(msgs) + [{"role": "user", "content":
                              "根据我们的聊天对话,总结一个恰当的标题。要求:标题简短精炼,"
                              "不超过 30 字,不含特殊字符与换行;请通过调用 set_title 工具提交标题。"}]
        msg = _nonstream_msg(msgs, os.environ.get("FLASH_MODEL", ""), tools=[TITLE_TOOL])
        t = ""
        tcs = getattr(msg, "tool_calls", None) or []
        if tcs:
            try:
                t = (_json.loads(getattr(tcs[0].function, "arguments", "") or "{}")
                     or {}).get("title") or ""
            except ValueError:
                t = ""
        if not t:
            t = getattr(msg, "content", "") or ""
            t = t.strip().splitlines()[0] if t.strip() else ""
        bad = ' /\\:*?"<>|\n\r\t'
        out = "".join("_" if ch in bad else ch for ch in t).strip("_")
        return out[:40]
    except Exception:
        return ""


def _sync_runtime(s):
    """把本轮 usage 回填活会话;首轮生成对话标题(与 bash 一致)。"""
    try:
        s.last_prompt_tokens = int(os.environ.get("WCHAT_LAST_PROMPT_TOKENS") or 0)
        s.last_cached_tokens = int(os.environ.get("WCHAT_LAST_CACHED_TOKENS") or 0)
    except ValueError:
        pass
    if not getattr(s, "chat_title", ""):
        _t = _gen_title(s.messages_json)
        if _t:
            s.chat_title = _t
            os.environ["WCHAT_CHAT_TITLE"] = _t


def main(argv):
    # 工作根锁定:与 bash 一致按 pwd -P。缺省时 hardwall.detail 会退回 "/tmp",
    # 导致真实工作目录内命令被误判 P2(路径越界) —— 入口统一补齐。
    os.environ.setdefault("WORK_ROOT", os.path.realpath(os.getcwd()))
    _load_ct_config()
    _apply_runtime_defaults()
    if not os.environ.get("AUTO_AUDIT_LOG"):
        _ct = os.environ.get("CT_HOME") or os.path.join(os.environ.get("HOME", ""), ".ct")
        os.environ["AUTO_AUDIT_LOG"] = os.path.join(_ct, "auto", "audit.log")
    if not os.environ.get("WCHAT_HISTORY_DIR"):
        _ct = os.environ.get("CT_HOME") or os.path.join(os.environ.get("HOME", ""), ".ct")
        os.environ["WCHAT_HISTORY_DIR"] = os.path.join(_ct, "history")
    if argv and argv[0] == "repl":
        from wchat_py.session import Session, seed_system
        from wchat_py.repl import run_repl, session_text_handler
        _s = Session.from_env()
        _extra = ""
        try:
            from wchat_py import skill as _skill
            _sd = os.environ.get("SKILLS_DIR_DEFAULT") or os.path.join(
                os.environ.get("HOME", ""), "." + "c" + "t", "skills")
            _r = _skill.scan_skills(_sd,
                                    os.environ.get("SKILL_MAX_INJECT", "30"),
                                    os.environ.get("SKILL_DESC_MAX", "200"),
                                    os.environ.get("SKILLS_DIR_EXTERNAL", "0"))
            _extra = _r if isinstance(_r, str) else ""
        except Exception:
            _extra = ""
        seed_system(_s, extra=_extra)
        if not getattr(_s, "model", ""):
            _s.model = os.environ.get("FLASH_MODEL", "") or os.environ.get("WCHAT_MODEL", "")
        from wchat_py.repl import _default_meta as _dm

        def _meta(action, arg):
            return _dm(action, arg, session=_s)
        from wchat_py.repl import run_interactive

        def _on_quit():
            try:
                if _s.msg_count <= 1 or os.environ.get("WCHAT_FORGET") == "1":
                    return
                _d = os.environ.get("WCHAT_HISTORY_DIR", "")
                if not _d:
                    return
                _do_save = True
                if sys.stdin.isatty():
                    sys.stdout.write("│ 退出前保存对话? [Y/n] "); sys.stdout.flush()
                    try:
                        _ans = (sys.stdin.readline() or "").strip()
                    except Exception:
                        _ans = ""
                    sys.stdout.write("\n"); sys.stdout.flush()
                    if _ans[:1] in ("N", "n"):
                        sys.stdout.write("未保存对话\n"); sys.stdout.flush()
                        _do_save = False
                if not _do_save:
                    return
                import time as _t
                from wchat_py.session import save_history as _sh
                _title = os.environ.get("WCHAT_CHAT_TITLE", "")
                if not _title:
                    try:
                        _title = _gen_title(_s.messages_json)
                    except Exception:
                        _title = ""
                _name = os.environ.get("WCHAT_HISTORY_NAME") or (
                    (_title + ".json") if _title else ("chat-%d.json" % int(_t.time())))
                try:
                    _sh(_d, _s.messages_json, _name, _title)
                    sys.stdout.write("保存记录 %s\n" % os.path.join(_d, _name)); sys.stdout.flush()
                except Exception as _e:
                    sys.stderr.write("保存失败: %s\n" % _e); sys.stderr.flush()
            finally:
                _auto_summary()

        if sys.stdin.isatty():
            from wchat_py.render import StreamBox, render_reason, render_markdown
            _box = StreamBox(int(os.environ.get("STREAM_WIN", "10")))
            _reason_acc = []
            _text_acc = []

            def _cl_stream(m):
                def _emit(d):
                    _text_acc.append(d)
                    _box.append(d)
                    sys.stdout.write(_box.paint_full())
                    sys.stdout.flush()

                def _emit_reason(d):
                    _reason_acc.append(d)
                    _box.append(d)
                    sys.stdout.write(_box.paint_full())
                    sys.stdout.flush()
                _t, _tc, _err = _call_llm(m, "", on_text=_emit, on_reason=_emit_reason)
                if _err:
                    sys.stderr.write("API 请求失败: %s\n" % _err); sys.stderr.flush()
                return _t, _tc, _err

            def _finish(r):
                sys.stdout.write(_box.close()); sys.stdout.flush()
                if _reason_acc:
                    sys.stdout.write(render_reason("".join(_reason_acc)))
                    _reason_acc.clear()
                sys.stdout.write(render_markdown(r or "")); sys.stdout.write("\n")
                sys.stdout.flush()
                _text_acc.clear()
                _sync_runtime(_s)

            def _run_tool_boxed(n, a):
                # 工具执行/权限提示前关箱并 flush 思考,避免提示被下一帧 paint 擦掉;
                # 横幅必须在关箱**之后**打印,否则刚打到箱内就被 close 擦掉(用户实测)。
                sys.stdout.write(_box.close()); sys.stdout.flush()
                if _reason_acc:
                    sys.stdout.write(render_reason("".join(_reason_acc)))
                    _reason_acc.clear()
                # tool_calls 之前的前导正文:流箱已擦,这里补渲染
                if _text_acc:
                    sys.stdout.write(render_markdown("".join(_text_acc)))
                    sys.stdout.write("\n"); sys.stdout.flush()
                    _text_acc.clear()
                sys.stdout.write(_banner_text(n, a))
                sys.stdout.write("\n"); sys.stdout.flush()
                return _run_tool(n, a)

            _h = session_text_handler(_s, _cl_stream, _run_tool_boxed,
                                      write_final=False, on_final=_finish)
            _first = os.environ.pop("WCHAT_FIRST_TURN", "")
            if _first:
                _h(_first)
            return run_interactive(_h, on_meta=_meta, on_quit=_on_quit)
        def _finish_plain(r):
            if r:
                sys.stdout.write(r + "\n"); sys.stdout.flush()
            _sync_runtime(_s)

        _h = session_text_handler(_s, lambda m: _call_llm(m, ""), lambda n, a: _run_tool(n, a),
                                  write_final=False, on_final=_finish_plain)
        _first = os.environ.pop("WCHAT_FIRST_TURN", "")
        if _first:
            _h(_first)
        return run_repl(sys.stdin, _h, on_meta=_meta, on_quit=_on_quit)
    if argv and argv[0] == "run-st":
        st = parse(argv[1:])
        from wchat_py import audit as _audit
        _alog = os.environ.get("AUTO_AUDIT_LOG", "")
        _audit.record(_alog, "session", verdict="started")
        _mr = int(os.environ.get("MAX_TOOL_ROUNDS") or 5)
        _rc = run_turn(st["content"], st.get("model", ""), _mr)
        _audit.record(_alog, "session", verdict="finished")
        return _rc
    if argv and argv[0] == "parse":
        from wchat_py.shellio import assign
        return cmd_parse(argv[1:])
    if "-h" in argv or "--help" in argv:
        help_user()
        return 0
    # 参数路由：-u/usage、有正文走单轮、其余进 REPL(交互/标准输入)
    st = parse(argv)
    if st.get("auto_mode"):
        os.environ["AUTO_MODE"] = st["auto_mode"]
        os.environ["WCHAT_AUTO_MODE"] = st["auto_mode"]
        from wchat_py import auto_enable as _ae
        if not _ae.enable(st["auto_mode"]):
            os.environ.pop("AUTO_MODE", None)
    if str(st.get("forget_history", "0")) not in ("0", "", "False", "None"):
        os.environ["WCHAT_FORGET"] = "1"
    if st.get("model"):
        os.environ["WCHAT_MODEL"] = st["model"]
    if st.get("thinking"):
        os.environ["WCHAT_THINKING"] = st["thinking"]
    if st["action"] == "usage":
        from wchat_py.session import Session, usage_lines
        for ln in usage_lines(Session.from_env()):
            inp_msg(ln)
        return 0
    if st.get("content"):
        from wchat_py import audit as _audit
        _alog = os.environ.get("AUTO_AUDIT_LOG", "")
        _mr = int(os.environ.get("MAX_TOOL_ROUNDS") or 5)
        if str(st.get("quick_mode")) == "1":
            _rc = run_turn(st["content"], st.get("model", ""), _mr)
            _audit.record(_alog, "session", verdict="finished")
            return _rc
        # 非 -st/-to:与 master 一致——先跑这一轮,然后进入交互循环(不退出)。
        os.environ["WCHAT_FIRST_TURN"] = st["content"]
        return main(["repl"])
    return main(["repl"])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))