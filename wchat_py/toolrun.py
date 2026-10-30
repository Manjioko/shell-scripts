#!/usr/bin/env python3
"""工具调用层:参数校验 → 横幅 → 分发执行 → 把结果文本交给会话层。

## 为什么单独一个模块

这段代码原本长在 `cli.py` 里(431 行的 `_run_tool` + 校验/横幅),而 `cli.py` 同时还是
参数解析、帮助、审批链、会话循环、配置装配的宿主 —— 结果任何一次"改工具"都要在一个
2000 行的文件里找位置,任何一次"改入口"都要担心碰坏工具。切开之后:

    cli.py     入口:参数解析 / help / main          (只做"把命令行翻译成一次会话")
    turn 逻辑  仍在 cli.py(run_turn/_call_llm,测试按名字打桩,搬走会破坏接缝)
    toolrun.py 本文件:工具协议(必填校验/横幅/分发)
    approve.py 审批与授权记账
    runtime.py 运行期配置装配

## 与其他层的两条边界(改动前先看)

1. **不碰权限裁决**:审批是 `cli._shell_approved` / `cli._auto_judge` 的职责,这里只在
   执行前调用它们。这些名字**在调用时**从 `cli` 取(不是模块级 import),因为测试
   会 `cli._auth_think = fake` 打桩 —— 模块级 import 会让打桩失效,那是"闸门绿但
   没测到"的经典形状。
2. **不碰终端渲染**:横幅文本只返回字符串,由会话层决定怎么画。
"""
import os
import sys
from wchat_py.approve import (_APPROVED_CMDS, _cmd_key, _grant_path,
                             _note_path_grants, _path_guard)
from wchat_py.imgstate import _IMG_REFS, _IMG_SEQ, _IMG_URLS
from wchat_py.render import args_preview, dim, ellipsis, tool_banner
from wchat_py.runtime import _trunc_int


def _seam():
    """取"留在 cli 的审批/记账入口"——**每次调用都重新取**,不要提到模块级。

    为什么:测试用 `cli._auth_think = fake` 打桩,若这里模块级 `from wchat_py.cli
    import _auth_think`,拿到的是打桩**之前**的函数对象,桩永远不生效 —— 现象是
    "闸门全绿但审批真去调了模型"。这就是本项目说的接口缝(seam):跨层调用只认
    名字,不认绑定时刻。
    """
    from wchat_py import cli
    return cli


_RED = "\033[1;31m"


_ZERO = "\033[0m"


GREY = "\033[2m"


ZERO = "\033[0m"


def _status(text):
    """上屏一行"非正文"状态(已读取/已列出/已写入…)。

    统一规则(2026-11 用户报"画面杂乱"):**只有助手正文用正常亮度**,其余一律灰化,
    与思考内容同色,并压成一行 —— 工具状态行里带文件名/行数,长路径会把行撑得很宽。
    """
    from wchat_py.render import dim
    s = dim(str(text or "").strip())
    if s:
        sys.stdout.write(" " + s + "\n")
        sys.stdout.flush()


def _args_obj(args_json):
    """参数解析成 dict;不是合法 JSON 对象时返回 None(原文由 _run_tool 负责回显)。"""
    import json as _j
    try:
        a = _j.loads(args_json or "{}")
    except Exception:
        return None
    return a if isinstance(a, dict) else None


def _banner_text(name, args_json):
    """master 的工具横幅:shell 显示 'shell · 命令原文',其余 'name · args 预览'。

    空命令不许静默留白(界面留白会被当成"命令很短"),但**也不能瞎猜原因**:
    原来这里一律写"(命令为空 —— 本次调用未带 command 参数)",而真实事故里
    模型发的是 `{"cmd": "..."}` / `{"path":..., "pattern":...}` —— 参数一个字
    没少,只是**键名不对**。那句假归因把"模型写错键"说成"wchat 丢了参数",
    人和模型都被它带偏(用户据此认为工具调用被解析坏了)。所以分四种说清:
    带空 command / 缺 command 但有别的键 / 根本不是 JSON / 一个参数都没有。
    """
    from wchat_py.render import tool_banner, args_preview
    if name == "run_shell_command":
        a = _args_obj(args_json)
        cmd = (a or {}).get("command") if a is not None else (args_json or "")
        if str(cmd or "").strip():
            return tool_banner("shell", cmd)
        if a is None:
            why = "⚠ (参数不是合法 JSON,未执行)"
        elif "command" in a:
            why = "⚠ (命令为空:command 是空串/纯空白,未执行)"
        elif a:
            why = "⚠ (缺 command —— 本次收到的是 %s,键名不对,未执行)" % "/".join(a)
        else:
            why = "⚠ (本次调用没带任何参数,未执行)"
        # 不用红色:非正文一律灰(与思考同色),靠 `⚠` 与前缀 `未执行` 保持可辨识度。
        return tool_banner("shell", why)
    return tool_banner(name or "", args_preview(args_json or ""))


