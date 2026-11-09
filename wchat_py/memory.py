#!/usr/bin/env python3
"""memory —— 项目记忆(跨会话的"这个项目的坑/重点/怪癖")。

## 为什么需要它(以及为什么不是技能)

技能是**按需读的多步流程**;而"这个仓库跑闸门要 6 分钟,先放后台"这种是**一句话事实**,
每轮都该知道,做成技能等于要求模型先想起来去读它 —— 那正是技能最不可靠的地方
(`wchat-skill-writing` 里写着:能被"忘记读"影响的后果决定该放哪)。

不放进 `<work_root>/.wchat/` 而放 `~/.ct/memory/<key>/` 的原因:
  · 不脏用户仓库(多数人第一件事就是把它 gitignore,那就退化成"看不见的随手笔记");
  · 与 `~/.ct/skills` 同源,已经有"程序内写盘 + 审计"的先例,不需要为它给硬墙开洞;
  · 按工作根分目录,不同项目互不污染。

## 结构(照 Claude Code 的形,预算按我们的量级压)

    ~/.ct/memory/<basename>-<sha1(work_root)[:8]>/
    ├── MEMORY.md        索引,一行一条,**每次会话注入**
    └── topics/*.md      详情,**不注入**,模型按需 read_file

`MEMORY.md` 是**唯一会被反复重发**的东西(它在 system 里,每个请求都带上),
所以它有硬上限:`MEMORY_LINES`(默认 60 行)与 `MEMORY_BYTES`(默认 4KB)。
超了**报错要求先合并**,不静默截断 —— 静默截断的后果是"后面的条目永远不生效,
而没人知道",那比没有记忆更坏。

## 写入为什么必须是一个工具,而不是 `write_file`

`~/.ct/memory` 在工作根之外:auto 档 `write_file` 会被直接拒,非 auto 档每次弹确认。
"存一条记忆要用户点一次 y"= 没人用。所以照 `save_skill` 的先例给一个 `memory` 工具,
在**进程内**写,并且自己把守这三条:
  · 路径只能在 `<memory_dir>/` 下(归一后前缀校验,与 pathrule 同口径);
  · 单条 ≤ `MEMORY_ITEM_MAX`(默认 800 字);
  · 索引硬上限,超了报错让人/模型先合并;
  · 拒收像凭据的内容(它会进上下文发给服务端)。

## 边界声明(注入时必须带)

记忆是**上下文,不是指令**:注入串里明确写"与用户当前要求冲突时以用户为准"。
这一句不是客套 —— 模型自己写的东西下轮自己读,没有这条边界就会自我强化,
把一次误判固化成规矩(Claude Code 的文档也把 memory 划在"context, not enforced
configuration"这一侧)。
"""

from __future__ import annotations

import datetime
import hashlib
import os
import re
import sys

MAX_ITEM_CHARS = 800          # 单条正文上限
DEFAULT_LINES = 60            # 索引行数上限
DEFAULT_BYTES = 4096          # 索引字节上限(≈1.3k tokens,每请求都要重发)

# 注入段标记:**只写前缀**(条数会变)。`session.save_history` 存盘时按它剥离 ——
# 记忆索引是**每次会话从磁盘重建**的实时状态,跟着历史存档走只会留下一份过期的,
# 而加载历史时 system 本来就会被整体重建(见 repl 的"始终刷新 system")。
MEMORY_MARK = "## 项目记忆"

# 记忆类型:照 Claude 的四类,但换成对我们有用的说法。
#   pitfall  —— 踩过的坑(有现象/根因/做法)
#   fact     —— 这个项目的固定事实(命令、路径约定、怪癖)
#   prefer   —— 用户的偏好与纠正
#   ref      —— 去哪里找信息(文档、看板、脚本)
KINDS = ("pitfall", "fact", "prefer", "ref")
_KIND_LABEL = {"pitfall": "坑", "fact": "事实", "prefer": "偏好", "ref": "入口"}

