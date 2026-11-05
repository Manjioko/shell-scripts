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
print("DISPATCH=", mcp_boot._dispatch("mcp_tools", '{}')[:200])
""")
# 锁:文件可以留着(下次 claim 会跳过非数字行),但**绝不能含活着的 pid**
ck("MCP_ENABLE 关回 0", "ENABLE= 0" in out, out.strip() or err.strip())
ck("MCP_STATE 标成 off(横幅据此不再撒谎)", "STATE= off" in out, out.strip())
ck("锁里没有本进程 pid(失败当场放锁)", "'<无文件>'" in out or "OWNERS= ''" in out, out.strip())
ck("不注册 mcp_call(模型不会看到死工具)", "HAS_CALL= False" in out, out.strip())
# 文案在惰性化时改过(旧:"MCP 启动时未就绪"),但这条守的**语义**没变:
# 残留调用必须得到明确拒绝,且话里不能出现任何"再试试"的诱导。
ck("残留调用得到明确拒绝(不诱导重试)",
   "DISPATCH= 错误:MCP 本次会话连接失败" in out and "不要再次调用" in out, out.strip())
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

print("=== 6. 惰性:启动不碰网络,首次调用才探测,且只探一次 ===")
# 守的是"启动卡死"这个真实观感:旧版 -mcp 在 main() 之前同步探测,实测 8.9s
# 终端一个字都没有(真实冷启动是分钟级),用户只会认为程序挂了。
_, out, _ = py("""
calls = {"n": 0}
def fake_list(timeout=None):
    calls["n"] += 1
    return [{"name": "a", "status": "ok", "transport": "s",
             "tools": [{"name": "t1", "description": "d"}]}], None
mb.list_json = fake_list
mb.sock_state = lambda: 2
os.environ["MCP_LAZY"] = "1"
mcp_boot.install(["wchat"])                       # 无 -mcp
print("AFTER_INSTALL_CALLS=", calls["n"])
print("STATE=", os.environ.get("MCP_STATE"))
spec = json.loads(tools.build(provider="", mcp_enable=(1 if os.environ.get("MCP_ENABLE") == "1" else 0)))
names = [t["function"]["name"] for t in spec]
print("HAS_MCP=", any(n.startswith("mcp_") for n in names))
desc0 = [t["function"]["description"] for t in spec if t["function"]["name"] == "mcp_tools"]
print("DESC_HAS_CATALOG_BEFORE=", bool(desc0) and "本次会话可用的 server" in desc0[0])
mcp_boot._dispatch("mcp_tools", "{}")
print("AFTER_FIRST_CALL_CALLS=", calls["n"])
mcp_boot._dispatch("mcp_tools", "{}")
mcp_boot._dispatch("mcp_call", '{"server":"a","tool":"t1"}')
print("AFTER_MORE_CALLS=", calls["n"])
spec2 = json.loads(tools.build(provider="", mcp_enable=1))
desc2 = [t["function"]["description"] for t in spec2 if t["function"]["name"] == "mcp_tools"]
print("DESC_HAS_CATALOG_AFTER=", bool(desc2) and "本次会话可用的 server" in desc2[0])
print("MANIFEST_EMPTY=", os.environ.get("MCP_MANIFEST") == "")
""")
ck("惰性启动当场不探测(0 次)", "AFTER_INSTALL_CALLS= 0" in out, out.strip())
ck("惰性态标成 lazy(横幅不许写'已启用')", "STATE= lazy" in out, out.strip())
ck("惰性态仍登记 mcp_* 工具(模型看得见入口)", "HAS_MCP= True" in out, out.strip())
ck("探测前描述里没有目录(不编造可用性)", "DESC_HAS_CATALOG_BEFORE= False" in out, out.strip())
ck("首次调用触发且只触发一次探测", "AFTER_FIRST_CALL_CALLS= 1" in out, out.strip())
ck("后续调用不再重复探测", "AFTER_MORE_CALLS= 1" in out, out.strip())
ck("探测后目录进 mcp_tools 的描述(不动 system 前缀)",
   "DESC_HAS_CATALOG_AFTER= True" in out, out.strip())
ck("目录不走 MCP_MANIFEST(否则要重建 system)", "MANIFEST_EMPTY= True" in out, out.strip())

print("=== 6b. 路由提示必须到模型手里(否则有工具却不知道什么时候用) ===")
# 这条守的是本轮自己引入的回退:ROUTE_HINT 原来由 manifest() 注入 system,
# 改成惰性后 MCP_MANIFEST 置空 → 两端都落空,模型只能靠用户提醒才想起用 MCP。
_, out, _ = py("""
from wchat_py import cli
os.environ["MCP_LAZY"] = "1"
mcp_boot.install(["wchat"])          # 惰性:登记工具但不探测
spec = json.loads(tools.build(provider="", mcp_enable=1))
call = [t for t in spec if t["function"]["name"] == "mcp_call"][0]["function"]["description"]
print("CALL_HAS_ROUTE=", "运行态" in call and "mcp_call" in call)
print("CALL_HAS_DIVISION=", "read_file" in call and "web_fetch" in call)
print("SYSTEM_HAS_ROUTE=", "运行态" in cli._system_prompt_text())
""")
ck("mcp_call 描述里带『什么时候该用』的路由提示", "CALL_HAS_ROUTE= True" in out, out.strip())
ck("说清与内置工具的分工(read_file/web_fetch 给不出运行态)",
   "CALL_HAS_DIVISION= True" in out, out.strip())
ck("不再靠 system 注入路由提示(那样中途加工具就得重建 system)",
   "SYSTEM_HAS_ROUTE= False" in out, out.strip())

print("=== 7. 探测失败是会话级终态:不无限重启 ===")
# 这是这次改动最容易做坏的地方:旧版 _BOOT_SRV=None 的语义是"失败",
# 惰性化后变成"还没探",照抄旧判断会**每次调用都探一遍**。
_, out, _ = py("""
calls = {"n": 0}
def fake_list(timeout=None):
    calls["n"] += 1
    return [{"name": "a", "status": "offline", "issue": {"rawMessage": "spawn uvx ENOENT"},
             "durationMs": 31, "tools": []}], None
