#!/usr/bin/env python3
"""项目记忆闸门:存储/预算/安全/注入/工具五组,每组一条"不这么写就会静默坏掉"。

**全离线**:临时 `CT_HOME` + 临时工作根,不联网、不碰真实 `~/.ct`。

钉住的东西(每条对应一个真会踩的坑,见 `wchat_py/memory.py` 文件头):
  0. 项目隔离:同名的两个仓库(`.../a/src` 与 `.../b/src`)必须分开;
     同一仓库的子目录必须**共用**一份(否则在子目录里开会话就"记忆全丢了")。
  1. 预算:索引超行数/超字节 **报错**,不静默截断(静默截断 = 后面条目永不生效且无人知)。
  2. 安全:像凭据的内容拒收(记忆会进上下文发给服务端);`topic` 名不许逃逸出
     `topics/`;单条正文超限报错。
  3. 注入:带**边界声明**("不是用户指令")与"何时查/何时存";无索引时**一个字都不加**
     (新项目每次开都提示"0 条"是噪音);索引里不写与标题重复的 topic 名。
  4. 工具:`append/list/read/rewrite` 四个动作;`read` 既认 topic 名也认标题;
     `rewrite` 超限同样报错;工具走 `_run_tool` 的完整分派(不是自己调函数)。
  5. CLI:`wchat memory list/show/prune/remove`;`prune` 默认只列不删。

用法:`python3 tests/memory_test.py`(可用 MODULE=… 指向变异体)
"""
import importlib
import io
import contextlib
import json
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

MODULE = os.environ.get("MODULE") or "wchat_py.memory"
M = importlib.import_module(MODULE)

P = F = 0


