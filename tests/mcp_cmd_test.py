#!/usr/bin/env python3
"""`wchat mcp add/remove` 的闸门:不动用户文件的排版、不吞注释、不落明文密钥。

守的是"包一层上游 `mcporter config add`"会踩的四个坑(拿真实清单实测过的,
不是假想),以及本项目刚在 tools_spec.json 上吃过的排版 diff:

  1. 丢顶层 `_comment`(随包模板里那段"绝不许出现明文密钥"的告示);
  2. 整份重排(缩进 4→2、server 内部键序打乱)⇒ git blame 全变成别人的名字;
  3. 权限 644(我们按 600 落盘,里面有 ${VAR} 引用);
  4. 不认 `allowedTools`(我们靠它裁工具,不写就把全部工具都暴露出去)。

全部离线:不启动 mcporter,不碰网络。
"""
import io
import json
import os
import shutil
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


def run(argv, ct="", stdin=""):
    """跑一份真实进程,避免 mcp_cmd 的全局状态(env/文件)在本进程里互相污染。"""
    env = dict(os.environ, CT_HOME=ct, WCHAT_NO_SETUP="1")
    r = subprocess.run([sys.executable, "-m", "wchat_py.cli", "mcp"] + argv,
                       cwd=REPO, env=env, capture_output=True, text=True,
                       input=stdin)
    return r.returncode, r.stdout, r.stderr


TEMPLATE = os.path.join(REPO, "configs", "mcporter.example.json")


def fresh():
    d = tempfile.mkdtemp()
    shutil.copyfile(TEMPLATE, os.path.join(d, "mcporter.json"))
    os.chmod(os.path.join(d, "mcporter.json"), 0o600)
    return d


def read(d):
    with open(os.path.join(d, "mcporter.json"), encoding="utf-8") as f:
        return f.read()


print("=== 1. 只插入一段:注释/缩进/键序/末尾空行都不许动 ===")
d = fresh()
before = read(d)
before_nl = len(before) - len(before.rstrip("\n"))
rc, out, err = run(["add", "demo", "--command", "npx", "--arg", "-y",
                    "--arg", "pkg", "-y"], d)
after = read(d)
ck("add 成功", rc == 0 and "已写入" in out, (out + err)[:200])
ck("顶层 _comment 还在(上游会整段吞掉)",
   "_comment" in after and "绝不许出现明文密钥" in after, after[:200])
ck("顶层键序不变", list(json.loads(after).keys()) == ["_comment", "imports", "mcpServers"],
   repr(list(json.loads(after).keys())))
ck("缩进仍是 4 空格", '\n    "_comment"' in after, repr(after[:30]))
ck("末尾空行数不变(照抄原文件的 \\n\\n)",
   len(after) - len(after.rstrip("\n")) == before_nl, repr(after[-6:]))
diff_lines = [l for l in subprocess.run(
    ["diff", TEMPLATE, os.path.join(d, "mcporter.json")],
    capture_output=True, text=True).stdout.split("\n") if l.startswith(("<", ">"))]
ck("改动只有新增(+1 个逗号改造),没有整片重排",
   len([l for l in diff_lines if l.startswith(">")]) <= 8 and
   not any(l.startswith("<") for l in diff_lines), repr(diff_lines))
ck("原 chrome 配置一个字节没动",
   json.loads(after)["mcpServers"]["chrome"] ==
   json.loads(before)["mcpServers"]["chrome"])

print("=== 2. 权限:写回仍是 600(清单里有 ${VAR} 引用)===")
mode = oct(os.stat(os.path.join(d, "mcporter.json")).st_mode & 0o777)
ck("清单 600", mode == "0o600", mode)
ck("留了 .bak 备份", os.path.isfile(os.path.join(d, "mcporter.json.bak")))

print("=== 3. allowedTools 要能写(我们靠它裁工具)===")
d = fresh()
rc, out, err = run(["add", "tuned", "--url", "https://x.example/mcp",
                    "--allow-tools", "a,b", "-y"], d)
e = json.loads(read(d))["mcpServers"].get("tuned") or {}
ck("allowedTools 写进去了(上游 config add 没这个参数)",
   e.get("allowedTools") == ["a", "b"], json.dumps(e, ensure_ascii=False))
ck("http 形态用 baseUrl(与 mcporter 自己的产物同名字段)", e.get("baseUrl") == "https://x.example/mcp",
   json.dumps(e, ensure_ascii=False))

