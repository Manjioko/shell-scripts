#!/usr/bin/env python3
"""会话状态对象 + `-u`(print_usage) 渲染（主循环/状态所有权起步）。

`Session` 收拢 print_usage 依赖的字段（消息计数、token/缓存/图片计数、上下文窗口/阈值、
auto 计数、图片观测/标定）。`usage_lines(s)` 复刻 bash `print_usage` 的每行文案（不含 `│ `
前缀——由调用方用 inp_msg 加）。字段来源（env/config/文件）后续接；此处先保证**渲染逐字对齐**。
"""
import json
from dataclasses import dataclass, field
import os
import sys


@dataclass
class Session:
    messages_json: str = "[]"
    msg_count: int = 0
    last_prompt_tokens: int = 0
    last_cached_tokens: int = 0
    last_image_tokens: int = 0
    ctx_window: int = 0
    token_threshold: int = 0
    ctx_overflow_gate: int = 0
    img_prune_events: int = 0
    img_obs_unexpected: int = 0
    img_obs_tokens: int = 0
    img_ref_count: int = 0
    model: str = ""
    autocal: int = 1
    calib_count: int = 0
    thinking: str = "disabled"
    web_provider: str = "none"
    web_timeout: int = 0
    chat_title: str = ""
    auto_mode: str = ""
    auto_approvals: int = 0
    auto_max: int = 200
    tool_round: int = 0
    max_tool_rounds: int = 250
    auto_ck_note: str = ""
    backup_count: int = 0
    audit_log: str = ""
    ro_count: int = 0
    rw_count: int = 0
    pro_model: str = ""
    flash_model: str = ""
    auto_tiers: list = field(default_factory=lambda: ["sandbox", "full"])
    input_images: list = field(default_factory=list)
    deny_streak: int = 0
    deny_streak_max: int = 3
    allow_ro: list = field(default_factory=list)
    allow_rw: list = field(default_factory=list)

    @classmethod
    def from_env(cls, env=None):
        """从环境构造状态（过渡期：bash/编排层把这些经 env 传出；全 Python 后改为进程内持有）。"""
        e = env if env is not None else os.environ

        def i(k, d=0):
            try:
                return int(e.get(k, d))
            except (TypeError, ValueError):
                return d

        return cls(
            messages_json=e.get("WCHAT_MESSAGES_JSON", "[]"),
            msg_count=i("WCHAT_MSG_COUNT", 0),
            last_prompt_tokens=i("WCHAT_LAST_PROMPT_TOKENS", 0),
            last_cached_tokens=i("WCHAT_LAST_CACHED_TOKENS", 0),
            last_image_tokens=i("WCHAT_LAST_IMAGE_TOKENS", 0),
            ctx_window=i("CTX_WINDOW", 0),
            token_threshold=i("TOKEN_THRESHOLD", 0),
            ctx_overflow_gate=i("CTX_OVERFLOW_GATE", 0),
            img_prune_events=i("IMG_PRUNE_EVENTS", 0),
            img_obs_unexpected=i("WCHAT_IMG_OBS_UNEXPECTED", 0),
            img_obs_tokens=i("WCHAT_IMG_OBS_TOKENS", 0),
            img_ref_count=i("WCHAT_IMG_REF_COUNT", 0),
            model=e.get("WCHAT_MODEL", ""),
            autocal=i("IMG_TOKENS_AUTOCAL", 1),
            calib_count=i("WCHAT_CALIB_COUNT", 0),
            thinking=e.get("WCHAT_THINKING", "disabled"),
            web_provider=e.get("WEB_SEARCH_PROVIDER", "none"),
            web_timeout=i("WEB_SEARCH_TIMEOUT", 0),
            chat_title=e.get("WCHAT_CHAT_TITLE", ""),
            auto_mode=e.get("WCHAT_AUTO_MODE", ""),
            auto_approvals=i("WCHAT_AUTO_APPROVALS", 0),
            auto_max=i("AUTO_MAX_APPROVALS", 200),
            tool_round=i("WCHAT_TOOL_ROUND", 0),
            max_tool_rounds=i("MAX_TOOL_ROUNDS", 250),
            auto_ck_note=e.get("WCHAT_AUTO_CK_NOTE", ""),
            backup_count=i("WCHAT_BACKUP_COUNT", 0),
            audit_log=e.get("WCHAT_AUDIT_LOG", ""),
            ro_count=i("WCHAT_RO_COUNT", 0),
            rw_count=i("WCHAT_RW_COUNT", 0),
            pro_model=e.get("PRO_MODEL", ""),
            flash_model=e.get("FLASH_MODEL", ""),
            auto_tiers=(e.get("AUTO_TIERS", "").split() or ["sandbox", "full"]),
            deny_streak_max=i("AUTO_DENY_STREAK", 3),
            allow_ro=e.get("WCHAT_ALLOW_RO", "").split(),
            allow_rw=e.get("WCHAT_ALLOW_RW", "").split(),
        )


