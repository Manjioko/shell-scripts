#!/usr/bin/env python3
"""session.Session.from_env 自检：env 字段正确读入 + usage_lines 反映真实值。末行 RESULT ...。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wchat_py.session import Session, usage_lines, save_history, messages_list  # noqa: E402
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

print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)