print("=== 4. 明文密钥必须拒,而且**什么都不写** ===")
d = fresh()
before = read(d)
for leak in ("sk-abcdefghijklmnopqrstuv", "ghp_" + "a" * 24,
             "-----BEGIN RSA PRIVATE KEY-----"):
    rc, out, err = run(["add", "leaky", "--command", "npx",
                        "--env", "TOKEN=" + leak, "-y"], d)
    ck("拒绝 %s…" % leak[:12], rc != 0 and "真实密钥" in out, (out + err)[:160])
ck("拒绝后清单一个字都没变", read(d) == before)
d = fresh()
rc, out, err = run(["add", "good1", "--command", "uvx",
                    "--env", "TOKEN=${GITHUB_TOKEN}", "-y"], d)
ck("${VAR} 占位放行", rc == 0 and "已写入" in out, (out + err)[:160])
ck("占位原样写入", json.loads(read(d))["mcpServers"]["good1"]["env"]["TOKEN"]
   == "${GITHUB_TOKEN}", read(d)[:0])

print("=== 5. 重名不静默覆盖(那份配置里可能有手写的 env 引用)===")
d = fresh()
run(["add", "dup", "--command", "npx", "-y"], d)
before = read(d)
rc, out, err = run(["add", "dup", "--command", "uvx", "-y"], d)
ck("重名被拒", rc != 0 and "已存在同名" in out, (out + err)[:160])
ck("重名时文件没变", read(d) == before)
ck("给出明确下一步(先 remove 再加)", "remove" in out, out[:200])

print("=== 6. 坏 JSON 不许被'顺手修好' ===")
d = tempfile.mkdtemp()
p = os.path.join(d, "mcporter.json")
with open(p, "w", encoding="utf-8") as f:
    f.write('{"mcpServers": {"x": },}')
bad_before = open(p, encoding="utf-8").read()
rc, out, err = run(["add", "y", "--command", "npx", "-y"], d)
ck("坏 JSON 明确报错而不是覆盖", rc != 0 and "读不了" in out, (out + err)[:200])
ck("坏文件原样保留(用户的字面内容不丢)", open(p, encoding="utf-8").read() == bad_before)

print("=== 7. dry-run 只看不写 / 非法名拒绝 ===")
d = fresh()
before = read(d)
rc, out, err = run(["add", "d1", "--url", "https://x.example/mcp", "--dry-run"], d)
ck("dry-run 说没写", "没有写入" in out, out[:200])
ck("dry-run 后文件没变", read(d) == before)
rc, out, err = run(["add", "bad name", "--command", "npx", "-y"], d)
ck("非法 server 名被拒(带空格会毁掉 mcp_call 的选择器)", rc != 0, (out + err)[:160])

print("=== 8. remove:先备份再删,并回显被删内容 ===")
d = fresh()
run(["add", "gone", "--command", "npx", "--description", "要删的", "-y"], d)
rc, out, err = run(["remove", "gone"], d)
ck("remove 成功", rc == 0 and "已从清单移除" in out, (out + err)[:200])
ck("真的删了", "gone" not in json.loads(read(d))["mcpServers"])
ck("回显被删内容(便于手动恢复)", "要删的" in out, out[:300])
ck("remove 也留备份", os.path.isfile(os.path.join(d, "mcporter.json.bak")))
rc, out, err = run(["remove", "没有这个"], d)
ck("删不存在的:说清现状而不是静默成功", rc != 0 and "没有" in out, (out + err)[:200])

print("=== 9. 非交互无参数:给命令行而不是卡住等输入 ===")
d = fresh()
rc, out, err = run(["add"], d, stdin="")
ck("非交互时给出一行式示例(不阻塞)", rc != 0 and "--command npx" in out, (out + err)[:200])

print("=== 10. 加完就记住'这机器要用 MCP'(下次不用带 -mcp)===")
d = fresh()
run(["add", "mem1", "--command", "npx", "-y"], d)
ck("写下 .enabled 标记", os.path.isfile(os.path.join(d, "mcp", ".enabled")))
code = ("import os, sys\n"
        "sys.path.insert(0, %r)\n"
        "os.environ['CT_HOME'] = %r\n"
        "os.environ['WCHAT_NO_SETUP'] = '1'\n"
        "from wchat_py import mcp_boot\n"
        "print('ARMED=', mcp_boot._lazy_armed())\n" % (REPO, d))
