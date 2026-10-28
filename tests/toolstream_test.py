#!/usr/bin/env python3
"""tool_calls 流式解析的闸门:分片合并 + 空工具名处理,必须与 master 一致。

背景(一次真实困惑):界面出现两次

     ⏺ shell ·
    └─ 完成 · 共 0 行 ┘

用户问"是不是没对齐 master"。查下来**两处都没对齐**,而且这两处是同一个 bug 的两半:

  1. **index 缺省值**:master 是 `jq '.index // 0'`(缺了归 0);
     Python 写的是 `_idx = len(tcs)`(**每片新开一个 slot**)。
     后果是一旦网关/代理下发的分片不带 index,一次调用会被拆成两个 slot:
         slot0: name=run_shell_command, args='{"command":'     ← 半截
         slot1: name='',                args='"ls -la"}'        ← 无 name
     也就是说 —— **`{"command": ""}` 不是模型自己发的**,是分片被拆散后
     落到空 name slot 上的必然结果。这解释了"为什么会凭空出现空参数调用"。

  2. **空 name/id 的处理**:master 是 `[[ -z "$fn_name" || -z "$fn_id" ]] && continue`;
     Python 没有这道守卫,会拿空名去查表,得到"工具  尚未迁移到 Python 入口"
     这种既不是成功也不是真错误的回传 —— 又一条静默失败。

判据全部用**假 Popen 喂真实事件流**走 `_call_llm`,不测内部函数 ——
因为要测的正是"分片怎么被合并"这条链,绕过它就测不到。
"""
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

os.environ["WCHAT_API_LOG"] = "0"
os.environ["WCHAT_NO_SETUP"] = "1"

import wchat_py.cli as cli  # noqa: E402

P = F = 0