mb.list_json = fake_list
mb.sock_state = lambda: 2
os.environ["MCP_LAZY"] = "1"
mcp_boot.install(["wchat"])
outs = [mcp_boot._dispatch("mcp_tools", "{}") for _ in range(4)]
print("PROBES=", calls["n"])
print("SAME=", len(set(outs)) == 1)
print("NO_RETRY_WORD=", "不要再次调用" in outs[0] and "不要改写" in outs[0])
print("HAS_RAW_REASON=", "ENOENT" in outs[0])
print("HAS_RECOVER=", "MCPORTER_CONFIG=" in outs[0])
print("STATE=", mcp_boot._ST[0])
print("ENABLE=", os.environ.get("MCP_ENABLE"))
print("SPEC_HAS_MCP=", any(t["function"]["name"] == "mcp_call"
      for t in json.loads(tools.build(provider="", mcp_enable=(1 if os.environ.get("MCP_ENABLE") == "1" else 0)))))
""")
ck("不可恢复原因只探 1 次,四次调用不再探", "PROBES= 1" in out, out.strip())
ck("四次调用返回**完全相同**的话(不诱导换参数)", "SAME= True" in out, out.strip())
ck("失败话术明说不要重试、不要改写", "NO_RETRY_WORD= True" in out, out.strip())
ck("把上游原文交给模型(它能分辨是命令错还是超时)", "HAS_RAW_REASON= True" in out, out.strip())
ck("给出用户可执行的恢复命令", "HAS_RECOVER= True" in out, out.strip())
ck("终态记为 failed", "STATE= failed" in out, out.strip())
ck("失败后不注册 mcp_*,不再踩空", "ENABLE= 0" in out and "SPEC_HAS_MCP= False" in out, out.strip())

print("=== 8. 可恢复原因(超时)最多重试一次,总量仍然封顶 ===")
_, out, _ = py("""
calls = {"n": 0}
def fake_list(timeout=None):
    calls["n"] += 1
    return [{"name": "a", "status": "offline",
             "issue": {"rawMessage": "mcporter timed out after 45000ms"},
             "durationMs": 45000, "tools": []}], None