r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
ck("下次启动进入按需模式(不用再记 -mcp)", "ARMED= True" in r.stdout,
   (r.stdout + r.stderr)[:200])

print("=== 11. --json:从别家程序/README 拷来的清单要能直接用 ===")
# MCP 生态里最常见的一步就是"从 README 复制一段 JSON"。不支持它,用户就得手动翻译成
# 命令行参数 —— 而 `args` 数组、`env` 字典用 key=value 根本表达不了。
d = fresh()
rc, out, err = run(["add", "--json",
                    '{"mcpServers":{"p1":{"command":"npx","args":["-y","srv"],'
                    '"env":{"K":"${KEY}"}},"n1":{"url":"https://mcp.example/mcp"}}}',
                    "-y"], d)
srvs = json.loads(read(d))["mcpServers"]
ck("整份清单:mcpServers 里两条都进来", rc == 0 and "p1" in srvs and "n1" in srvs, (out + err)[:300])
ck("env 字典原样保留(命令行 key=value 表达不了嵌套)",
   srvs.get("p1", {}).get("env") == {"K": "${KEY}"}, json.dumps(srvs.get("p1"), ensure_ascii=False))
ck("url 归类到 baseUrl(mcporter 认的字段名)",
   srvs.get("n1", {}).get("baseUrl") == "https://mcp.example/mcp",
   json.dumps(srvs.get("n1"), ensure_ascii=False))

d = fresh()
rc, out, err = run(["add", "--json", '{"name":"withname","command":"uvx"}', "-y"], d)
ck("单条带 name", "withname" in json.loads(read(d))["mcpServers"], (out + err)[:200])
d = fresh()
rc, out, err = run(["add", "byname", "--json", '{"command":"npx"}', "-y"], d)
ck("单条不带 name:用位置参数当名字",
   "byname" in json.loads(read(d))["mcpServers"], (out + err)[:200])

print("=== 12. 粘 JSON 当位置参数:必须立刻说清楚,绝不许卡住等输入 ===")
# 实测缺陷:第一版把该 JSON 当 server 名,然后掉进交互向导等 stdin —— 真终端里就是
# "敲完回车什么都没发生,一直挂着"。**必须用真 pty 测**:用 DEVNULL 时 stdin 不是 tty,
# 走的是"非交互"分支,根本碰不到会挂的那条路(变异验证时就是这么漏掉的)。
d = fresh()
before = read(d)
code = ("import os, pty, sys, tempfile\n"
        "m, s = pty.openpty()\n"
        "import subprocess\n"
        "r = subprocess.run([sys.executable, '-m', 'wchat_py.cli', 'mcp', 'add',\n"
        "                    '{\"mcpServers\":{\"x\":{}}}'], cwd=%r, timeout=20,\n"
        "                   stdin=s, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)\n"
        "out = r.stdout\n"
        "print('RC=', r.returncode)\n"
        "print('HAS_HINT=', '--json' in out)\n"
        "print('SAYS_JSON=', '是一段 JSON' in out)\n" % REPO)
try:
    r = subprocess.run([sys.executable, "-c", code],
                       env=dict(os.environ, CT_HOME=d, WCHAT_NO_SETUP="1"),
                       capture_output=True, text=True, timeout=90)
    ok = r.stdout
except subprocess.TimeoutExpired:
    ok = ""
ck("粘 JSON 不卡死(真 pty 下 20s 内返回)", "RC=" in ok, (ok or "超时")[:300])
ck("非零退出而不是当成名字写了进去", "RC= 2" in ok, ok[:200])
ck("明确指出『这是一段 JSON』", "SAYS_JSON= True" in ok, ok[:300])
ck("给出 --json / stdin / 一行式三条出路", "HAS_HINT= True" in ok, ok[:200])
ck("没写任何东西", read(d) == before)

print("=== 13. JSON 路径的校验同样生效(密钥/坏 JSON/空条目) ===")
d = fresh()
rc, out, err = run(["add", "--json",
                    '{"name":"leak","command":"npx","env":{"K":"ghp_%s"}}' % ("a" * 24),
                    "-y"], d)