_LINE_RE = re.compile(r"^-\s*\[(?P<kind>[a-z]+)\]\s*(?P<title>[^(（]+?)\s*(?:[(（](?P<more>[^)）]*)[)）])?\s*$")

# 像凭据的东西:记忆会被发给服务端,这一类必须在这里就拦住(而不是指望事后发现)。
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\b(api[_-]?key|secret|token|password|passwd|credential)\b\s*[:=]\s*\S{8,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


class MemoryError_(Exception):
    """可预期的业务失败:话已经是给人/模型看的,原样回传。"""


# ─────────────────────────────────────────────────────────── 位置
def ct_home():
    return os.environ.get("CT_HOME") or os.path.join(os.environ.get("HOME") or "", ".ct")


def work_root():
    """工作根。与硬墙/回滚点同一个来源(`WORK_ROOT`),不另立一套。"""
    return os.path.realpath(os.environ.get("WORK_ROOT") or os.getcwd())


def memory_home():
    """记忆根目录。`MEMORY_DIR` 可覆盖(闸门与多环境用)。"""
    return os.environ.get("MEMORY_DIR") or os.path.join(ct_home(), "memory")


def _project_root(root):
    """项目根:**最近的 git 仓库根**;不在仓库里就用工作根本身。

    为什么要往上找 git 根:否则 `cd 子目录` 开一次会话就"记忆全丢了" —— 而人经常
    就在子目录里开工。这与 Claude Code 的口径一致("同一仓库的 worktree 与子目录
    共用一份 auto memory")。找不到 `.git` 就老实用工作根,不猜。
    """
    r = os.path.realpath(root or work_root())
    cur = r
    for _ in range(64):
        if os.path.exists(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return r


def project_key(root=None):
    """项目键 = `<basename>-<sha1(项目根真实路径)[:8]>`。

    为什么带哈希:两个都叫 `src` 的项目必须分开(按名字分绝对会撞),而路径本身太长
    不适合当目录名。为什么还留 basename:人在 `~/.ct/memory/` 里一眼能认出是哪个项目。
    """
    r = _project_root(root)
    h = hashlib.sha1(r.encode("utf-8")).hexdigest()[:8]
    base = os.path.basename(r.rstrip("/")) or "root"
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-")[:40] or "root"
    return "%s-%s" % (base, h)


def project_dir(root=None):
    # realpath:macOS 上 `/var` 是 `/private/var` 的 firmlink,同一条输出里出现两种
    # 写法会让人以为"生成了两个目录"。
    return os.path.realpath(os.path.join(memory_home(), project_key(root)))


def index_path(root=None):
    return os.path.join(project_dir(root), "MEMORY.md")


def topics_dir(root=None):
    return os.path.join(project_dir(root), "topics")


def _limits():
    def _i(name, default):
        try:
            v = int(os.environ.get(name, "") or default)
            return v if v > 0 else default
        except ValueError:
            return default
    return _i("MEMORY_LINES", DEFAULT_LINES), _i("MEMORY_BYTES", DEFAULT_BYTES)


# ─────────────────────────────────────────────────────────── 读
def _read(path):
    try:
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


def parse_index(text):
    """把索引正文解析成条目列表(保序)。解析不了的行走 `raw` 旁路,不丢。"""
    items = []
    for ln in (text or "").split("\n"):
        s = ln.strip()
        if not s:
            continue
        m = _LINE_RE.match(s)
        if not m:
            items.append({"kind": "", "title": s, "topic": "", "raw": s})
            continue
        items.append({
            "kind": m.group("kind"),
            "title": m.group("title").strip(),
            "topic": (m.group("more") or "").strip(),
            "raw": s,
        })
    return items


def entries(root=None):
    return parse_index(_read(index_path(root)))


def index_stats(root=None):
    text = _read(index_path(root))
    lines = [x for x in text.split("\n") if x.strip()]
    return {"count": len(lines), "lines": len(lines),
            "bytes": len(text.encode("utf-8")),
            "max_lines": _limits()[0], "max_bytes": _limits()[1]}


def render_line(kind, title, topic=""):
    k = kind if kind in KINDS else "fact"
    t = " ".join(str(title or "").split())
    return "- [%s] %s%s" % (k, t, ("(%s)" % topic) if topic else "")


# ─────────────────────────────────────────────────────────── 校验
def _check_secret(text):
    for pat in _SECRET_PATTERNS:
        if pat.search(text or ""):
            raise MemoryError_(
                "这段内容看起来含凭据(密钥/token/密码/私钥)。记忆会被放进上下文、"
                "随请求发给服务端,所以这里直接拒。请把值放进 ~/.ct/config,"
                "记忆里只写\"key 在 config 里\"这类指路信息。")


def _norm_topic(topic):
    """topic 只能是 `topics/` 下的一个安全文件名。"""
    t = str(topic or "").strip()
    if not t:
        return ""
    # 允许中日韩:中文标题自动生成的 topic 名如果只留 ASCII,会退化成 "6.md"
    # (实测:标题「闸门跑 6 分钟…」→ 只剩 6),索引里那行 `(6)` 谁也看不懂。
    # 路径安全不靠这个正则,靠 `_safe_join` 的前缀校验。
    t = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff._-]+", "-", t).strip("-.")[:60]
    if not t:
        raise MemoryError_("topic 名字归一后是空的,请换一个")
    return t if t.endswith(".md") else t + ".md"


def _slug(text):
    """标题 → topic 名(不含 .md)。自动落盘与"要不要在索引里写出来"共用同一份。"""
    return (re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "-", str(text or "")).strip("-")[:40]
            or "note")


