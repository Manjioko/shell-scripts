#!/usr/bin/env python3
"""session.Session.from_env 自检：env 字段正确读入 + usage_lines 反映真实值。末行 RESULT ...。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wchat_py.session import (Session, usage_lines, save_history, messages_list, compact_status,
                               model_status_lines, set_pro, set_flash, thinking_on, set_auto,
                               auto_off, add_image, orchestrate_turn, orchestrate, seed_system, enter_auto,
                               record_allow, record_deny, make_checkpoint, Aborted,
                               PENDING_PREFIX)  # noqa: E402
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
ck("md 思考显示(默认两行)", "思考显示: 两行(默认;-tf=on 展开)" in j, j)
ck("md 不再单列 shell 档位(跟 -tf 走,不做第二个开关)", "shell输出" not in j, j)
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


# ── orchestrate: 每小轮边界注入"用户等待时的补充信息"(M4 边输出边输入)──────────────
# poll_queue 在**每个小轮边界(第2轮起)** drain 一次;多条合并成**一条** user,前缀 PENDING_PREFIX;
# 首个小轮不注入;注入位置必须在上一条 tool 结果之后(不能插在 tool_calls 与 tool 之间)。
oi = Session()
_qs = {"n": 0, "polls": []}


def _cl_inject(msgs):
    _qs["n"] += 1
    if _qs["n"] == 1:
        return "", [{"id": "cX", "name": "list_dir", "arguments": "{}"}], None
    return "done", [], None


def _poll_inject():
    # 第一次被 poll(第2小轮)返回两条(验证合并),之后空
    if not _qs["polls"]:
        _qs["polls"].append(1)
        return ["补充A", "补充B"]
    return []


_tr = orchestrate(oi, "原始问题", _cl_inject, lambda n, a: "OUT", poll_queue=_poll_inject)
_mi = messages_list(oi.messages_json)
_roles = [m["role"] for m in _mi]
ck("M4注入:最终文本", _tr == "done", _tr)
ck("M4注入:序列合法(user/assistant/tool/user/assistant)", _roles == ["user", "assistant", "tool", "user", "assistant"], str(_roles))
_inj = [m for m in _mi if m.get("role") == "user" and (m.get("content") or "").startswith(PENDING_PREFIX)]
ck("M4注入:恰好一条合并后的 user", len(_inj) == 1, repr([m.get("content") for m in _mi if m.get("role") == "user"]))
ck("M4注入:合并了 补充A 与 补充B", bool(_inj) and "补充A" in _inj[0]["content"] and "补充B" in _inj[0]["content"], repr(_inj[0]["content"] if _inj else ""))
ck("M4注入:在 tool 结果之后(不在 tool_calls 与 tool 之间)", _roles.index("user") < len(_roles) - 1 and _roles[3] == "user", str(_roles))
# 负对照:不传 poll_queue 时不注入(非 TUI 零改动)
onq = Session()
_qs2 = {"n": 0}


def _cl_noq(msgs):
    _qs2["n"] += 1
    if _qs2["n"] == 1:
        return "", [{"id": "cY", "name": "list_dir", "arguments": "{}"}], None
    return "done", [], None


orchestrate(onq, "原始", _cl_noq, lambda n, a: "OUT")
_mn = messages_list(onq.messages_json)
ck("M4注入负对照:无 poll_queue 时不注入(user 只有原始一条)",
   [m["role"] for m in _mn] == ["user", "assistant", "tool", "assistant"], str([m["role"] for m in _mn]))
ck("M4注入负对照:无 PENDING_PREFIX 消息",
   not any(PENDING_PREFIX in (m.get("content") or "") for m in _mn))



# ── orchestrate: Ctrl+C 保留语义(建议1)──────────────────────────────────────
# 旧语义是"中断即整轮回滚":已经跑完的工具轮(读进来的长上下文)和临中断前流出的半截正文
# 全被丢掉,下一句重发要**再跑一遍工具、再付一次全量输入**。新语义只丢"没跑完的那部分",
# 已完成工具轮 + 半截正文都保留;半截/未执行的 tool_calls 仍不落库(否则下一轮 400)。

def _orphans(ms):
    declared, n = set(), 0
    for m in ms:
        if m.get("role") == "assistant":
            for tc in (m.get("tool_calls") or []):
                declared.add(tc.get("id"))
        elif m.get("role") == "tool":
            if m.get("tool_call_id") not in declared:
                n += 1
    return n


# A: 跑完一轮工具后,第二轮流到一半被打断(带半截正文 + 一个半截 tool_call)
oa = Session()
_sA = {"n": 0}


def _cl_A(m):
    _sA["n"] += 1
    if _sA["n"] == 1:
        return "", [{"id": "c1", "name": "list_dir", "arguments": "{}"}], None
    raise Aborted(text="半截回答", tool_calls=[{"id": "c2", "name": "read_file", "arguments": "{}"}])


try:
    orchestrate(oa, "开始", _cl_A, lambda n, a: "TOOLOUT")
    ck("A 应抛出 Aborted", False)
except Aborted as _eA:
    _mA = messages_list(oa.messages_json)
    ck("A 角色序列(工具轮保留+半截assistant)", [m["role"] for m in _mA] == ["user", "assistant", "tool", "assistant"], str([m["role"] for m in _mA]))
    ck("A 工具结果保留(上下文没白扔)", _mA[2]["content"] == "TOOLOUT" and _mA[2]["tool_call_id"] == "c1", repr(_mA[2]))
    ck("A 半截正文保留", _mA[-1]["content"] == "半截回答", repr(_mA[-1]))
    ck("A 半截assistant无tool_calls", not _mA[-1].get("tool_calls"), repr(_mA[-1]))
    ck("A 半截tool_call未落库(无孤儿)", _orphans(_mA) == 0, str(_mA))
    ck("A kept_msgs=3", _eA.kept_msgs == 3, str(_eA.kept_msgs))
    ck("A msg_count=4", oa.msg_count == 4, str(oa.msg_count))

# B: 同样跑完一轮工具,但第二轮**没有**流出正文就断 → 只保留工具轮,不追加空 assistant
ob = Session()
_sB = {"n": 0}


def _cl_B(m):
    _sB["n"] += 1
    if _sB["n"] == 1:
        return "", [{"id": "c1", "name": "list_dir", "arguments": "{}"}], None
    raise Aborted()


try:
    orchestrate(ob, "开始", _cl_B, lambda n, a: "TOOLOUT")
    ck("B 应抛出 Aborted", False)
except Aborted as _eB:
    _mB = messages_list(ob.messages_json)
    ck("B 空半截不落地(只保留工具轮)", [m["role"] for m in _mB] == ["user", "assistant", "tool"], str([m["role"] for m in _mB]))
    ck("B kept_msgs=2", _eB.kept_msgs == 2, str(_eB.kept_msgs))

# C: 第一轮就断(什么都还没完成)→ 用户句 + 半截正文,别凭空补空 assistant
oc2 = Session()


def _cl_C(m):
    raise Aborted(text="半句")


try:
    orchestrate(oc2, "开始", _cl_C, lambda n, a: "")
    ck("C 应抛出 Aborted", False)
except Aborted as _eC:
    _mC = messages_list(oc2.messages_json)
    ck("C 首轮即断:用户句 + 半截assistant", [m["role"] for m in _mC] == ["user", "assistant"], str([m["role"] for m in _mC]))
    ck("C kept_msgs=1", _eC.kept_msgs == 1, str(_eC.kept_msgs))

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

# 图片部件:会话层只存 [[IMGREF:id]] 标记;真正的 image_url 部件在**请求建体时**
# 由 request.expand_refs 用 ref_map 展开(见 request.resolve_inputs)。分两段断言,
# 避免退回到"在会话层要求 list"的旧写法(那会把图片正常工作误判成退化)。
import re as _re
from wchat_py import request as _req, imgstate as _imgstate

_img = os.path.join(tempfile.mkdtemp(), "a.png")
open(_img, "wb").write(b"\x89PNG\r\n")
_si = Session()
_si.input_images = [_img]
_cap = {}


def _cl_img(m):
    _cap["msgs"] = list(m)
    return "ok", [], None


orchestrate(_si, "看看图", _cl_img, lambda n, a: "")
_um = _cap["msgs"][-1]
ck("会话层 user content 是字符串(只放标记)", isinstance(_um["content"], str), str(type(_um["content"])))
_ref_ids = _re.findall(r"\[\[IMGREF:([A-Za-z0-9_]+)\]\]", str(_um["content"]))
ck("会话层含 IMGREF 标记", len(_ref_ids) == 1, repr(_um["content"])[:120])
_ref_map = {i: {"url": _imgstate._IMG_URLS.get(i, "")} for i in _ref_ids}
_ue = _req.expand_refs(_cap["msgs"], _ref_map, "<gone>", "<hint>")[-1]["content"]
ck("请求层 content 是部件数组", isinstance(_ue, list), str(type(_ue)))
_imgs = [p for p in _ue if isinstance(p, dict) and p.get("type") == "image_url"]
ck("请求层含 image_url data:image/png",
   bool(_imgs) and _imgs[0]["image_url"]["url"].startswith("data:image/png"),
   str(_imgs)[:80])
ck("图片消费后清空", _si.input_images == [])

print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)