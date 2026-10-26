#!/usr/bin/env python3
"""history_list 自检（空目录/多文件/排序/过滤非 json）。末行 RESULT ...。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wchat_py.session import history_list  # noqa: E402

p = f = 0


def ck(name, ok, detail=""):
    global p, f
    if ok:
        p += 1
        print("  ok   %s" % name)
    else:
        f += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


d = tempfile.mkdtemp()
ck("空目录 -> []", history_list(d) == [])
open(os.path.join(d, "b.json"), "w").write("[]")
open(os.path.join(d, "a.json"), "w").write("[]")
open(os.path.join(d, "notes.txt"), "w").write("x")
open(os.path.join(d, "c.json"), "w").write("[]")
ck("排序且只含 json", history_list(d) == ["a.json", "b.json", "c.json"], str(history_list(d)))
ck("不存在目录 -> []", history_list(os.path.join(d, "nope")) == [])
ck("空串 -> []", history_list("") == [])

# --- save_history 命名:只有一个 .json 后缀(用户实测 bug:xxx.json -> xxx.json.json)---
from wchat_py.session import save_history, Session  # noqa: E402

try:                                    # 老代码没有这个函数 → 干净地记 FAIL,不 ImportError 崩栈
    from wchat_py.session import history_name
except ImportError:
    history_name = None
ck("提供 history_name 归一函数", history_name is not None)
if history_name is None:
    history_name = lambda s: ""         # noqa: E731  后续断言全红,便于定位

ck("history_name 剥掉已有 .json", history_name("xxx.json") == "xxx.json", history_name("xxx.json"))
ck("history_name 空标题兜底", history_name("") == "chat.json", history_name(""))
ck("history_name 清洗非法字符", history_name('a/b:c*d?.json') == "abcd.json", history_name('a/b:c*d?.json'))

sd = os.path.join(d, "hist")
os.makedirs(sd, exist_ok=True)
# 场景:加载历史后 chat_title = basename(含 .json),再 -fs 保存
title = "我的对话.json"
p1 = save_history(sd, '[{"role":"user","content":"hi"}]', title, title)
ck("落盘名不以 .json.json 结尾", not p1.endswith(".json.json"), p1)
ck("落盘名正是 xxx.json", os.path.basename(p1) == "我的对话.json", os.path.basename(p1))
ck("目录里只有一份文件", sorted(os.listdir(sd)) == ["我的对话.json"], str(os.listdir(sd)))
# 反复"加载→保存"不许叠加后缀
p2 = save_history(sd, '[{"role":"user","content":"hi"}]', os.path.basename(p1), title)
ck("二次保存同名(幂等)", p2 == p1, "%s vs %s" % (p2, p1))
# 显式 WCHAT_HISTORY_NAME 同样归一
p3 = save_history(sd, '[]', "custom.json", "")
ck("显式名也归一为单个 .json", os.path.basename(p3) == "custom.json", os.path.basename(p3))
# 加载该历史后 chat_title 应为带 .json 的 basename(这正是 bug 的入口条件)
s = Session()
s.chat_title = os.path.basename(p1)
ck("加载态 chat_title 带 .json(复现条件)", s.chat_title.endswith(".json"), s.chat_title)

print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)