def _safe_join(base, name):
    """前缀校验:归一后的路径必须仍在 base 下(防 `../`、防绝对路径)。"""
    p = os.path.realpath(os.path.join(base, name))
    rb = os.path.realpath(base)
    if not (p == rb or p.startswith(rb + os.sep)):
        raise MemoryError_("路径越界: %r 不在 %s 下" % (name, base))
    return p


# ─────────────────────────────────────────────────────────── 写
def _ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def _write_index(text, root=None):
    """原子写索引。**超上限直接报错**,不截断(见文件头)。"""
    max_lines, max_bytes = _limits()
    lines = [ln for ln in (text or "").split("\n") if ln.strip()]
    if len(lines) > max_lines:
        raise MemoryError_(
            "索引会变成 %d 行,超过上限 %d 行。请先合并/淘汰旧条目再写"
            "(索引在 system 里,每个请求都要重发一遍;超了不报错的话,"
            "后面的条目永远不生效而没人知道)。" % (len(lines), max_lines))
    data = ("\n".join(lines) + "\n") if lines else ""
    nbytes = len(data.encode("utf-8"))
    if nbytes > max_bytes:
        raise MemoryError_(
            "索引会变成 %d 字节,超过上限 %d 字节。请把详情挪进 topics/ 再写"
            "(索引只放一行摘要)。" % (nbytes, max_bytes))
    p = index_path(root)
    _ensure_dir(os.path.dirname(p))
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(data)
    os.replace(tmp, p)
    return p


def save_index(text, root=None):
    return _write_index(text, root)


