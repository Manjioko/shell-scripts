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
    ctx_overflow_gate: int = 80
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
    max_tool_rounds: int = 25
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
            ctx_overflow_gate=i("CTX_OVERFLOW_GATE", 80),
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
            max_tool_rounds=i("MAX_TOOL_ROUNDS", 25),
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


def save_history(history_dir, messages_json, name, title=""):
    """把当前 messages 存成历史文档（原子写，经 msgstore.save_doc）。返回落盘路径。"""
    from wchat_py import msgstore
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


def orchestrate(session, content, call_llm, run_tool, max_rounds=5):
    """带工具续发的一轮编排（llm/tool 可注入，便于离线自测配对）。

    call_llm(msgs)->(text, tool_calls, err)；tool_calls 为 [{id,name,arguments},...]；
    run_tool(name, arguments)->str。有 tool_calls → 追加 assistant(tool_calls) + 每个 tool 结果
    → 续发；无则落 assistant(text) 结束。回写 Session 并返回最终 text（err 时返回 err）。
    """
    msgs = messages_list(session.messages_json)
    if session.input_images:
        _parts = [{"type": "text", "text": content}]
        for _p in session.input_images:
            _parts.append({"type": "image_url", "image_url": {"url": _data_url(_p)}})
        msgs.append({"role": "user", "content": _parts})
        session.input_images = []
    else:
        msgs.append({"role": "user", "content": content})
    text = ""
    for _ in range(max_rounds):
        text, tcs, err = call_llm(msgs)
        if err:
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
                out = run_tool(tc.get("name") or "", tc.get("arguments") or "{}")
                if isinstance(out, str) and out.startswith("IMGJSON:"):
                    try:
                        _d = json.loads(out[len("IMGJSON:"):])
                    except ValueError:
                        _d = None
                    if _d:
                        msgs.append({"role": "tool", "content": _d.get("text", "图片已读取"),
                                     "tool_call_id": tc.get("id") or ("call_%d" % i)})
                        msgs.append({"role": "user", "content": [
                            {"type": "text", "text": "(工具 read_image 返回的图片)"},
                            {"type": "image_url", "image_url": {"url": _d.get("url", "")}}]})
                        continue
                msgs.append({"role": "tool", "content": out,
                             "tool_call_id": tc.get("id") or ("call_%d" % i)})
            continue
        msgs.append({"role": "assistant", "content": text or ""})
        break
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