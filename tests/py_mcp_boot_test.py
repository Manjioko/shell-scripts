#!/usr/bin/env python3
"""MCP 启动链的闸门:**超时粒度 / 失败善后 / 不重复枚举**。

守的是 2026-10-30 那次的真实事故形状(不是假想):

  4 个 stdio server 全声明了,第一次冷启动要把依赖装齐,`npx mcporter list`
  超过 60s → 外框 `subprocess.run(timeout=60)` 把**整条命令**杀掉 →
  一个 server 都没起来,用户只看到一句"mcporter 超时"。更糟的是三件善后全没做:

    1. 锁没放(`~/.ct/mcp/owners` 里的 pid 占到会话退出,下一次 -mcp 被
       "已有会话独占"再挡一道 —— 用户以为是第二个故障);
    2. `MCP_ENABLE` 仍是 1,`tools.build` 无条件塞进 mcp_*,于是 banner 写
       "MCP: 已启用"、模型每轮都看得见死工具、每次调用都踩空;
    3. 归因话术只有一个分支("daemon 有阻塞标记"),冷启动被误导成 daemon 坏了。

本闸门全部离线:把 `mcp_bridge._run` / `list_json` 打桩,物理上不碰 npx 与 daemon。
"""
import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

P = F = 0


def ck(name, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  ok   %s" % name)
    else:
        F += 1
        print("  FAIL %s%s" % (name, ("  ← " + str(detail)) if detail else ""))


CERTS = """\
import json, os, sys, tempfile
sys.path.insert(0, %r)
tmp = tempfile.mkdtemp()
os.environ["CT_HOME"] = tmp
os.environ["WCHAT_NO_SETUP"] = "1"
with open(os.path.join(tmp, "mcporter.json"), "w") as f:
    json.dump({"mcpServers": {"a": {"command": "npx"},
                              "b": {"command": "npx"},
                              "c": {"command": "npx"}}}, f)
from wchat_py import mcp_bridge as mb
from wchat_py import mcp_boot
from wchat_py import tools
""" % REPO


def py(body):
    """在**干净子进程**里跑一段证书 + 片段。

    必须独立进程:install() 会改全局(os.environ / cli._run_tool / tools.build /
    atexit),同一进程里连跑两个场景会互相污染,得到"看起来对"的假绿。
    """
    r = subprocess.run([sys.executable, "-c", CERTS + body],
                       capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


print("=== 1. 超时粒度:per-server 交给 mcporter,外框按 server 数自适应 ===")
_, out, _ = py("print('B=', mb._discovery_budget())")
ck("3 个 server → per=20000ms / 外框 80s", "B= (20000, 80)" in out, out.strip())

_, out, _ = py("""
os.environ["MCP_DISCOVERY_TIMEOUT_MS"] = "5000"
print("B=", mb._discovery_budget())
""")
ck("per 可配:5000ms → 外框 35s", "B= (5000, 35)" in out, out.strip())

_, out, _ = py("""
os.environ["MCP_DISCOVERY_TIMEOUT_MS"] = "0"
print("B=", mb._discovery_budget())
""")
ck("per=0 视为未设(不许把预算算成 0/负)", "B= (20000, 80)" in out, out.strip())

print("=== 2. --timeout 必须真的进 argv(否则 mcporter 用自己默认值)===")
_, out, _ = py("""
cap = {}
def fake_run(argv, timeout=60):
    cap["argv"] = list(argv); cap["timeout"] = timeout
    return '{"servers":[],"counts":{}}', None
mb._run = fake_run
mb.list_json()
print("ARGV=", cap["argv"])
print("OUTER=", cap["timeout"])
mb.list_json(timeout=99)
print("OUTER2=", cap["timeout"])
print("PER2=", cap["argv"][cap["argv"].index("--timeout") + 1])
""")
ck("argv 里有 --timeout 20000",
   "'--timeout', '20000'" in out, out.strip())
ck("外框取预算值 80s", "OUTER= 80" in out, out.strip())
ck("显式 timeout 只改外框(99),per-server 不变",
   "OUTER2= 99" in out and "PER2= 20000" in out, out.strip())

print("=== 3. 启动失败:三件善后一件都不许少 ===")
_, out, err = py("""
mb.list_json = lambda timeout=None: (None, "mcporter 未在 80s 内返回(结果未知)")
mb.sock_state = lambda: 2
mcp_boot.install(["wchat", "-mcp"])
owners = os.path.join(os.environ["CT_HOME"], "mcp", "owners")
own = open(owners).read() if os.path.exists(owners) else "<无文件>"
spec = tools.build(provider="", mcp_enable=1, mcp_tools=None)
names = [t["function"]["name"] for t in json.loads(spec)]
print("ENABLE=", os.environ.get("MCP_ENABLE"))
print("STATE=", os.environ.get("MCP_STATE"))
print("OWNERS=", repr(own))
print("HAS_CALL=", "mcp_call" in names)
print("DISPATCH=", mcp_boot._dispatch("mcp_tools", '{}')[:22])
""")
# 锁:文件可以留着(下次 claim 会跳过非数字行),但**绝不能含活着的 pid**
ck("MCP_ENABLE 关回 0", "ENABLE= 0" in out, out.strip() or err.strip())
ck("MCP_STATE 标成 off(横幅据此不再撒谎)", "STATE= off" in out, out.strip())
ck("锁里没有本进程 pid(失败当场放锁)", "'<无文件>'" in out or "OWNERS= ''" in out, out.strip())
ck("不注册 mcp_call(模型不会看到死工具)", "HAS_CALL= False" in out, out.strip())
ck("残留调用得到明确拒绝(不诱导重试)", "DISPATCH= 错误:MCP 启动时未就绪" in out, out.strip())
ck("给出手动预热命令(带固定清单路径)", "MCPORTER_CONFIG=" in err and "list --json" in err,
   err.strip()[:200])

print("=== 4. 部分可用:降级而不是全废,且不重复枚举 ===")
_, out, _ = py("""
calls = {"n": 0}
def fake_list(timeout=None):
    calls["n"] += 1
    return [
        {"name": "a", "status": "ok", "transport": "STDIO npx -y a",
         "tools": [{"name": "t1", "description": "d"}]},
        {"name": "b", "status": "offline", "error": "Timeout",
         "issue": {"rawMessage": "Timeout"}, "durationMs": 30884,
         "transport": "STDIO uvx b", "tools": []},
    ], None
mb.list_json = fake_list
mb.sock_state = lambda: 2
mcp_boot.install(["wchat", "-mcp"])
print("STATE=", os.environ.get("MCP_STATE"))
print("USABLE=", os.environ.get("MCP_USABLE"))
print("BOOT=", [s["name"] for s in mcp_boot._BOOT_SRV[0]])
before = calls["n"]
body = mcp_boot._dispatch("mcp_tools", '{"server":"a"}')
print("DISPATCH_OK=", "t1" in body)
print("EXTRA=", calls["n"] - before)
""")
ck("有可用 server → degraded 而不是关掉", "STATE= degraded" in out, out.strip())
ck("可用比例如实(1/2)", "USABLE= 1/2" in out, out.strip())
ck("缓存只含可用的那个", "BOOT= ['a']" in out, out.strip())
ck("dispatch 能用缓存里的 server", "DISPATCH_OK= True" in out, out.strip())
ck("每次 dispatch 不再重跑 list(原来热态也白花 1~2s)",
   "EXTRA= 0" in out, out.strip())

print("=== 5. 归因不许编病因(冷启动 ≠ daemon 阻塞) ===")
_, out, _ = py("""
import io, contextlib
srv = [{"name": "b", "status": "offline", "error": "Timeout",
        "issue": {"rawMessage": "Timeout"}, "durationMs": 70,
        "transport": "STDIO uvx b", "tools": []}]
buf = io.StringIO()
with contextlib.redirect_stderr(buf):
    mcp_boot._surface(srv, None)
a = buf.getvalue()
buf2 = io.StringIO()
with contextlib.redirect_stderr(buf2):
    mcp_boot._surface(srv, "Previous daemon exited unexpectedly; no replacement was launched.")
b = buf2.getvalue()
print("COLD_HAS_DAEMON=", "异常退出" in a)
print("COLD_POINTS_AT_RAW=", "原因(上游原文)" in a)
print("DAEMON_HAS_MARK=", "异常退出" in b)
print("DAEMON_HAS_RECOVER=", "foreground --log" in b)
""")
ck("冷启动失败不误报成 daemon 阻塞", "COLD_HAS_DAEMON= False" in out, out.strip())
ck("冷启动失败指向『原因(上游原文)』", "COLD_POINTS_AT_RAW= True" in out, out.strip())
ck("真 daemon 标记仍被识别", "DAEMON_HAS_MARK= True" in out, out.strip())
ck("真 daemon 标记给出恢复步骤", "DAEMON_HAS_RECOVER= True" in out, out.strip())

print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
sys.exit(0 if F == 0 else 1)