ck("JSON 里的明文密钥同样被拒", rc != 0 and "真实密钥" in out, (out + err)[:200])
rc, out, err = run(["add", "--json", '{"name":', "-y"], d)
ck("坏 JSON 明确报错", rc != 0 and "不是合法 JSON" in out, (out + err)[:200])
rc, out, err = run(["add", "--json", '{"name":"empty"}', "-y"], d)
ck("既无 command 又无 url 的条目被拒(装上也是死的)",
   rc != 0 and "连不上" in out, (out + err)[:200])

print("=== 14. Ctrl+C 必须安静退出,不许吐栈 ===")
# 真实反馈:在 `wchat mcp add` 的向导里按 Ctrl+C,用户看到的是一段
# KeyboardInterrupt traceback(而且因为 `_ask` 在 except 里重新 raise,
# 还多出 "During handling of the above exception…" 两层链)。用户的心理预期是
# "算了,不弄了",不是"程序崩了"。约定与 -st 中断一致:rc=130。
#
# **三条测试纪律**(都踩过,写在这里免得下一个人重踩):
#   · 必须用真 pty —— Ctrl+C 是终端行为,SIGINT 只对前台进程组生效,DEVNULL 下
#     isatty 为假会走"非交互"分支,得到的是假绿(变异验证时被逮到过);
#   · 直接调 `mcp_cmd.main`,不 exec 完整 `wchat` 入口 —— 入口会先跑 bootstrap
#     (可能建 venv / 装依赖),在闸门环境里偶发挂起,曾把整道闸门拖到 90s 超时;
#   · 子进程自带 `signal.alarm` 自保,父进程用 WNOHANG 轮询到点就 kill ——
#     "测试挂住"必须是**一条红断言**,不能是整道闸门超时(那样连结论行都没有)。
d = fresh()
before = read(d)
code = (
    "import os, pty, select, signal, sys, time\n"
    "sys.path.insert(0, %r)\n"
    "pid, fd = pty.fork()\n"
    "if pid == 0:\n"
    "    signal.alarm(20)          # 自保:无论如何 20s 内必须死\n"
    "    os.environ['CT_HOME'] = %r\n"
    "    os.environ['WCHAT_NO_SETUP'] = '1'\n"
    "    from wchat_py import mcp_cmd\n"
    "    sys.exit(mcp_cmd.main(['add']))\n"
    "buf = b''\n"
    "end = time.time() + 3\n"
    "while time.time() < end:\n"
    "    r, _, _ = select.select([fd], [], [], 0.2)\n"
    "    if r:\n"
    "        try: buf += os.read(fd, 8192)\n"
    "        except OSError: break\n"
    "os.write(fd, b'\\x03')\n"
    "st = None\n"
    "end2 = time.time() + 12\n"
    "while time.time() < end2:\n"
    "    p, w = os.waitpid(pid, os.WNOHANG)\n"
    "    if p:\n"
    "        st = w\n"
    "        break\n"
    "    r, _, _ = select.select([fd], [], [], 0.2)\n"
    "    if r:\n"
    "        try: buf += os.read(fd, 8192)\n"
    "        except OSError: pass\n"
    "if st is None:\n"
    "    try: os.kill(pid, 9)\n"
    "    except OSError: pass\n"
    "    try: os.waitpid(pid, 0)\n"
    "    except OSError: pass\n"
    "    print('HUNG= True')\n"
    "else:\n"
    "    print('HUNG= False')\n"
    "    print('RC=', st >> 8)\n"
    "out = buf.decode('utf-8', 'replace')\n"
    "print('HAS_TRACEBACK=', 'Traceback' in out)\n"
    "print('HAS_CHAIN=', 'During handling' in out)\n"
    "print('HAS_CANCEL=', '已取消' in out)\n" % (REPO, d))
r = subprocess.run([sys.executable, "-c", code], cwd=REPO,
                   env=dict(os.environ, CT_HOME=d, WCHAT_NO_SETUP="1"),
                   capture_output=True, text=True, timeout=60)
ck("Ctrl+C 之后进程会退出(挂住 = 失败,不是超时)",
   "HUNG= False" in r.stdout, (r.stdout + r.stderr)[:400])
ck("Ctrl+C 退出码 130(与 -st 中断同约定)", "RC= 130" in r.stdout, (r.stdout + r.stderr)[:300])
ck("Ctrl+C 不吐 traceback", "HAS_TRACEBACK= False" in r.stdout, r.stdout[:200])
ck("也不带 'During handling…' 那层异常链", "HAS_CHAIN= False" in r.stdout, r.stdout[:200])
ck("说一句人话就退出", "HAS_CANCEL= True" in r.stdout, r.stdout[:200])
ck("中断后清单一个字没改", read(d) == before)