mb.list_json = fake_list
mb.sock_state = lambda: 2
os.environ["MCP_LAZY"] = "1"
mcp_boot.install(["wchat"])
for _ in range(5):
    mcp_boot._dispatch("mcp_tools", "{}")
print("PROBES=", calls["n"])
print("CAP=", os.environ.get("MCP_MAX_PROBES", "<未设>"))
""")
ck("超时类只自动重试 1 次(总探测 2 次),5 次调用后不再增长",
   "PROBES= 2" in out, out.strip())

print("=== 9. 多实例:不再互斥(座位机制已按用户反馈移除)===")
# 旧版一把整机锁:第二个 wchat 直接 MCP_ENABLE=0,一个都用不了(实测过)。
# 中途改过"按 server 占座",但实测证明那是**假保护**:daemon 全机共享,几条会话调
# chrome 会命中 daemon 里**同一条**连接,座位挡不住真并发,只会让第二个会话被完全拒绝;
# 且座位文件 kill -9 后残留 + pid 复用 ⇒ 永久锁死。现已退掉,任何人任何时候都能调。
_, out, _ = py("""
import subprocess, time
other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
time.sleep(0.2)
mb.list_json = lambda timeout=None: ([
    {"name": "busy", "status": "ok", "transport": "s", "tools": [{"name": "t", "description": "d"}]},
    {"name": "free", "status": "ok", "transport": "s", "tools": [{"name": "u", "description": "d"}]},
], None)
mb.sock_state = lambda: 2
os.environ["MCP_LAZY"] = "1"
mcp_boot.install(["wchat"])
ok, why = mcp_boot.ensure_ready()
print("READY=", ok)
print("BOTH_LISTED=", sorted(s["name"] for s in mcp_boot._BOOT_SRV[0]))
r_busy = mcp_boot._dispatch("mcp_call", '{"server":"busy","tool":"t"}')
r_free = mcp_boot._dispatch("mcp_call", '{"server":"free","tool":"u"}')
print("BUSY_ALLOWED=", "正被另一个" not in r_busy)
print("FREE_WORKS=", "错误" not in r_free[:6])
print("ENABLE_STILL_ON=", os.environ.get("MCP_ENABLE"))
other.kill(); other.wait()
""")
ck("另一个会话不会让本会话失能", "READY= True" in out, out.strip())
ck("两个 server 都列出", "BOTH_LISTED= ['busy', 'free']" in out, out.strip())
ck("★ 不再拒绝(座位机制已移除:假保护,真并发在 daemon 层挡不住)",
   "BUSY_ALLOWED= True" in out, out.strip())
ck("server 可正常调用", "FREE_WORKS= True" in out, out.strip())
ck("有 server 在跑不影响整体启用", "ENABLE_STILL_ON= 1" in out, out.strip())
_src_mb = open(os.path.join(REPO, "wchat_py", "mcp_boot.py"), encoding="utf-8").read()
ck("互斥机制已整体移除(源码里没有 claim_server / 座位)",
   "def claim_server" not in _src_mb and "_SEATS" not in _src_mb, "还留着座位实现")


print("=== 10. daemon 引用计数:还有人用就绝不停 ===")
_, out, _ = py("""
import subprocess, time
other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
time.sleep(0.2)
mcp_boot._claim_daemon()
rows = mcp_boot._owners_read()
rows.append((other.pid, 0))
mcp_boot._owners_write(rows)
left = mcp_boot._release_daemon()
print("LEFT=", [p for p, _ in left])
print("WOULD_STOP=", not left)
other.kill(); other.wait()
""")
ck("自己退出后另一会话仍在账本上 ⇒ 不该停 daemon",
   "WOULD_STOP= False" in out, out.strip())

print("=== 11. auto / -no-mcp 不进入惰性(能力扩张必须有人签字) ===")
_, out, _ = py("""
mcp_boot.install(["wchat", "-auto"])
print("AUTO_ENABLE=", os.environ.get("MCP_ENABLE"))
mcp_boot.install(["wchat", "-no-mcp"])
print("NOMCP_DISABLE=", os.environ.get("MCP_DISABLE"))
""")
ck("auto 档不带 -mcp 时惰性**不开**(无人值守不许模型自己拉起外部能力)",
   "AUTO_ENABLE= None" in out, out.strip())
ck("-no-mcp 记成显式关闭", "NOMCP_DISABLE= 1" in out, out.strip())

print("=== 12. 显式 -mcp:探测前先说话(不许静默卡住) ===")
_, out, err = py("""
mb.list_json = lambda timeout=None: (None, "stub")
mb.sock_state = lambda: 2
mcp_boot.install(["wchat", "-mcp"])
""")
ck("探测开始前就打出『正在连接』(旧版 8.9s 全静默)",
   "正在连接" in err, err.strip()[:200])
ck("说清可 Ctrl+C 中断", "Ctrl+C" in err, err.strip()[:300])

print("=== 13. Ctrl+C 打断探测:落终态、撤工具、不装死 ===")
# 三个都要:停在 busy 会让之后每次调用都说"正在探测中";不撤工具会让模型对着
# 一个必然失败的 mcp_* 反复踩空;而"能再试"就是无限重启的入口。
_, out, _ = py("""
def boom(timeout=None):
    raise KeyboardInterrupt
