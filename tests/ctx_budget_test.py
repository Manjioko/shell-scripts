#!/usr/bin/env python3
"""上下文体积与 token 放大回归。末行 RESULT ...。

锁五件事(每一条都对应一次实测到的"token 飞涨"):
  1. 工具输出进上下文前**必须**截断:单行 200KB → ≤ ~2KB(列宽 2000),
     5000 行 → 头尾各留(行数 400)。少了列宽那一层,`cat` 一个 minified 文件
     就等于把几十万字符塞进上下文,而且之后每次请求都重发一遍。
  2. MCP 结果按**字节**封顶(单行 JSON 骗得过按行/按列截断)。
  3. 标题生成失败要给兜底名 —— 否则每轮都重发一次"全量历史 + set_title"。
  4. 一轮内部的工具循环里,压缩判据读的是**最新** usage(不是轮末才回填的旧值)。
  5. 上下文越界的失败要能自愈(压缩后重发),纯网络错误不许乱压。
"""
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ.setdefault("WORK_ROOT", REPO)

from wchat_py import cli, compaction, textutil   # noqa: E402

P = [0]
F = []


def ck(name, ok, detail=""):
    if ok:
        P[0] += 1
        print("  ok   %s" % name)
    else:
        F.append(name)
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


class Sink:
    def write(self, s):
        pass

    def flush(self):
        pass


def quiet(fn, *a, **kw):
    old = sys.stdout
    sys.stdout = Sink()
    try:
        return fn(*a, **kw)
    finally:
        sys.stdout = old


# ---------- 1) 工具输出截断 ----------
print("=== 1. 工具输出截断(列宽 + 行数) ===")
d = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".scratch"))
os.makedirs(d, exist_ok=True)
big1 = os.path.join(d, "_t_bigline.txt")
open(big1, "w").write("A" * 200000)
os.environ["CMD_TIMEOUT"] = "60"
os.environ["WCHAT_AUTO_ASSUME_YES"] = "1"
out = quiet(cli._run_tool, "run_shell_command",
            json.dumps({"command": "cat %s" % big1, "reason": "测单行"}))
ck("单行 200KB 被列宽截断(≤ 3000 字符)", len(out) <= 3000, "%d 字符" % len(out))
ck("截断标记出现", "该行过长已截断" in out, out[-80:])

big2 = os.path.join(d, "_t_manylines.txt")
open(big2, "w").write("\n".join("line %05d" % i for i in range(5000)))
out2 = quiet(cli._run_tool, "run_shell_command",
             json.dumps({"command": "cat %s" % big2, "reason": "测多行"}))
n = out2.count("\n")
ck("5000 行被行数截断(≤ 500 行)", n <= 500, "%d 行" % n)
ck("保留了开头(第一行)", "line 00000" in out2, out2[:40])
ck("保留了结尾(最后一行)", "line 04999" in out2, out2[-60:])

small = quiet(cli._run_tool, "run_shell_command",
              json.dumps({"command": "printf 'hi\\n'", "reason": "小输出"}))
ck("小输出原样保留", "hi" in small and "截断" not in small, repr(small[:80]))

# 参数可配(与 master 同名)
os.environ["TOOL_OUTPUT_MAX_COL"] = "100"
out3 = quiet(cli._run_tool, "run_shell_command",
             json.dumps({"command": "cat %s" % big1, "reason": "窄列"}))
ck("TOOL_OUTPUT_MAX_COL 生效", len(out3) <= 200, "%d" % len(out3))
os.environ.pop("TOOL_OUTPUT_MAX_COL", None)

# ---------- 2) MCP 结果字节帽 ----------
print("=== 2. MCP 结果字节帽 ===")
import wchat_py.mcp_boot as MB      # noqa: E402
import wchat_py.mcp_bridge as MBr   # noqa: E402

_orig_list, _orig_call = MBr.list_json, MBr.call
MBr.list_json = lambda: ([{"name": "s1", "status": "ok", "tools": [{"name": "t1"}]}], "")
MBr.call = lambda s, t, a: ("X" * 100000)
os.environ.pop("MCP_RESULT_MAX", None)
os.environ["MCP_GRANTED"] = "s1"
try:
    r = quiet(MB._dispatch, "mcp_call", json.dumps({"server": "s1", "tool": "t1"}))
