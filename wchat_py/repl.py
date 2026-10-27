#!/usr/bin/env python3
"""REPL 读行循环骨架（B 阶段主循环迁移第二刀）。

职责：读行 → `cmdline.classify` → 分发：
  · text   → `on_text(arg)`（默认接 `cli.run_turn`，单轮已有 1c/run_turn）
  · quit   → 结束
  · 其余   → `on_meta(action, arg)`（默认打印"尚未在 Python 主循环实现"占位，副作用逐步迁）

只做**循环与分发**，不碰 tty/信号/网络——便于用假 stream + 假 handler 单测。
"""
import os
import subprocess
import sys

from wchat_py.cmdline import classify


def _msg_content_text(c):
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        texts = [p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text"]
        nimg = sum(1 for p in c if isinstance(p, dict) and p.get("type") == "image_url")
        t = "\n".join(x for x in texts if x)
        if nimg:
            t += ("\n" if t else "") + "[含 %d 张图片]" % nimg
        return t
    return ""


def _clip_line(s, n=200):
    return s if len(s) <= n else s[:n] + "…"


def _render_history(msgs):
    """把加载的历史按 master get_history 的格式渲染成 markdown。"""
    from wchat_py.render import render_markdown
    tc_names = {}
    for m in msgs:
        for tc in (m.get("tool_calls") or []) if isinstance(m, dict) else []:
            tc_names[tc.get("id") or ""] = (tc.get("function") or {}).get("name") or "?"
    parts = []
    for m in msgs[1:]:
        role = m.get("role", "")
        content = _msg_content_text(m.get("content"))
        reasoning = m.get("reasoning_content") or ""
        if reasoning:
            parts.append("### 💭 思考\n" + reasoning + "\n")
        if role == "tool":
            name = tc_names.get(m.get("tool_call_id") or "")
            parts.append("## tool → %s" % name if name else "## tool")
            disp = content.replace("\\n", "\n").lstrip("\ufeff")
            lines = [ln for ln in disp.splitlines() if ln.strip()]
            if not lines:
                parts.append("*内容为空*")
            else:
                shown = "\n".join(_clip_line(ln) for ln in lines[:5])
                if len(lines) > 5:
                    shown += "\n…(其余 %d 行已省略)" % (len(lines) - 5)
                parts.append(shown)
        else:
            parts.append("## " + role)
            if content:
                parts.append(content)
            if role == "assistant" and m.get("tool_calls"):
                tc_lines = ["🔧 %s: %s" % ((tc.get("function") or {}).get("name") or "?",
                                           ((tc.get("function") or {}).get("arguments") or "")
                                           .replace("\n", " ").replace("\r", " "))
                            for tc in m["tool_calls"]]
                parts.append("\n".join(_clip_line(x) for x in tc_lines[:6]))
            elif not content:
                parts.append("*（本轮无文本内容）*")
        parts.append("")
    if parts:
        try:
            sys.stdout.write(render_markdown("\n".join(parts)))
        except Exception:
            sys.stdout.write("\n".join(parts))
    else:
        sys.stdout.write("⚠️ 历史记录为空\n")
    sys.stdout.flush()


def _default_meta(action, arg, session=None):
    from wchat_py.session import Session as _S
    sess = session if session is not None else _S.from_env()
    if action == "help":
        from wchat_py.cli import help_user
        help_user()
        return True
    if action == "usage":
        from wchat_py.cli import inp_msg
        from wchat_py.session import Session, usage_lines
        for ln in usage_lines(sess):
            inp_msg(ln)
        return True
    if action == "list_history":
        import glob as _glob
        import json as _json
        d = os.environ.get("WCHAT_HISTORY_DIR", "")
        files = []
        for f in _glob.glob(os.path.join(d, "*.json")):
            try:
                files.append((os.path.getmtime(f), f))
            except OSError:
                pass
        files.sort(key=lambda x: (-x[0], x[1]))
        if not files:
            sys.stdout.write("(无历史)\n")
            return True
        sys.stdout.write("请从以下编号中选择你要加载的历史记录：\n")
        for i, (_mt, f) in enumerate(files):
            name = os.path.basename(f).replace("\n", " ").replace("\r", " ").replace("\t", " ")
            sys.stdout.write("%d. %s\n" % (i, name))
        sys.stdout.write("_>"); sys.stdout.flush()
        try:
            num = (sys.stdin.readline() or "").strip()
        except KeyboardInterrupt:
            sys.stdout.write("\n"); sys.stdout.flush()
            return True
        except Exception:
            num = ""
        if not num:
            num = "0"
        if not num.isdigit() or not (0 <= int(num) < len(files)):
            sys.stdout.write("请选择正确的编号\n"); sys.stdout.flush()
            return True
        path = files[int(num)][1]
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                data = _json.load(f)
            msgs = data if isinstance(data, list) else data.get("messages") or []
            if not isinstance(msgs, list) or not msgs:
                raise ValueError("empty")
        except Exception:
            sys.stdout.write("\033[1;31m❌ 历史文件格式错误或为空: %s\033[0m\n" % path)
            sys.stdout.flush()
            return True
        # 始终刷新 system(技能/MCP 清单可能是旧的)
        from wchat_py.cli import _system_prompt_text
        _sys = {"role": "system", "content": _system_prompt_text()}
        if msgs and isinstance(msgs[0], dict) and msgs[0].get("role") == "system":
            msgs = [_sys] + msgs[1:]
        else:
            msgs = [_sys] + msgs
        sess.messages_json = _json.dumps(msgs, ensure_ascii=False)
        sess.msg_count = len(msgs)
        sess.chat_title = os.path.basename(path)
        os.environ["WCHAT_CHAT_TITLE"] = sess.chat_title
        if isinstance(data, dict):
            if data.get("model"):
                sess.model = data["model"]; os.environ["WCHAT_MODEL"] = data["model"]
            if data.get("thinking"):
                th = data["thinking"]
                if isinstance(th, dict):
                    th = th.get("type")
                if th in ("enabled", "disabled"):
                    sess.thinking = th; os.environ["WCHAT_THINKING"] = th
        try:
            from wchat_py.cli import _reset_image_refs
            _reset_image_refs()
        except Exception:
            pass
        sys.stdout.write("\033[H\033[2J")
        sys.stdout.write("切换到 %s(共 %d 条消息)\n\n" % (path, len(msgs)))
        sys.stdout.flush()
        _render_history(msgs)
        return True
    if action == "compact":
        try:
            from wchat_py import compact_run
            compact_run.compact(sess, auto=False)
        except Exception as e:
            sys.stdout.write("压缩失败: %s\n" % e)
            sys.stdout.flush()
        return True
    if action == "show_msgs":
        from wchat_py.cli import inp_msg
        from wchat_py.session import Session
        inp_msg(sess.messages_json)
        return True
    if action == "save_history":
        from wchat_py.session import save_history
        d = os.environ.get("WCHAT_HISTORY_DIR", "")
        if not d:
            sys.stdout.write("⚠️ 未设置 WCHAT_HISTORY_DIR,未保存\n")
            return True
        from wchat_py.cli import _gen_title
        title = os.environ.get("WCHAT_CHAT_TITLE", "") or _gen_title(sess.messages_json)
        # 名字交给 save_history 统一清洗(标题里的 .json 必须剥,否则 xxx.json.json)
        name = os.environ.get("WCHAT_HISTORY_NAME") or title
        path = save_history(d, sess.messages_json, name, title)
        sys.stdout.write("✅ 已保存: %s\n" % path)
        return True
    if action == "model_status":
        from wchat_py.cli import inp_msg
        from wchat_py.session import Session, model_status_lines
        for ln in model_status_lines(sess):
            inp_msg(ln)
        return True
    if action in ("model_pro", "thinking_on", "model_flash"):
        from wchat_py.cli import inp_msg
        from wchat_py.session import Session, set_pro, set_flash, thinking_on
        s = sess
        if action == "model_pro":
            _m = set_pro(s)
            os.environ["WCHAT_MODEL"] = _m
            inp_msg("✅ 已切换到 pro 模型 (%s)" % _m)
        elif action == "thinking_on":
            thinking_on(s)
            os.environ["WCHAT_THINKING"] = "enabled"
            inp_msg("✅ 已开启思考模式")
        else:
            _m = set_flash(s)
            os.environ["WCHAT_MODEL"] = _m
            os.environ["WCHAT_THINKING"] = "disabled"
            inp_msg("✅ 已恢复默认 (model=%s, thinking=disabled)" % _m)
        return True
    if action == "skill_save":
        import json as _json
        import re as _re
        from wchat_py.cli import _call_llm, inp_msg
        try:
            _msgs = _json.loads(sess.messages_json)
        except ValueError:
            _msgs = []
        if len(_msgs) <= 1:
            inp_msg("⚠️ 当前对话为空,没有可沉淀的经验")
            return True
        inp_msg("📦 正在从当前对话提炼技能…")
        _conv = []
        for _m in _msgs:
            _c = _m.get("content")
            if not isinstance(_c, str):
                _c = "[非文本内容]"
            _conv.append("%s: %s" % (_m.get("role"), _c))
        from wchat_py.cli import _skill_draft
        _d = _skill_draft("\n".join(_conv)) or {}
        _name = str(_d.get("name", "")).strip().lower()
        _name = _re.sub(r"[^a-z0-9-]+", "-", _name).strip("-")[:30]
        _desc = str(_d.get("description", "")).strip()
        _body = str(_d.get("body", "")).strip()
        if not _name or not _desc or not _body:
            inp_msg("⚠️ 技能提炼失败(模型未按 save_skill 工具提交草稿)")
            return True
        _desc = " ".join(_desc.split())
        inp_msg('──────── 技能草稿 ────────')
        inp_msg("name: %s" % _name)
        inp_msg("description: %s" % _desc)
        inp_msg('──────── 正文 ────────')
        inp_msg(_body)
        inp_msg('────────────────────────')
        if sys.stdin.isatty():
            sys.stdout.write("\033[2m│\033[0m 是否保存该技能?(y 或 yes,n/回车 = 取消): ")
            sys.stdout.flush()
            try:
                _ans = (sys.stdin.readline() or "").strip().lower()
            except KeyboardInterrupt:
                _ans = ""
            except Exception:
                _ans = ""
            sys.stdout.write("\n"); sys.stdout.flush()
            if _ans not in ("y", "yes"):
                inp_msg("已取消")
                return True
        _sd = os.environ.get("SKILLS_DIR_DEFAULT") or os.path.join(
            os.environ.get("CT_HOME") or os.path.join(os.environ.get("HOME", ""), ".ct"),
            "skills")
        _dir = os.path.join(_sd, _name)
        try:
            os.makedirs(_dir, exist_ok=True)
            with open(os.path.join(_dir, "SKILL.md"), "w", encoding="utf-8") as _f:
                _f.write("---\nname: %s\ndescription: %s\n---\n\n%s\n"
                         % (_name, _desc, _body))
            inp_msg("✅ 已保存技能: %s" % os.path.join(_dir, "SKILL.md"))
        except OSError as _e:
            inp_msg("⚠️ 保存技能失败: %s" % _e)
        return True
    if action == "auto_on":
        from wchat_py.cli import inp_msg
        from wchat_py.session import enter_auto
        from wchat_py import auto_enable as _ae
        s = sess
        tier = arg or s.auto_mode or (s.auto_tiers[0] if s.auto_tiers else "")
        if tier not in s.auto_tiers:
            inp_msg("未知档位: %s(可选: %s)" % (tier, " ".join(s.auto_tiers)))
            return True
        os.environ["AUTO_MODE"] = tier
        os.environ["WCHAT_AUTO_MODE"] = tier
        if not _ae.enable(tier):
            os.environ.pop("AUTO_MODE", None)
            os.environ.pop("WCHAT_AUTO_MODE", None)
            return True
        enter_auto(s, tier)
        return True
    if action == "auto_off":
        from wchat_py.cli import inp_msg, _auto_summary
        from wchat_py.session import auto_off
        s = sess
        if not s.auto_mode:
            inp_msg("当前未开启 auto")
        else:
            _auto_summary()
            os.environ.pop("AUTO_MODE", None)
            os.environ.pop("WCHAT_AUTO_MODE", None)
            auto_off(s)
            inp_msg("✅ 已关闭 auto,恢复逐次人工确认")
        return True
    if action == "image":
        from wchat_py.cli import inp_msg
        from wchat_py.session import Session, add_image
        if not arg:
            inp_msg("用法: -img <图片路径>")
        else:
            add_image(sess, arg)
            inp_msg("[图片] %s" % arg)
        return True
    if action == "clear":
        sys.stdout.write("\033[H\033[2J")
        sys.stdout.flush()
        return True
    if action == "new_session":
        import json as _json
        from wchat_py.cli import inp_msg
        # master: messages=msg init + system_inject_all + chat_title="" + clear
        # 这里保留当前 system(已含技能/MCP 注入)作为新会话首条,清空其余消息与标题。
        try:
            _msgs = _json.loads(sess.messages_json or "[]")
        except ValueError:
            _msgs = []
        _sysmsg = (_msgs[0] if _msgs and _msgs[0].get("role") == "system"
                   else {"role": "system", "content": "You are a helpful assistant."})
        sess.messages_json = _json.dumps([_sysmsg], ensure_ascii=False)
        sess.msg_count = 1
        sess.chat_title = ""
        sess.last_prompt_tokens = 0
        sess.last_cached_tokens = 0
        sess.input_images = []
        try:
            from wchat_py.cli import _reset_image_refs
            _reset_image_refs()
        except Exception:
            pass
        os.environ.pop("WCHAT_CHAT_TITLE", None)
        sys.stdout.write("\033[H\033[2J"); sys.stdout.flush()
        inp_msg("✅ 已开始新会话")
        return True
    sys.stdout.write("[元命令 %s] 尚未在 Python 主循环实现(仍在 bash)\n" % action)
    return True


_PLACE_IMG = "📋 [图片] "
_PLACE_FILE = "📋 [文件] "
_PLACE_SKIP = "📋 [跳过] "


def parse_placeholders(text):
    """把 Ctrl-F/@ 带进来的占位行解析成授权 + 转述(等价 bash parse_placeholders)。
    返回 (新文本, 图片路径列表)。"""
    imgs = []
    out = []
    for line in (text or "").split("\n"):
        raw = line
        line = line.replace("\u200b", "")
        if line.startswith(_PLACE_IMG):
            p = line[len(_PLACE_IMG):].strip()
            if os.path.isfile(p) and _is_image(p):
                imgs.append(p)
                _grant_read(p)
                continue
        elif line.startswith(_PLACE_FILE):
            p = line[len(_PLACE_FILE):].strip()
            if os.path.isfile(p) and _is_text_file(p):
                _grant_read(p)
                out.append("(用户通过 @ 选择了文件: %s。该文件已获只读授权,如需查看内容,"
                           "请先调用 read_file 工具读取,再回答用户问题。)" % p)
                continue
        elif line.startswith(_PLACE_SKIP):
            p = line[len(_PLACE_SKIP):].strip()
            out.append("(用户尝试选择文件: %s,但该文件不是文本/图片格式,已被跳过。"
                       "请勿尝试读取;如需处理请告知用户先转换格式。)" % p)
            continue
        out.append(raw)
    return "\n".join(out), imgs


def _grant_read(path):
    try:
        from wchat_py import cli as _cli
        _cli._GRANTED_PATHS.add(("r", os.path.realpath(os.path.expanduser(path))))
    except Exception:
        pass


def session_text_handler(session, call_llm, run_tool, out=None, write_final=True, on_final=None,
                         on_abort=None):
    """返回 on_text 处理器：正文 → orchestrate(Session, ...) → 输出最终文本。

    write_final=False 时不在末尾整段输出（正文已由 call_llm 的 on_text 流式画过）。
    on_final(r) 若给：编排结束后调用（用于关流箱 + markdown 渲染）。
    on_abort() 若给：Ctrl+C 打断本轮时调用（关流箱 + 丢弃半截输出的提示）；不给则
    直接打印与 master 同款提示。**打断不是错误**：本轮 assistant 输出整体丢弃，
    用户那句已写入，回到输入框等用户补充后重发。
    Session 跨轮持久（messages 累积），这是 REPL 与 ask_ai 编排的接线点。
    """
    from wchat_py.session import orchestrate, Aborted
    out = out if out is not None else sys.stdout

    def handler(text):
        text, _imgs = parse_placeholders(text)
        if _imgs:
            try:
                session.input_images.extend(_imgs)
            except Exception:
                pass
        try:
            r = orchestrate(session, text, call_llm, run_tool)
        except Aborted:
            if on_abort is not None:
                on_abort()
            else:
                out.write("\n⏹ 已中断,本轮输出已丢弃(-q 可退出)\n\n")
                out.flush()
            return
        if on_final is not None:
            on_final(r)
        elif write_final:
            out.write((r or "") + "\n")

    return handler


def run_repl_loop(next_line, on_text, on_meta=None, on_quit=None):
    """核心循环：next_line()->str|None(EOF)。可注入，便于单测交互与非交互两种来源。

    on_quit() 在读到 -q 时、返回前调用一次（用于退出保存历史）。
    """
    on_meta = on_meta or _default_meta
    while True:
        if os.environ.get("AUTO_MODE") or os.environ.get("WCHAT_AUTO_MODE"):
            if os.environ.pop("WCHAT_AUTO_MARKER_ALREADY", "") != "1":
                try:
                    from wchat_py.auto_enable import marker_text
                    _m = marker_text()
                    if _m:
                        sys.stdout.write(_m + "\n"); sys.stdout.flush()
                except Exception:
                    pass
        try:
            line = next_line()
        except KeyboardInterrupt:
            # 输入行上按 Ctrl+C:取消这一行输入,回提示符(与 master 的 input_aborted 同一语义)。
            # 不接住的话异常会一路冒到 main 变成 traceback 退出,而用户只是想清掉这行。
            sys.stdout.write("\n")
            sys.stdout.flush()
            continue
        if line is None:
            return 0
        line = line.rstrip("\n")
        if line == "":
            continue
        action, arg = classify(line)
        if action == "quit":
            if on_quit is not None:
                on_quit()
            return 0
        if action == "text":
            # Ctrl+C 兜底:流式读循环自己会转成 Aborted(见 cli._call_llm),这里是
            # 工具执行/渲染等其它路径上的中断。**绝不带着 traceback 退出**——master
            # 的语义是"取消本轮,回输入框等下一句",用户按 Ctrl+C 只是想打断,不是想
            # 关掉整个会话(用户实测:迁移版直接崩到 KeyboardInterrupt 栈)。
            try:
                on_text(arg)
            except KeyboardInterrupt:
                sys.stdout.write("\n\033[2m⏹ 已中断,本轮输出已丢弃(-q 可退出)\033[0m\n\n")
                sys.stdout.flush()
                continue
        else:
            if on_meta(action, arg) is False:
                return 0


def run_repl(stream, on_text, on_meta=None, on_quit=None):
    it = iter(stream)
    return run_repl_loop(lambda: next(it, None), on_text, on_meta, on_quit)


_IMG_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")
_TEXT_EXTS = (".txt", ".md", ".markdown", ".py", ".js", ".ts", ".json", ".yaml", ".yml",
              ".toml", ".ini", ".cfg", ".sh", ".bash", ".zsh", ".c", ".h", ".cpp", ".hpp",
              ".java", ".go", ".rs", ".rb", ".php", ".sql", ".html", ".css", ".xml", ".csv",
              ".log", ".conf", ".env", ".dockerfile", "makefile", ".gitignore")


def _is_image(p):
    return os.path.splitext(p)[1].lower() in _IMG_EXTS


def _is_text_file(p):
    ext = os.path.splitext(p)[1].lower()
    try:
        with open(p, "rb") as f:
            chunk = f.read(4096)
        if b"\0" in chunk:
            return False
    except OSError:
        return False
    return ext in _TEXT_EXTS or os.path.basename(p).lower() in _TEXT_EXTS


def _pick_file():
    """Ctrl-F 文件选择器:用系统原生对话框(macOS AppleScript / Linux zenity|kdialog / WSL)。"""
    import shutil
    if sys.platform == "darwin":
        try:
            p = subprocess.run(
                ["osascript", "-e", "try", "-e", "POSIX path of (choose file)",
                 "-e", "on error", "-e", 'return "CANCELLED"', "-e", "end try"],
                capture_output=True, text=True)
            return (p.stdout or "").strip()
        except OSError:
            return ""
    if os.environ.get("WSL_DISTRO_NAME"):
        try:
            p = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command",
                 "Add-Type -AssemblyName System.Windows.Forms;"
                 "$f=New-Object System.Windows.Forms.OpenFileDialog;"
                 "if($f.ShowDialog() -eq 'OK'){$f.FileName}else{'CANCELLED'}"],
                capture_output=True, text=True)
            return (p.stdout or "").strip()
        except OSError:
            return ""
    for cmd in (["zenity", "--file-selection"], ["kdialog", "--getopenfilename"]):
        if shutil.which(cmd[0]):
            try:
                p = subprocess.run(cmd, capture_output=True, text=True)
                return (p.stdout or "").strip()
            except OSError:
                return ""
    return ""