def ck(name, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  ok   %s" % name)
    else:
        F += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


CAP = {}


class _FStdin(object):
    def write(self, s):
        CAP["req"] = s

    def close(self):
        pass


class _FStdout(object):
    def __init__(self, lines):
        self._l = lines

    def __iter__(self):
        return iter(self._l)


def _popen_with(events):
    lines = [json.dumps(e, ensure_ascii=False) + "\n" for e in events]

    class Popen(object):
        def __init__(self, argv, **kw):
            self.stdin = _FStdin()
            self.stdout = _FStdout(lines)
            self.returncode = 0

        def wait(self):
            return 0
    return Popen


def call(events):
    real = subprocess.Popen
    subprocess.Popen = _popen_with(events)
    try:
        return cli._call_llm([{"role": "user", "content": "x"}], "m")
    finally:
        subprocess.Popen = real


def tc(index=None, name=None, args=None, cid=None):
    d = {}
    if index is not None:
        d["index"] = index
    if name is not None:
        d["name"] = name
    if args is not None:
        d["arguments"] = args
    if cid is not None:
        d["id"] = cid
    return {"t": "tool_calls", "v": [d]}


DONE = {"t": "done", "v": "tool_calls"}

print("=== 1. 分片带 index:正常合并(基线行为不许退化) ===")
_t, tcs, _e = call([tc(0, "run_shell_command", '{"command":', "c1"),
                    tc(0, None, '"ls -la"}'), DONE])
ck("合并成一个调用", len(tcs) == 1, repr(tcs))
ck("name 对", tcs and tcs[0]["name"] == "run_shell_command", repr(tcs))
ck("参数完整", tcs and json.loads(tcs[0]["arguments"])["command"] == "ls -la",
   repr(tcs))

print("=== 2. 分片缺 index:必须归到同一个 slot(对齐 master '.index // 0') ===")
_t, tcs, _e = call([tc(None, "run_shell_command", '{"command":', "c1"),
                    tc(None, None, '"ls -la"}'), DONE])
ck("缺 index 也只能有一个调用", len(tcs) == 1, repr(tcs))
ck("name 没丢", tcs and tcs[0]["name"] == "run_shell_command", repr(tcs))
ck("参数没被截成半截",
   tcs and json.loads(tcs[0]["arguments"]).get("command") == "ls -la", repr(tcs))
ck("不会产生空 name 的 slot",
   all((x.get("name") or "").strip() for x in tcs), repr(tcs))

print("=== 3. 并行多个调用(index 0/1)不许被并成一个 ===")
_t, tcs, _e = call([{"t": "tool_calls", "v": [
    {"index": 0, "id": "a", "name": "read_file", "arguments": '{"path":"x"}'},
    {"index": 1, "id": "b", "name": "list_dir", "arguments": '{"path":"."}'}]},
    DONE])
ck("两个独立调用", len(tcs) == 2, repr(tcs))
ck("名字各自正确",
   [x["name"] for x in tcs] == ["read_file", "list_dir"], repr(tcs))

print("=== 4. 空 name 的调用:执行侧必须有守卫(对齐 master 的 continue) ===")
# 注意:守卫在 run_turn 的工具循环里,不在 _run_tool 内部 —— 这是分层:
# `_consume_stream` 只负责解析,过滤是执行侧的职责。所以这里查**真实执行路径**。
_src = open(os.path.join(REPO, "wchat_py", "cli.py"), encoding="utf-8").read()
_loop = _src[_src.index("每个调用都必须有配对的 tool 结果"):]
_loop = _loop[:_loop.index("sys.stdout.write(box.close())")] \
    if "sys.stdout.write(box.close())" in _loop else _loop
ck("工具循环里有空 name/id 守卫", "_tcn.strip()" in _loop and "_tci.strip()" in _loop)
ck("守卫在任何执行动作之前",
   _loop.index("_tcn.strip()") < _loop.index("_run_tool("),
   "顺序不对:守卫在执行之后等于没守")
ck("守卫的解释里引用了 master 的行为", "legacy_wchat.sh" in _loop,
   "缺 master 出处注释,后人不知道为什么要守")

print("=== 5. 全程无名无 id:解析层照收,执行侧必须拦住 ===")
# 分层是刻意的:`_consume_stream` 只做解析(不臆造/不丢弃),过滤归执行侧。
# 所以这里验两件事:解析出什么 + 执行侧会不会执行它。
_t, tcs2, _e = call([tc(None, None, '{"command":', None), DONE])
ck("解析层原样收下(不擅自造名字)",
   len(tcs2) == 1 and not (tcs2[0].get("name") or ""), repr(tcs2))
ck("它的 name 确实为空(所以必须靠执行侧守卫)",
   not (tcs2[0].get("name") or "").strip(), repr(tcs2))
_throw = _loop[_loop.index("_tcn.strip()"):]
ck("空名分支里不调 _run_tool(否则又变成'假执行')",
   "_run_tool(" not in _throw[:_throw.index("continue")],
   "空名分支居然调了执行")

print("=== 6. 结构锁:两处对齐的代码必须在位 ===")
src = open(os.path.join(REPO, "wchat_py", "cli.py"), encoding="utf-8").read()
ck("缺 index 归 0(不是 len(tcs))",
   'if _idx is None:' in src and "_idx = 0" in src, "找不到 index 归零")
ck("不再用 len(tcs) 当 index 默认值",
   "_idx = len(tcs)" not in src, "仍有 len(tcs) 兜底")
# 用 AST 判断守卫**真的在 if 条件里**,而不是只查字符串存在
# (变异 `if False:` 能骗过字符串检查,骗不过 AST)
import ast as _ast
_guard_ok = False
_tree = _ast.parse(src)
for _node in _ast.walk(_tree):
    if isinstance(_node, _ast.If):
        _seg = _ast.get_source_segment(src, _node.test) or ""
        if "_tcn.strip()" in _seg and "_tci.strip()" in _seg:
            _guard_ok = True
            break
ck("空 name/id 守卫真的在 if 条件里(不是死代码)", _guard_ok,
   "AST 里找不到以空名判断为条件的 if")
ck("守卫后面紧跟 continue/return(不是只判断不动作)",
   "continue" in _loop or "return" in _loop, "守卫没有实际拦截动作")

print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
sys.exit(0 if F == 0 else 1)