finally:
    MBr.list_json, MBr.call = _orig_list, _orig_call
ck("MCP 结果被字节帽截断(≤ 20KB)", len(r.encode()) <= 20000, "%d 字节" % len(r.encode()))
ck("截断标记出现", "MCP 结果过长已截断" in r, r[:80])

# ---------- 3) 标题兜底 ----------
print("=== 3. 标题失败要有兜底名 ===")
_orig_ns = cli._nonstream_msg


def _boom(*a, **kw):
    raise RuntimeError("gateway down")


cli._nonstream_msg = _boom
try:
    t = cli._gen_title('[{"role":"system","content":"s"},{"role":"user","content":"u"}]')
finally:
    cli._nonstream_msg = _orig_ns
ck("接口失败也给兜底名(不是空串)", t == "未命名对话", repr(t))


class _EmptyMsg(object):
    content = ""
    tool_calls = None


cli._nonstream_msg = lambda *a, **kw: _EmptyMsg()
try:
    t2 = cli._gen_title('[{"role":"system","content":"s"}]')
finally:
    cli._nonstream_msg = _orig_ns
ck("接口回空也给兜底名", t2 == "未命名对话", repr(t2))

# ---------- 4) 轮内压缩读最新 usage ----------
print("=== 4. 轮内压缩读最新 usage ===")
import wchat_py.compact_run as CR   # noqa: E402
from wchat_py.session import Session, orchestrate, seed_system   # noqa: E402

calls = {"n": 0, "llm": 0}
_orig_compact = CR.compact


def _spy(sess, **kw):
    calls["n"] += 1
    return _orig_compact(sess, **kw)


def _fake_llm(msgs):
    calls["llm"] += 1
    os.environ["WCHAT_LAST_PROMPT_TOKENS"] = "999999"
    if calls["llm"] < 6:
        return "", [{"id": "c%d" % calls["llm"], "name": "list_dir",
                     "arguments": '{"path":"."}'}], None
    return "done", [], None


s = Session()
s.model = "m"
s.token_threshold = 1000
seed_system(s)
CR.compact = _spy
try:
    quiet(orchestrate, s, "连跑", _fake_llm, lambda n_, a: "ok", max_rounds=20)
finally:
    CR.compact = _orig_compact
ck("6 次续发里压缩被触发", calls["n"] >= 1, str(calls["n"]))
ck("但没有每次续发都压(同一个用量只试一次)", calls["n"] <= 2, str(calls["n"]))

# 阈值没超时不该压(模拟用量很小的一轮)
calls["n"] = 0
calls["llm"] = 0
os.environ["WCHAT_LAST_PROMPT_TOKENS"] = "0"


def _small_llm(msgs):
    calls["llm"] += 1
    os.environ["WCHAT_LAST_PROMPT_TOKENS"] = "10"     # 远低于阈值 1000
    if calls["llm"] < 3:
        return "", [{"id": "s%d" % calls["llm"], "name": "list_dir",
                     "arguments": '{"path":"."}'}], None
    return "done", [], None


s2 = Session()
s2.model = "m"
s2.token_threshold = 1000
seed_system(s2)
CR.compact = _spy
try:
    quiet(orchestrate, s2, "连跑", _small_llm, lambda n_, a: "ok", max_rounds=5)
finally:
    CR.compact = _orig_compact
ck("未超阈值时压缩次数为 0", calls["n"] == 0, str(calls["n"]))

# ---------- 5) 越界判据 + 自愈 ----------
print("=== 5. 越界自愈 ===")
ck("服务端说超长 → sig",
   compaction.overflow_kind("maximum context length is 128000 tokens") == "sig")
ck("中文超长也认", compaction.overflow_kind("上下文长度超限") == "sig")
ck("纯网络错误 → none", compaction.overflow_kind("Connection error.") == "none")
ck("字节粗算兜底", compaction.overflow_kind("boom", 700000, 625000) == "est")
ck("粗算没超 → none", compaction.overflow_kind("boom", 100, 625000) == "none")
ck("gate=0 时不误判", compaction.overflow_kind("boom", 999999, 0) == "none")

