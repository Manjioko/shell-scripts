#!/usr/bin/env python3
"""Ctrl+C 打断的"保留语义"闸门(直接测 orchestrate 层,不依赖 TUI/pty/真信号)。

为什么是这一层:直接测 orchestrate 层的打断保留(不依赖 pty/真信号)。
TUI 里 Ctrl+C 的**交互行为**(busy 打断不退出 / idle 退出 / "正在中断")由
`tests/tui_test.py` 的第 13/16 节覆盖;这里只锁**产品级保证**:打断后本轮已完成的部分
不白扔,且半截 tool_calls 绝不落库(否则下一轮网关 400)。

断言:
  1. 模型流式中途被 Aborted,orchestrate 把异常**原样抛出**(上层据此提示,不吞成 traceback);
  2. 已流出的半截正文作为一条 **无 tool_calls** 的 assistant 消息**保留**在上下文里;
  3. 半截/未执行的 tool_calls **绝不落库**(上下文里任何消息都不得带 tool_calls 键);
  4. kept_msgs 如实回填(供上层说"保留了几条")。

不联网、不依赖 TUI。末行 RESULT ...。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wchat_py.session import Session, Aborted, orchestrate, messages_list  # noqa: E402

p = f = 0


def ck(name, ok, detail=""):
    global p, f
    if ok:
        p += 1
        print("  ok   %s" % name)
    else:
        f += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


_TC = [{"id": "call_1", "name": "run_shell_command", "arguments": "{\"command\": \"true\"}"}]


def _interrupting_llm(msgs):
    # 模拟"流式输出到一半被 Ctrl+C":抛出 Aborted,携带半截正文 + 半截 tool_calls。
    raise Aborted(text="半截正文ABC", tool_calls=_TC)


_s = Session()
_ab = None
try:
    orchestrate(_s, "hello", _interrupting_llm, lambda n, a: "OUT")
except Aborted as e:
    _ab = e
except Exception as e:  # 不应走到这里
    ck("打断以 Aborted 抛出(不是其它异常/裸栈)", False, repr(e))

ck("打断以 Aborted 抛出(原样向上)", _ab is not None)
ck("Aborted 携带半截正文", _ab is not None and _ab.text == "半截正文ABC", repr(getattr(_ab, "text", None)))

_ml = messages_list(_s.messages_json)
_roles = [m.get("role") for m in _ml]
ck("用户那句保留(user 在上下文里)", "hello" in [m.get("content") for m in _ml if m.get("role") == "user"],
   repr(_roles))

# 半截正文作为无 tool_calls 的 assistant 保留
_assist = [m for m in _ml if m.get("role") == "assistant"]
ck("半截正文保留为 assistant 消息", any("半截正文ABC" in (m.get("content") or "") for m in _assist),
   repr([m.get("content") for m in _assist]))
ck("保留的 assistant 不带 tool_calls(无孤儿)",
   all("tool_calls" not in m for m in _ml),
   repr([("tool_calls" in m) for m in _ml]))

ck("kept_msgs 如实回填(=1)", _ab is not None and _ab.kept_msgs == 1, repr(getattr(_ab, "kept_msgs", None)))

print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)