_REQUIRED_CACHE = {}


def _required_of(name):
    """从 tools_spec.json 取该工具的必填参数名(唯一真源,不在这里抄第二份)。

    为什么必须落到这里:幂等工具收到空参数时**不会报错**,只会做一件看似合理的事 ——
    `run_shell_command` 执行 `bash -c ""` 拿到 0 行输出、`read_file` 读到工作目录本身。
    用户看到的是"命令输出为空很奇怪",而真因是**参数根本没送到**。
    实测:一次工具调用只带了 `{"command": ""}`,界面显示 `⏺ shell · `(空白)、
    结果"完成 · 共 0 行",模型于是以为工具坏了、白烧一轮去排查。
    """
    if name in _REQUIRED_CACHE:
        return _REQUIRED_CACHE[name]
    req = []
    try:
        import json as _j
        spec_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools_spec.json")
        with open(spec_path, encoding="utf-8") as f:
            for t in _j.load(f) or []:
                fn = (t or {}).get("function") or {}
                if fn.get("name") == name:
                    req = list((fn.get("parameters") or {}).get("required") or [])
                    break
    except (OSError, ValueError):
        req = []
    _REQUIRED_CACHE[name] = req
    return req


def _missing_required(name, a):
    """返回缺失或为空的必填参数名列表。空串/纯空白算缺失。"""
    miss = []
    for k in _required_of(name):
        v = a.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            miss.append(k)
    return miss


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


def _trunc_for_context(text):
    """工具输出 → 进上下文的文本(唯一截断出口)。

    参数与 master 同源:`TOOL_OUTPUT_MAX_COL`(2000) / `TOOL_OUTPUT_MAX_LINES`(400)。
    组件不可用时**原样返回**(降级为不截断,不丢内容,也不炸调用链)。
    """
    if not text:
        return text
    try:
        from wchat_py.textutil import truncate_output
        return truncate_output(text,
                               _trunc_int("TOOL_OUTPUT_MAX_COL", 2000),
                               _trunc_int("TOOL_OUTPUT_MAX_LINES", 400))
    except Exception:
        return text