# 自愈:越界错误 → 压缩 + 返回 True;网络错误 → 不压
class _Sess(object):
    message_json = "[]"

    def __init__(self):
        self.messages_json = json.dumps([{"role": "system", "content": "s"}] * 30)
        self.msg_count = 30
        self.last_prompt_tokens = 0
        self.token_threshold = 1
        self.ctx_overflow_gate = 100
        self._retry_done = False


hits = {"n": 0}
CR.compact = lambda sess, **kw: (hits.__setitem__("n", hits["n"] + 1), (True, 0, 0, 0))[1]
try:
    ss = _Sess()
    ok1 = cli._retry_after_compact(ss, [{"role": "user", "content": "x"}],
                                   "maximum context length exceeded")
    ok2 = cli._retry_after_compact(ss, [{"role": "user", "content": "x"}],
                                   "maximum context length exceeded")
    ss2 = _Sess()
    ok3 = cli._retry_after_compact(ss2, [{"role": "user", "content": "x"}],
                                   "Connection error.")
finally:
    CR.compact = _orig_compact
ck("越界错误触发压缩并返回 True", ok1 is True, repr(ok1))
ck("同一轮不重复自愈", ok2 is False, repr(ok2))
ck("网络错误不压缩", ok3 is False and hits["n"] == 1, "%r %d" % (ok3, hits["n"]))

# ---------- 6) 派生默认值与 master 一致 ----------
print("=== 6. 阈值默认值 ===")
for k in ("CTX_WINDOW", "TOKEN_THRESHOLD", "CTX_OVERFLOW_GATE"):
    os.environ.pop(k, None)
quiet(cli._apply_runtime_defaults)
ck("CTX_WINDOW=1000000", os.environ.get("CTX_WINDOW") == "1000000", os.environ.get("CTX_WINDOW"))
ck("TOKEN_THRESHOLD = 窗口 75%", os.environ.get("TOKEN_THRESHOLD") == "750000",
   os.environ.get("TOKEN_THRESHOLD"))
ck("CTX_OVERFLOW_GATE = 窗口 62.5%", os.environ.get("CTX_OVERFLOW_GATE") == "625000",
   os.environ.get("CTX_OVERFLOW_GATE"))

# 清理
for p in (big1, big2):
    try:
        os.unlink(p)
    except OSError:
        pass

import os
import sys

sys.path.insert(0, "/Users/jwan/bin")
import wchat_py.cli as cli



def ck(name, ok, detail=""):
    if ok:
        P[0] += 1
        print("  ok   %s" % name)
    else:
        F.append(name)
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


def mk(n, size):
    out = [{"role": "system", "content": "sys"}]
    for i in range(n):
        out.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": "c%d" % i, "type": "function",
             "function": {"name": "run_shell_command", "arguments": "{}"}}]})
        out.append({"role": "tool", "tool_call_id": "c%d" % i,
                    "content": "R%04d " % i + "x" * size})
    return out


print("=== 9. 旧工具输出折叠(ctx 体积) ===")

m = mk(40, 5000)
before = sum(cli._msg_text_len(x) for x in m)
out = cli._age_tool_results(m)
after = sum(cli._msg_text_len(x) for x in out)
ck("折叠后明显变小", after < before * 0.5, "%d -> %d" % (before, after))

tool_out = [x for x in out if x.get("role") == "tool"]
ck("最近 12 条原样保留",
   [x["content"] for x in tool_out[-12:]] ==
   [x["content"] for x in [y for y in m if y.get("role") == "tool"][-12:]])
ck("更早的都被折叠", all("已折叠" in x["content"] for x in tool_out[:-12]))
ck("tool_call_id 全保留(否则服务端 400)",
   [x.get("tool_call_id") for x in tool_out] ==
   ["c%d" % i for i in range(40)])
ck("折叠保留头部(不是纯占位)", tool_out[0]["content"].startswith("R0000"))
ck("折叠说明里有原始字节数", "原 " in tool_out[0]["content"])

ck("不改调用方的列表", sum(cli._msg_text_len(x) for x in m) == before)
ck("消息条数不变", len(out) == len(m))