def history_list(history_dir):
    """列出已存历史文件（`~/.ct/history/*.json` 的 basename，排序）。

    状态所有权起步：把 bash `-l` 里 `ls $HISTORY_DIR` 的部分搬成纯函数，便于单测/复用。
    """
    if not history_dir:
        return []
    try:
        names = os.listdir(history_dir)
    except OSError:
        return []
    return sorted(n for n in names if n.endswith(".json"))


def messages_list(messages_json):
    try:
        v = json.loads(messages_json) if messages_json else []
        return v if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


def _sanitize_name(s):
    """标题 → 合法文件名片段(复刻 master save_history 的清洗顺序)。

    末尾 `.json` 必须剥掉,这是用户实测的 bug 来源:加载历史时
    `chat_title = basename(path)`,本身就带着 `.json`;保存时再拼一个就成
    `xxx.json.json`(反复加载→保存会一路叠下去)。
    """
    import re
    name = (s or "").strip()
    name = "".join("_" if c.isspace() else c for c in name)
    name = re.sub(r'[\\/:*?"<>|]', "", name)
    while name.lower().endswith(".json"):
        name = name[:-5]
    return name or "chat"


def history_name(title):
    """标题 → 历史文件名(带且仅带一个 .json)。"""
    return _sanitize_name(title) + ".json"


def save_history(history_dir, messages_json, name, title=""):
    """把当前 messages 存成历史文档（原子写，经 msgstore.save_doc）。返回落盘路径。

    name/title 任给其一即可:name(WCHAT_HISTORY_NAME)优先,否则用 title;
    无论哪来源都过 `_sanitize_name`,保证落盘名只有一个 `.json` 后缀。
    """
    from wchat_py import msgstore
    name = history_name(name or title)
    path = os.path.join(history_dir, name)
    msgstore.save_doc(path, {"messages": messages_list(messages_json), "title": title})
    return path


def compact_status(s):
    """返回 (cur, threshold, due)。cur 用 last_prompt_tokens，无则按字节粗算(同 -u)。"""
    est = len((s.messages_json or "").encode("utf-8")) * 2 // 5
    cur = s.last_prompt_tokens if s.last_prompt_tokens > 0 else est
    from wchat_py import compaction
    due = bool(s.token_threshold > 0 and compaction.due(cur, s.token_threshold))
    return cur, s.token_threshold, due


def model_status_lines(s):
    """复刻 bash `-md` 的模型状态行（不含 `│ ` 前缀）。"""
    out = [
        "模型: %s" % s.model,
        "思考: %s" % s.thinking,
        "联网搜索: %s(超时 %ss)" % (s.web_provider or "none", s.web_timeout),
        "压缩阈值: %d tokens" % s.token_threshold,
        "标题: %s" % (s.chat_title or "（空）"),
    ]
    if s.auto_mode:
        out.append("auto: %s 档 · 已自动放行 %d/%d 次 · 本轮工具 %d/%d 轮"
                   % (s.auto_mode, s.auto_approvals, s.auto_max, s.tool_round, s.max_tool_rounds))
        out.append("  回滚点: %s · 备份 %d 份 · 审计 %s"
                   % (s.auto_ck_note or "无", s.backup_count, s.audit_log))
        out.append("  越界名单: 只读 %d 项 / 可写 %d 项 · 输入 -noauto 关闭" % (s.ro_count, s.rw_count))
    else:
        out.append("auto: 关闭(权限逐次人工确认)")
    return out


