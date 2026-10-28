#!/usr/bin/env python3
"""API 调用记账(apilog)的闸门。末行 RESULT ...。

这道闸门守的第一件事**不是功能,是隐私**:审计日志绝不能变成对话内容备份。
所以第一条断言就是"灌一段带密钥和私事的话进去,日志里一个字都不许出现"。

其余每条对应一个会让"记账"失去意义的失败:
  2. record 抛异常不许影响主流程(记账是观测,不是功能);
  3. 每个调用点都必须带 source 标签 —— 没有标签就归因不了,账本等于白记;
  4. 每次调用的 usage 必须独立(上次的 prompt_tokens 不许漏到这次);
  5. 轮转有上限(记账把磁盘写爆属于自伤);
  6. audit 的 A1 判据真能抓出"非主对话带全量历史"。
"""
import json
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from wchat_py import apilog  # noqa: E402

P = F = 0


def ck(name, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  ok   %s" % name)
    else:
        F += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


def fresh():
    d = tempfile.mkdtemp(prefix="apilog_", dir=REPO)
    os.environ["WCHAT_API_LOG_DIR"] = os.path.join(d, "usage")
    os.environ["WCHAT_API_LOG"] = "1"
    return d


def lines():
    p = os.path.join(os.environ["WCHAT_API_LOG_DIR"], "calls.jsonl")
    try:
        with open(p, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]
    except OSError:
        return []


# ---------------- 1) 绝不记录正文(隐私) ----------------
print("=== 1. 不记录正文 ===")
d = fresh()
SECRET = "sk-super-secret-key-abcdef123456"
PRIVATE = "我家的门牌是幸福路 88 号,银行卡密码 654321"
msgs = [{"role": "system", "content": "you are helpful"},
        {"role": "user", "content": PRIVATE},
        {"role": "assistant", "content": "好的"},
        {"role": "user", "content": "key=%s" % SECRET}]
apilog.record("main", "m1", msgs, usage={"prompt": 1234, "cached": 1000, "completion": 56})
raw = open(os.path.join(os.environ["WCHAT_API_LOG_DIR"], "calls.jsonl"),
           encoding="utf-8").read()
ck("日志里没有密钥原文", SECRET not in raw)
ck("日志里没有私事原文", "幸福路" not in raw and "654321" not in raw)
ck("日志里没有 system 正文", "you are helpful" not in raw)
ck("但记了结构:条数", any(r.get("msgs") == 4 for r in lines()))
ck("但记了结构:字节数", any(int(r.get("bytes") or 0) > 0 for r in lines()))
ck("但记了 token", any(r.get("prompt") == 1234 for r in lines()))
ck("记了缓存命中", any(r.get("cached") == 1000 for r in lines()))
ck("记了角色序列", any(r.get("roles") == "system,user,assistant,user" for r in lines()),
   repr([r.get("roles") for r in lines()]))
ck("记了指纹(可判重复但不含内容)", all(len(r.get("dg") or "") == 8 for r in lines()))
shutil.rmtree(d, ignore_errors=True)

# ---------------- 2) 记账失败不许影响主流程 ----------------
print("=== 2. 记账失败不影响主流程 ===")
d = fresh()
# 把目录指到一个**文件**上,os.makedirs 必然失败
bad = os.path.join(d, "not-a-dir")
open(bad, "w").write("x")
os.environ["WCHAT_API_LOG_DIR"] = os.path.join(bad, "usage")
try:
    apilog.record("main", "m", [{"role": "user", "content": "hi"}],
                  usage={"prompt": 1})
    ck("写盘失败被吞掉(不抛)", True)
except Exception as e:
    ck("写盘失败被吞掉(不抛)", False, "%s: %s" % (type(e).__name__, e))
shutil.rmtree(d, ignore_errors=True)

# 关掉开关就完全不写
d = fresh()
os.environ["WCHAT_API_LOG"] = "0"
apilog.record("main", "m", [{"role": "user", "content": "hi"}], usage={"prompt": 1})
ck("WCHAT_API_LOG=0 时不写", lines() == [], repr(lines()))
os.environ["WCHAT_API_LOG"] = "1"
shutil.rmtree(d, ignore_errors=True)

# ---------------- 3) 结构锁:每个非流式调用点都要带 source ----------------
print("=== 3. 归因标签(结构锁) ===")
cli_src = open(os.path.join(REPO, "wchat_py", "cli.py"), encoding="utf-8").read()
cmp_src = open(os.path.join(REPO, "wchat_py", "compact_run.py"), encoding="utf-8").read()
# 调用点(排除 def 定义行)
calls = []
for name, src in (("cli.py", cli_src), ("compact_run.py", cmp_src)):
    for i, ln in enumerate(src.split("\n"), 1):
        t = ln.strip()
        if t.startswith("#") or "def " in t:
            continue
        if "_nonstream(" in t or "_nonstream_msg(" in t:
            calls.append((name, i, t))
ck("确实找到了调用点(判据不是空转)", len(calls) >= 4, "找到 %d 处" % len(calls))
# 跨行调用:source= 可能出现在下一行,所以看"调用点附近 3 行"里有没有 source=
_untagged = []
for name, ln_no, _t in calls:
    src = cli_src if name == "cli.py" else cmp_src
    window = "\n".join(src.split("\n")[ln_no - 1:ln_no + 2])
    if "source=" not in window:
        _untagged.append("%s:%d" % (name, ln_no))
ck("每个非流式调用点都带 source", not _untagged, repr(_untagged))

# 记账标签集合必须能覆盖五类(有标签才谈得上归因)
tags = set()
for ln in (cli_src + cmp_src).split("\n"):
    if "source=" in ln:
        for part in ln.split("source=")[1:]:
            tags.add(part.split(",")[0].split(")")[0].strip().strip('"\''))
ck("覆盖 auth/skill/title/compact 四类非主调用",
   {"auth", "skill", "title", "compact"} <= tags, repr(sorted(tags)))

# 流式主对话也要记(结构锁:record("main"
ck("流式主对话也在记账", 'apilog.record(\n                "main"' in cli_src or
   '"main", _model, msgs' in cli_src)

# 结构锁:**不许再有绕过记账的裸 SDK 调用**。
# 实测踩过:auto 模式的终审器直接调 client.chat.completions.create,
# 于是它是唯一不记账的调用点 —— 无人值守跑长任务时终审几十次,账本上是 0。
_bare = []
for i, ln in enumerate(cli_src.split("\n"), 1):
    t = ln.strip()
    if t.startswith("#"):
        continue
    if "chat.completions.create" in t:
        _bare.append(i)
# 只允许出现在 _nonstream / _nonstream_msg 这两个记账出口内部(各一处)
ck("cli.py 里裸 SDK 调用只剩记账出口那两处",
   len(_bare) == 2, "第 %r 行" % (_bare,))

# 终审器必须走 _nonstream(带 source),不许自己 create
_final = cli_src[cli_src.index("def _default_final_call"):]
_final = _final[:_final.index("\ndef ", 10)] if "\ndef " in _final[10:] else _final
ck("终审器走 _nonstream(已记账)", 'source="auth_final"' in _final)
ck("终审器不再自己调 create", "completions.create" not in _final)

# 连通性自检也要记账
setup_src = open(os.path.join(REPO, "wchat_py", "setup.py"), encoding="utf-8").read()
ck("连通性自检记账(source=ping)", '"ping"' in setup_src)

# ---------------- 4) usage 独立性 ----------------
print("=== 4. usage 独立 ===")
d = fresh()
apilog.record("main", "m", [{"role": "user", "content": "a"}], usage={"prompt": 500})
apilog.record("main", "m", [{"role": "user", "content": "b"}], usage=None)
rows = lines()
ck("第一笔记到 500", rows[0].get("prompt") == 500, repr(rows[0].get("prompt")))
ck("第二笔没拿到 usage 就记 0(不沿用上一笔)",
   rows[1].get("prompt") == 0, repr(rows[1].get("prompt")))
shutil.rmtree(d, ignore_errors=True)

# ---------------- 5) 轮转上限 ----------------
print("=== 5. 轮转 ===")
d = fresh()
p = os.path.join(os.environ["WCHAT_API_LOG_DIR"], "calls.jsonl")
os.makedirs(os.path.dirname(p), exist_ok=True)
old_max = apilog.MAX_BYTES
apilog.MAX_BYTES = 200
try:
    for i in range(40):
        apilog.record("main", "m", [{"role": "user", "content": "x" * 50}],
                      usage={"prompt": i})
    files = sorted(x for x in os.listdir(os.path.dirname(p)) if x.startswith("calls"))
    ck("触发过轮转", len(files) > 1, repr(files))
    ck("轮转后文件数有上限(<= KEEP+1)", len(files) <= apilog.KEEP + 1, repr(files))
    ck("当前文件不超上限", os.path.getsize(p) <= apilog.MAX_BYTES * 2,
       str(os.path.getsize(p)))
finally:
    apilog.MAX_BYTES = old_max
shutil.rmtree(d, ignore_errors=True)

# ---------------- 6) audit 判据真能抓 ----------------
print("=== 6. audit 判据 ===")
rows = [
    {"src": "main", "msgs": 30, "bytes": 90000, "sys_bytes": 3000,
     "prompt": 20000, "cached": 0, "dg": "aaaaaaaa", "model": "m", "ts": 1000},
    # A1:标题调用带了 30 条消息(把整个会话发给了 AI)
    {"src": "title", "msgs": 30, "bytes": 90000, "sys_bytes": 100,
     "prompt": 20000, "cached": 0, "dg": "bbbbbbbb", "model": "m", "ts": 1001},
    # A3:主对话 prompt 很大但缓存 0
    {"src": "main", "msgs": 31, "bytes": 91000, "sys_bytes": 3000,
     "prompt": 21000, "cached": 0, "dg": "cccccccc", "model": "m", "ts": 1002},
]
codes = [c for c, _i, _m in apilog.audit(rows)]
ck("抓到 A1(非主对话带大上下文)", "A1" in codes, repr(codes))
ck("抓到 A3(大 prompt 但缓存 0)", "A3" in codes, repr(codes))

# 正常形状不该被误报
clean = [
    {"src": "main", "msgs": 5, "bytes": 3000, "sys_bytes": 800,
     "prompt": 900, "cached": 800, "dg": "d1", "model": "m", "ts": 2000},
    {"src": "auth", "msgs": 2, "bytes": 2600, "sys_bytes": 2532,
     "prompt": 700, "cached": 700, "dg": "d2", "model": "m", "ts": 2001},
    {"src": "main", "msgs": 6, "bytes": 3100, "sys_bytes": 800,
     "prompt": 950, "cached": 900, "dg": "d3", "model": "m", "ts": 2002},
]
ck("正常形状不误报", apilog.audit(clean) == [], repr(apilog.audit(clean)))

# A2:同一进程内,同一份内容 60s 内发两次
dup = [
    {"src": "main", "msgs": 5, "bytes": 3000, "sys_bytes": 800, "pid": 111,
     "prompt": 900, "cached": 900, "dg": "same", "model": "m", "ts": 3000},
    {"src": "main", "msgs": 5, "bytes": 3000, "sys_bytes": 800, "pid": 111,
     "prompt": 900, "cached": 900, "dg": "same", "model": "m", "ts": 3005},
]
ck("抓到 A2(同进程内内容相同且间隔短)", "A2" in [c for c, _i, _m in apilog.audit(dup)],
   repr(apilog.audit(dup)))

# A2 反锁:**跨进程**的相同内容不许报 —— 那是用户自己问了两次同样的话。
# (这条是实测踩出来的:连跑两次相同的 -st,旧判据把它当成"重复发送"报了出来)
cross = [
    {"src": "main", "msgs": 5, "bytes": 3000, "sys_bytes": 800, "pid": 111,
     "prompt": 900, "cached": 900, "dg": "same", "model": "m", "ts": 4000},
    {"src": "main", "msgs": 5, "bytes": 3000, "sys_bytes": 800, "pid": 222,
     "prompt": 900, "cached": 900, "dg": "same", "model": "m", "ts": 4005},
]
ck("跨进程相同内容不报 A2(反锁)", apilog.audit(cross) == [], repr(apilog.audit(cross)))

# 老账本兼容:升级前记的行没有 pid 字段。这时**不能**判 A2 ——
# 没有会话边界就无从判断"是同一次任务里重发,还是两次独立提问"。
old_rows = [
    {"src": "main", "msgs": 5, "bytes": 3000, "prompt": 900, "cached": 900,
     "dg": "same", "model": "m", "ts": 5000},
    {"src": "main", "msgs": 5, "bytes": 3000, "prompt": 900, "cached": 900,
     "dg": "same", "model": "m", "ts": 5005},
]
ck("没有 pid 的老记录不报 A2(兼容)", apilog.audit(old_rows) == [],
   repr(apilog.audit(old_rows)))

# ---------------- 7) 工具定义必须记账(否则账本虚报 5 倍) ----------------
print("=== 7. 工具定义成本 ===")
d = fresh()
tl = [{"type": "function", "function": {"name": "t%d" % i,
      "description": "x" * 200, "parameters": {"type": "object"}}} for i in range(10)]
apilog.record("main", "m", [{"role": "user", "content": "hi"}],
              usage={"prompt": 3000}, tools=tl)
r = lines()[0]
tb = int(r.get("tools_bytes") or 0)
ck("记了工具定义字节", tb > 1000, str(tb))
ck("总字节 = 正文 + 工具定义",
   int(r.get("total_bytes") or 0) == int(r.get("bytes") or 0) + tb,
   repr({k: r.get(k) for k in ("bytes", "tools_bytes", "total_bytes")}))
ck("没有传入 tools 时记 0(不编数字)", apilog.tools_bytes(None) == 0)
# 真实量级自检:10 个内置工具的 schema 是 4 位数 KB 级,记成 0 就是漏账
ck("空 tools 列表记 0", apilog.tools_bytes([]) == 0)
shutil.rmtree(d, ignore_errors=True)


# ---------------- 8) 记账挂钩不许把调用打坏(真实执行) ----------------
# 这条是**踩出来的**:给 _nonstream 加记账时误写成 `tools=tools`,而那个函数
# 根本没有 tools 形参 → NameError 被 except 吞掉 → 终审器永远返回空 →
# auto 模式下连 `echo hello` 都判"终审不可用,安全兜底拒绝"。
# 静态检查看不出来(语法完全正确),必须**真跑一次**才能发现。
print("=== 8. 挂钩不打坏调用(真实执行) ===")


class _FakeUsage(object):
    prompt_tokens = 11
    completion_tokens = 2
    prompt_tokens_details = None


class _FakeMsg(object):
    content = "ALLOW ok"


class _FakeChoice(object):
    message = _FakeMsg()


class _FakeResp(object):
    choices = [_FakeChoice()]
    usage = _FakeUsage()


class _FakeCompletions(object):
    @staticmethod
    def create(**kw):
        return _FakeResp()


class _FakeChat(object):
    completions = _FakeCompletions()


class _FakeClient(object):
    chat = _FakeChat()


import wchat_py.llm as _llm   # noqa: E402
import wchat_py.cli as _cli   # noqa: E402

_orig_mk = _llm._make_client
_llm._make_client = lambda *a, **k: _FakeClient()

d = fresh()
try:
    got = _cli._nonstream([{"role": "user", "content": "x"}], "m", source="auth")
    ck("_nonstream 正常返回(挂钩没把它打坏)", got == "ALLOW ok", repr(got))
    rows = lines()
    ck("_nonstream 记了账", any(r.get("src") == "auth" for r in rows),
       repr([r.get("src") for r in rows]))
    ck("_nonstream 记到真 usage", any(r.get("prompt") == 11 for r in rows),
       repr([r.get("prompt") for r in rows]))

    got2 = _cli._nonstream_msg([{"role": "user", "content": "x"}], "m", source="title")
    ck("_nonstream_msg 正常返回", got2 is not None)
    ck("_nonstream_msg 也记了账", any(r.get("src") == "title" for r in lines()),
       repr([r.get("src") for r in lines()]))

    # 终审器端到端:必须拿到非空判定,否则 auto 模式会被兜底规则全拒。
    out = _cli._default_final_call("echo hi", "测试")
    ck("终审器返回非空判定(否则 auto 全被拒)", bool(out), repr(out))
finally:
    _llm._make_client = _orig_mk
    shutil.rmtree(d, ignore_errors=True)

# 结构锁:_nonstream 里不许引用 tools(它没有这个形参)
_b = cli_src[cli_src.index("def _nonstream(msgs"):]
_b = _b[:_b.index("def _usage_dict")]
ck("_nonstream 里不引用 tools(它没有这个形参)", "tools=tools" not in _b)



print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
sys.exit(0 if F == 0 else 1)