mb.list_json = boom
mb.sock_state = lambda: 2
os.environ["MCP_LAZY"] = "1"
mcp_boot.install(["wchat"])
r1 = mcp_boot._dispatch("mcp_tools", "{}")
r2 = mcp_boot._dispatch("mcp_tools", "{}")
print("ST=", mcp_boot._ST[0])
print("ENABLE=", os.environ.get("MCP_ENABLE"))
print("SAME=", r1 == r2)
print("PROBES=", mcp_boot._PROBES[0])
print("NO_STACK=", "Traceback" not in r1)
""")
ck("中断后不卡在 busy", "ST= failed" in out, out.strip())
ck("中断后撤下 mcp_*(不留给模型反复踩空)", "ENABLE= 0" in out, out.strip())
ck("中断后再调用返回同一句且不再探测",
   "SAME= True" in out and "PROBES= 1" in out, out.strip())

print("=== 14. 子进程 cwd 必须固定(否则每换一个项目目录就多一个 server 实例)===")
# 2026-11 用户实测:每调一次 MCP 就多一个 chrome-devtools-mcp,第二个起全部报
# "The browser is already running for …chrome-profile"。根因是 mcporter 把**调用方
# 进程的 cwd** 算进 daemon 连接身份:同 daemon 同清单,从 projA/projB/projC 各调一次
# → 建 3 个连接、spawn 3 个 server,各抢同一个 Chrome profile。
# 已排除:--root 无效、显式 --config 无效、relay 开关无关、探测超时不会堆积、
# "一次冲突后雪崩"不成立。固定 cwd 后 4 个目录 → 恒为 1 个实例、全部成功。
_, out, _ = py("""
cap = {}
class _R:
    stdout = '{"servers":[],"counts":{}}'
    stderr = ''
    returncode = 0
def fake_run(argv, **kw):
    cap.update(kw)
    return _R()