def set_pro(s):
    s.model = s.pro_model or s.model
    return s.model


def set_flash(s):
    s.model = s.flash_model or s.model
    s.thinking = "disabled"
    return s.model


def thinking_on(s):
    s.thinking = "enabled"
    return s.thinking


def set_auto(s, tier):
    if tier not in s.auto_tiers:
        return None
    s.auto_mode = tier
    return tier


def auto_off(s):
    was = s.auto_mode
    s.auto_mode = ""
    return was


def add_image(s, path):
    s.input_images.append(path)
    return len(s.input_images)


class Aborted(Exception):
    """用户 Ctrl+C 打断本轮输出(与 master 的 ask_ai 返回 130 同义)。

    与普通异常的区别在语义而不在类型:**本轮 assistant 输出整体丢弃**,但用户那句
    已经写进 messages(用户能看到自己说过什么,便于补充后重发)。
    """


def orchestrate_turn(session, content, llm):
    """一轮对话的编排骨架（不含流式/工具；llm(messages)->text 可注入）。

    messages 归属 Session：追加 user → 调 llm → 追加 assistant → 回写 messages_json/msg_count。
    返回 assistant 文本。多轮调用即累积上下文——这是 ask_ai 编排的最小可测内核。
    """
    msgs = messages_list(session.messages_json)
    msgs.append({"role": "user", "content": content})
    text = llm(msgs)
    msgs.append({"role": "assistant", "content": text})
    session.messages_json = json.dumps(msgs, ensure_ascii=False)
    session.msg_count = len(msgs)
    return text


def _data_url(path):
    import base64
    import mimetypes
    mt = mimetypes.guess_type(path)[0] or "application/octet-stream"
    try:
        with open(path, "rb") as f:
            return "data:%s;base64,%s" % (mt, base64.b64encode(f.read()).decode())
    except OSError:
        return path