print("=== 15. 子命令/帮助不装 MCP(横幅与 .enabled 副作用都不许有) ===")
# install() 在 cli.main 之前跑,过去对 `wchat mcp add` 也照常登记工具、打横幅,
# 探测成功还会写下 .enabled —— 用户在 add 里 Ctrl+C 取消,却被记成"要用 MCP"。
d = fresh()
for _args, _label in ((["mcp"], "wchat mcp"), (["skills"], "wchat skills"),
                      (["memory"], "wchat memory"), (["--help"], "wchat --help")):
    _c = ("import os, sys\n"
          "sys.path.insert(0, %r)\n"
          "os.environ['CT_HOME'] = %r\n"
          "os.environ['WCHAT_NO_SETUP'] = '1'\n"
          "os.environ['MCP_LAZY'] = '1'\n"
          "from wchat_py import mcp_boot\n"
          "print('SKIP=', mcp_boot._skip_mcp(['wchat'] + %r))\n"
          "mcp_boot.install(['wchat'] + %r)\n"
          "print('ENABLE=', os.environ.get('MCP_ENABLE'))\n"
          % (REPO, d, _args, _args))
    _r = subprocess.run([sys.executable, "-c", _c], capture_output=True, text=True)
    ck("%s 跳过 MCP 安装(不登记工具/不打横幅)" % _label,
       "SKIP= True" in _r.stdout and "ENABLE= None" in _r.stdout, _r.stdout[:200])
ck("⇒ 因此也不会由子命令写下 .enabled 标记",
   not os.path.isfile(os.path.join(d, "mcp", ".enabled")))
_c = ("import os, sys\n"
      "sys.path.insert(0, %r)\n"
      "os.environ['CT_HOME'] = %r\n"
      "from wchat_py import mcp_boot\n"
      "print('SKIP=', mcp_boot._skip_mcp(['wchat']))\n"
      "print('SKIP2=', mcp_boot._skip_mcp(['wchat', '-mcp']))\n"
      "print('SKIP3=', mcp_boot._skip_mcp(['wchat', 'repl']))\n" % (REPO, d))
_r = subprocess.run([sys.executable, "-c", _c], capture_output=True, text=True)
ck("对话入口(wchat / wchat -mcp / wchat repl)不被跳过",
   "SKIP= False" in _r.stdout and "SKIP2= False" in _r.stdout
   and "SKIP3= False" in _r.stdout, _r.stdout[:200])

print("=== 16. mcp_* 必须在**三处**同时登记(执行/界面/校验)===")
# 2026-11 用户实测:能跑,但界面打 `⏺ mcp_tools · ⚠ 未知工具名,不执行`,
# 模型据此去猜别的名字,连开个网页都试了五次。只登记执行层与 schema 层是不够的。
d = fresh()
code = ("import sys\n"
        "sys.path.insert(0, %r)\n"
        "from wchat_py import mcp_boot, toolrun, tools\n"
        "mcp_boot.install(['wchat'])\n"
        "print('KNOWN=', [n for n in toolrun.known_tools() if n.startswith('mcp_')])\n"
        "print('BANNER=', toolrun._banner_text('mcp_tools', '{}').replace(chr(10), ' '))\n"
        "mcp_boot.turn_off()\n"
        "print('AFTER_OFF=', [n for n in toolrun.known_tools() if n.startswith('mcp_')])\n" % REPO)
r = subprocess.run([sys.executable, "-c", code],
                   env=dict(os.environ, CT_HOME=d, WCHAT_NO_SETUP="1", MCP_LAZY="1"),
                   capture_output=True, text=True, timeout=90)
ck("known_tools 认识 mcp_*(校验层同源)",
   "KNOWN= ['mcp_tools', 'mcp_schema', 'mcp_call']" in r.stdout, (r.stdout + r.stderr)[:400])
ck("横幅不再谎报『未知工具名』", "未知工具名" not in r.stdout, r.stdout[:400])
ck("-mcp=off 之后校验层也撤掉(不留下'认识但不可用')",
   "AFTER_OFF= []" in r.stdout, r.stdout[:400])

print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
sys.exit(0 if F == 0 else 1)