#!/usr/bin/env python3
"""shell/pty 孤儿进程闸门。

守的是 2026-11 实测出来的三类缺口(全部真实复现过,不是假想):

  A. wchat 被 `kill`(SIGTERM)或关终端(SIGHUP)时,前台命令的清理**只**依赖
     "pty master 关闭 → 内核给前台进程组发 SIGHUP"。命令自己 `trap '' HUP` 就整组
     活下来 —— 实测残留 `bash -c trap '' HUP TERM; sleep NNN` + `sleep NNN` 两个,
     ppid=1。用户的意思是"我要退出",命令不该继续跑。
  B. `_ACTIVE["pgid"]` 必须在 pty_run 结束时归零:pgid 会被系统复用,留着会误杀
     下一条命令。
  C. 两套 SIGTERM handler 互相覆盖:MCP 的 `_on_term` 若不带上前台进程组清理,
     谁后装谁生效,前台命令就没人管了。

**不用真 sleep 长命令**:用 `sleep <唯一数字>` 精确匹配进程,测完立刻清。
全部离线,不联网。
"""
import json
import os
import signal
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
BASE = "/tmp/_zsh_orphan_gate"

P = F = 0


def ck(name, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  ok   %s" % name)
    else:
        F += 1
        print("  FAIL %s%s" % (name, ("  ← " + str(detail)) if detail else ""))


def ps_nums(nums):
    p = subprocess.run(["ps", "-Ao", "pid,ppid,pgid,command"], capture_output=True, text=True)
    out = []
    for line in p.stdout.split("\n"):
        if "grep" in line:
            continue
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        cmd = parts[3]
        for n in nums:
            if ("sleep %d" % n) in cmd or cmd.strip().endswith(" %d" % n):
                try:
                    out.append((int(parts[0]), int(parts[1]), int(parts[2]), cmd))
                except ValueError:
                    pass
                break
    return out


def cleanup(nums):
    for pid, *_ in ps_nums(nums):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    time.sleep(0.6)


CHILD = '''
import os, sys, json, time
sys.path.insert(0, %r)
os.environ.update(WCHAT_NO_SETUP="1", AUTO_MODE="full", WORK_ROOT=%r, CT_HOME=%r)
from wchat_py import toolrun
toolrun._seam = lambda: type("S", (), {
    "_shell_approved": lambda self, c, strict=False: True,
    "_auth_think": lambda self, c: ("Y", ""),
    "_bump_auto": lambda self: None,
})()
print("STARTED", flush=True)
out = toolrun._run_tool("run_shell_command", json.dumps({"command": %r, "timeout": 60}))
print("OUT", (out or "")[:120].replace("\\n", " "), flush=True)
''' % (REPO, BASE, os.path.join(BASE, "ct"), "")


def run_cmd(cmd):
    code = CHILD.replace('json.dumps({"command": %r, "timeout": 60})' % "",
                         'json.dumps({"command": %r, "timeout": 60})' % cmd)
    return subprocess.Popen([sys.executable, "-c", code], cwd=BASE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def case_kill_ignoring_hup(num, sig):
    """wchat 收到 sig 时,前台命令 `trap '' HUP TERM` 是否还被清掉。"""
    cleanup([num])
    p = run_cmd("trap '' HUP TERM; sleep %d" % num)
    p.stdout.readline()
    time.sleep(2.5)
    running = ps_nums([num])
    p.send_signal(sig)
    try:
        p.wait(timeout=20)
    except subprocess.TimeoutExpired:
        p.kill(); p.wait()
    time.sleep(3)
    after = ps_nums([num])
    cleanup([num])
    return running, after


print("=== 1. 前台进程组登记/注销(_ACTIVE)===")
from wchat_py import termpty as tp                                   # noqa: E402

ck("初始为 0(没有前台命令)", tp._ACTIVE["pgid"] == 0, tp._ACTIVE["pgid"])
ck("kill_active_group() 在没命令时什么都不做(不误杀)",
   tp.kill_active_group() is False)

print("=== 2. wchat 被 SIGTERM:忽略 HUP 的命令也必须被收掉 ===")
# 这条是本次的核心修复。修之前实测残留 bash + sleep 两个进程(ppid=1)。
os.makedirs(BASE, exist_ok=True)
running, after = case_kill_ignoring_hup(901, signal.SIGTERM)
ck("命令确实跑起来了(否则下面的断言是假绿)", len(running) >= 1, running)
ck("SIGTERM 后没有残留(主动 killpg,不依赖 SIGHUP)",
   not after, [("%d ppid=%d %s" % (x[0], x[1], x[3][:40])) for x in after])

print("=== 3. 对照:SIGKILL 无法捕获,如实记录这条边界 ===")
# SIGKILL 拦不住,只能靠 pty master 关闭 → SIGHUP。忽略 HUP 的命令会活下来 ——
# 这是内核层面的边界,不是我们的 bug。断言它**确实是**这个行为,免得以后误以为已修。
running, after = case_kill_ignoring_hup(902, signal.SIGKILL)
ck("SIGKILL 场景命令确实在跑", len(running) >= 1, running)
ck("SIGKILL + 忽略 HUP ⇒ 仍会残留(已知边界,不是回归)",
   bool(after), "改成 False 说明这条边界被修掉了,应更新本断言")

print("=== 4. 超时路径仍然收干净(改动不许破坏既有行为)===")
for num, cmd, label in ((903, "sleep 903", "单进程"),
                        (904, "sleep 904 | cat", "管道"),
                        (905, "trap '' TERM; sleep 905", "忽略 TERM")):
    cleanup([num])
    code = CHILD.replace('json.dumps({"command": %r, "timeout": 60})' % "",
                         'json.dumps({"command": %r, "timeout": 3})' % cmd)
    p = subprocess.Popen([sys.executable, "-c", code], cwd=BASE,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        out = p.communicate(timeout=150)[0] or ""
    except subprocess.TimeoutExpired:
        p.kill(); out = ""
    time.sleep(2)
    after = ps_nums([num])
    cleanup([num])
    ck("超时收尾:%s 无残留" % label, not after,
       [("%d %s" % (x[0], x[3][:40])) for x in after])

print("=== 4b. pty_run 结束后必须把 pgid 归零(否则会误杀下一条命令)===")
# pgid 会被系统复用。留住旧值 = 下一次 wchat 被 kill 时,处理器可能去打一个
# 已经属于别人的进程组。这条只能靠读模块状态验证(进程行为上看不出来)。
code = '''
import os, sys, json
sys.path.insert(0, %r)
os.environ.update(WCHAT_NO_SETUP="1", AUTO_MODE="full", WORK_ROOT=%r, CT_HOME=%r)
from wchat_py import termpty
termpty.install_signal_cleanup()
print("BEFORE", termpty._ACTIVE["pgid"], flush=True)
termpty.pty_run(["bash", "-c", "echo hi"], timeout=10)
print("AFTER", termpty._ACTIVE["pgid"], flush=True)
''' % (REPO, BASE, os.path.join(BASE, "ct"))
r = subprocess.run([sys.executable, "-c", code], cwd=BASE, capture_output=True, text=True, timeout=120)
ck("跑命令前 pgid 为 0", "BEFORE 0" in r.stdout, r.stdout[:200])
ck("pty_run 返回后 pgid 归零(不留复用隐患)",
   "AFTER 0" in r.stdout, (r.stdout + r.stderr)[:300])

print("=== 5. 命令内部 nohup & 是显式脱离,不清(设计边界)===")# 用户/模型写 `nohup … &` 就是要求它活下来,杀掉反而违背意图。
# 这条断言的作用是**把边界写死**:以后若有人顺手"统一清理",这里会红。
num = 906
cleanup([num])
p = run_cmd("nohup sleep %d >/dev/null 2>&1 & sleep 0.3; echo done" % num)
try:
    p.communicate(timeout=120)
except subprocess.TimeoutExpired:
    p.kill()
time.sleep(2)
after = ps_nums([num])
ck("内部 nohup & ⇒ 留下(预期行为,不当孤儿处理)", bool(after),
   "改成 False 说明有人把它也清了,需确认是否违背用户意图")
cleanup([num])

print("=== 6. handler 必须带上 MCP 清理(两套 handler 不许互相顶掉)===")
# MCP 的 _on_term 若只清 daemon、不杀前台组,那么"最后装的那个"决定了行为。
from wchat_py import mcp_boot                                    # noqa: E402

src = open(os.path.join(REPO, "wchat_py", "mcp_boot.py"), encoding="utf-8").read()
i = src.index("def _on_term")
body = src[i:i + 1200]
ck("MCP 的 _on_term 会先杀前台进程组",
   "kill_active_group" in body, body[:200])
src_t = open(os.path.join(REPO, "wchat_py", "toolrun.py"), encoding="utf-8").read()
ck("toolrun 跑命令前安装清理处理器",
   "install_signal_cleanup" in src_t, "")
ck("install_signal_cleanup 支持串入额外清理(extra)",
   "extra" in open(os.path.join(REPO, "wchat_py", "termpty.py"),
                   encoding="utf-8").read())

cleanup([901, 902, 903, 904, 905, 906])


def py(body):
    """在干净子进程里跑一段片段(带本项目最小环境)。"""
    head = ("import os, sys, json\n"
            "sys.path.insert(0, %r)\n"
            "os.environ['CT_HOME'] = %r\n"
            "os.environ['WCHAT_NO_SETUP'] = '1'\n"
            "from wchat_py import mcp_boot, mcp_bridge as mb\n" % (REPO, os.path.join(BASE, "ct")))
    r = subprocess.run([sys.executable, "-c", head + body], cwd=BASE,
                       capture_output=True, text=True, timeout=180)
    return r.returncode, r.stdout, r.stderr

print("=== 7. 启动不许打印 MCP 横幅(用户明确要求:这是噪音)===")
src = open(os.path.join(REPO, "wchat_py", "mcp_boot.py"), encoding="utf-8").read()
bad = [l for l in src.split("\n")
       if "按需模式" in l and "stderr.write" in l]
ck("惰性分支不再刷『按需模式』横幅(注释里留说明不算)",
   not bad, bad[:2])

print("=== 8. 已删的三类机制不许回来(它们都是治症状)===")
# 根因(连接复用)已在 mcp_bridge.child_env() 修掉。判别标准:如果还需要下面这些,
# 说明根因没修好。每一类都有一次真实事故:
#   · 座位:让第二个会话完全不能用,且 kill -9 后 pid 复用 ⇒ 永久锁死;
#   · 连接数启发式 + 自动重启:4 server 也可能有 5 条正常连接,误判;
#     重启还会杀掉 MCP 托管浏览器里所有已开页面;
#   · 出错后自动重启重试:没上限时变成"MCP 无限重启"(实测踩过)。
_src = open(os.path.join(REPO, "wchat_py", "mcp_boot.py"), encoding="utf-8").read()
for name, why in (("claim_server", "按 server 座位的互斥"),
                  ("_SEATS", "座位状态"),
                  ("_daemon_hygiene", "连接数启发式"),
                  ("_heal_profile_conflict", "出错后自动重启 daemon"),
                  ("_is_profile_conflict", "profile 冲突识别(不再需要自愈)")):
    ck("已移除:%s(%s)" % (name, why), ("def " + name) not in _src and (name + " =") not in _src,
       "源码里还在")
ck("文件头记着『不要加回来』的理由(后来人能被拦住)",
   "不要加回来" in _src, "")

print("=== 9. daemon 生命周期治理保留(与连接复用无关,治的是孤儿)===")
# 用户上一轮明确要求过:daemon 不能变孤儿。这部分与本次根因无关,保留。
for name, why in (("reap_orphan_daemon", "收割 kill -9 留下的孤儿"),
                  ("_install_signal_handlers", "SIGTERM/SIGHUP 也走清理"),
                  ("_claim_daemon", "引用计数(还有人用就不停)")):
    ck("保留:%s(%s)" % (name, why), ("def " + name) in _src, "被误删了")

print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
sys.exit(0 if F == 0 else 1)