def orchestrate(session, content, call_llm, run_tool, max_rounds=None):
    """带工具续发的一轮编排（llm/tool 可注入，便于离线自测配对）。

    max_rounds 缺省读 MAX_TOOL_ROUNDS(缺省 250,与 master 一致)；跑满说明模型陷入
    工具循环，会打印上限提示后结束本轮（而不是静默停下等人补“继续”）。
    """
    if max_rounds is None:
        try:
            max_rounds = int(os.environ.get("MAX_TOOL_ROUNDS") or 250)
        except ValueError:
            max_rounds = 250
    # 与 master 一致:轮次计数按"每次用户输入"复位(7894 行 tool_round=0)
    session.tool_round = 0
    session._retry_done = False   # 每轮最多自愈(压缩重发)一次
    _tried = -1          # 已经试过压缩的用量值(同一个值不重复试,见下面的注释)
    msgs = messages_list(session.messages_json)
    if session.input_images:
        from wchat_py import img as _img
        from wchat_py import cli as _cli
        try:
            _maxside = int(os.environ.get("IMG_INLINE_MAX_SIDE") or 2048)
        except ValueError:
            _maxside = 2048
        _markers = []
        _shrunk = 0
        _n = len(session.input_images)
        for _p in session.input_images:
            _url, _mime, _orig, _actual = _img.load_data_url(_p, _maxside)
            if _orig and _actual and _actual < _orig:
                sys.stdout.write(" ⤷ %s 已缩到 %dpx(精度随之下调)\n"
                                 % (os.path.basename(_p), _actual))
                _shrunk += 1
            _ref = _cli._register_image_url(_p, _url, _actual)
            _markers.append("[[IMGREF:%s]]" % _ref)
        sys.stdout.write(" 🖼 %d/%d 张原图已随本轮消息交给主模型"
                         "(其中 %d 张已缩过,逐张档位见上面各行的说明)\n"
                         % (_n, _n, _shrunk))
        sys.stdout.flush()
        msgs.append({"role": "user",
                     "content": content + ("\n" if content else "") + "\n".join(_markers)})
        session.input_images = []
    else:
        msgs.append({"role": "user", "content": content})
    text = ""
    hit_limit = True
    # 中断回滚点:用户消息已写入(与 master 一致,中断后用户那句保留),本轮之后
    # 追加的 assistant/tool 全部丢弃——否则半截正文配上半截 tool_calls 会污染下一轮
    # (孤儿 tool_calls 让网关 400)。
    _n_user = len(msgs)
    for _ in range(max_rounds):
        # 主动压缩:最近一次请求带回的 usage 超阈值 → 本次请求前先摘要重写历史。
        #
        # ⚠ 必须读**最新**用量,不能只读 session.last_prompt_tokens:
        # 那个字段只在"一轮结束"时由 _sync_runtime 回填,一轮内部的工具循环里
        # 它一直是本轮开始时的旧值 —— 实测 6 次续发、每次 usage 都报 999999
        # (阈值 1000),压缩一次都不触发。master 在**每次续发前**都查一次
        # (wchat.sh tool_run_and_parse),这里对齐:env 里的值由 _consume_stream
        # 每次收到 usage 事件就写,是新鲜的。
        # `_tried` 记住"这个用量已经试过压缩了":压不动(消息还不够多/工具链过长)
        # 时不要每轮重试,等下一个 usage 事件带来新数值再说。
        try:
            from wchat_py import compaction as _cmp, compact_run as _cr
            try:
                _live = int(os.environ.get("WCHAT_LAST_PROMPT_TOKENS") or 0)
            except (TypeError, ValueError):
                _live = 0
            _cur = max(_live, int(session.last_prompt_tokens or 0))
            if _cur > 0 and _cur != _tried:
                session.last_prompt_tokens = _cur     # 让 compact 的提示语也看到真值
                if _cmp.due(_cur, session.token_threshold):
                    _tried = _cur
                    _cr.compact(session, auto=True)
                    msgs = messages_list(session.messages_json)
                    _n_user = len(msgs)
        except Exception:
            pass
        try:
            text, tcs, err = call_llm(msgs)
        except Aborted:
            del msgs[_n_user:]
            session.messages_json = json.dumps(msgs, ensure_ascii=False)
            session.msg_count = len(msgs)
            raise
        if err:
            # 请求失败自愈(对齐 master retry_after_compact):判定是上下文越界就
            # 压缩后重发一次。没有它,越界 = 直接失败,用户手动重发又付一次全量输入。
            try:
                from wchat_py import cli as _cli
                if _cli._retry_after_compact(session, msgs, err):
                    msgs = messages_list(session.messages_json)
                    _n_user = len(msgs)
                    continue
            except Exception:
                pass
            session.messages_json = json.dumps(msgs, ensure_ascii=False)
            session.msg_count = len(msgs)
            return err
        if tcs:
            msgs.append({"role": "assistant", "content": text or None,
                         "tool_calls": [{"id": tc.get("id") or ("call_%d" % i), "type": "function",
                                         "function": {"name": tc.get("name") or "",
                                                      "arguments": tc.get("arguments") or "{}"}}
                                        for i, tc in enumerate(tcs)]})
            for i, tc in enumerate(tcs):
                try:
                    session.tool_round = int(session.tool_round or 0) + 1
                except (TypeError, ValueError):
                    session.tool_round = 1
                out = run_tool(tc.get("name") or "", tc.get("arguments") or "{}")
                if isinstance(out, str) and out.startswith("IMGJSON:"):
                    try:
                        _d = json.loads(out[len("IMGJSON:"):])
                    except ValueError:
                        _d = None
                    if _d:
                        _ref = _d.get("ref") or ""
                        _txt = _d.get("text", "图片已读取")
                        if _ref:
                            _txt += "\n[[IMGREF:%s]]" % _ref
                        msgs.append({"role": "tool", "content": _txt,
                                     "tool_call_id": tc.get("id") or ("call_%d" % i)})
                        continue
                msgs.append({"role": "tool", "content": out,
                             "tool_call_id": tc.get("id") or ("call_%d" % i)})
            continue
        hit_limit = False
        msgs.append({"role": "assistant", "content": text or ""})
        break
    if hit_limit:
        print("⚠️ 已达工具轮次上限 %d（MAX_TOOL_ROUNDS），本轮结束；可再发“继续”接着跑" % max_rounds)
    session.messages_json = json.dumps(msgs, ensure_ascii=False)
    session.msg_count = len(msgs)
    return text


