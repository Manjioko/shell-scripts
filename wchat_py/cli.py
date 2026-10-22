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
        elif a == "-st" or a == "-to":
            quick_mode = 1
            forget_history = 1
        else:
            unknown.append(a)
        i += 1
    return {"content": content, "model": model, "thinking": thinking,
            "forget_history": str(forget_history),
            "quick_mode": str(quick_mode), "seen_help": str(seen_help), "action": action,
            "unknown": " ".join(unknown)}


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

    msgs = msgmod.append_user(msgmod.init(), content)
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


def _call_llm(msgs, model, on_text=None):
    """调一次 llm stream, 返回 (text, tool_calls, error)。事件走同一条 JSON-lines seam。

    on_text(delta) 若给: 每个 text 增量实时回调(供流式渲染); text 仍被累积返回。
    """
    import json as _json, subprocess
    from wchat_py import tools as _tools
    try:
        _tb = _tools.build()
        _tlist = _json.loads(_tb) if isinstance(_tb, str) else _tb
    except Exception:
        _tlist = []
    req = {"model": model or os.environ.get("FLASH_MODEL", ""), "messages": msgs,
           "thinking": "disabled", "tools": _tlist}
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
        elif t == "tool_calls":
            tcs = ev.get("v") or []
        elif t == "error":
            err = ev.get("v")
    p.wait()
    return "".join(text), tcs, err


