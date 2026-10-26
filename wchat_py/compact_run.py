#!/usr/bin/env python3
"""上下文压缩的“网络 + 布局”编排(纯布局在 compaction.py)。

对应 master compact_messages: 摘要走 flash 非流式; 失败也走 apply(硬截断,保旧摘要)。
"""
import json
import os

SUMMARY_PREFIX = "【历史对话摘要】"
COMPACT_SYS = (
    "你是对话历史压缩助手。任务:把用户提供的旧对话记录压缩成一段精炼的摘要,"
    "替代原始记录用于后续继续对话。要求:1. 保留用户的整体目标与意图、关键决策与结论、"
    "已执行的命令及其重要结果、出现的错误与最终修复方式、涉及的重要文件路径、"
    "用户的个人偏好与约束;2. 丢弃客套话、重复内容与冗长的命令输出;"
    "3. 用中文输出,不超过 500 字,只输出摘要正文,不要任何前缀或解释。"
)


def _content_text(m):
    c = m.get("content") if isinstance(m, dict) else None
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(p.get("text", "") for p in c
                         if isinstance(p, dict) and p.get("type") == "text")
    return ""


def summarize(text):
    from wchat_py.cli import _nonstream_msg
    try:
        msg = _nonstream_msg([{"role": "system", "content": COMPACT_SYS},
                              {"role": "user", "content": text}],
                             os.environ.get("FLASH_MODEL", ""))
        return getattr(msg, "content", "") or ""
    except Exception:
        return ""


def compact(sess, auto=False):
    """压缩 sess.messages_json。返回 (changed, total, pin, keep)。"""
    from wchat_py import compaction
    from wchat_py import msg as msgmod
    try:
        msgs = json.loads(sess.messages_json or "[]")
    except ValueError:
        return False, 0, 0, 0
    total = len(msgs)
    try:
        keep = int(os.environ.get("KEEP_MSGS") or 16)
    except ValueError:
        keep = 16
    if total <= keep:
        if not auto:
            print("消息数量未达到压缩门槛(当前 %d 条)" % total, flush=True)
        return False, total, 0, keep
    cut = total - keep
    while cut > 1 and isinstance(msgs[cut], dict) and msgs[cut].get("role") == "tool":
        cut -= 1
    if cut <= 1:
        if not auto:
            print("工具链过长,跳过本次压缩", flush=True)
        return False, total, 0, keep
    pin = 0
    for m in msgs[1:]:
        if _content_text(m).startswith(SUMMARY_PREFIX):
            pin += 1
        else:
            break
    try:
        pinmax = int(os.environ.get("SUMMARY_PIN_MAX") or 4)
    except ValueError:
        pinmax = 4
    if pinmax > 0 and pin >= pinmax:
        print("📦 旧摘要已攒到 %d 条(上限 %d),本次合并重写一次——"
              "这种有损合并每 %d 次压缩才发生一次" % (pin, pinmax, pinmax), flush=True)
        pin = 0
    if cut <= 1 + pin:
        if not auto:
            print("📦 除 %d 条已锁定摘要外没有新内容可压缩,跳过本次" % pin, flush=True)
        return False, total, pin, keep
    start = 1 + pin
    try:
        text = msgmod.render_for_summary(msgs, start, cut)
    except Exception:
        text = ""
    if auto:
        print("📦 上下文 %s tokens 已超阈值 %s,正在自动压缩历史…"
              % (getattr(sess, "last_prompt_tokens", 0) or
                 os.environ.get("WCHAT_LAST_PROMPT_TOKENS", "?"),
                 getattr(sess, "token_threshold", 0) or
                 os.environ.get("TOKEN_THRESHOLD", "?")), flush=True)
    else:
        print("📦 正在压缩历史…(当前 %s tokens,阈值 %s)"
              % (getattr(sess, "last_prompt_tokens", 0) or
                 os.environ.get("WCHAT_LAST_PROMPT_TOKENS", "0") or "0",
                 getattr(sess, "token_threshold", 0) or
                 os.environ.get("TOKEN_THRESHOLD", "0")), flush=True)
    summary = summarize(text)
    new_json = compaction.apply(json.dumps(msgs, ensure_ascii=False), pin, summary, cut,
                                SUMMARY_PREFIX)
    sess.messages_json = new_json
    try:
        sess.msg_count = len(json.loads(new_json))
    except ValueError:
        pass
    sess.last_prompt_tokens = 0
    os.environ["WCHAT_LAST_PROMPT_TOKENS"] = "0"
    if summary:
        print("✅ 压缩完成:%d 条消息 → 保留 %d 条旧摘要 + 1 条新摘要 + 最近 %d 条原文"
              % (total, pin, keep), flush=True)
    else:
        print("⚠️  压缩失败(网络/接口错误),已回退:保留 system + %d 条旧摘要 + 最近 %d 条原文"
              % (pin, keep), flush=True)
    return True, total, pin, keep