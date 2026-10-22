#!/usr/bin/env python3
"""session.Session.from_env 自检：env 字段正确读入 + usage_lines 反映真实值。末行 RESULT ...。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wchat_py.session import (Session, usage_lines, save_history, messages_list, compact_status,
                               model_status_lines, set_pro, set_flash, thinking_on, set_auto,
                               auto_off, add_image, orchestrate_turn, orchestrate, seed_system, enter_auto,
                               record_allow, record_deny, make_checkpoint)  # noqa: E402
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

# orchestrate: 工具续发闭环 + 配对
oc = Session()
_st = {"n": 0}


def _cl(msgs):
    _st["n"] += 1
    if _st["n"] == 1:
        return "", [{"id": "c1", "name": "list_dir", "arguments": "{}"}], None
    return "final-answer", [], None


_t = orchestrate(oc, "do it", _cl, lambda n, a: "OUT")
_ml = messages_list(oc.messages_json)
ck("orchestrate 最终文本", _t == "final-answer", _t)
ck("orchestrate 角色序列", [m["role"] for m in _ml] == ["user", "assistant", "tool", "assistant"], str([m["role"] for m in _ml]))
ck("tool 配对 id", _ml[2]["tool_call_id"] == _ml[1]["tool_calls"][0]["id"] == "c1")
ck("tool 结果内容", _ml[2]["content"] == "OUT")
ck("msg_count=4", oc.msg_count == 4)

# seed_system
sd = Session()
t = seed_system(sd, "BASE", "SKILLS")
_sm = messages_list(sd.messages_json)
ck("seed system 单条", len(_sm) == 1 and _sm[0]["role"] == "system", str(_sm))
ck("seed 合并 base+extra", _sm[0]["content"] == "BASE\n\nSKILLS", repr(_sm[0]["content"]))
ck("seed return 文本", t == "BASE\n\nSKILLS")
ck("seed msg_count=1", sd.msg_count == 1)

# enter_auto
ea = Session.from_env({"AUTO_TIERS": "sandbox full"})
ea.auto_approvals = 5
ck("enter_auto full", enter_auto(ea, "full") == "full" and ea.auto_mode == "full" and ea.auto_approvals == 0,
   str((ea.auto_mode, ea.auto_approvals)))
ck("enter_auto 非法档 None", enter_auto(ea, "bogus") is None)

# 刹车计数
bs = Session.from_env({"AUTO_DENY_STREAK": "2"})
ck("record_allow 计数+清零", record_allow(bs) == 1 and bs.deny_streak == 0)
bs.deny_streak = 1
ck("record_deny 达阈值触发刹车", record_deny(bs) is True and bs.deny_streak == 2)
ck("record_allow 清刹车", record_allow(bs) == 2 and bs.deny_streak == 0)
ck("阈值从 env", bs.deny_streak_max == 2)

# 名单装配 + 回滚点
al = Session.from_env({"AUTO_TIERS": "full", "WCHAT_ALLOW_RO": "a b c", "WCHAT_ALLOW_RW": "x y"})
enter_auto(al, "full")
ck("名单 → ro/rw 计数", (al.ro_count, al.rw_count) == (3, 2), str((al.ro_count, al.rw_count)))
_ck_repo = make_checkpoint(al, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ck("git 仓回滚点 git@", _ck_repo.startswith("git@"), _ck_repo)
ck("非 git 回滚点空", make_checkpoint(al, tempfile.mkdtemp()) == "")

print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)