#!/usr/bin/env python3
"""结构性闸门:tools_spec.json 里声明的**每个**工具都必须有 _run_tool 实现。

为什么需要它:此前的闸门只测"已迁移工具的行为",没有一条断言"schema 声明的工具
集合 ⊆ 分发实现集合"。于是 read_image/read_skill/web_fetch/web_search 四个在
schema 里可见、模型会调用,但 _run_tool 落到"尚未迁移"兜底——单元/端到端全绿也发现不了。
本闸门用**行为**判断:给每个工具最小参数,返回里不得出现"尚未迁移"。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wchat_py.cli as cli
from wchat_py.tools_run import list_dir  # noqa: F401  (确保依赖可导入)

# 每个工具的最小参数(值为空/非法也应进入各自分支,而不是落到兜底)
ARGS = {
    "list_dir": {},
    "read_file": {"path": "/nonexistent-xyz"},
    "grep": {"pattern": "x", "path": "/nonexistent-xyz"},
    "write_file": {"path": "/nonexistent-xyz/a", "content": "x"},
    "edit_file": {"path": "/nonexistent-xyz", "old_string": "a", "new_string": "b"},
    "read_image": {"path": "/nonexistent-xyz.png"},
    "read_skill": {"name": "__no_such_skill__"},
    "run_shell_command": {"command": "true"},
    "web_fetch": {"url": "not-a-url"},
    "web_search": {"query": "x"},
}

T = F = 0


def ck(name, ok, detail=""):
    global T, F
    T += 1
    if ok:
        print("ok -", name)
    else:
        print("FAIL -", name, detail)
        F += 1


spec = json.load(open(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "wchat_py", "tools_spec.json")))
tools = spec if isinstance(spec, list) else spec.get("tools", spec)
names = [(t.get("function", t) or {}).get("name") for t in tools]
names = [n for n in names if n]

os.environ.setdefault("WCHAT_AUTO_ASSUME_YES", "1")   # 避免 run_shell_command 弹确认
for n in names:
    try:
        out = cli._run_tool(n, json.dumps(ARGS.get(n, {})))
    except Exception as e:  # 工具内部异常不算未迁移,但记录
        ck("工具 %s 可分发(异常=%r)" % (n, e), True)
        continue
    ck("工具 %s 已实现(非'尚未迁移')" % n, "尚未迁移" not in str(out), str(out)[:80])

print("RESULT total=%d pass=%d fail=%d" % (T, T - F, F))
sys.exit(0 if F == 0 else 1)