def add(kind, title, body="", topic="", root=None):
    """追加一条记忆。返回 (索引路径, topic 路径或空串)。

    `body` 非空就写进 `topics/<topic>.md`(并在索引行里记下 topic 名),
    这样索引永远只有一行摘要,详情不占常驻预算。
    """
    kind = (kind or "fact").strip().lower()
    if kind not in KINDS:
        raise MemoryError_("kind 只能是 %s 之一(收到 %r)"
                           % ("/".join(KINDS), kind))
    t = " ".join(str(title or "").split())
    if not t:
        raise MemoryError_("title 不能为空(索引里一条就靠它一眼认出)")
    if len(t) > 120:
        raise MemoryError_("title 太长(%d 字),索引一行放不下;请压到 120 字内,"
                           "详情写进 body" % len(t))
    if body and len(body) > MAX_ITEM_CHARS:
        raise MemoryError_("单条正文 %d 字超过上限 %d 字;请拆成两条,"
                           "或只留「现象/根因/做法」三行" % (len(body), MAX_ITEM_CHARS))
    _check_secret(t + "\n" + (body or ""))

    topic_path = ""
    tname = _norm_topic(topic)
    if body:
        if not tname:
            # 没给 topic 名就用标题自动生成一个(可读、可推断)
            tname = _norm_topic(_slug(t))
            stem = tname[:-3]
            n = 1
            while os.path.exists(os.path.join(topics_dir(root), tname)):
                n += 1
                tname = "%s-%d.md" % (stem, n)
        td = topics_dir(root)
        _ensure_dir(td)
        topic_path = _safe_join(td, tname)
        now = datetime.datetime.now().isoformat(timespec="seconds")
        with open(topic_path, "w", encoding="utf-8") as fh:
            # `title:` 是给 `action=read` 用的:索引行可能不写 topic 名(名字就是从标题
            # 生成的,写出来是 `- [pitfall] 闸门先放后台(闸门先放后台)` 这种废话),
            # 那时模型只能拿标题来要详情 —— 得有东西能对得上。
            fh.write("---\ntype: %s\nmodified: %s\ntitle: %s\n---\n\n%s\n"
                     % (kind, now, t, body.strip()))
    elif tname:
        topic_path = _safe_join(topics_dir(root), tname)

    # 索引行只在 topic 名**不等于**标题 slug 时才写出名字:自动生成的名字写出来
    # 只是把标题重复一遍,而索引每一行都要在每个请求里重发。
    _ts = tname[:-3] if tname else ""
    line = render_line(kind, t, "" if _ts == _slug(t) else _ts)
    cur = _read(index_path(root)).rstrip("\n")
    new = (cur + "\n" if cur else "") + line
    p = _write_index(new, root)
    return p, topic_path


def _find_by_title(title, root=None):
    """按标题找 topic 文件:先精确、再子串。找不到返回空串。"""
    t = str(title or "").strip()
    if not t:
        return ""
    cands = []
    for n in topics_list(root):
        p = os.path.join(topics_dir(root), n)
        m = re.search(r"^title:\s*(.+)$", _read(p)[:600], re.M)
        ft = (m.group(1).strip() if m else "")
        if ft == t:
            return p
        if ft and (t in ft or ft in t):
            cands.append(p)
    return cands[0] if len(cands) == 1 else ""


def topics_list(root=None):
    d = topics_dir(root)
    try:
        return sorted(x for x in os.listdir(d) if x.endswith(".md"))
    except OSError:
        return []


def show(root=None):
    """返回 (索引路径, 索引正文, [(topic 文件, 字节数)])。"""
    return index_path(root), _read(index_path(root)), [
        (n, os.path.getsize(os.path.join(topics_dir(root), n)))
        for n in topics_list(root)]


def prune(days=90, apply=False, root=None):
    """列出(或删除)`modified`(缺失则用 mtime)早于 N 天且索引里没引用的 topic。

    **默认只列不删**:记忆是模型写的,误判要能留下痕迹给人看;真删是一次明确动作。
    """
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 90
    cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
    referenced = {e["topic"] for e in entries(root) if e.get("topic")}
    out = []
    for n in topics_list(root):
        p = os.path.join(topics_dir(root), n)
        mod = None
        head = _read(p)[:400]
        m = re.search(r"^modified:\s*(\S+)", head, re.M)
        if m:
            try:
                mod = datetime.datetime.fromisoformat(m.group(1))
            except ValueError:
                mod = None
        if mod is None:
            try:
                mod = datetime.datetime.fromtimestamp(os.path.getmtime(p))
            except OSError:
                continue
        if mod >= cutoff:
            continue
        stale = n[:-3] not in referenced and n not in referenced
        out.append((n, mod.date().isoformat(), stale))
        if apply:
            try:
                os.remove(p)
            except OSError:
                pass
    return out


