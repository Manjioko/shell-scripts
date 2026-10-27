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


def seed_mcp(force=False):
    """把模板复制成 ~/.ct/mcporter.json。已存在则**拒绝**,返回 (路径或None, 说明)。

    `force=True` 也不覆盖:清单里可能有用户自己的 server 与占位配置。
    要重来请用户自己删/改名(这是有意的:模型不该能重置用户的 MCP 配置)。
    """
    dest, src = mcp_dest(), mcp_template()
    if os.path.exists(dest) and not force:
        return None, "已存在,未改动: %s" % dest
    if os.path.exists(dest) and force:
        return None, "已存在,拒绝覆盖(请先自己移走): %s" % dest
    if not os.path.isfile(src):
        return None, "包里没有模板: %s" % src
    try:
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        tmp = dest + ".tmp"
        shutil.copyfile(src, tmp)
        os.chmod(tmp, 0o600)        # 里面可能有 env 引用,按凭据级处理
        os.replace(tmp, dest)
    except OSError as e:
        return None, "写入失败: %s" % e
    _write_marker("mcp", {"version": _version(), "path": dest})
    return dest, "已写入 %s(记得 export 模板里引用的环境变量)" % dest


def migrate_from_legacy():
    """把老的清单位搬到 ~/.ct/mcporter.json(只在目标不存在时)。

    为什么要有:清单从上游默认位收进 `~/.ct/` 之后,老用户如果什么都不做,会突然
    发现 `-mcp` 里 server 全没了 —— 那不是"没配",是"我们换了读取位置"。所以第一次
    启动时**复制**一份过去(不删原件:原件里可能还有别的工具在读写,不该我们代删)。
    """
    dest = mcp_dest()
    if os.path.exists(dest):
        return None
    home = os.environ.get("HOME") or ""
    legacy = os.path.join(home, ".mcporter", "mcporter.json")
    if not os.path.isfile(legacy):
        return None
    try:
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        tmp = dest + ".tmp"
        shutil.copyfile(legacy, tmp)
        os.chmod(tmp, 0o600)
        os.replace(tmp, dest)
    except OSError:
        return None
    _write_marker("mcp", {"version": _version(), "path": dest, "migrated_from": legacy})
    return dest


# ------------------------------------------------------------------ 入口小工具

def cmd_skills(argv):
    """`wchat skills` —— 看技能从哪来、哪些被自己改过。只读。"""
    if argv and argv[0] in ("--seed", "seed", "--sync"):
        added, skipped, msg = seed_skills(force=True)
        sys.stdout.write("播种技能: %s\n" % msg)
        for n in added:
            sys.stdout.write("  + %s\n" % n)
        for n in skipped:
            sys.stdout.write("  = %s(已存在,未改动)\n" % n)
        return 0
    if argv and argv[0] in ("-h", "--help", "help"):
        sys.stdout.write("用法: wchat skills [--seed]\n"
                         "  默认:列出 ~/.ct/skills 里的技能与来源\n"
                         "  --seed:把随包技能补进 ~/.ct/skills(只补缺,不覆盖)\n")
        return 0
    rows = skills_status()
    d = skills_dest()
    if not rows:
        sys.stdout.write("技能目录 %s 里还没有技能。\n" % d)
        sys.stdout.write("跑 `wchat skills --seed` 可把随包的 %d 个技能补进去。\n"
                         % len(_existing_skills(skills_src())))
        return 0
    sys.stdout.write("技能目录: %s\n" % d)
    for name, src, dirty in rows:
        tag = "包里带的" if src == "packaged" else "自己写的"
        if dirty:
            tag += ",已本地修改"
        sys.stdout.write("  %-28s %s\n" % (name, tag))
    return 0


def cmd_mcp(argv):
    """`wchat mcp [status|init]` —— 清单在哪、有没有、能不能初始化。"""
    sub = argv[0] if argv else "status"
    dest = mcp_dest()
    if sub in ("init", "--init"):
        p, msg = seed_mcp()
        sys.stdout.write("%s\n" % msg)
        if p:
            sys.stdout.write("下一步:编辑 %s,把需要的 server 打开;然后 `wchat -mcp` 启动。\n" % p)
            return 0
        return 1
    if sub in ("-h", "--help", "help"):
        sys.stdout.write("用法: wchat mcp [status|init]\n"
                         "  status:清单路径 + 有哪些 server(不打印任何 env 值)\n"
                         "  init: 从随包模板生成清单(已存在则拒绝)\n")
        return 0
    # status
    sys.stdout.write("清单路径: %s\n" % dest)
    if not os.path.isfile(dest):
        sys.stdout.write("状态: 不存在(`wchat mcp init` 可从模板生成)\n")
        sys.stdout.write("模板: %s\n" % mcp_template())
        return 0
    try:
        with open(dest, encoding="utf-8", errors="replace") as f:
            d = json.load(f)
    except (OSError, ValueError) as e:
        sys.stdout.write("状态: 读不了(%s)\n" % e)
        return 1
    servers = list((d.get("mcpServers") or {}).keys())
    sys.stdout.write("状态: 有,%d 个 server: %s\n" % (len(servers), ", ".join(servers) or "(空)"))
    return 0