mb.subprocess.run = fake_run
root = mb._mcp_root()
mb.list_json()
cwd1 = cap.get("cwd")
os.chdir("/")
mb.list_json()
cwd2 = cap.get("cwd")
print("HAS_CWD=", bool(cwd1))
print("STABLE=", cwd1 == cwd2)
print("IS_ROOT=", cwd1 == root)
print("NOT_CALLER_CWD=", cwd1 != os.getcwd())
print("ROOT_EXISTS=", os.path.isdir(root or ""))
print("ENV_STILL_SCOPED=", "MCPORTER_CONFIG" in (cap.get("env") or {}))
""")
ck("_run 给子进程带上固定 cwd", "HAS_CWD= True" in out, out.strip())
ck("cwd 不随调用方目录变化(这条就是根因所在)",
   "STABLE= True" in out and "NOT_CALLER_CWD= True" in out, out.strip())
ck("用的是 _mcp_root()", "IS_ROOT= True" in out, out.strip())
ck("该目录会被自动创建", "ROOT_EXISTS= True" in out, out.strip())

# 建不出来时必须降级(不传 cwd),而不是抛异常把 MCP 弄挂
_, out, _ = py("""
import builtins
real_makedirs = os.makedirs
def boom(p, *a, **k):
    if p.endswith("root"):
        raise OSError("permission denied")
    return real_makedirs(p, *a, **k)
os.makedirs = boom
print("ROOT=", mb._mcp_root())
cap = {}
class _R:
    stdout = '{"servers":[],"counts":{}}'
    stderr = ''
    returncode = 0
def fake_run(argv, **kw):
    cap.update(kw)
    return _R()
mb.subprocess.run = fake_run
mb.list_json()
print("NO_CWD=", "cwd" not in cap)
print("STILL_RAN=", "env" in cap)
""")
ck("目录建不出来时降级为不固定 cwd(不阻塞使用)",
   "ROOT= None" in out and "NO_CWD= True" in out and "STILL_RAN= True" in out, out.strip())

print("=== 15. daemon socket 路径必须尊重上游环境变量 ===")
# 写死 `~/.mcporter/daemon/user.sock` 的后果不是报错,而是 sock_state() 回答的是
# **另一个 daemon** 的状态 —— 而 _atexit_stop 正是拿它当"要不要停 daemon"的闸门。
# 2026-11 实测:隔离测试里 sock_state() 恒为 0,因为它连的其实是默认 daemon。
_, out, _ = py("""
for k in ("MCPORTER_DAEMON_SOCKET", "MCPORTER_DAEMON_DIR"):
    os.environ.pop(k, None)