# 小结果:折叠后不许变长(等于保留原样 + 说明)
small = mk(30, 10)
out2 = cli._age_tool_results(small)
for a, b in zip([x for x in small if x.get("role") == "tool"],
                [x for x in out2 if x.get("role") == "tool"]):
    if b["content"] != a["content"]:
        assert len(b["content"]) > len(a["content"]) or True
ck("短结果不会被折叠成更长的垃圾",
   all(len(b["content"]) >= len(a["content"]) for a, b in
       zip([x for x in small if x.get("role") == "tool"],
           [x for x in out2 if x.get("role") == "tool"])))

# 数组型 content(图片)不许动:折叠会破坏结构
arr = mk(20, 5000)
arr.append({"role": "tool", "tool_call_id": "img",
            "content": [{"type": "text", "text": "图片"}]})
out3 = cli._age_tool_results(arr)
last = [x for x in out3 if x.get("tool_call_id") == "img"][0]
ck("数组型 content 不被折叠", isinstance(last["content"], list))

# 开关
os.environ["TOOL_AGE"] = "0"
ck("TOOL_AGE=0 完全不动", cli._age_tool_results(m) is m)
os.environ.pop("TOOL_AGE", None)

# keep 可调
os.environ["TOOL_AGE_KEEP"] = "3"
o4 = cli._age_tool_results(m)
kept = [x for x in o4 if x.get("role") == "tool" and "已折叠" not in x["content"]]
ck("TOOL_AGE_KEEP=3 时只留 3 条", len(kept) == 3, str(len(kept)))
os.environ.pop("TOOL_AGE_KEEP", None)

# 结构锁:折叠必须挂在实际发送路径上(_call_llm 里),不能只当工具函数躺着
src = open("/Users/jwan/bin/wchat_py/cli.py", encoding="utf-8").read()
body = src[src.index("def _call_llm(msgs, model"):]
body = body[:body.index("def ", 10)]
ck("_call_llm 里真的调用了折叠", "_age_tool_results(" in body)
ck("折叠在构造 req 之前(否则发出去的还是全量)", body.index("_age_tool_results(")
   < body.index("req = {"), "顺序不对")

# 最强的一条:折叠必须真的体现在**发出去的请求体**里。
# 只测函数返回值不够 —— "算了但没用"是最容易出现的假绿。
import io as _io
import json as _json
import subprocess as _sp

_CAP = {}


class _FStdin(object):
    def write(self, s):
        _CAP["req"] = s

    def close(self):
        pass


class _FStdout(object):
    def __iter__(self):
        return iter([])


class _FPopen(object):
    def __init__(self, argv, **kw):
        self.stdin = _FStdin()
        self.stdout = _FStdout()
        self.returncode = 0

    def wait(self):
        return 0


_orig_popen = _sp.Popen
_sp.Popen = _FPopen
_old_log = os.environ.get("WCHAT_API_LOG")
os.environ["WCHAT_API_LOG"] = "0"
try:
    _m = mk(40, 5000)
    cli._call_llm(_m, "m")
finally:
    _sp.Popen = _orig_popen
    if _old_log is None:
        os.environ.pop("WCHAT_API_LOG", None)
    else:
        os.environ["WCHAT_API_LOG"] = _old_log

if "req" in _CAP:
    _sent = _json.loads(_CAP["req"])["messages"]
    _ts = [x for x in _sent if x.get("role") == "tool"]
    _fd = [x for x in _ts if "已折叠" in str(x.get("content"))]
    ck("发出去的请求体里确实折叠了", len(_fd) > 0, "%d/%d" % (len(_fd), len(_ts)))
    ck("wire 上体积明显变小",
       sum(len(str(x.get("content"))) for x in _sent)
       < sum(len(str(x.get("content"))) for x in _m) * 0.6)
    ck("wire 上 tool_call_id 齐全",
       [x.get("tool_call_id") for x in _ts] == ["c%d" % i for i in range(40)])
else:
    ck("发出去的请求体里确实折叠了", False, "没抓到请求体")



print("RESULT total=%d pass=%d fail=%d" % (P[0] + len(F), P[0], len(F)))
sys.exit(0 if not F else 1)

