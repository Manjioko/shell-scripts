#!/usr/bin/env python3
"""session.Session.from_env 自检：env 字段正确读入 + usage_lines 反映真实值。末行 RESULT ...。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wchat_py.session import (Session, usage_lines, save_history, messages_list, compact_status,
                               model_status_lines, set_pro, set_flash, thinking_on, set_auto,
                               auto_off, add_image, orchestrate_turn)  # noqa: E402
from wchat_py import msgstore  # noqa: E402

p = f = 0


def ck(name, ok, detail=""):
    global p, f
    if ok:
        p += 1
        print("  ok   %s" % name)
    else:
        f += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


env = {
    "CTX_WINDOW": "64000", "TOKEN_THRESHOLD": "50000", "CTX_OVERFLOW_GATE": "90",
    "IMG_PRUNE_EVENTS": "2", "IMG_TOKENS_AUTOCAL": "0", "WCHAT_MODEL": "stub-pro",
    "WCHAT_LAST_PROMPT_TOKENS": "1234", "WCHAT_LAST_CACHED_TOKENS": "1000",
    "WCHAT_LAST_IMAGE_TOKENS": "12", "WCHAT_MSG_COUNT": "7",
}
s = Session.from_env(env)
ck("msg_count", s.msg_count == 7, str(s.msg_count))
ck("ctx_window", s.ctx_window == 64000)
ck("token_threshold", s.token_threshold == 50000)
ck("ctx_overflow_gate", s.ctx_overflow_gate == 90)
ck("img_prune_events", s.img_prune_events == 2)
ck("model", s.model == "stub-pro", s.model)
ck("last_prompt_tokens", s.last_prompt_tokens == 1234)
ck("autocal", s.autocal == 0)

lines = usage_lines(s)
joined = "\n".join(lines)
ck("usage 反映真实 pct", "已用 2%" in joined, joined)          # 1234*100/50000 = 2
ck("usage 有窗口行", "窗口 64000 tokens" in joined)
ck("usage model=stub-pro", "model=stub-pro" in joined)

# 缺字段容错（空 env → 默认）
s2 = Session.from_env({})
ck("空env默认 ctx_window=0", s2.ctx_window == 0)
ck("空env默认 autocal=1", s2.autocal == 1)
ck("空env model=?", "model=?" in "\n".join(usage_lines(s2)))

# save_history 往返（经 msgstore 原子写）
d = tempfile.mkdtemp()
mj = '[{"role":"user","content":"hi"}]'
path = save_history(d, mj, "s.json", "title-x")
ck("save 落盘", os.path.exists(path), path)
ck("load 回读等值", msgstore.load(path) == messages_list(mj), repr(msgstore.load(path)))
ck("messages_list 容错", messages_list("not json") == [])

# compact_status: cur>=threshold => due
s3 = Session.from_env({"TOKEN_THRESHOLD": "100", "WCHAT_LAST_PROMPT_TOKENS": "200"})
cur, th, due = compact_status(s3)
ck("compact due(cur>=th)", due is True and cur == 200 and th == 100, str((cur, th, due)))
s4 = Session.from_env({"TOKEN_THRESHOLD": "100", "WCHAT_LAST_PROMPT_TOKENS": "50"})
ck("compact 不需压缩", compact_status(s4)[2] is False)
s5 = Session.from_env({"TOKEN_THRESHOLD": "0"})
ck("阈值为0 不due", compact_status(s5)[2] is False)

# model_status_lines: auto 开/关
ma = Session.from_env({"WCHAT_MODEL": "stub-pro", "WCHAT_THINKING": "enabled",
                       "WEB_SEARCH_PROVIDER": "openai", "WEB_SEARCH_TIMEOUT": "9",
                       "TOKEN_THRESHOLD": "50000", "WCHAT_AUTO_MODE": "full",
                       "WCHAT_AUTO_APPROVALS": "3", "AUTO_MAX_APPROVALS": "50",
                       "WCHAT_TOOL_ROUND": "2", "MAX_TOOL_ROUNDS": "25",
                       "WCHAT_AUTO_CK_NOTE": "ck-1", "WCHAT_BACKUP_COUNT": "4",
                       "WCHAT_AUDIT_LOG": "/a.log", "WCHAT_RO_COUNT": "2", "WCHAT_RW_COUNT": "1"})
j = "\n".join(model_status_lines(ma))
ck("md 模型/思考", "模型: stub-pro" in j and "思考: enabled" in j, j)
ck("md 联网/阈值", "联网搜索: openai(超时 9s)" in j and "压缩阈值: 50000 tokens" in j)
ck("md auto 开", "auto: full 档 · 已自动放行 3/50 次 · 本轮工具 2/25 轮" in j, j)
ck("md 回滚点/备份/审计", "回滚点: ck-1 · 备份 4 份 · 审计 /a.log" in j)
ck("md 越界名单", "越界名单: 只读 2 项 / 可写 1 项" in j)
mc = Session.from_env({})
jk = "\n".join(model_status_lines(mc))
ck("md 标题空 => （空）", "标题: （空）" in jk, jk)
ck("md auto 关", "auto: 关闭(权限逐次人工确认)" in jk)

# 切换函数
sw = Session.from_env({"PRO_MODEL": "pro-x", "FLASH_MODEL": "flash-y", "AUTO_TIERS": "sandbox full"})
ck("set_pro", set_pro(sw) == "pro-x" and sw.model == "pro-x")
ck("set_flash+thinking off", set_flash(sw) == "flash-y" and sw.thinking == "disabled")
ck("thinking_on", thinking_on(sw) == "enabled" and sw.thinking == "enabled")
ck("set_auto 合法", set_auto(sw, "full") == "full" and sw.auto_mode == "full")
ck("set_auto 非法", set_auto(sw, "bogus") is None)
ck("auto_off 返回旧值", auto_off(sw) == "full" and sw.auto_mode == "")
ck("add_image 计数", add_image(sw, "/tmp/a.png") == 1 and sw.input_images == ["/tmp/a.png"])

# orchestrate_turn: 多轮累积(user/assistant 交替, msg_count 递增)
ot = Session()


def _echo(msgs):
    return "echo:" + msgs[-1]["content"]


ck("turn1 返回", orchestrate_turn(ot, "hi", _echo) == "echo:hi")
ck("turn1 msg_count=2", ot.msg_count == 2, str(ot.msg_count))
orchestrate_turn(ot, "again", _echo)
ml = messages_list(ot.messages_json)
ck("turn2 msg_count=4", ot.msg_count == 4)
ck("角色交替", [m["role"] for m in ml] == ["user", "assistant", "user", "assistant"], str(ml))
ck("上下文累积", ml[2]["content"] == "again" and ml[1]["content"] == "echo:hi")

print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)