base = mb.sock_path()
print("DEFAULT_IS_HOME=", base.endswith("/.mcporter/daemon/user.sock"))
os.environ["MCPORTER_DAEMON_DIR"] = "/tmp/somedir"
print("DIR_RESPECTED=", mb.sock_path() == "/tmp/somedir/daemon/user.sock")
os.environ["MCPORTER_DAEMON_SOCKET"] = "/tmp/other/s.sock"
print("SOCKET_WINS=", mb.sock_path() == "/tmp/other/s.sock")
del os.environ["MCPORTER_DAEMON_SOCKET"]
os.environ["MCPORTER_DAEMON_DIR"] = "/tmp/definitely-not-there-xyz"
print("NO_SOCK_STATE=", mb.sock_state())
""")
ck("默认仍指向 ~/.mcporter(不改变正常行为)", "DEFAULT_IS_HOME= True" in out, out.strip())
ck("MCPORTER_DAEMON_DIR 生效", "DIR_RESPECTED= True" in out, out.strip())
ck("MCPORTER_DAEMON_SOCKET 优先级更高", "SOCKET_WINS= True" in out, out.strip())
ck("换目录后 sock_state 判的是**那个**目录(2=不存在)",
   "NO_SOCK_STATE= 2" in out, out.strip())

print("=== 16. SIGTERM/SIGHUP 必须走清理(atexit 只在正常退出时跑)===")
# 实测:kill -TERM 与 kill -9 都会留下孤儿 daemon;而 daemon **永不自退**
# (idleTimeoutMs=None,该字段上游只认自己的 canonical 路径、忽略 MCPORTER_CONFIG)。
_, out, _ = py("""
import signal as _sig
mcp_boot._install_signal_handlers()
print("TERM_HOOKED=", _sig.getsignal(_sig.SIGTERM) is mcp_boot._on_term)
print("HUP_HOOKED=", _sig.getsignal(_sig.SIGHUP) is mcp_boot._on_term)
print("INT_UNTOUCHED=", _sig.getsignal(_sig.SIGINT) is _sig.default_int_handler)
""")
ck("SIGTERM 被接管(kill / 关终端)", "TERM_HOOKED= True" in out, out.strip())
ck("SIGHUP 被接管", "HUP_HOOKED= True" in out, out.strip())
ck("不抢 SIGINT(那是 REPL/流式输出的地盘)", "INT_UNTOUCHED= True" in out, out.strip())

print("=== 17. 孤儿收割:三条保护条件,一条都不能少 ===")
# 收割是"杀进程"操作,误杀代价高:daemon socket 是全机一个,Claude Desktop 等
# 别家程序也可能拉起它。所以必须三条同时成立:归属标记的 pid 就是当前 daemon、
# 账本里没有活着的 wchat、socket 真可连。
_, out, _ = py("""
# 1) 没有归属标记 → 不动(可能是别家拉起的)
print("NO_MARK=", mcp_boot.reap_orphan_daemon()[0])
# 2) 有标记,但账本里还有活着的 wchat → 不动
mcp_boot._remember_daemon_owner(999999)
mcp_boot._owners_write([(os.getpid(), 0)])
print("BUSY=", mcp_boot.reap_orphan_daemon()[0])
mcp_boot._owners_write([])
# 3) 标记里的 pid 与当前 daemon 不符 → 只清标记,不动 daemon
mb.sock_state = lambda: 0
mcp_boot.daemon_pid = lambda: 4242
print("STALE=", mcp_boot.reap_orphan_daemon()[0])
print("MARK_CLEARED=", not os.path.isfile(mcp_boot._daemon_owner_path()))
# 4) MCP_NO_REAP=1 一律不收割
mcp_boot._remember_daemon_owner(4242)
os.environ["MCP_NO_REAP"] = "1"
print("OPTOUT=", mcp_boot.reap_orphan_daemon()[0])
""")
ck("没有归属标记时不动(防误杀别家程序拉起的 daemon)", "NO_MARK= none" in out, out.strip())
ck("有别的 wchat 会话在用时不动", "BUSY= busy" in out, out.strip())
ck("daemon 换过实例时只作废标记、不动进程", "STALE= stale" in out, out.strip())
ck("作废时把标记清掉(不留下会误导下一次的残留)", "MARK_CLEARED= True" in out, out.strip())
ck("MCP_NO_REAP=1 可整体关掉", "OPTOUT= skip" in out, out.strip())

print("=== 18. 清理只跑一次(atexit 与信号处理器不许重复停 daemon) ===")
_, out, _ = py("""
n = {"c": 0}
mb.daemon_stop = lambda timeout=20: (n.__setitem__("c", n["c"] + 1) or True)
mb.sock_state = lambda: 0
mcp_boot._OWN[0] = True
mcp_boot._atexit_stop()
mcp_boot._atexit_stop()
mcp_boot._atexit_stop()
print("STOP_CALLS=", n["c"])
""")
ck("重复清理只停一次 daemon(信号 + atexit 可能都触发)",
   "STOP_CALLS= 1" in out, out.strip())

print("=== 19. 子进程环境必须最小且稳定(连接复用的根因)===")
# 2026-11 用户实测:mcporter 把**整套环境变量**算进 daemon 的连接身份 ——
# 同一 cwd、同一清单,只改一个环境变量,连接数就整套翻倍且永不回收:
#     list --json → 4 条;FOO=1 → 8 条;BAR=2 → 12 条;WCHAT_DEBUG=1 → 16 条
# 而 wchat 原来传 dict(os.environ),并且自己在会话里不断改 MCP_STATE / MCP_USABLE /
# MCP_GRANTED / MCP_ENABLE / WCHAT_LAST_*_TOKENS… ⇒ 每调一次 chrome 多一条连接,
# 而浏览器 profile 是全局独占锁 ⇒ 最终必然 `The browser is already running`。
_, out, _ = py("""
import io, contextlib
# 会话内会变的那些:全都不许传给子进程
for k in ("MCP_STATE", "MCP_USABLE", "MCP_GRANTED", "MCP_ENABLE", "MCP_ONLY",
          "WCHAT_LAST_PROMPT_TOKENS", "WCHAT_LAST_COMPLETED", "ZZ_RANDOM_VAR"):
    os.environ[k] = "x"
