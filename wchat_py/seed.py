#!/usr/bin/env python3
"""seed —— 首次使用时把随包资源"播种"进 `~/.ct/`,之后只读不写。

两条纪律(都是为了让"自动播种"不变成"自动覆盖"):

1. **只补缺,不覆盖**。目标已存在就一个字都不动;`~/.ct/skills/` 里丢过 5 个技能
   文件且不可恢复,这个目录不允许任何自动写入覆盖既有内容。
2. **不是同步**。播种只发生一次(留 `.seeded.json` 记录版本与清单)。包升级后不会
   把用户改过的技能悄悄刷回去 —— 想更新由人显式做。这一点是刻意的:技能是用户资产,
   不是程序资产。

为什么需要它:新用户装完 wchat 是空的,`skills/` 里那 8 个技能(含"碰 ~/.ct 前必读"
这类安全前置)得自己抄过去才能用;而 mcporter 清单不存在时 `-mcp` 直接不可用。
"""
import json
import os
import re
import shutil
import sys

MARKER = ".seeded.json"


def _ct_home():
    return os.environ.get("CT_HOME") or os.path.join(os.environ.get("HOME") or "", ".ct")


def _pkg_root():
    """包根(发布包里 = libexec;源码树里 = 仓库根)。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _marker_path(what):
    return os.path.join(_ct_home(), MARKER if what == "all" else "%s.%s" % (MARKER, what))


def _read_marker(what):
    try:
        with open(_marker_path(what), encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def _write_marker(what, data):
    p = _marker_path(what)
    try:
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, p)
        return p
    except OSError:
        return ""


def _version():
    try:
        from wchat_py.version import __version__
        return __version__
    except Exception:
        return ""


# ------------------------------------------------------------------ 技能

def skills_src():
    """随包的技能源目录。"""
    return os.path.join(_pkg_root(), "skills")


def skills_dest():
    return os.environ.get("SKILLS_DIR_DEFAULT") or os.path.join(_ct_home(), "skills")


def _existing_skills(d):
    """目录里已有的技能名(只认 `<name>/SKILL.md`)。"""
    out = set()
    try:
        for name in os.listdir(d):
            if os.path.isfile(os.path.join(d, name, "SKILL.md")):
                out.add(name)
    except OSError:
        pass
    return out


def seed_skills(force=False):
    """把随包技能补进 ~/.ct/skills。返回 (新增列表, 跳过列表, 说明)。

    `force=False`(默认)= 只补缺:目标已有的同名技能**不碰**,只补包里多出来的。
    `force=True` 也不覆盖 —— 它只表示"即使已有 marker 也再检查一次新增技能"。
    这条是硬的:没有任何参数能让本函数覆盖用户已有的 SKILL.md。
    """
    src = skills_src()
    dest = skills_dest()
    if not os.path.isdir(src):
        return [], [], "包里没有 skills/ 目录,跳过(源码树外运行时正常)"
    try:
        names = sorted(n for n in os.listdir(src)
                       if os.path.isfile(os.path.join(src, n, "SKILL.md")))
    except OSError as e:
        return [], [], "读不到技能源: %s" % e
    if not names:
        return [], [], "包里 skills/ 是空的"

    have = _existing_skills(dest)
    added, skipped = [], []
    os.makedirs(dest, exist_ok=True)
    for n in names:
        if n in have:
            skipped.append(n)          # ← 已存在:一个字都不改
            continue
        s, d = os.path.join(src, n), os.path.join(dest, n)
        try:
            shutil.copytree(s, d)
            added.append(n)
        except FileExistsError:
            skipped.append(n)
        except OSError as e:
            return added, skipped, "写入失败(%s): %s" % (n, e)

    mk = _read_marker("skills")
    merged = sorted(set(mk.get("seeded") or []) | set(added))
    _write_marker("skills", {"version": _version(),
                             "seeded": merged,
                             "skipped_existing": sorted(skipped)})
    msg = "新增 %d 个" % len(added)
    if skipped:
        msg += ",已存在 %d 个未改动" % len(skipped)
    return added, skipped, msg


def skills_status():
    """返回 [(名字, 来源, 是否被本地改过)];来源 = packaged / local。

    "本地改过"只做一条可判定的近似:和包里的同名技能逐字节比 mtime+size 不现实,
    所以按**内容**比(读 SKILL.md 正文)。比不了(包里没有同名)就记 local。
    """
    dest, src = skills_dest(), skills_src()
    rows = []
    for n in sorted(_existing_skills(dest)):
        mine = os.path.join(dest, n, "SKILL.md")
        theirs = os.path.join(src, n, "SKILL.md")
        try:
            a = open(mine, "rb").read()
        except OSError:
            continue
        if os.path.isfile(theirs):
            try:
                b = open(theirs, "rb").read()
                rows.append((n, "packaged", a != b))
            except OSError:
                rows.append((n, "packaged", False))
        else:
            rows.append((n, "local", False))
    return rows


# ------------------------------------------------------------------ MCP 清单

def mcp_template():
    """随包的 mcporter 清单模板。**只允许 `${VAR}` 占位,不许出现明文密钥。**"""
    return os.path.join(_pkg_root(), "configs", "mcporter.example.json")


def mcp_dest():
    return os.environ.get("MCP_CONFIG") or os.path.join(_ct_home(), "mcporter.json")


def migrate_chrome_profile():
    """给清单里**已有的** chrome 条目补上 `--isolated`。

    为什么需要一次性迁移(2026-11):
      chrome-devtools-mcp 默认用 `~/.cache/chrome-devtools-mcp/chrome-profile`,
      那是**全机独占锁**。任何别的程序(Claude Desktop、残留 daemon、用户自己开的
      chrome-devtools-mcp)抢到它,wchat 这边就全体报
      `The browser is already running for …/chrome-profile`,而且**重启 daemon 也治不好**
      (锁在别的进程手里)。

      为什么用 `--isolated` 而不是 `--userDataDir`:mcporter 把
      `--userDataDir/--user-data-dir/--channel/--browserUrl/…` 列为**连接选择标志**
      (`CONNECTION_VALUE_FLAGS`),带这些的标志会被它的 chrome-owner 直接拒绝
      ("only canonical auto-connect is supported",实测)。`--isolated` 不在该名单里,
      实测可用,且 Chrome 用临时 profile、完全不碰全局锁。

    `seed` 的纪律是"只补缺、不覆盖",所以模板改了传不到已装机的用户 —— 这条迁移
    只做一件极窄的事:**在 chrome 的 args 数组里增量加一项**,别的字节一个都不动。
    已经是 `--userDataDir=…` / `--user-data-dir=…` / `--isolated` 的跳过(用户选过,尊重)。

    ⚠ 必须**文本级**插入,不能用 json.dump 重写:实测重写会把
    `"args": ["mcp-server-git"]` 这种紧凑数组展开成多行,diff 从"1 行"变成整份重排
    (本项目在 tools_spec.json 上吃过一模一样的亏)。

    返回 (做了吗, 说明)。
    """
    dest = mcp_dest()
    if not os.path.isfile(dest):
        return False, "清单不存在"
    try:
        with open(dest, encoding="utf-8", errors="replace") as f:
            text = f.read()
        data = json.loads(text)
    except (OSError, ValueError) as e:
        return False, "清单读不了: %s" % e
    servers = data.get("mcpServers") or {}
    if not isinstance(servers, dict):
        return False, "mcpServers 结构异常"

    targets = []
    for name, entry in servers.items():
        if not isinstance(entry, dict):
            continue
        args = entry.get("args")
        if not isinstance(args, list):
            continue
        blob = " ".join(str(x) for x in [name, entry.get("command") or ""] + list(args))
        if "chrome-devtools-mcp" not in blob and "chrome_devtools_mcp" not in blob:
            continue
        if any(str(a).startswith("--userDataDir") or str(a).startswith("--user-data-dir")
               or str(a) == "--isolated" for a in args):
            continue
        targets.append(name)
    if not targets:
        return False, "无需迁移"

    new_text = text
    for name in targets:
        new_text = _insert_args_item(new_text, name,
                                     "--isolated")
        if new_text is None:
            return False, "定位不到 %s 的 args(清单格式特殊,未改动)" % name
    try:
        shutil.copyfile(dest, dest + ".bak")
        try:
            os.chmod(dest + ".bak", 0o600)
        except OSError:
            pass
        tmp = dest + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(new_text)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, dest)
    except OSError as e:
        return False, "写入失败: %s" % e
    return True, "已给 %s 补上独立 profile" % ", ".join(targets)


def _insert_args_item(text, server_name, item):
    """在 `"<server_name>": { ... "args": [ ... ] ... }` 的数组末尾插入一项。

    纯文本操作,保留原有缩进与紧凑写法。定位不到返回 None(调用方不改文件)。
    """
    # 1) 找到该 server 的条目起点
    m = re.search(r'("\s*%s"\s*:\s*\{)' % re.escape(server_name), text)
    if not m:
        return None
    start = m.end()
    # 2) 从条目起点向后找 "args" 与其后的 [ ... ]
    am = re.search(r'("args"\s*:\s*\[)', text[start:])
    if not am:
        return None
    arr_open = start + am.end()
    depth = 1
    i = arr_open
    while i < len(text) and depth:
        if text[i] == "[":
            depth += 1
        elif text[i] == "]":
            depth -= 1
            if depth == 0:
                break
        i += 1
    if depth:
        return None
    close = i                                  # 指向收尾的 ']'
    inner = text[arr_open:close]
    quoted = json.dumps(item)                  # 带引号、按 JSON 转义
    if "\n" not in inner:
        # 单行数组:["a", "b"] 或 [] 或 [ ]
        stripped = inner.strip()
        if not stripped:
            new_inner = " %s " % quoted
            lead = " " if not inner.startswith((" ", "") ) else inner[:1]
            return text[:arr_open] + (" " + quoted + " ") + text[close:]
        # 保留原有空格风格,在最后一项后追加
        sep = "" if stripped.endswith(",") else ","
        body = inner.rstrip()
        return text[:arr_open] + body + sep + " " + quoted + text[close:]
    # 多行数组:在收尾 ']' 所在行之前插入一行,缩进跟随数组内已有元素
    line_start = text.rfind("\n", 0, close) + 1
    close_line = text[line_start:close]
    tail_indent = re.match(r"^(\s*)", close_line).group(1)
    # 数组内元素的缩进 = 上一行非空元素的缩进
    prev_items = [ln for ln in inner.split("\n") if ln.strip()]
    item_indent = tail_indent + "    "
    if prev_items:
        item_indent = re.match(r"^(\s*)", prev_items[-1]).group(1) or item_indent
    # 上一项若没有逗号,补一个
    if prev_items and not prev_items[-1].rstrip().endswith(","):
        # 在上一项末尾补逗号
        last = text.rfind(prev_items[-1], arr_open, close)
        if last >= 0:
            eol = text.find("\n", last)
            text = text[:eol] + "," + text[eol:]
            close += 1
            line_start = text.rfind("\n", 0, close) + 1
    return text[:line_start] + item_indent + quoted + "\n" + text[line_start:]


def ensure_manifest():
    """首次启动时让 `~/.ct/mcporter.json` 就位。**只有一个入口、一条路径**。

    顺序(目标已存在就什么都不做):
      1. 老位置有清单 → 复制过来(老用户升级后不会突然"server 全没了");
      2. 否则 → 用随包模板生成(新用户开箱就有一份能用的,默认只开 chrome)。

    为什么不保留 `wchat mcp init`:有了这一步,init 永远是空操作或重复机制。
    少一个命令就少一个"到底该跑哪个"的问题 —— 用户不必知道清单是怎么来的。

    不删老位置的原件:那个目录里还有 mcporter 自己的 daemon 状态与 vault,
    哪些该留由上游决定,不该我们代删。
    """
    dest = mcp_dest()
    if os.path.exists(dest):
        return None, "已存在"
    home = os.environ.get("HOME") or ""
    legacy = os.path.join(home, ".mcporter", "mcporter.json")
    if os.path.isfile(legacy):
        src, why = legacy, "从老位置复制(升级迁移)"
    else:
        src, why = mcp_template(), "从随包模板生成"
    if not os.path.isfile(src):
        return None, "没有可用的源(模板缺失: %s)" % src
    try:
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        tmp = dest + ".tmp"
        shutil.copyfile(src, tmp)
        os.chmod(tmp, 0o600)      # 清单里可能有 env 引用,按凭据级处理
        os.replace(tmp, dest)
    except OSError as e:
        return None, "写入失败: %s" % e
    _write_marker("mcp", {"version": _version(), "path": dest,
                          "source": src, "why": why})
    return dest, why


def migrate_from_legacy():
    """兼容旧名。等价于 ensure_manifest(),返回路径或 None。"""
    return ensure_manifest()[0]


# ------------------------------------------------------------------ 入口小工具

def cmd_skills(argv):
    """`wchat skills` —— 看技能从哪来、哪些被自己改过;安装/卸载第三方技能。"""
    # 安装/卸载委派给 skillinstall(那是**唯一**会往 ~/.ct/skills 写东西的两条路径之一,
    # 另一条是只补缺的 seed)。放这里而不是 cli.py:入口已经 1190 行,分层闸门守着 1250。
    if argv and argv[0] in ("add", "install", "remove", "rm", "uninstall"):
        from wchat_py import skillinstall
        return skillinstall.main(list(argv))
    if argv and argv[0] in ("--seed", "seed", "--sync"):
        added, skipped, msg = seed_skills(force=True)
        sys.stdout.write("播种技能: %s\n" % msg)
        for n in added:
            sys.stdout.write("  + %s\n" % n)
        for n in skipped:
            sys.stdout.write("  = %s(已存在,未改动)\n" % n)
        return 0
    if argv and argv[0] in ("-h", "--help", "help"):
        sys.stdout.write("用法: wchat skills [--seed] | add <来源> | remove <名字>\n"
                         "  默认:列出 ~/.ct/skills 里的技能与来源\n"
                         "  --seed:把随包技能补进 ~/.ct/skills(只补缺,不覆盖)\n"
                         "  add <来源>:安装第三方技能(owner/repo、URL、本地目录、压缩包);\n"
                         "              装之前会把来源/文件/依赖摊开给你看,默认不装\n"
                         "  remove <名字>:移出技能(进 .trash/,不删除)\n")
        return 0
    rows = skills_status()
    d = skills_dest()
    if not rows:
        sys.stdout.write("技能目录 %s 里还没有技能。\n" % d)
        sys.stdout.write("跑 `wchat skills --seed` 可把随包的 %d 个技能补进去。\n"
                         % len(_existing_skills(skills_src())))
        sys.stdout.write("或 `wchat skills add <owner/repo>` 装一个第三方的。\n")
        return 0
    sys.stdout.write("技能目录: %s\n" % d)
    for name, src, dirty in rows:
        tag = "包里带的" if src == "packaged" else "自己写的"
        if os.path.isfile(os.path.join(d, name, ".source.json")):
            tag = "装的(第三方)"
        if dirty:
            tag += ",已本地修改"
        sys.stdout.write("  %-28s %s\n" % (name, tag))
    return 0


def cmd_mcp(argv):
    """`wchat mcp` —— 清单在哪、有哪些 server、文件是哪来的。默认只读。

    **没有 init 子命令**:清单在首次启动时自动就位(见 ensure_manifest)。
    多一个命令只会让人问"我该跑哪个",而答案永远是"不用跑"。

    add/remove/help 委派给 mcp_cmd(那是**唯一**会往清单里写东西的路径),
    与 `-mcp add` 共用一份实现。
    """
    from wchat_py import mcp_cmd
    rc = mcp_cmd.main(list(argv or []))
    if rc is not None:
        return rc
    sub = argv[0] if argv else "status"
    if sub in ("-h", "--help", "help"):
        return _help()
    dest = mcp_dest()
    sys.stdout.write("清单路径: %s\n" % dest)
    if not os.path.isfile(dest):
        # 正常不该走到这里(启动时会补),但手工删掉后要说清楚怎么回来。
        # 也不能无条件说"下次启动会自动补":随包模板缺失(装包不完整)时**永远补不出来**,
        # 那句承诺会把用户指向"再启一次"这种无效动作,而真因在安装包。
        if os.path.isfile(mcp_template()):
            sys.stdout.write("状态: 不存在(下次启动 wchat 会自动补一份;模板: %s)\n"
                             % mcp_template())
        else:
            sys.stdout.write("状态: 不存在,而且**随包模板也缺失**(%s)\n" % mcp_template())
            sys.stdout.write("  → 这是安装包不完整:清单无法自动生成,MCP 会一直不可用。\n"
                             "  → 修复:重装本包(brew reinstall wchat / 重新 dpkg -i);"
                             "装完再跑一次 wchat mcp 确认。\n")
        return 0
    mk = _read_marker("mcp")
    if mk.get("why"):
        sys.stdout.write("来源: %s\n" % mk["why"])
    try:
        with open(dest, encoding="utf-8", errors="replace") as f:
            d = json.load(f)
    except (OSError, ValueError) as e:
        sys.stdout.write("状态: 读不了(%s)\n" % e)
        return 1
    servers = list((d.get("mcpServers") or {}).keys())
    sys.stdout.write("状态: 有,%d 个 server: %s\n"
                     % (len(servers), ", ".join(servers) or "(空)"))
    return 0