def _file_placeholder(path):
    if _is_image(path):
        tag = "📋 [图片] "
    elif _is_text_file(path):
        tag = "📋 [文件] "
    else:
        tag = "📋 [跳过] "
    return tag + path + "\u200b"


def run_interactive(on_text, on_meta=None, prompt="❯ ", on_quit=None):
    """交互式：经 input_helper.py(prompt_toolkit) 读行；缺文件时退回 input()。

    Ctrl-F(rc=4) 按 master 逻辑:弹系统文件选择器 → 把占位行带回输入框继续编辑;
    取消且输入为空 → 本轮无输入。
    """
    _repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _helper = os.path.join(_repo, "input_helper.py")
    _ct = os.environ.get("CT_HOME") or os.path.join(os.environ.get("HOME", ""), ".ct")
    _hist = os.path.join(_ct, "input_history")
    _clip = os.path.join(_ct, "clip")

    def nxt():
        if os.path.isfile(_helper):
            default = ""
            while True:
                from wchat_py import cli as _cli
                argv = [_cli._runtime_fallback(), _helper, "--history", _hist,
                        "--clip-dir", _clip, "--no-commands"]
                if default:
                    argv += ["--default", default]
                try:
                    p = subprocess.run(argv, capture_output=True, text=True)
                except OSError:
                    p = None
                if p is None:
                    break
                if p.returncode == 4:
                    cur = (p.stdout or "")
                    if cur.endswith("\n"):
                        cur = cur[:-1]
                    path = _pick_file()
                    if not path or path == "CANCELLED" or not os.path.isfile(path):
                        if not cur:
                            return ""
                        default = cur
                    else:
                        default = cur + ("\n" if cur else "") + _file_placeholder(path)
                    continue
                if p.returncode == 1:
                    return None
                _t = (p.stdout or "")
                if _t.endswith("\n"):
                    _t = _t[:-1]
                return _t
        try:
            return input(prompt)
        except EOFError:
            return None
    return run_repl_loop(nxt, on_text, on_meta, on_quit)


if __name__ == "__main__":
    from wchat_py.cli import run_turn
    sys.exit(run_repl(sys.stdin, lambda t: run_turn(t)))