e1 = mb.child_env()
print("NO_VOLATILE=", not any(k in e1 for k in (
    "MCP_STATE", "MCP_USABLE", "MCP_GRANTED", "MCP_ENABLE", "MCP_ONLY",
    "WCHAT_LAST_PROMPT_TOKENS", "ZZ_RANDOM_VAR")))
print("KEEPS_PATH=", "PATH" in e1)
print("KEEPS_HOME=", "HOME" in e1)
# 环境再变,传下去的东西必须一模一样(这就是连接复用的前提)
os.environ["MCP_STATE"] = "ok"
os.environ["ZZ_ANOTHER"] = "1"
e2 = mb.child_env()
print("STABLE=", e1 == e2)
print("HAS_PROFILE_VAR=", bool(e1.get("WCHAT_MCP_CHROME_PROFILE")))
""")
ck("会话内会变的变量一律不传(MCP_STATE/USABLE/GRANTED/ENABLE/TOKENS…)",
   "NO_VOLATILE= True" in out, out.strip())
ck("PATH/HOME 等『能跑起来』的项保留",
   "KEEPS_PATH= True" in out and "KEEPS_HOME= True" in out, out.strip())
ck("★ 环境再变,传下去的环境**逐字节相同**(连接复用的前提)", "STABLE= True" in out, out.strip())
ck("注入 chrome 的隔离 profile 变量", "HAS_PROFILE_VAR= True" in out, out.strip())

# 清单里 ${VAR} 引用到的变量必须传(否则 server 端拿到空串,症状是"连上了但全 401")
_, out, _ = py("""
p = mb.config_path()
with open(p, "w") as f:
    json.dump({"mcpServers": {"s": {"command": "x",
                                    "env": {"T": "${MY_SECRET_TOKEN}"}}}}, f)
os.environ["MY_SECRET_TOKEN"] = "abc"
e = mb.child_env()
print("REF_PASSED=", e.get("MY_SECRET_TOKEN") == "abc")
""")
ck("清单里 ${VAR} 引用到的变量会被传下去", "REF_PASSED= True" in out, out.strip())

print("=== 20. chrome 必须走隔离 profile(不与别家程序抢全局锁)===")
# chrome-devtools-mcp 默认 profile 是**全机独占锁**;任何别的程序抢到它,wchat 全体报
# `The browser is already running`。用 --isolated 后 Chrome 用临时 profile。
# 为什么不用 --userDataDir:mcporter 把 `--userDataDir/--channel/…` 列为连接选择标志,
# 带这些会被它的 chrome-owner 直接拒绝(实测);`--isolated` 不在名单里,可用。
_src_seed = open(os.path.join(REPO, "wchat_py", "seed.py"), encoding="utf-8").read()
_tpl = open(os.path.join(REPO, "configs", "mcporter.example.json"), encoding="utf-8").read()
ck("模板里 chrome 带 --isolated", '"--isolated"' in _tpl, _tpl[:200])
ck("有一次性迁移,给已装机的旧清单补上(seed 只补缺不覆盖,模板改了传不过去)",
   "def migrate_chrome_profile" in _src_seed, "")
ck("迁移是文本级插入,不重写整份文件(否则整份 diff)",
   "_insert_args_item" in _src_seed, "")

print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
sys.exit(0 if F == 0 else 1)