def _shell_approved(cmd):
    """shell 工具人工审批：auto=full 由硬墙裁决(此处放行)；显式 ASSUME_YES 放行；
    有 tty 弹 y/N；否则 fail-closed 拒（无头环境绝不静默执行）。"""
    if os.environ.get("AUTO_MODE") == "full":
        return True
    if os.environ.get("WCHAT_AUTO_ASSUME_YES") == "1":
        return True
    try:
        if not sys.stdin.isatty():
            return False
    except Exception:
        return False
    sys.stdout.write("执行命令? 必须逐字输入 yes 确认: ")
    sys.stdout.flush()
    ans = (sys.stdin.readline() or "").strip()
    return ans == "yes"


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
        resolved = path if os.path.isabs(path) else os.path.join(os.getcwd(), path)
        content, status = list_dir(resolved, path)
        if status:
            sys.stdout.write(status + "\n")
        return content
    if name == "read_file":
        path = a.get("path") or ""
        resolved = path if os.path.isabs(path) else os.path.join(os.getcwd(), path)
        work_root = os.environ.get("WORK_ROOT") or os.getcwd()
        content, status = read_file(resolved, path, a.get("offset"), a.get("limit"),
                                    os.environ.get("READ_MAX_CHARS", "30000"), work_root)
        if status:
            sys.stdout.write(status + "\n")
        return content
    if name == "grep":
        work_root = os.environ.get("WORK_ROOT") or os.getcwd()
        content, status = grep(a.get("pattern") or "", a.get("path") or "",
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
        content, status = edit_file(resolved, path, a.get("old_string") or "",
                                    a.get("new_string") or "", work_root)
        if status:
            sys.stdout.write(status + "\n")
        return content
    if name == "run_shell_command":
        cmd = a.get("command") or ""
        from wchat_py.hardwall import detail as _hd_detail
        _auto = os.environ.get("AUTO_MODE") == "full"
        _r, _s, _m = _hd_detail(cmd, _auto)
        if _r:
            return ("[硬墙 %s] %s" % (_r, _m))
        if not _shell_approved(cmd):
            return ("用户未批准执行命令: %s\n(非 auto 档需人工确认;当前无交互 tty 或未确认,已拒)"
                    % cmd)
        from wchat_py.termpty import pty_run
        try:
            _timeout = float(os.environ.get("CMD_TIMEOUT", "20"))
        except ValueError:
            _timeout = 20.0
        _disp, _full, _rc, _to, _intr = pty_run(
            ["bash", "-c", cmd], timeout=_timeout, win=int(os.environ.get("WIN", "10")))
        if _to:
            _full += "\n[命令超过单条上限 %gs,已终止 · rc=%s]\n" % (_timeout, _rc)
        return _full
    return "错误:工具 %s 尚未迁移到 Python 入口(1d 进行中)" % name


def run_turn(content, model="", max_rounds=5):
    """单轮内的工具链闭环: 请求 -> 有 tool_calls 就执行并续发 -> 无则结束。"""
    import json as _json
    from wchat_py import msg as msgmod
    from wchat_py.render import StreamBox
    msgs = msgmod.append_user(msgmod.init(), content)
    box = StreamBox(int(os.environ.get("STREAM_WIN", "10")))
    for _ in range(max_rounds):
        text, tcs, err = _call_llm(msgs, model)
        if text:
            box.append(text); sys.stdout.write(box.paint()); sys.stdout.flush()
        if err:
            sys.stderr.write("API 请求失败: %s\n" % err); return 1
        if not tcs:
            return 0
        # assistant 的 tool_calls 消息(正文也带上, 否则调用前的正文会丢)
        msgs.append({"role": "assistant", "content": text or None,
                     "tool_calls": [{"id": tc.get("id") or ("call_%d" % i), "type": "function",
                                     "function": {"name": tc.get("name") or "",
                                                  "arguments": tc.get("arguments") or "{}"}}
                                    for i, tc in enumerate(tcs)]})
        # 每个调用都必须有配对的 tool 结果, 否则下一轮 400(孤儿 tool_calls)
        from wchat_py.render import tool_banner, args_preview
        for tc in tcs:
            sys.stdout.write(tool_banner(tc.get("name") or "", args_preview(tc.get("arguments") or "")))
            sys.stdout.write("\n"); sys.stdout.flush()
            out = _run_tool(tc.get("name") or "", tc.get("arguments") or "{}")
            msgs.append({"role": "tool", "content": out,
                         "tool_call_id": tc.get("id") or ("call_%d" % tcs.index(tc))})
    return 0


def main(argv):
    if argv and argv[0] == "repl":
        from wchat_py.session import Session, seed_system
        from wchat_py.repl import run_repl, session_text_handler
        _s = Session.from_env()
        _extra = ""
        try:
            from wchat_py import skill as _skill
            _sd = os.environ.get("SKILLS_DIR_DEFAULT") or os.path.join(
                os.environ.get("HOME", ""), "." + "c" + "t", "skills")
            _r = _skill.scan_skills(_sd, "8", "200", "")
            _extra = _r if isinstance(_r, str) else ""
        except Exception:
            _extra = ""
        seed_system(_s, extra=_extra)
        from wchat_py.repl import run_interactive
        if sys.stdin.isatty():
            from wchat_py.render import StreamBox
            _box = StreamBox(int(os.environ.get("STREAM_WIN", "10")))

            def _cl_stream(m):
                def _emit(d):
                    _box.append(d)
                    sys.stdout.write(_box.paint())
                    sys.stdout.flush()
                return _call_llm(m, "", on_text=_emit)

            _h = session_text_handler(_s, _cl_stream, lambda n, a: _run_tool(n, a), write_final=False)
            return run_interactive(_h)
        _h = session_text_handler(_s, lambda m: _call_llm(m, ""), lambda n, a: _run_tool(n, a))
        return run_repl(sys.stdin, _h)
    if argv and argv[0] == "run-st":
        st = parse(argv[1:])
        return run_turn(st["content"], st.get("model", ""))
    if argv and argv[0] == "parse":
        from wchat_py.shellio import assign
        return cmd_parse(argv[1:])
    if "-h" in argv or "--help" in argv:
        help_user()
        return 0
    # 1b 起才接管其它参数；现在明确告知"还没迁到"，不静默成功。
    sys.stderr.write("cli: 该参数尚未迁移到 Python 入口（B 阶段 1b 起逐步接管）: %r\n" % (argv,))
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))