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
import re
import sys

# MCP 清单默认位:**在 ~/.ct/ 下**,与 config/history/skills 同源。
# (上游默认位留给 mcporter 自己的 daemon socket / vault —— 那是运行态,不是配置。)
def inp_msg(msg=""):
    """等价 bash 的 inp_msg：每行加暗色 `│ ` 前缀(多行也逐行加)。"""
    for ln in str(msg).split("\n"):
        sys.stdout.write("\033[2m│\033[0m " + ln + "\n")


def help_user():
    """打印 `-h`。正文在 `helptext.py`:入口只负责"加前缀上屏",不持有 60 行静态文案。"""
    from wchat_py.helptext import help_lines
    for ln in help_lines():
        inp_msg(ln)


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
    think_full = ""
    allow_ro = []
    allow_rw = []
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
        elif a == "-tf" or a.startswith("-tf="):
            think_full = a.split("=", 1)[1] if "=" in a else "on"
        elif a == "-sf" or a.startswith("-sf="):
            # `-sf` 已并入 `-tf`。**必须明说**:静默忽略会让用户以为档位变了,
            # 而屏幕上一点反应都没有(这类"看起来成功"的静默最费时间)。
            sys.stderr.write("⚠ -sf 已并入 -tf(-tf=on 展开 / -tf=off 只留收尾行),本次忽略\n")
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
        elif a == "-l":
            action = "list_history"
            break
        elif a == "-auto" or a.startswith("-auto="):
            auto_mode = a.split("=", 1)[1] if "=" in a else "sandbox"
        elif a == "-st":
            quick_mode = 1
            forget_history = 1
        elif a == "--allow" or a == "--allow-write" or a.startswith("--allow=") \
                or a.startswith("--allow-write="):
            _is_w = a.startswith("--allow-write")
            if "=" in a:
                _raw = a.split("=", 1)[1]
            else:
                _raw = argv[i + 1] if i + 1 < len(argv) else ""
                if _raw:
                    i += 1
            if _raw:
                _np = os.path.realpath(os.path.expanduser(_raw))
                (allow_rw if _is_w else allow_ro).append(_np)
        else:
            unknown.append(a)
        i += 1
    return {"content": content, "model": model, "thinking": thinking,
            "forget_history": str(forget_history),
            "quick_mode": str(quick_mode), "seen_help": str(seen_help), "action": action,
            "auto_mode": auto_mode, "unknown": " ".join(unknown),
            "think_full": think_full,
            "allow_ro": "\n".join(allow_ro), "allow_rw": "\n".join(allow_rw)}


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
    _runtime = _runtime_fallback()
    try:
        p = subprocess.Popen([_runtime, os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "wchat_py", "_run.py"), "llm", "stream"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
    except OSError as _e:
        sys.stderr.write("无法启动模型子进程(%s): %s\n"
                         "多半是 wchat 被升级而当前进程仍指向旧目录,"
                         "跑一次 `brew reinstall wchat` 后重开。\n" % (_runtime, _e))
        return 1
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


def _consume_stream(p, on_text, on_reason):
    """读 llm 子进程的 JSON-lines 事件流,返回 (text, tcs, err)。

    抽成独立函数只为一件事:让 Ctrl+C 的 try/finally 包住整个读循环而不把循环体
    再缩进一层(缩进走样过一次,整段语句块全错)。
    """
    import json as _json
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
                    # 缺 index 时**必须归到 0**,与 master 的 `jq '.index // 0'` 一致。
                    # 原来写的是 `len(tcs)`(每片新开一个 slot),后果是:
                    #   首片 {name, args:"{\"command\":"} → slot0(有 name、args 半截)
                    #   次片 {args:"\"ls\"}"}          → slot1(name 空、args 半截)
                    # 于是产生**一个空名工具调用 + 一个参数残缺的调用** ——
                    # 用户看到的那两次"shell · 空命令"就是这么来的。
                    # 实测:`{"command": ""}` 不可能是模型自己发的,它是分片被拆散后
                    # 落到空 name slot 上的必然结果。
                    _idx = 0
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
                os.environ["WCHAT_LAST_COMPLETION_TOKENS"] = str(int(_u.get("completion") or 0))
                os.environ["WCHAT_LAST_USAGE_SEEN"] = "1"
            except (TypeError, ValueError):
                pass
        elif t == "error":
            err = ev.get("v")
    return "".join(text), tcs, err


def _call_llm(msgs, model, on_text=None, on_reason=None):
    """调一次 llm stream, 返回 (text, tool_calls, error)。事件走同一条 JSON-lines seam。

    on_text(delta) 若给: 每个 text 增量实时回调(供流式渲染); text 仍被累积返回。
    on_reason(delta) 若给: 思考增量回调(-dp/思考模式); 未给但有 on_text 时暗色直出。
    """
    import json as _json, subprocess
    from wchat_py import tools as _tools
    # 每次调用的 usage 必须**独立**:先清空,拿不到就记 0。
    # 不清的话,这次请求失败(没有 usage 事件)会把上一次的 prompt_tokens 当成本次的
    # 写进账本 —— 账面上看着花了钱,实际是上一次的旧值,后续所有"用量"分析都跟着歪。
    for _k in ("WCHAT_LAST_PROMPT_TOKENS", "WCHAT_LAST_CACHED_TOKENS",
               "WCHAT_LAST_IMAGE_TOKENS", "WCHAT_LAST_COMPLETION_TOKENS"):
        os.environ.pop(_k, None)
    os.environ.pop("WCHAT_LAST_USAGE_SEEN", None)
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
    _runtime = _runtime_fallback()
    try:
        p = subprocess.Popen([_runtime, os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "wchat_py", "_run.py"), "llm", "stream"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
    except OSError as _e:
        # 解释器路径失效时不要抛栈:回一句能据此行动的话。
        # (真实故障:brew 升级删了旧 Cellar,而本进程 sys.executable 还指着它)
        return "", None, ("无法启动模型子进程(%s): %s\n"
                          "多半是 wchat 被升级而当前进程仍指向旧目录,"
                          "跑一次 `brew reinstall wchat` 后重开。" % (_runtime, _e))
    p.stdin.write(_json.dumps(req, ensure_ascii=False)); p.stdin.close()

    # Ctrl+C 打断流式输出(与 master handle_ctrl_c 同一套语义):
    # 把 SIGINT 换成本处理器 → 只置标志 + kill 掉读流的子进程,让读循环立刻 EOF;
    # **不抛 KeyboardInterrupt**,否则它会一路冒到 main 变成 traceback 退出
    # (用户实测:master 是"取消本轮输出、回输入框",迁移版直接崩)。
    # 必须显式 kill:终端 Ctrl+C 发给整个前台进程组,但子进程若正卡在 socket
    # recv,只装处理器不杀它就还得等下一行(停滞场景会一直挂)。
    global _LLM_ABORT
    _LLM_ABORT = False

    def _on_sigint(signum, frame):
        global _LLM_ABORT
        _LLM_ABORT = True
        try:
            p.kill()
        except Exception:
            pass

    _old_int = None
    try:
        import signal as _signal
        _old_int = _signal.signal(_signal.SIGINT, _on_sigint)
    except (ValueError, OSError):
        _old_int = None          # 非主线程装不了处理器:退回默认行为
    try:
        text, tcs, err = _consume_stream(p, on_text, on_reason)
    finally:
        if _old_int is not None:
            try:
                import signal as _signal
                _signal.signal(_signal.SIGINT, _old_int)
            except (ValueError, OSError, TypeError):
                pass
        try:
            p.wait()
        except Exception:
            pass
        # 记账:流式主对话。**必须放在 finally 里** —— 被 Ctrl+C 打断的请求也**已经**
        # 产生了输入侧的 token 消耗(服务端按收到的内容计费),不记就等于漏账。
        # 这里每被调用一次就记一笔:agent 工具循环里一次用户请求会触发 N 次,
        # 每次都要重发全量上下文 —— 那正是 token 消耗的主要来源,必须逐次可见。
        try:
            from wchat_py import apilog as _apilog
            _apilog.record(
                "main", _model, msgs,
                usage={"prompt": os.environ.get("WCHAT_LAST_PROMPT_TOKENS"),
                       "cached": os.environ.get("WCHAT_LAST_CACHED_TOKENS"),
                       "image": os.environ.get("WCHAT_LAST_IMAGE_TOKENS"),
                       "completion": os.environ.get("WCHAT_LAST_COMPLETION_TOKENS")},
                ok=not bool(err), extra={"think": _think}, tools=_tlist)
        except Exception:
            pass
        if _ref_tmp:
            try:
                os.unlink(_ref_tmp)
            except OSError:
                pass
    if _LLM_ABORT:
        # 本轮输出整体丢弃:由 orchestrate 回滚 messages 并让 REPL 打印"已中断"
        from wchat_py.session import Aborted
        raise Aborted()
    for _s in tcs:
        _s.pop("_i", None)
    return "".join(text), tcs, err


def _shell_approved(cmd, strict=True, reason=None):
    """shell 工具人工审批：auto=full 由硬墙裁决(此处放行)；显式 ASSUME_YES 放行；
    有 tty 弹确认；否则 fail-closed 拒（无头环境绝不静默执行）。
    strict=True 必须逐字 yes；strict=False 接受 y/yes(普通确认,同 master)。

    提示块走 `render.confirm_block`(与 `⏺ shell · …` 同一个块形状)。
    **命令正文与处置建议都不上屏**:横幅已给过命令,建议是给模型的。
    `cmd`/`reason` 仍保留在签名里供审计与将来使用,不用于屏幕显示。"""
    if os.environ.get("AUTO_MODE") == "full":
        return True
    if os.environ.get("WCHAT_AUTO_ASSUME_YES") == "1":
        return True
    try:
        if not sys.stdin.isatty():
            return False
    except Exception:
        return False
    from wchat_py.render import confirm_block
    return confirm_block("需要人工确认", strict=strict)


from wchat_py.prompts import AUTO_FINAL_SYS as _AFS, AUTH_HEAD as _AH, AUTH_TAIL as _AT
from wchat_py.session import Aborted          # Ctrl+C 打断本轮的信号(见 session.Aborted)
AUTO_FINAL_SYS = _AFS
AUTH_SYS = _AH + "{workdir}" + _AT


_VERDICT_RE = re.compile(
    r"(?<![A-Za-z])(ALLOW|DENY|YES|NO|Y|N)(?![A-Za-z])", re.IGNORECASE)
# 判定词前面常有的装饰:`**ALLOW**`、`- ALLOW`、`> ALLOW`、`1. ALLOW`、`【ALLOW】`
_VERDICT_LEAD = " \t\r\n*_`#>~-•·)]}"
_VERDICT_SEP = " \t\r\n:：,，。.、;；-—()（）[]【】\"'“”*_`"
# 判定词前的否定语境:把 "我不能 ALLOW 这条" 读成放行是**最危险的误判**
# (auto 模式无人看屏幕,放行错一条可能毁掉用户环境)。宁可判无判定(拒绝)。
# 英文用 \b 词边界:否则 "not " 在行首(前面没有空格)会匹配不到。
_NEG_RE = re.compile(
    r"(不能|不可|不得|不应|不要|不该|无法|禁止|拒绝|切勿|不准"
    r"|\bnot\b|\bcannot\b|\bcan't\b|\bdon't\b|\bnever\b|\bn't\b)", re.IGNORECASE)


def _negated(pre):
    return _NEG_RE.search(pre) is not None


def _parse_verdict(raw):
    """把初审/终审模型的回复解析成 ('Y'|'N'|None, reason)。

    **为什么要容错**:提示词要求"只回一行 ALLOW 或 DENY",但模型实际会回
    `ALLOW：只读命令`(中文冒号)、`**ALLOW** ...`(markdown)、`ALLOW。...`(句号)、
    `ALLOW\t...`(tab)、`ALLOW（无风险）`(括号)。旧实现是"按空格切成 token、再全等
    比对",上面每一种都→无判定→fail-closed 拒绝,用户看到的是"auto 终审不可用"。

    安全边界**不放宽**:只有在**第一个**判定词明确是 ALLOW 系列时才放行;既没判定词
    也没 Y/N 开头 → None(fail-closed)。中文口语("可以/安全")不做猜测 —— 猜错方向
    是放行一条危险命令,代价远大于多拒一次。

    只在**结论行**里找判定词(首个非空行),避免把理由里的 "不要 DENY 这条" 读成 DENY。
    """
    if not raw:
        return None, None
    text = str(raw).replace("\r", "")
    lines = [l.strip(_VERDICT_LEAD) for l in text.split("\n")]
    lines = [l for l in lines if l][:5]      # 只在开头几行找,别扫到理由深处
    if not lines:
        return None, None
    for line in lines:
        for m in _VERDICT_RE.finditer(line):
            tok = m.group(1).upper()
            # 否定语境里的 ALLOW("我不能 ALLOW"/"not allow")是**拒绝**,不是放行。
            # 显式判 N,而不是落到下面的首字符兜底(那样是偶然正确,换个措辞就错)。
            if tok in ("ALLOW", "YES", "Y") and _negated(line[:m.start()]):
                return "N", line.strip(_VERDICT_SEP)
            reason = line[m.end():].strip(_VERDICT_SEP)
            if tok in ("ALLOW", "YES", "Y"):
                return "Y", reason
            return "N", reason
    # 兜底:首字符 Y/N(与 _auth_think 同口径,兼容 "Y安全" 这类无分隔写法)
    head = lines[0]
    if head[:1] in ("Y", "y"):
        return "Y", head[1:].strip(_VERDICT_SEP)
    if head[:1] in ("N", "n"):
        return "N", head[1:].strip(_VERDICT_SEP)
    return None, None


def _judge_text(resp):
    """取判定文本:优先 content;content 为空时退回 reasoning_content。

    思考型模型(deepseek-reasoner 等)可能把内容整段放进 reasoning_content,
    此时 content 是空串 —— 旧实现直接当"无模型输出"拒绝,同样是误判。
    """
    if resp is None:
        return ""
    try:
        msg = resp.choices[0].message
    except (AttributeError, IndexError, TypeError):
        return ""
    txt = getattr(msg, "content", "") or ""
    if txt.strip():
        return txt
    extra = getattr(msg, "model_extra", None) or {}
    alt = getattr(msg, "reasoning_content", None) or extra.get("reasoning_content") or ""
    return alt if str(alt).strip() else ""


def _auth_think(cmd):
    """初审：Y 放行 / N(带原因) 交终审。异常返回 (None,None) 供调用方 fail-closed。"""
    model = os.environ.get("AUTH_MODEL") or os.environ.get("PRO_MODEL") or ""
    _sys = AUTH_SYS.replace("{workdir}", os.environ.get("WORK_ROOT") or os.getcwd())
    try:
        raw = _nonstream([{"role": "system", "content": _sys},
                          {"role": "user", "content": cmd}], model, source="auth")
    except Exception:
        return None, None
    # 与终审共用同一套容错解析:两处口径必须一致,否则"初审认、终审不认"会
    # 让同一条回复在两道闸上得到不同结论(且报错形式完全不同)。
    return _parse_verdict(raw)

def _nonstream(msgs, model, source="?"):
    """非流式单次调用(审批器/终审器用),返回 content；失败抛异常由调用方处理。

    `source` 归因标签:审批器每次**工具调用**都要问一次模型,是容易被忽略的固定成本。
    记账时必须能和主对话分开 —— 否则"token 消耗快"永远归因不到审批链上。
    """
    from wchat_py.llm import _make_client
    cli = _make_client(os.environ.get("BASE_URL", ""),
                       os.environ.get("API_KEY") or os.environ.get("WCHAT_API_KEY") or "",
                       os.environ.get("API_KEY_HEADER", "api-key"))
    try:
        resp = cli.chat.completions.create(model=(model or "default"), messages=msgs,
                                           stream=False,
                                           extra_body={"thinking": {"type": "disabled"}})
    except Exception:
        # 审批器/终审器不带 tools,这里不能引用 tools(未定义)
        _log_call(source, model, msgs, None, ok=False)
        raise
    _log_call(source, model, msgs, getattr(resp, "usage", None), ok=True)
    return _judge_text(resp)


def _usage_dict(u):
    """SDK 的 usage 对象 → 记账用的扁平字典(缺字段一律 0,不沿用上一轮)。"""
    if u is None:
        return {}
    d = getattr(u, "prompt_tokens_details", None)
    return {"prompt": getattr(u, "prompt_tokens", None) or 0,
            "completion": getattr(u, "completion_tokens", None) or 0,
            "cached": (getattr(d, "cached_tokens", 0) if d is not None else 0) or 0,
            "image": (getattr(d, "image_tokens", 0) if d is not None else 0) or 0}


def _log_call(source, model, msgs, usage, ok=True, tools=None):
    """**所有**非流式调用的记账出口;不走它就会重新变成盲区。

    `tools` 也要记:标题/技能这类调用只带一个很小的 tools schema,但也算输入成本。
    """
    try:
        from wchat_py import apilog
        apilog.record(source, model, msgs, usage=_usage_dict(usage), ok=ok, tools=tools)
    except Exception:
        pass


def _nonstream_msg(msgs, model, tools=None, source="?"):
    """非流式单次调用,返回 message 对象(支持 tools/function calling)。

    `source` 是记账归因标签 —— 这一层以前**完全不记 usage**,于是"token 消耗快"
    时没法判断钱花在标题、压缩、还是提炼技能上。
    """
    from wchat_py.llm import _make_client
    cli = _make_client(os.environ.get("BASE_URL", ""),
                       os.environ.get("API_KEY") or os.environ.get("WCHAT_API_KEY") or "",
                       os.environ.get("API_KEY_HEADER", "api-key"))
    kw = dict(model=(model or "default"), messages=msgs, stream=False,
              extra_body={"thinking": {"type": "disabled"}})
    if tools:
        kw["tools"] = tools
    try:
        resp = cli.chat.completions.create(**kw)
    except Exception:
        _log_call(source, model, msgs, None, ok=False, tools=tools)
        raise
    _log_call(source, model, msgs, getattr(resp, "usage", None), ok=True, tools=tools)
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
            msg = _nonstream_msg(msgs, model, tools=[SAVE_SKILL_TOOL], source="skill")
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
    """auto 模式的终审器。

    输入只有「命令 + 模型自述的执行理由 + 初审结论」—— **不含会话内容**。
    这一点很关键:终审发生在每次工具调用,如果带上会话历史,auto 模式下
    每条命令都要重付一次全量上下文。

    ⚠ 这里原来**直接调 SDK、绕过了 `_nonstream`**,于是它是唯一一个不记账的
    调用点 —— auto 模式无人值守跑长任务时,终审可能被调用几十次,而账本上是 0。
    统一走 `_nonstream` 就不再需要单独维护记账。
    """
    model = os.environ.get("AUTH_MODEL") or os.environ.get("PRO_MODEL") or ""
    msgs = [{"role": "system", "content": AUTO_FINAL_SYS},
            {"role": "user", "content": "命令: %s\n执行理由: %s\n初审结论: N\n" % (cmd, reason)}]
    try:
        text = _nonstream(msgs, model, source="auth_final") or ""
    except Exception as _e:
        if os.environ.get("WCHAT_TRACE") == "1":
            sys.stderr.write("TRACE final EXC %r\n" % (_e,)); sys.stderr.flush()
        return ""
    if os.environ.get("WCHAT_TRACE") == "1":
        sys.stderr.write("TRACE final model=%r text=%.50r\n" % (model, text))
        sys.stderr.flush()
    return text


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
    return _parse_verdict(raw)


def _retry_after_compact(session, msgs, err):
    """失败自愈:判定是上下文越界 → 压缩历史,返回 True(调用方重发一次)。

    只在**真的判成越界**时动手:纯网络抖动压一次历史是白烧一次摘要请求。
    判据与 master 同源(compaction.overflow_kind):接口错误文本优先,字节粗算兜底。
    每轮最多自愈一次(标记挂在 session 上),避免"压缩→仍越界→再压缩"的死循环。
    """
    if getattr(session, "_retry_done", False):
        return False
    try:
        from wchat_py import compaction as _cmp
        from wchat_py import compact_run as _cr
        try:
            _est = len(json.dumps(msgs, ensure_ascii=False).encode("utf-8")) * 2 // 5
        except Exception:
            _est = 0
        gate = int(getattr(session, "ctx_overflow_gate", 0) or 0)
        kind = _cmp.overflow_kind(err, _est, gate)
        if kind == "none":
            return False
        session._retry_done = True
        sys.stdout.write("\033[2m📦 上下文越界(%s),压缩历史后重发一次…\033[0m\n" % kind)
        sys.stdout.flush()
        _cr.compact(session, auto=True)
        return True
    except Exception:
        return False


def _system_prompt_text():
    """单轮(-st)与 REPL 共用的 system:基础句 + 技能清单 + MCP 清单(与 master 一致)。"""
    base = "You are a helpful assistant."
    # 首次运行把随包技能补进 ~/.ct/skills(只补缺、不覆盖;失败也不阻止启动)。
    # 放在这里而不是 main():扫描与注入之间不该有任何东西改目录内容。
    try:
        from wchat_py import seed as _seed
        _seed.seed_skills()
    except Exception:
        pass
    try:
        from wchat_py import skill as _sk
        _extra = _sk.injection()
        if _extra:
            base += "\n\n" + _extra
    except Exception:
        pass
    _mcp = os.environ.get("MCP_MANIFEST", "")
    if _mcp:
        base += "\n\n" + _mcp
    # 项目记忆:只有当**这个工作根**已经有索引时才注入(空索引不该占一行预算,
    # 也不该在每次新项目里都提示一次"你还没有记忆" —— 那是纯噪音)。
    # 有索引时注入 = 索引全文 + 边界声明 + "何时查/何时存"的契约(见 memory.injection)。
    try:
        from wchat_py import memory as _mem
        _memtxt = _mem.injection()
        if _memtxt:
            base += _memtxt
    except Exception:
        pass
    return base


def run_turn(content, model="", max_rounds=None):
    """单轮内的工具链闭环: 请求 -> 有 tool_calls 就执行并续发 -> 无则结束。

    max_rounds 缺省读 MAX_TOOL_ROUNDS(250,与 master 一致)。写死 5 会让 `-st` 一个
    正经任务(读+改+测+迭代)在第 5 次工具调用处静默截断。
    """
    if max_rounds is None:
        try:
            max_rounds = int(os.environ.get("MAX_TOOL_ROUNDS") or 250)
        except ValueError:
            max_rounds = 250
    import json as _json
    from wchat_py import msg as msgmod
    from wchat_py.render import StreamBox, render_markdown, render_reason
    msgs = msgmod.append_user(msgmod.init(_system_prompt_text()), content)
    _retry_state = {"done": False}      # 本轮最多自愈(压缩重发)一次
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
        try:
            from wchat_py import compaction as _cmp, compact_run as _cr
            try:
                _ls = int(os.environ.get("WCHAT_LAST_PROMPT_TOKENS") or 0)
                _th = int(os.environ.get("TOKEN_THRESHOLD") or 0)
            except ValueError:
                _ls = _th = 0
            if _cmp.due(_ls, _th):
                import types as _types
                _ss = _types.SimpleNamespace(messages_json=_json.dumps(msgs),
                                             msg_count=len(msgs),
                                             last_prompt_tokens=_ls,
                                             token_threshold=_th)
                _cr.compact(_ss, auto=True)
                msgs = _json.loads(_ss.messages_json)
        except Exception:
            pass
        try:
            text, tcs, err = _call_llm(msgs, model, on_text=_on_text, on_reason=_on_reason)
        except Aborted:
            # -st 单轮模式被打断:关箱、丢弃本轮输出、退 130(脚本调用方按"被中断"处理)
            sys.stdout.write(box.close()); sys.stdout.flush()
            sys.stdout.write("\n\033[2m⏹ 已中断,本轮输出已丢弃\033[0m\n"); sys.stdout.flush()
            return 130
        if os.environ.get("WCHAT_TRACE") == "1":
            sys.stderr.write("TRACE round tcs=%d names=%r err=%r\n"
                             % (len(tcs), [t.get("name") for t in tcs], err)); sys.stderr.flush()
        if text:
            last_text = text
        if err:
            # 请求失败自愈(对齐 master retry_after_compact):
            # 若判定是**上下文越界**,先压缩再重发一次。没有这一步的话,越界 =
            # 直接报错,用户手动重发 → 又付一次全量输入,而且拿不到 usage →
            # last_prompt_tokens 不更新 → 压缩永不触发(死循环)。
            if not _retry_state["done"]:
                import types as _types
                _ss = _types.SimpleNamespace(
                    messages_json=_json.dumps(msgs), msg_count=len(msgs),
                    last_prompt_tokens=_trunc_int("WCHAT_LAST_PROMPT_TOKENS", 0),
                    token_threshold=_trunc_int("TOKEN_THRESHOLD", 0),
                    ctx_overflow_gate=_trunc_int("CTX_OVERFLOW_GATE", 0))
                if _retry_after_compact(_ss, msgs, err):
                    _retry_state["done"] = True
                    msgs = _json.loads(_ss.messages_json)
                    continue
            sys.stdout.write(box.close()); sys.stdout.flush()
            sys.stderr.write("API 请求失败: %s\n" % err); return 1
        if not tcs:
            sys.stdout.write(box.close()); sys.stdout.flush()
            if reason:
                sys.stdout.write(render_reason("".join(reason)))
            sys.stdout.write(render_markdown(last_text)); sys.stdout.write("\n"); sys.stdout.flush()
            return 0
        # 工具执行前先关流箱并 flush 思考:否则权限提示/工具横幅会打在箱内光标处,
        # 下一帧 paint 会回箱顶重画,把提示擦掉(用户实测:提示打在箱上方且被滚掉)。
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
            _tcn = tc.get("name") or ""
            _tci = tc.get("id") or ""
            # master: `[[ -z "$fn_name" || -z "$fn_id" ]] && continue`
            # (legacy_wchat.sh:7297)。**空的工具名不该被执行** —— Python 版原来会
            # 拿空名去查表,得到"工具  尚未迁移"这种既不是成功也不是真错误的回传。
            # 但 continue 会让这条 assistant(tool_calls) 变成孤儿 → 下一轮 400,
            # 所以不能简单跳过:必须**补一条说明性的 tool 结果**把配对补上,
            # 同时明确告诉模型"这条调用没有名字,请重新发起"。
            if not _tcn.strip() or not _tci.strip():
                _bad = ("错误:这条工具调用缺少 %s(name=%r, id=%r),无法执行。"
                        "请重新发起这次调用并带上完整的工具名与 id。"
                        % ("工具名" if not _tcn.strip() else "调用 id", _tcn, _tci))
                sys.stdout.write("\033[2m⚠ 跳过一条无名工具调用(name=%r id=%r)\033[0m\n"
                                 % (_tcn, _tci))
                if _tci.strip():
                    msgs.append({"role": "tool", "content": _bad, "tool_call_id": _tci})
                else:
                    # 连 id 都没有:无法构造合法的 tool 结果,只能整轮放弃,
                    # 否则下一轮必 400(孤儿 tool_calls 没有配对)。
                    sys.stdout.flush()
                    return ("", None,
                            "模型返回了缺少工具名与调用 id 的调用,无法继续"
                            "(参数分片可能被网关拆坏了)。请重试一次。")
                continue
            # 同上:横幅后不留空行,空行放在整个工具块之后。
            sys.stdout.write(_banner_text(_tcn, tc.get("arguments") or ""))
            sys.stdout.flush()
            out = _run_tool(tc.get("name") or "", tc.get("arguments") or "{}")
            sys.stdout.write("\n"); sys.stdout.flush()
            from wchat_py.imgstate import imgjson_parts as _ijp, with_markers as _iwm
            _img = _ijp(out)
            if _img:
                _txt, _refs = _img
                msgs.append({"role": "tool", "content": _iwm(_txt, _refs),
                             "tool_call_id": tc.get("id") or ("call_%d" % tcs.index(tc))})
                continue
            msgs.append({"role": "tool", "content": out,
                         "tool_call_id": tc.get("id") or ("call_%d" % tcs.index(tc))})
    sys.stdout.write(box.close()); sys.stdout.flush()
    if reason:
        sys.stdout.write(render_reason("".join(reason)))
    sys.stdout.write(render_markdown(last_text)); sys.stdout.write("\n"); sys.stdout.flush()
    sys.stdout.write("\033[2m⏱ 已达上限 %d 次:本轮助手工具调用过多,已停止自动执行\033[0m\n"
                     % max_rounds)
    return 0


TITLE_TOOL = {"type": "function", "function": {
    "name": "set_title",
    "description": "提交为本次对话总结的标题",
    "parameters": {"type": "object", "properties": {
        "title": {"type": "string", "description": "对话标题,简短精炼,不超过 30 字,不含特殊字符与换行"}},
        "required": ["title"]}}}


def _gen_title(msgs_json):
    """根据对话生成短标题(与 master overview 一致:调用 set_title 工具)。

    **失败必须给兜底名**(master: `[[ -z "$title" ]] && title="未命名对话"`)。
    不给兜底的后果实测过:chat_title 一直是空 → 每轮结束都重新发一次
    "全量历史 + set_title" 的请求去要标题 → 4 轮对话白烧 4 次全量输入。
    """
    import json as _json
    try:
        msgs = _json.loads(msgs_json)
        msgs = list(msgs) + [{"role": "user", "content":
                              "根据我们的聊天对话,总结一个恰当的标题。要求:标题简短精炼,"
                              "不超过 30 字,不含特殊字符与换行;请通过调用 set_title 工具提交标题。"}]
        msg = _nonstream_msg(msgs, os.environ.get("FLASH_MODEL", ""),
                             tools=[TITLE_TOOL], source="title")
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
        return out[:40] or "未命名对话"
    except Exception:
        return "未命名对话"


def main(argv):
    # 工作根锁定:与 bash 一致按 pwd -P。缺省时 hardwall.detail 会退回 "/tmp",
    # 导致真实工作目录内命令被误判 P2(路径越界) —— 入口统一补齐。
    os.environ.setdefault("WORK_ROOT", os.path.realpath(os.getcwd()))
    _exe = _runtime_fallback()
    if _exe != sys.executable:
        sys.stderr.write("⚠ 当前解释器已不存在(多半是 brew 升级删了旧目录),"
                         "临时改用 %s;跑一次 `brew reinstall wchat` 可彻底修好\n" % _exe)
        sys.executable = _exe
    _load_ct_config()
    _apply_runtime_defaults()
    # 让 ~/.ct/mcporter.json 就位(**唯一入口**,见 seed.ensure_manifest):
    #   目标不存在 → 老位置有清单就搬过来,否则从随包模板生成;
    #   已存在 → 什么都不做(绝不覆盖用户改过的)。
    # 无条件执行,不要求带 -mcp:这是"配置文件就位",不是"启用能力";
    # 否则新用户会看到"没有清单",而正确的默认状态应该是有清单但没启用。
    # 必须在任何 MCP 相关读取之前;失败不阻止启动。
    try:
        from wchat_py import seed as _seed0
        _mp, _mwhy = _seed0.ensure_manifest()
        if _mp is None and _mwhy != "已存在":
            # 不许静默:以前这里把原因扔了,用户只看到 -mcp 那句"全部 server 探测失败",
            # 真因(装包漏了模板)完全不可见 —— v0.3.2 的 brew 包就是这样。
            sys.stderr.write("⚠ MCP 清单没有就位: %s\n"
                             "  → 随包模板缺失(装包不完整)或目标目录不可写时,清单永远生成不了,"
                             "本次 MCP 不可用;修复: brew reinstall wchat(或重装 deb),"
                             "并确认 ~/.ct 可写。\n" % _mwhy)
    except Exception as _me:
        sys.stderr.write("⚠ MCP 清单初始化失败: %s(本次 MCP 不可用)\n" % _me)
    # 配置:检测(只提示不接管)与填写(逐项问)分开。
    #   · `wchat -config …`  → 交给 setup.configure(),它负责校验/写盘/验连通
    #   · 其余入口           → setup.ensure_ready():缺配置就打印"哪里不对 + 下一步"
    #                          并退出(退出码 2),**不进 REPL** —— 半配置状态进循环
    #                          只会让用户下一步撞 `API 请求失败: Connection error.`
    try:
        from wchat_py import setup as _setup
        if argv and argv[0] in ("-config", "--config", "config", "-setup", "--setup", "setup"):
            return _setup.configure(argv[1:])
        # 子命令:必须排在 parse(argv) 之前 —— 否则 "model"/"mcp"/"skills" 会被
        # 当成一次对话的正文发给模型(词面完全合法,不报错,只是行为诡异)。
        if argv and argv[0] in ("model", "ctmodel"):
            return _setup.cmd_model(argv[1:])
        if argv and argv[0] == "mcp":
            from wchat_py import seed as _seed
            return _seed.cmd_mcp(argv[1:])
        if argv and argv[0] == "skills":
            from wchat_py import seed as _seed
            return _seed.cmd_skills(argv[1:])
        if argv and argv[0] == "memory":
            # 子命令必须排在 parse(argv) 之前:否则 "memory" 会被当成对话正文发给模型
            # (词面完全合法,不报错,只是行为诡异 —— 与 model/mcp/skills 同一条坑)。
            from wchat_py import memory as _mem
            return _mem.cmd_memory(argv[1:])
        if argv and argv[0] in ("usage", "apilog"):
            from wchat_py import apilog as _apilog
            return _apilog.main(argv[1:])
        _rc = _setup.ensure_ready(argv)
        if _rc is not None:
            return _rc
    except SystemExit:
        raise
    except Exception:
        pass            # 配置检测本身出错不该挡住正常使用
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
        # system 只由 `_system_prompt_text()` 一处构建:技能清单(含标题)/项目记忆/MCP
        # 三段都在里面。以前这里复制了一份 `scan_skills` 调用,后果是 REPL 启动的 system
        # **既没有标题也没有记忆段** —— 与单轮路径两副面孔。
        seed_system(_s, base=_system_prompt_text(), append_mcp=False)
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
                    # 默认保存:回车 = 是,只有明确打 n/no 才不保存(与 master 一致)。
                    # 提示语必须留"保存对话"四个字 —— e2e 探针靠它判断该答 n 了。
                    from wchat_py.render import confirm_block
                    if not confirm_block("退出前保存对话", default_yes=True,
                                         ask="是否保存", no_word="不保存"):
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
                _name = os.environ.get("WCHAT_HISTORY_NAME") or _title or (
                    "chat-%d" % int(_t.time()))
                try:
                    _sh(_d, _s.messages_json, _name, _title)
                    sys.stdout.write("保存记录 %s\n" % os.path.join(_d, _name)); sys.stdout.flush()
                except Exception as _e:
                    sys.stderr.write("保存失败: %s\n" % _e); sys.stderr.flush()
            finally:
                _auto_summary()

        _first_act = os.environ.pop("WCHAT_FIRST_ACTION", "")
        if _first_act:
            _meta(_first_act, "")
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
                # 横幅后**不留空行**(2026-12 用户要求):横幅与它下面那行
                # `└─ 已读取…` 是同一个工具块的两半,中间空一行会让它们看着不相关。
                # 块与块之间的空行由块**末尾**的 `sys.stdout.write("\n")` 负责。
                sys.stdout.write(_banner_text(n, a))
                sys.stdout.flush()
                out = _run_tool(n, a)
                sys.stdout.write("\n"); sys.stdout.flush()
                return out

            def _abort():
                # 关箱并丢弃半截正文/思考(本轮输出整体不渲染,与 master 一致)
                sys.stdout.write(_box.close()); sys.stdout.flush()
                _reason_acc.clear()
                _text_acc.clear()
                sys.stdout.write("\n\033[2m⏹ 已中断,本轮输出已丢弃(-q 可退出)\033[0m\n\n")
                sys.stdout.flush()
                _sync_runtime(_s)

            _h = session_text_handler(_s, _cl_stream, _run_tool_boxed,
                                      write_final=False, on_final=_finish, on_abort=_abort)
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
        _mr = int(os.environ.get("MAX_TOOL_ROUNDS") or 250)
        _rc = run_turn(st["content"], st.get("model", ""), _mr)
        _audit.record(_alog, "session", verdict="finished")
        return _rc
    if argv and argv[0] == "parse":
        from wchat_py.shellio import assign
        return cmd_parse(argv[1:])
    if "-h" in argv or "--help" in argv:
        help_user()
        return 0
    if "--version" in argv or "-V" in argv:
        from wchat_py.version import __version__
        print("wchat %s" % __version__)
        return 0
    # 参数路由：-u/usage、有正文走单轮、其余进 REPL(交互/标准输入)
    st = parse(argv)
    for _p in (st.get("allow_ro") or "").splitlines():
        _p = _p.strip()
        if _p:
            _GRANTED_PATHS.add(("r", _p))
    for _p in (st.get("allow_rw") or "").splitlines():
        _p = _p.strip()
        if _p:
            _ALLOW_RW.append(_p)
            # master: --allow-write 只在 -auto=full 生效;非 auto 仍逐次确认
            if st.get("auto_mode") == "full":
                _GRANTED_PATHS.add(("rw", _p))
    if _ALLOW_RW:
        os.environ["WCHAT_ALLOW_WRITE"] = " ".join(_ALLOW_RW)
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
    if st.get("think_full"):
        _apply_think_flag(st["think_full"])
    if st["action"] == "list_history":
        os.environ["WCHAT_FIRST_ACTION"] = "list_history"
    if st["action"] == "usage":
        from wchat_py.session import Session, usage_lines
        for ln in usage_lines(Session.from_env()):
            inp_msg(ln)
        return 0
    if st.get("content"):
        from wchat_py import audit as _audit
        _alog = os.environ.get("AUTO_AUDIT_LOG", "")
        _mr = int(os.environ.get("MAX_TOOL_ROUNDS") or 250)
        if str(st.get("quick_mode")) == "1":
            _rc = run_turn(st["content"], st.get("model", ""), _mr)
            _audit.record(_alog, "session", verdict="finished")
            return _rc
        # 非 -st:与 master 一致——先跑这一轮,然后进入交互循环(不退出)。
        os.environ["WCHAT_FIRST_TURN"] = st["content"]
        return main(["repl"])
    return main(["repl"])


# ─────────────────────────────────────────────────────────────────────────────
# 兼容门面:下面这些名字历史上长在 cli 里,测试与 mcp_boot 直接取 `cli.X`。
# 实现已经搬到分层模块(toolrun/approve/runtime/imgstate),这里只做**再导出**,
# 不写第二份实现。新增代码请直接 import 目标模块,别再往这里加东西。
# ─────────────────────────────────────────────────────────────────────────────
from wchat_py.approve import (_APPROVED_CMDS, _ALLOW_RW, _GRANTED_PATHS,  # noqa: E402
                              _cmd_key, _grant_path, _note_path_grants, _path_guard)
from wchat_py.imgstate import (_IMG_REFS, _IMG_SEQ, _IMG_URLS,  # noqa: E402
                              _register_image_url, _reset_image_refs)
from wchat_py.runtime import (_apply_runtime_defaults, _auto_summary,  # noqa: E402
                              apply_think_flag as _apply_think_flag,
                              _bump_auto, _detect_web_provider, _load_ct_config,
                              _runtime_fallback, _skills_dir_default, _sync_runtime,
                              _trunc_int)
from wchat_py.toolrun import (_args_obj, _auto_backup, _banner_text,  # noqa: E402
                              _missing_required, _required_of, _run_tool,
                              _trunc_for_context)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))