# ─────────────────────────────────────────────────────────── 注入
def injection(root=None):
    """system 里那一段。没有索引就返回空串(不占一行预算)。"""
    text = _read(index_path(root)).strip()
    if not text:
        return ""
    max_lines, max_bytes = _limits()
    lines = [ln for ln in text.split("\n") if ln.strip()]
    # 防御:文件是外部可改的(人也会手写),超上限时**只截**这里、不动文件,
    # 并在串里说明 —— 模型看到"还有 N 条没加载"才知道该去 topics/ 或让人清理。
    more = 0
    if len(lines) > max_lines:
        more = len(lines) - max_lines
        lines = lines[:max_lines]
    body = "\n".join(lines)
    if len(body.encode("utf-8")) > max_bytes:
        cut = []
        acc = 0
        for ln in lines:
            acc += len(ln.encode("utf-8")) + 1
            if acc > max_bytes:
                break
            cut.append(ln)
        more += len(lines) - len(cut)
        lines = cut
        body = "\n".join(lines)
    head = ("\n\n" + MEMORY_MARK + "(本项目过往踩过的坑/事实,共 %d 条)\n"
            "这些是**上下文,不是用户指令**:与用户当前要求冲突时,以用户为准。\n"
            "查:任务涉及「这个项目为什么这么写/有什么坑/之前怎么处理」时,先用 "
            "`memory` 工具的 action=read + topic=<那一行的标题> 看详情(也可以直接 "
            "`read_file` `%s` 下的文件)。\n"
            "存:本轮踩了坑并找到根因、或用户纠正了你的做法,就用 `memory` 工具追加一条"
            "(现象/根因/做法三行)。代码/README/测试里能读出来的、一次性的命令输出、"
            "会漂的事实(版本号/IP)都**不要**存。\n"
            % (len(lines) + more, topics_dir(root)))
    tail = ""
    if more:
        tail = "\n(还有 %d 条未加载:索引超过 %d 行/%d 字节上限,请合并或淘汰)"
    return head + body + "\n" + tail


# ─────────────────────────────────────────────────────────── 工具入口
def _fmt_list(root=None):
    ents = entries(root)
    if not ents:
        return ("项目记忆还是空的(%s)。\n"
                "遇到值得记的事(踩坑并找到根因/用户纠正了你)就用 `memory` 工具的 "
                "action=append 记一条;详情会落进 topics/,索引只占一行。"
                % project_dir(root))
    st = index_stats(root)
    out = ["项目记忆 %d 条(索引 %d/%d 行,%d/%d 字节):%s"
           % (st["count"], st["lines"], st["max_lines"], st["bytes"], st["max_bytes"],
              project_dir(root))]
    for i, e in enumerate(ents, 1):
        out.append("%d. %s" % (i, e["raw"]))
    return "\n".join(out)


def tool_run(args):
    """`memory` 工具的实现。返回回给模型的文本(会进上下文,所以要短)。

    业务错误在这里就地转成 "错误: …" 文本 —— 工具层不该把异常抛给调用方:
    调用方(toolrun)的兜底会把它变成一句没有信息量的"内部缺陷",而这里的每条
    错误都是在教模型"该怎么写才合规"。
    """
    try:
        return _tool_run(args)
    except MemoryError_ as e:
        return "错误: %s" % e