def _run_tool(name, args_json):
    """执行一个工具。目前只接 list_dir(1d 的第一个);其余**明示未迁移**, 不静默成功。"""
    import json as _json
    from wchat_py.tools_run import list_dir, read_file, grep, write_file, edit_file
    try:
        a = _json.loads(args_json or "{}")
    except Exception:
        return ("错误:工具参数不是合法 JSON(收到的原始参数:%r)。"
                "请重新发起这次工具调用,并给出完整合法的 JSON 参数。"
                % ((args_json or "")[:200],))
    if not isinstance(a, dict):
        return "错误:工具参数必须是 JSON 对象,收到 %r" % (type(a).__name__,)
    # 必填参数校验:缺了就**立刻说清楚**,不要拿空值去执行。
    # 这是"静默失败"的典型入口 —— 空命令会成功返回 0 行、空路径会读到当前目录,
    # 两者都不报错,却让调用方以为"工具出了问题"。
    _miss = _missing_required(name, a)
    if _miss:
        # 措辞与 master 对齐:bash 版在 run_shell_command 分支里有
        #   `if [[ -z "$command" ]]; then content="错误:命令参数为空,工具调用参数解析失败: ..."`
        # (legacy_wchat.sh:7331)。迁移到 Python 时**这一段漏掉了** ——
        # 于是空命令被 `bash -c ""` 执行,拿到 0 行 + rc=0,界面显示"完成",
        # 整条链看起来都正常。用户看到的就是"命令输出为空很奇怪"。
        # 这里保留 master 的原话来指路(SEARCH 时能对上),再补上"缺哪个参数"。
        _raw = _json.dumps(a, ensure_ascii=False) if a else "(空)"
        # 措辞:shell 保持 master 原话("命令参数为空"),其它工具说"参数为空"。
        # 不为了一致性把 master 那句改掉 —— 那句是排障时的搜索锚点。
        _what = "命令参数为空" if name == "run_shell_command" else "参数为空"
        # 把"收到了什么键"直接说出来:真实事故里模型发的是 {"cmd": ...},
        # 光说"缺少 command"它还会照原样再发一遍(实测多烧 1 轮)。
        _got = (";本次收到的参数键:%s" % "/".join(a)) if a else ""
        _hint = ""
        for _wrong, _right in (("cmd", "command"), ("cmdline", "command"),
                               ("command_line", "command"), ("shell", "command")):
            if _wrong in a and _right in _miss:
                _hint = ";键名疑似写错:%s 应为 %s" % (_wrong, _right)
                break
        return ("错误:%s,工具调用参数解析失败: %s"
                "(缺少必填参数:%s;工具:%s%s%s。请重新发起这次调用并补上这些参数,"
                "不要假设它们有默认值。)"
                % (_what, _raw, "/".join(_miss), name, _got, _hint))
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
            _status(status)
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
            _status(status)
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
            _status(status)
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
            _status(status)
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
            _status(status)
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
                    _status("🖼 同一张图已在上下文(引用号 %s),未重复挂载" % _rid)
                    sys.stdout.flush()
                    return ("read_image: %s —— 这张图本会话已经给过你了(见引用号 %s 所在的那条消息),"
                            "本次不再重复挂载。请回看那条消息里的画面作答;若那里已看不到图片本体"
                            "(例如它被回收过),再调用一次本工具,我会收回旧引用并重新挂载。"
                            % (a.get("path") or _p, _rid))
                _st["gone"] = True
                _status("♻ 收回旧引用 #%d 并重新挂载(两次原则)" % _st["id"])
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
            _status("🖼 原图已交给主模型 %s(已缩到 %spx)" % (_disp, _side))
        else:
            _dw = _w or _ow or _side
            _dh = _h or _oh or _side
            _status("🖼 原图已交给主模型 %s(交付 %dx%d,与原文件同尺寸)"
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
        _hd_approved = False
        if os.environ.get("WCHAT_TRACE") == "1":
            sys.stderr.write("TRACE hd r=%r s=%r m=%.30r\n" % (_r, _s, _m)); sys.stderr.flush()
        if _r:
            if _auto:
                # 屏幕上只给**拒绝理由**,不再回显命令正文:
                # 用户明确说过两个问题 —— ①内容被摊开(画面乱) ②看不到理由。
                # 原来这行是 `⛔ 硬墙拒绝(H1):<整条命令>`,把 _m(理由) 丢了;
                # 现在调过来,并按非正文统一灰化(与思考同色)。
                sys.stdout.write(" %s⛔ 硬墙拒绝(%s):%s%s\n"
                                 % (GREY, _r, dim(_m or "未给出理由", 0), ZERO))
                _audit.record(os.environ.get("AUTO_AUDIT_LOG", ""), "shell",
                              rule=_r, verdict="auto_deny", cmd=cmd)
                return ("[硬墙 %s] %s" % (_r, _m))
            # 非 auto:master 保留人的最终裁量权——升级为严格确认(逐字 yes),
            # 而不是代码层直接拒(否则与 master 行为不一致)。
            # ⚠ 这一支**必须给命令正文**:人要靠它决定敢不敢逐字敲 yes。所谓"拒绝时
            # 不显示内容"只适用于 auto 那种"程序直接拒、人不需要做决定"的形状。
            sys.stdout.write("\033[2K \033[1;31m⛔ 高危命令(规则 %s),任何模式都不自动执行\033[0m\n" % _r)
            sys.stdout.write(" %s   命令:%s%s\n" % (GREY, ellipsis(cmd, None), ZERO))
            if _m:
                sys.stdout.write(" %s   原因:%s%s\n" % (GREY, dim(_m, 0), ZERO))
            sys.stdout.write(" \033[1;31m确需执行?必须输入 yes(n/回车/Ctrl+C = 拒绝): \033[0m")
            sys.stdout.flush()
            try:
                _ans = (sys.stdin.readline() or "").strip()
            except KeyboardInterrupt:
                _ans = ""
            except Exception:
                _ans = ""
            sys.stdout.write("\n"); sys.stdout.flush()
            if _ans != "yes":
                _audit.record(os.environ.get("AUTO_AUDIT_LOG", ""), "shell",
                              rule=_r, verdict="human_no", cmd=cmd)
                return ("用户拒绝执行高危命令(规则 %s): %s\n请换方案,或把命令告知用户请他手动执行。"
                        % (_r, cmd))
            # 人已逐字 yes → 直接执行。**绝不能再走审批器**:
            # 下面若残留一个 N 判定,会紧接着再弹一次"是否执行?"把刚批的命令又问一遍
            # (实测:每次都变成"硬墙 yes" + "人工确认 y" 两道)。master 用 hd_approved=1 挡住。
            _hd_approved = True
            _audit.record(os.environ.get("AUTO_AUDIT_LOG", ""), "shell",
                          rule=_r, verdict="human_yes", cmd=cmd)
            # 人工批过的命令同样要把外部路径落账,否则随后对同一目录的读还要再问
            _note_path_grants(cmd, "rw")
        if _auto:
            _av, _ars = _seam()._auth_think(cmd)
            if _av != "Y":
                _v, _rs = _seam()._auto_judge(cmd, reason=(a.get("reason", "") if isinstance(a, dict) else ""))
                if _v == "Y":
                    _seam()._bump_auto()
                    # 一律"理由在前、命令不回显":这条是屏幕提示,命令正文对用户没信息量
                    # (他刚看过横幅那一行),而**判据**才是他要核对的。
                    sys.stdout.write(" %sauto 终审放行:%s%s\n"
                                     % (GREY, dim(_rs or "无理由", 0), ZERO))
                    _audit.record(os.environ.get("AUTO_AUDIT_LOG", ""), "final",
                                  rule="final", verdict="auto_allow", cmd=cmd)
                elif _v == "N":
                    sys.stdout.write(" %sauto 终审拒绝:%s%s\n"
                                     % (GREY, dim(_rs or "未给出理由", 0), ZERO))
                    _audit.record(os.environ.get("AUTO_AUDIT_LOG", ""), "final",
                                  rule="final", verdict="auto_deny", reason=_rs or "", cmd=cmd)
                    return "auto 终审拒绝(%s): %s" % (_rs or "no-reason", cmd)
                else:
                    # "无判定"必须自己解释清楚,否则用户只看到"被拒"却不知为何:
                    # 这一支是**终审器没回可用判定**(网络失败/输出不含 ALLOW|DENY),
                    # 安全上只能 fail-closed,但要把"为什么"说出来,并提示怎么继续。
                    sys.stdout.write(" %sauto 终审不可用(终审器没回可用判定,按安全兜底拒绝):%s%s\n"
                                     % (GREY, dim(_rs or "无模型输出", 0), ZERO))
                    _audit.record(os.environ.get("AUTO_AUDIT_LOG", ""), "final",
                                  rule="final", verdict="auto_deny", reason="judge-unavailable", cmd=cmd)
                    return "auto 终审不可用(无判定),已拒(安全兜底): %s" % cmd
            else:
                _seam()._bump_auto()
        if not _auto and not _hd_approved:
            # 本会话已批过同一条命令 → 不再问、也不再花一次审批请求。
            # (硬墙走不到这里:_hd_approved 为真时上面已跳开;硬墙永远每次都要人 yes)
            _ck = _cmd_key(cmd)
            if _ck and _ck in _APPROVED_CMDS:
                sys.stdout.write("%s↺ 本会话已批准过同一命令,直接执行%s\n" % (GREY, ZERO))
                sys.stdout.flush()
                _audit.record(os.environ.get("AUTO_AUDIT_LOG", ""), "shell",
                              decision="allow", reason="session-approved", cmd=cmd)
                _av2 = "Y"
            else:
                _av2, _ars2 = _seam()._auth_think(cmd)
            if _av2 != "Y":
                if os.environ.get("AUTO_MODE") == "read":
                    sys.stdout.write("该命令需人工确认: %s [y/N] " % cmd); sys.stdout.flush()
                    try:
                        _ans = (sys.stdin.readline() or "").strip().lower()
                    except KeyboardInterrupt:
                        _ans = ""
                    except Exception:
                        _ans = ""
                    if _ans not in ("y", "yes"):
                        _audit.record(os.environ.get("AUTO_AUDIT_LOG", ""), "shell",
                                      decision="deny", reason="human-n", cmd=cmd)
                        return "用户未批准(转人工): %s" % cmd
                    _APPROVED_CMDS.add(_ck)
                    _note_path_grants(cmd, "rw")
                elif not _seam()._shell_approved(cmd, strict=False):
                    _audit.record(os.environ.get("AUTO_AUDIT_LOG", ""), "shell",
                                  decision="deny", reason="no-approval", cmd=cmd)
                    return ("用户未批准执行命令: %s\n(非 auto 档需人工确认;"
                            "当前无交互 tty 或未确认,已拒)" % cmd)
                else:
                    # 人批了 → 记账:同一条命令本会话不再问;它碰到的外部路径也记账
                    _APPROVED_CMDS.add(_ck)
                    _note_path_grants(cmd, "rw")
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
            _status("后台启动 PID %s · 日志 %s" % (_p.pid, _log))
            return _msg
        from wchat_py.termpty import pty_run
        try:
            _timeout = float(os.environ.get("CMD_TIMEOUT", "120"))
        except ValueError:
            _timeout = 120.0
        _run_env = dict(os.environ,
                        GIT_PAGER="cat", PAGER="cat",
                        GIT_EDITOR="true", GIT_SEQUENCE_EDITOR="true",
                        GIT_TERMINAL_PROMPT="0")
        _disp, _full, _rc, _to, _intr = pty_run(
            ["bash", "-c", cmd], timeout=_timeout, win=int(os.environ.get("WIN", "10")),
            env=_run_env)
        # 屏幕只给**一行**:首行 + 行数脚注。用户报"shell 输出全量铺屏,画面杂乱无章" ——
        # 命令输出动辄几十上百行,把正文挤到看不见。完整内容仍然**原样进上下文**
        # (下面 _trunc_for_context 之后 return),所以模型的能力一点没减;减的只是屏幕噪音。
        # 灰色 + 压成一行,与思考内容同色;行数脚注跟在同一行,避免又多一行。
        from wchat_py.render import dim as _dim
        _n = len(_full.splitlines())
        if _disp:
            _tail = "共 %d 行" % _n
            if _n > 1:
                _tail += ",仅显示首行"
            if _to:
                _tail += ",超过 %ss 已终止" % os.environ.get("CMD_TIMEOUT", "120")
            elif _intr:
                _tail += ",已中断"
            sys.stdout.write("\033[2K\033[2m│\033[0m %s \033[2m…(%s)%s\r\n"
                             % (_dim(_disp[0]), _tail, ZERO))
        else:
            # 无输出时**必须**留一行汇总:"静默成功"是最难排查的失败形状(0 行 + rc=0)。
            if _to:
                sys.stdout.write("%s└─ 超过单命令上限 %ss,已终止 · 共 0 行%s\n"
                                 % (GREY, os.environ.get("CMD_TIMEOUT", "120"), _ZERO))
            elif _intr:
                sys.stdout.write("%s└─ 已中断 · 共 0 行%s\n" % (GREY, _ZERO))
            else:
                sys.stdout.write("%s└─ 完成 · 共 0 行%s\n" % (GREY, _ZERO))
        if _to:
            _full += "\n(命令超过单条上限 %ss,已终止 · rc=%s)" % (_timeout, _rc)
        sys.stdout.flush()
        # 进上下文前必须截断(master run_with_window → truncate_output)。
        # 两道闸:① 单行列宽(2000)② 行数(头尾各留)。
        # 少了第①道,`cat` 一个 minified 文件就等于把几十万字符塞进上下文,
        # 而且之后每次请求都重发一遍 —— 实测这一条就是 token 飞涨的主因。
        return _trunc_for_context(_full)
    return "错误:工具 %s 尚未迁移到 Python 入口(1d 进行中)" % name