def ck(name, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  ok   %s" % name)
    else:
        F += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


W = tempfile.mkdtemp(prefix="memory_gate_")
os.environ["CT_HOME"] = os.path.join(W, "ct")
os.environ["WCHAT_NO_SETUP"] = "1"
ROOT_A = os.path.join(W, "a", "src")
ROOT_B = os.path.join(W, "b", "src")
os.makedirs(ROOT_A)
os.makedirs(ROOT_B)
# 两个"仓库"各自带 .git:项目键按 git 根取(见 memory._project_root)
for _r in (ROOT_A, ROOT_B):
    os.makedirs(os.path.join(_r, ".git"))
os.environ["WORK_ROOT"] = ROOT_A


def tool(**kw):
    return M.tool_run(kw)


print("=== 0. 项目隔离:同名不同仓分开,同仓子目录共用 ===")
ka, kb = M.project_key(ROOT_A), M.project_key(ROOT_B)
ck("同名的两个 src 仓库键不同", ka != kb, "%r vs %r" % (ka, kb))
ck("项目键保留 basename 便于人认", ka.startswith("src-"), ka)
os.makedirs(os.path.join(ROOT_A, "sub", "deep"))
ck("同一仓库的深层子目录共用一份记忆",
   M.project_key(os.path.join(ROOT_A, "sub", "deep")) == ka,
   M.project_key(os.path.join(ROOT_A, "sub", "deep")))

print("=== 1. 写入与索引格式 ===")
os.environ["WORK_ROOT"] = ROOT_A
r = tool(action="append", kind="pitfall", title="闸门跑 6 分钟,先放后台再拉日志",
         body="现象: 前台跑会超时\n根因: py_hd 单道 ~3min\n做法: 后台跑 + sleep 拉日志")
ck("append 成功", r.startswith("✅"), r[:120])
idx = M._read(M.index_path())
ck("索引里有一行 [pitfall]", "- [pitfall] 闸门跑 6 分钟" in idx, idx)
ck("索引行不带与标题重复的 topic 名(去噪)",
   "(闸门跑-6-分钟" not in idx, idx)
ck("详情落进 topics/", len(M.topics_list()) == 1 and M.topics_list()[0].endswith(".md"),
   repr(M.topics_list()))
_body = M._read(os.path.join(M.topics_dir(), M.topics_list()[0]))
ck("详情文件带 type/modified/title 三个 frontmatter 字段",
   all(k in _body for k in ("type: pitfall", "modified:", "title:")), _body[:120])
r = tool(action="append", kind="fact", title="包管理器用 brew", topic="pm", body="不要 npm -g")
ck("显式 topic 会写进索引行", "(pm)" in M._read(M.index_path()), M._read(M.index_path()))
ck("非法 kind 被拒", tool(action="append", kind="bogus", title="x").startswith("错误"),
   tool(action="append", kind="bogus", title="x")[:80])
ck("空 title 被拒", tool(action="append", kind="fact", title="  ").startswith("错误"))

print("=== 2. 预算:超限报错,不静默截断 ===")
os.environ["MEMORY_LINES"] = "3"
try:
    M.save_index("\n".join("- [fact] 第%d条" % i for i in range(1, 6)))
    ck("索引超行数 → 报错", False, "没报错")
except M.MemoryError_ as e:
    ck("索引超行数 → 报错(而不是静默丢掉后面的条目)", "超过上限" in str(e), str(e)[:80])
ck("报错后文件没被改坏(仍是刚才那 2 条)", len(M.entries()) == 2, repr(M.entries()))
os.environ["MEMORY_BYTES"] = "60"
try:
    M.save_index("- [fact] " + "长" * 40)
    ck("索引超字节 → 报错", False, "没报错")
except M.MemoryError_ as e:
    ck("索引超字节 → 报错", "字节" in str(e), str(e)[:80])
os.environ.pop("MEMORY_BYTES", None)
os.environ.pop("MEMORY_LINES", None)

print("=== 3. 安全:凭据/逃逸/超长 ===")
for bad, label in (
        ("sk-" + "a" * 24, "OpenAI 风格 key"),
        ("ghp_" + "b" * 24, "GitHub token"),
        ("AKIA" + "C" * 16, "AWS key"),
        ("api_key = abcdefgh12345678", "key=value 形态"),
        ("-----BEGIN RSA PRIVATE KEY-----", "私钥头")):
    rr = tool(action="append", kind="fact", title="x", body="配置里写着 %s" % bad)
    ck("拒收%s" % label, rr.startswith("错误") and "凭据" in rr, rr[:100])
try:
    M._safe_join(M.topics_dir(), "../../../etc/passwd")
    ck("topic 逃逸被拒", False, "没拒")
except M.MemoryError_ as e:
    ck("topic 逃逸被拒", "越界" in str(e), str(e)[:60])
ck("topic 名里的 ../ 被归一掉(不会写出目录)",
   M._norm_topic("../../etc/passwd").endswith(".md")
   and "/" not in M._norm_topic("../../etc/passwd"), M._norm_topic("../../etc/passwd"))
rr = tool(action="append", kind="fact", title="x", body="字" * 900)
ck("单条正文超 800 字被拒", rr.startswith("错误") and "800" in rr, rr[:100])

print("=== 4. 注入:边界声明 + 何时查/何时存;无索引时一个字都不加 ===")
inj = M.injection()
ck("注入里有边界声明(上下文不是指令)", "不是用户指令" in inj and "以用户为准" in inj)
ck("注入里说了何时查(并给出可执行的动作:action=read / read_file)",
   "action=read" in inj and "topics" in inj, inj[-260:])
ck("注入里说了何时存", "memory" in inj and "现象" in inj)
ck("注入里有条目", "- [pitfall]" in inj, inj[-200:])
_empty = M.injection(os.path.join(W, "empty_root"))
ck("没有索引的项目注入为空串(不占预算)", _empty == "", repr(_empty[:80]))
os.environ["MEMORY_LINES"] = "1"
_inj2 = M.injection()
ck("索引超上限时注入只截前 N 条并说明还有几条",
   "还有" in _inj2 and "未加载" in _inj2, _inj2[-160:])
os.environ.pop("MEMORY_LINES", None)

print("=== 5. 工具:四个动作走 _run_tool 完整分派 ===")
os.environ["WORK_ROOT"] = ROOT_A
import wchat_py.toolrun as TR  # noqa: E402
_known = TR.known_tools()
ck("memory 已在工具注册表里(spec 有声明)", "memory" in _known, repr(_known[-3:]))
out = TR._run_tool("memory", json.dumps({"action": "list"}))
ck("_run_tool('memory', list) 走通并列出条目", "- [pitfall]" in out, out[:160])
out = TR._run_tool("memory", json.dumps({"action": "read", "topic": "闸门跑 6 分钟,先放后台再拉日志"}))
ck("read 能按**标题**找到详情(索引行不写 topic 名时的兜底)",
   "根因" in out, out[:120])
out = TR._run_tool("memory", json.dumps({"action": "read", "topic": "pm"}))
ck("read 也能按 topic 名找", "npm -g" in out, out[:120])
out = TR._run_tool("memory", json.dumps({"action": "append", "kind": "prefer",
                                        "title": "用户偏好中文注释"}))
ck("append 经工具分派可写", "✅" in out, out[:80])
out = TR._run_tool("memory", json.dumps({"action": "rewrite", "index": "- [fact] 只剩一条"}))
ck("rewrite 可整份重写", "✅" in out and len(M.entries()) == 1, out[:80])
_over = TR._run_tool("memory", json.dumps({"action": "rewrite", "index": "\n".join(
    "- [fact] 第%d条" % i for i in range(1, 200))}))
ck("rewrite 超限也报错(而不是把索引写坏)", _over.startswith("错误") and "超过上限" in _over,
   _over[:120])
ck("超限时索引仍是上一次的 1 条(没写坏)", len(M.entries()) == 1, repr(M.entries()))
out = TR._run_tool("memory", json.dumps({"action": "bogus"}))
ck("未知 action 给出可用取值", out.startswith("错误") and "append" in out, out[:120])
out = TR._run_tool("memory", json.dumps({"action": "append", "kind": "fact",
                                         "title": "key 泄漏", "body": "sk-" + "z" * 24}))
ck("经工具分派同样拒收凭据", out.startswith("错误") and "凭据" in out, out[:100])

print("=== 6. CLI:list / show / prune(默认只列不删)/ remove ===")


def cli(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        rc = M.cmd_memory(list(argv))
    return rc, buf.getvalue()


rc, out = cli("list")
ck("memory list rc=0 且有条目", rc == 0 and "[fact]" in out, out[:160])
rc, out = cli("show")
ck("memory show 打印索引与详情清单", rc == 0 and "索引:" in out, out[:160])
# 造一个 90 天前的孤儿 topic
old = os.path.join(M.topics_dir(), "stale.md")
with open(old, "w", encoding="utf-8") as fh:
    fh.write("---\ntype: fact\nmodified: 2000-01-01T00:00:00\ntitle: 很旧的一条\n---\n\nbody\n")
rc, out = cli("prune")
ck("prune 列出旧条目", rc == 0 and "stale.md" in out, out[:200])
ck("prune 默认**不删**", os.path.isfile(old))
rc, out = cli("prune", "90", "--apply")
ck("prune --apply 才删", not os.path.isfile(old), out[:200])
with open(old, "w", encoding="utf-8") as fh:
    fh.write("---\ntype: fact\nmodified: 2000-01-01T00:00:00\ntitle: 很旧的一条\n---\n\nbody\n")
rc, out = cli("remove", "stale")
ck("memory remove 删掉 topic", rc == 0 and not os.path.isfile(old), out[:160])
rc, out = cli("remove", "没有这个")
ck("remove 不存在的 → rc≠0 并说清", rc != 0 and "没有这个 topic" in out, out[:120])

print("=== 7. -md 显示记忆条数(有则显示,无则不占行)===")
os.environ["CT_HOME"] = os.path.join(W, "ct2")
os.environ["WORK_ROOT"] = ROOT_A
import wchat_py.session as SS  # noqa: E402
from wchat_py.session import Session  # noqa: E402
_j = "\n".join(SS.model_status_lines(Session.from_env({})))
ck("没有记忆时 -md 不显示『记忆:』", "记忆:" not in _j, _j[-120:])
M.tool_run({"action": "append", "kind": "fact", "title": "有一条"})
_j = "\n".join(SS.model_status_lines(Session.from_env({})))
ck("有记忆时 -md 显示条数与目录", "记忆: 1 条" in _j and "memory" in _j, _j[-160:])

print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
shutil.rmtree(W, ignore_errors=True)
sys.exit(0 if F == 0 else 1)

