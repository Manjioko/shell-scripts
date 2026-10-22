#!/usr/bin/env python3
"""会话状态对象 + `-u`(print_usage) 渲染（主循环/状态所有权起步）。

`Session` 收拢 print_usage 依赖的字段（消息计数、token/缓存/图片计数、上下文窗口/阈值、
auto 计数、图片观测/标定）。`usage_lines(s)` 复刻 bash `print_usage` 的每行文案（不含 `│ `
前缀——由调用方用 inp_msg 加）。字段来源（env/config/文件）后续接；此处先保证**渲染逐字对齐**。
"""
import json
from dataclasses import dataclass
import os


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