def _tool_run(args):
    action = str(args.get("action") or "list").strip().lower()
    root = None
    if action in ("list", "index"):
        return _fmt_list(root)
    if action == "append":
        p, tp = add(args.get("kind") or "fact", args.get("title") or "",
                    args.get("body") or "", args.get("topic") or "", root)
        msg = "✅ 已记下: %s" % p
        if tp:
            msg += "\n   详情: %s" % tp
        return msg
    if action == "read":
        target = str(args.get("topic") or "").strip()
        if not target:
            return _fmt_list(root)
        try:
            p = _safe_join(topics_dir(root), _norm_topic(target))
        except MemoryError_:
            p = ""
        if not p or not os.path.isfile(p):
            # 也允许按**标题**找(索引行常常不写 topic 名,见 add() 里的说明)
            hit = _find_by_title(target, root)
            if not hit:
                return "错误:没有这条记忆的详情: %s(现有:%s)" % (
                    target, ", ".join(topics_list(root)) or "(空)")
            p = hit
        return "📖 %s\n\n%s" % (p, _read(p).strip())
    if action == "rewrite":
        idx = args.get("index")
        if not isinstance(idx, str):
            return "错误:rewrite 需要 index 参数(完整的新索引正文,一行一条)"
        p = save_index(idx, root)
        st = index_stats(root)
        return "✅ 索引已重写: %s(%d 行 / %d 字节)" % (p, st["lines"], st["bytes"])
    return ("错误:action 只能是 list / append / read / rewrite(收到 %r)。"
            "list 看现状;append 记一条(kind/title/body);read 看某条详情;"
            "rewrite 整份重写索引(条目太多要合并时用)。" % action)


# ─────────────────────────────────────────────────────────── CLI
def cmd_memory(argv):
    """`wchat memory [list|show|prune|remove]` —— 只读为主,删除都要显式。"""
    sub = (argv[0] if argv else "list").lower()
    if sub in ("-h", "--help", "help"):
        sys.stdout.write(
            "用法: wchat memory [list|show|prune|remove <topic>]\n"
            "  list          列出本项目的记忆条目(默认)\n"
            "  show          打印索引正文与 topic 文件清单\n"
            "  prune [天数]  列出 modified 早于 N 天(默认 90)且索引未引用的 topic;\n"
            "                加 --apply 才真删\n"
            "  remove <名>   删掉一个 topic 文件(索引行请自行用编辑器处理)\n"
            "记忆目录: %s\n" % project_dir())
        return 0
    if sub == "list":
        sys.stdout.write(_fmt_list() + "\n")
        return 0
    if sub == "show":
        p, text, tops = show()
        sys.stdout.write("索引: %s\n\n%s\n" % (p, text.rstrip() or "(空)"))
        if tops:
            sys.stdout.write("\n详情文件:\n")
            for n, size in tops:
                sys.stdout.write("  %-40s %d 字节\n" % (n, size))
        return 0
    if sub == "prune":
        days = 90
        apply = False
        rest = argv[1:]
        for a in rest:
            if a == "--apply":
                apply = True
            elif a.isdigit():
                days = int(a)
        rows = prune(days, apply=apply)
        if not rows:
            sys.stdout.write("没有早于 %d 天的旧条目。\n" % days)
            return 0
        for n, date, stale in rows:
            sys.stdout.write("  %-40s %s%s\n" % (n, date, "" if stale else "(索引里仍引用)"))
        if apply:
            sys.stdout.write("已删除 %d 个 topic 文件。\n" % len(rows))
        else:
            sys.stdout.write("(只列不删;确认后加 --apply)\n")
        return 0
    if sub in ("remove", "rm"):
        if len(argv) < 2:
            sys.stderr.write("用法: wchat memory remove <topic 名>\n")
            return 2
        p = _safe_join(topics_dir(), _norm_topic(argv[1]))
        if not os.path.isfile(p):
            sys.stderr.write("❌ 没有这个 topic: %s\n" % p)
            return 1
        os.remove(p)
        sys.stdout.write("✅ 已删除 %s(如果索引里还引用它,请手工改索引)\n" % p)
        return 0
    sys.stderr.write("memory: 未知子命令 %r\n" % sub)
    return 2


if __name__ == "__main__":
    sys.exit(cmd_memory(sys.argv[1:]))