def seed_system(s, base="You are a helpful assistant.", extra=""):
    """用 system 消息初始化会话(新会话/刷新注入时用)。extra 可为技能清单文本。"""
    text = base + (("\n\n" + extra) if extra else "")
    _mcp = os.environ.get("MCP_MANIFEST", "")
    if _mcp:
        text += "\n\n" + _mcp
    s.messages_json = json.dumps([{"role": "system", "content": text}], ensure_ascii=False)
    s.msg_count = 1
    return text


def enter_auto(s, tier):
    """进入 auto 档（纯状态部分）：校验档位→置 auto_mode→清会话计数。回滚点/名单/刹车后续接。"""
    if tier not in s.auto_tiers:
        return None
    s.auto_mode = tier
    s.auto_approvals = 0
    s.deny_streak = 0
    s.ro_count = len(s.allow_ro)
    s.rw_count = len(s.allow_rw)
    return tier


def record_allow(s):
    s.auto_approvals += 1
    s.deny_streak = 0
    return s.auto_approvals


def record_deny(s):
    """连续拒绝 +1；达到阈值返回 True(刹车)。任一放行清零由 record_allow 负责。"""
    s.deny_streak += 1
    return s.deny_streak >= s.deny_streak_max


def make_checkpoint(s, work_root=None):
    """回滚点：work_root 是 git 仓库则记 HEAD 短哈希到 auto_ck_note；否则清空。"""
    import subprocess
    wr = work_root or os.environ.get("WORK_ROOT") or os.getcwd()
    try:
        out = subprocess.run(["git", "-C", wr, "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True)
        if out.returncode == 0 and out.stdout.strip():
            s.auto_ck_note = "git@" + out.stdout.strip()
            return s.auto_ck_note
    except Exception:
        pass
    s.auto_ck_note = ""
    return ""


def usage_lines(s):
    est = len((s.messages_json or "").encode("utf-8")) * 2 // 5
    out = ["消息条数: %d" % s.msg_count]
    cur = s.last_prompt_tokens if s.last_prompt_tokens > 0 else est
    pct = ("%d%%" % (cur * 100 // s.token_threshold)) if s.token_threshold > 0 else "?"
    if s.last_prompt_tokens > 0:
        out.append("上下文用量: %d tokens / 压缩点 %d(已用 %s)" % (cur, s.token_threshold, pct))
    else:
        out.append("上下文用量: 约 %d tokens(字节粗算,漏算 tools schema;精确值在下次请求后刷新)/ 压缩点 %d(已用 %s)"
                   % (cur, s.token_threshold, pct))
    if s.ctx_window > 0:
        wpct = "%d%%" % (cur * 100 // s.ctx_window)
        out.append("  窗口 %d tokens(已用 %s)· 越界判据 %s(仅失败时用作粗算兜底)"
                   % (s.ctx_window, wpct, s.ctx_overflow_gate))
    out.append("  图片可见性: 合法改写 %d 次 · 意外改写 %d 次(累计约 %d token 从打折变全价)"
               % (s.img_prune_events, s.img_obs_unexpected, s.img_obs_tokens))
    out.append("  活图: %d 张在上下文(回收发生时屏幕必打 ♻,没打过 ♻ 就没回收过)" % s.img_ref_count)
    if s.last_prompt_tokens > 0:
        out.append("  上次请求: 命中缓存 %d / %d tokens · 图片计费 %d tokens"
                   % (s.last_cached_tokens, s.last_prompt_tokens, s.last_image_tokens))
    else:
        out.append("  上次请求: 本次会话还没有拿到带 usage 的请求(所以上面是粗算)· 图片计费 %d tokens"
                   % s.last_image_tokens)
    out.append("  单价自标定: %d 条样本(model=%s，AUTOCAL=%d)"
               % (s.calib_count, s.model if s.model else "?", s.autocal))
    return out