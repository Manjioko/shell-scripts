#!/usr/bin/env python3
"""skillinstall —— 从 URL / 仓库 / 本地目录安装第三方技能。

为什么需要它:现有两条路都装不了"别人的技能" ——
  · `seed.seed_skills()` 只搬**随包**那 8 个,来源写死在包里;
  · `-skill` 只提炼**本次对话**得到的经验;
  · 模型自己 `git clone` + `cp` 会被硬墙拦(H5 写主目录配置 / H2 下载即执行 / H14 装包),
    而且那是**该拦的**:让模型在无人值守时下载并落盘第三方文本,没有任何预览与确认。
所以安装必须是**人跑的一条命令**(`wchat skills add <来源>`),程序只做四件事:
下载 → 发现 → 校验 → 落盘/记账。全程**不执行**仓库里的任何东西。

六条铁律(每条都对应一个真实会踩的坑):

1. **只下载不执行**。用 `git clone --depth 1`,不用 `curl|bash`(H2 口径,也不该用)。
2. **装之前把"装进来的是什么"摊开给人看**,默认答案是"不装"。装进来的是一段会常驻
   上下文、且模型**可能照着执行**的第三方文本 —— 这是整条链上唯一的真攻击面。
3. **已存在默认拒绝**;`--force` 只把旧的**改名备份**,绝不删除。`~/.ct/skills`
   丢过 5 个技能文件且不可恢复(见 skill `ct-home-layout`),这里不允许任何删除路径。
4. **软链一律解引用成实体文件**。`scan_skills(external=1)` 会**跳过**符号链接条目,
   `.claude-plugin` 时代的仓库(如 Auto-Redbook-Skills)正好把 `skills/<n>/SKILL.md`
   做成 `../../SKILL.md` 的软链 —— 保留链接 = 装上了但永远看不见,而且不报错。
5. **解引用前后都要防逃逸**。`../../../../etc/passwd` 这种链接必须在复制**之前**被拒,
   否则一次安装就把系统文件抄进了技能目录。
6. **装完记账**(`.source.json`:来源/ref/commit/逐文件 sha256)。用途:分得清"包里带的"
   还是"自己装的"、以后 `--update` 有依据、出事能核对"装进来的到底是不是预览的那份"。

依赖**永不自动安装**:只把仓库里检测到的 `requirements.txt` / `package.json` 原样打出来,
交给人跑(H14 同口径)。这条不是偷懒 —— 自动装依赖等于把"安装第三方代码"升级成
"安装第三方代码的任意依赖图",而后者没有任何人看过。

用法:
    wchat skills add <来源> [--name N] [--ref R] [--subdir D]
                           [--skill A,B] [--all] [--dry-run] [--force] [--yes]
    wchat skills remove <名字>
来源形态:`owner/repo` / `https://…` / `https://…/tree/ref/sub` / 本地目录 / .zip / .tar.gz
多技能来源:--skill 选装若干、--all 全装、--subdir 只装一类,或不加选择器交互弹菜单。
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

# 体积/文件数上限:技能是**文本 + 少量脚本**,20MB 已经很宽松。
# 上限的意义不是省磁盘,而是别让一次 `add` 把 node_modules 搬进 `~/.ct/skills`
# (那会让 `scan_skills` 每次启动都扫几千个目录)。
MAX_BYTES = 20 * 1024 * 1024
MAX_FILES = 2000

_NAME_OK = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
_DEPS = ("requirements.txt", "package.json", "pyproject.toml",
         "environment.yml", "Pipfile", "setup.py")


class SkillError(Exception):
    """可预期的业务失败:调用方直接把它的话原样给用户看(已经是人话)。"""


# ─────────────────────────────────────────────────────────── 名字与描述
def norm_name(raw):
    """技能名归一 + 校验。返回小写名;不合法就**报错**,不静默改名。

    为什么报错而不是"洗一洗":目录名是这个技能在系统提示里的唯一身份。静默把
    `XHS Note Creator` 洗成 `xhs-note-creator`,用户 `read_skill XHS Note Creator`
    就查不到,而屏幕上一切正常 —— 这类"看起来成功了"的不一致最费时间。
    """
    s = str(raw or "").strip()
    if not s:
        raise SkillError("技能名(frontmatter 的 name)是空的,无法安装")
    low = s.lower()
    if len(low) > 30:
        raise SkillError("技能名 %r 超过 30 字符(系统提示里每条都要占位),"
                         "请用 --name 指定一个短名" % s)
    if not _NAME_OK.match(low):
        raise SkillError("技能名 %r 不是合法标识(只允许 a-z0-9-、且不能以 - 开头/结尾),"
                         "请用 --name 指定一个合法名字" % s)
    return low


def _frontmatter(path):
    """取 frontmatter 里的 name / description(缩进行并入 description,与 skill.py 同口径)。"""
    try:
        with open(path, "rb") as fh:
            text = fh.read().decode("utf-8", "replace")
    except OSError as e:
        raise SkillError("读不到 %s: %s" % (path, e))
    name = desc = ""
    in_front = in_desc = False
    for line in text.split("\n"):
        if not in_front:
            if line.strip() == "---":
                in_front = True
            continue
        if line.strip() == "---":
            break
        if line.startswith("name:"):
            name = line[len("name:"):].strip()
            in_desc = False
        elif line.startswith("description:"):
            desc = line[len("description:"):].strip()
            in_desc = True
        elif in_desc and line[:1] in (" ", "\t"):
            desc += " " + line.strip()
        else:
            in_desc = False
    return name, " ".join(desc.split())


# ─────────────────────────────────────────────────────────── 来源解析
def parse_source(src):
    """把用户给的来源拆成 {kind, url, ref, subdir}。

    支持的写法(刻意只支持这几种,不猜):
      owner/repo                      GitHub 短名
      https://host/x/y(.git)          完整 URL
      https://host/x/y/tree/ref/sub   浏览器地址栏直接粘过来的那种
      /本地/目录  ./目录  ~/目录        已 clone 好的
      /本地/x.zip  /本地/x.tar.gz      离线包
    任意形态都可接 `#ref` 或 `#ref:子目录`。
    """
    s = str(src or "").strip()
    if not s:
        raise SkillError("没给来源。用法: wchat skills add <owner/repo | URL | 本地目录>")
    ref = subdir = ""
    if "#" in s:
        s, frag = s.split("#", 1)
        if ":" in frag:
            ref, subdir = frag.split(":", 1)
        else:
            ref = frag
    if s.startswith(("http://", "https://")):
        m = re.match(r"^(https?://[^/]+/[^/]+/[^/]+?)(?:\.git)?"
                     r"(?:/tree/([^/]+)(/.*)?)?/?$", s)
        if not m:
            raise SkillError("看不懂的仓库地址: %s。"
                             "支持 https://host/owner/repo 或 …/tree/分支/子目录" % s)
        url, tree_ref, tree_sub = m.group(1), m.group(2) or "", m.group(3) or ""
        if tree_ref and not ref:
            ref = tree_ref
        if tree_sub and not subdir:
            subdir = tree_sub.lstrip("/")
        return {"kind": "git", "url": url, "ref": ref, "subdir": subdir}
    if os.path.exists(os.path.expanduser(s)):
        p = os.path.realpath(os.path.expanduser(s))
        if os.path.isdir(p):
            return {"kind": "dir", "url": p, "ref": "", "subdir": subdir}
        low = p.lower()
        if low.endswith((".zip", ".tar.gz", ".tgz", ".tar")):
            return {"kind": "archive", "url": p, "ref": "", "subdir": subdir}
        raise SkillError("%s 既不是目录也不是 .zip/.tar.gz" % p)
    if re.match(r"^[\w.-]+/[\w.-]+$", s):
        return {"kind": "git", "url": "https://github.com/%s" % s,
                "ref": ref, "subdir": subdir}
    raise SkillError("看不懂的来源: %r。支持 owner/repo、https URL、本地目录或压缩包" % src)


def _git(args, cwd=None):
    try:
        r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                           text=True, timeout=180)
    except FileNotFoundError:
        # 唯一的外部依赖。新装的 macOS 没装 Xcode CLT 时 PATH 上没有 git ——
        # 报 "No such file or directory: 'git'" 对用户没有任何指导意义。
        raise SkillError(
            "找不到 git(它是安装技能唯一的外部依赖)。\n"
            "  修:macOS 跑 `xcode-select --install`;或用 `brew install git`;\n"
            "  也可以完全绕开 git:把仓库下载成 .zip/.tar.gz,再 "
            "`wchat skills add /路径/x.zip`")
    except subprocess.TimeoutExpired as e:
        raise SkillError("git 超时(180s): %s" % e)
    if r.returncode != 0:
        tail = (r.stderr or "").strip().split("\n")[-1:]
        raise SkillError("git %s 失败: %s" % (args[0], tail[0] if tail else "未知错误"))
    return r.stdout


def fetch_source(spec, tmpdir):
    """把来源取到本地,返回 (根目录, 元信息)。**不执行任何仓库内容。**"""
    if spec["kind"] == "dir":
        return spec["url"], {"commit": "", "how": "本地目录"}
    if spec["kind"] == "archive":
        dest = os.path.join(tmpdir, "unpacked")
        os.makedirs(dest, exist_ok=True)
        try:
            if spec["url"].lower().endswith(".zip"):
                with zipfile.ZipFile(spec["url"]) as z:
                    _safe_extract_zip(z, dest)
            else:
                import tarfile
                with tarfile.open(spec["url"]) as t:
                    _safe_extract_tar(t, dest)
        except (OSError, zipfile.BadZipFile, tarfile.TarError) as e:
            raise SkillError("解包失败: %s" % e)
        return _single_root(dest), {"commit": "", "how": "本地压缩包"}
    # git:浅克隆到临时目录。--depth 1 顺带把"看起来像仓库其实是个大 monorepo"的成本压住
    dest = os.path.join(tmpdir, "repo")
    args = ["clone", "--depth", "1", "--quiet"]
    if spec.get("ref"):
        args += ["--branch", spec["ref"]]
    args += ["--", spec["url"], dest]
    _git(args)
    commit = ""
    try:
        commit = _git(["rev-parse", "--short", "HEAD"], cwd=dest).strip()
    except SkillError:
        pass
    return dest, {"commit": commit, "how": "git 克隆"}


def _safe_extract_zip(z, dest):
    """解压前逐条检查落点,CVE-2007-4559 那类 `../../` 条目必须拒。"""
    for n in z.namelist():
        tgt = os.path.realpath(os.path.join(dest, n))
        if not (tgt == dest or tgt.startswith(dest + os.sep)):
            raise SkillError("压缩包里有越界条目(%s),已拒" % n)
    z.extractall(dest)


def _safe_extract_tar(t, dest):
    for m in t.getmembers():
        tgt = os.path.realpath(os.path.join(dest, m.name))
        if not (tgt == dest or tgt.startswith(dest + os.sep)):
            raise SkillError("压缩包里有越界条目(%s),已拒" % m.name)
        if m.issym() or m.islnk():
            raise SkillError("压缩包里有符号链接/硬链接(%s),已拒(无法判断真实落点)" % m.name)
    t.extractall(dest)


def _single_root(d):
    """压缩包常带一层顶级目录:只有"一个子目录且没有别的文件"时才下沉。"""
    kids = [x for x in os.listdir(d) if not x.startswith(".")]
    if len(kids) == 1:
        p = os.path.join(d, kids[0])
        if os.path.isdir(p):
            return p
    return d


# ─────────────────────────────────────────────────────────── 发现技能
def _is_skill_dir(p):
    return os.path.isfile(os.path.join(p, "SKILL.md"))


def discover(root):
    """在一个仓库/目录里找出所有技能目录。返回 [(目录, 名字提示)]。

    顺序按"明确性"从高到低,并且**去重**(同一份 SKILL.md 只收一次):
      1. `.claude-plugin/marketplace.json` 里 plugins[] 的 `skills` **显式列表**,
         以及 `source` 指的目录;
      2. `skills/`(允许一层分类分组,如 `skills/<分类>/<技能>/SKILL.md`);
      3. `.claude/skills/`;4. 根 `SKILL.md`(单技能仓库);
      5. 兜底:以上都没命中时,把 root 自身当技能集合再扫一遍
         (让 `--subdir <含技能的子目录>` 也能工作,报错文案就是这么建议的)。
    为什么必须去重:Auto-Redbook-Skills 那种仓库里,根 `SKILL.md` 与
    `skills/xhs-note-creator/SKILL.md`(软链)是**同一个文件**,不去重会装两遍。
    """
    found, seen = [], set()

    def add(d):
        md = os.path.join(d, "SKILL.md")
        try:
            real = os.path.realpath(md)
        except OSError:
            return
        if real in seen or not os.path.isfile(real):
            return
        seen.add(real)
        # `skills/<n>/SKILL.md` 经常是指向仓库根的软链(见文件头铁律 4)。这时技能
        # **实体**在链接目标那一层:那里才有正文引用的 scripts/、assets/。只装
        # `skills/<n>/` 会得到一个"说要跑 scripts/x.py 但没有 scripts 的技能" ——
        # 装完不报错,跑起来才失败,而且看起来像技能本身坏了。
        base = d
        if os.path.islink(md):
            tgt_dir = os.path.dirname(real)
            try:
                if os.path.realpath(tgt_dir) != os.path.realpath(d):
                    base = tgt_dir
            except OSError:
                pass
        found.append((base, os.path.basename(os.path.normpath(d))))

    def scan_skills_dir(base, _depth=0):
        try:
            names = sorted(os.listdir(base))
        except OSError:
            return
        for n in names:
            # 跳过隐藏目录(.git/.claude-plugin/…)与依赖目录:它们既不是技能,
            # 也不是"分类分组",不排除会让候选里混进 .git 之类的垃圾。
            if n.startswith(".") or n in ("node_modules", "__pycache__"):
                continue
            p = os.path.join(base, n)
            if not os.path.isdir(p):
                continue
            if _is_skill_dir(p):
                add(p)                 # 命中技能;它的子目录是 assets/scripts,不再深入
            elif _depth < 2:           # 多下钻一层,兼容 skills/<分类>/<技能> 布局
                scan_skills_dir(p, _depth + 1)

    mk = os.path.join(root, ".claude-plugin", "marketplace.json")
    if os.path.isfile(mk):
        try:
            with open(mk, encoding="utf-8") as fh:
                data = json.load(fh) or {}
            for plug in data.get("plugins") or []:
                if not isinstance(plug, dict):
                    continue
                src = plug.get("source")
                if isinstance(src, str):
                    base = os.path.realpath(os.path.join(root, src))
                elif src is None:
                    base = root
                else:
                    continue          # source 是 git-subdir 等结构:本地解析不了,跳过
                if not (base == root or base.startswith(root + os.sep)):
                    continue          # 清单里指到仓库外:忽略,不跟
                # 标准字段 plugins[].skills:显式列出每个技能目录(相对 base)。
                # 旧代码只认 source、不认这个数组,于是 xiaohongshu-skills 这类把
                # 139 个技能路径写进 skills 的仓库被误判成"没有技能"。补上它。
                listed = plug.get("skills")
                if isinstance(listed, list):
                    for sp in listed:
                        if not isinstance(sp, str):
                            continue
                        d = os.path.realpath(os.path.join(base, sp))
                        if not (d == root or d.startswith(root + os.sep)):
                            continue  # 指到仓库外:忽略
                        if _is_skill_dir(d):
                            add(d)
                        else:
                            scan_skills_dir(d)  # 条目本身是分组目录时继续往下找
                scan_skills_dir(os.path.join(base, "skills"))
                if _is_skill_dir(base):
                    add(base)
        except (OSError, ValueError):
            pass      # 清单坏了就当没有,继续按目录形态找

    scan_skills_dir(os.path.join(root, "skills"))
    scan_skills_dir(os.path.join(root, ".claude", "skills"))
    if _is_skill_dir(root):
        add(root)
    # 兜底:标准位置一个都没命中时,把 root 自身当技能集合再扫一遍。目的有二:
    #   · `--subdir <某个含技能的子目录>` 能工作(报错文案就是这么建议的);
    #   · 兼容 skills/<分类>/<技能> 这种"多一层分组"、且没写 marketplace 的仓库。
    if not found:
        scan_skills_dir(root)
    return found


# ─────────────────────────────────────────────────────────── 校验
def _walker(root, excludes):
    """按 `--exclude` 过滤后的 os.walk;与 `_ignore_factory` 共用同一份判据。

    两处必须同源:预览说 12 个文件、装进去 30 个,就没人再信预览了。
    """
    ex = _excluded_set(excludes)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir = "" if rel_dir == "." else rel_dir
        keep = []
        for d in dirnames:
            if d == ".git":
                continue
            rel = os.path.join(rel_dir, d) if rel_dir else d
            if _is_excluded(rel, ex):
                continue
            keep.append(d)
        dirnames[:] = keep
        yield dirpath, dirnames, filenames


def _excluded_set(excludes):
    return tuple(os.path.normpath(str(x)).strip("/") for x in (excludes or ()) if str(x).strip())


def _is_excluded(rel, ex):
    return any(rel == e or rel.startswith(e + os.sep) for e in ex)


def _ignore_factory(root, excludes):
    """给 `copytree` 用的 ignore 回调。判据与 `_walker` 同一份(`_is_excluded`)。"""
    ex = _excluded_set(excludes)

    def _ignore(dirpath, names):
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir = "" if rel_dir == "." else rel_dir
        out = set()
        for n in names:
            if n == ".git":
                out.add(n)
                continue
            rel = os.path.join(rel_dir, n) if rel_dir else n
            if _is_excluded(rel, ex):
                out.add(n)
        return out
    return _ignore


def _walk_files(root, excludes=()):
    """返回 [(相对路径, 绝对路径, 大小)]。跳过 .git;符号链接**解引用后**按普通文件算。

    这里必须 `followlinks=True`:真正的复制(`copytree(symlinks=False)`)也会跟随
    链接目录,两边口径不一致会让"预览说 3 个文件、装进去 30 个"。安全性由调用前的
    `_check_no_escape` 保证(所有链接的真实落点都必须在技能目录内),另加一个
    realpath 去重防环。
    """
    out = []
    seen_dirs = set()
    for dirpath, dirnames, filenames in _walker(root, excludes):
        real = os.path.realpath(dirpath)
        if real in seen_dirs:
            dirnames[:] = []
            continue
        seen_dirs.add(real)
        for f in filenames:
            p = os.path.join(dirpath, f)
            rel = os.path.relpath(p, root)
            try:
                st = os.stat(p)         # 跟随链接:链接 -> 真实大小
            except OSError as e:
                raise SkillError("读不了 %s: %s" % (rel, e))
            if not os.path.isfile(p):
                raise SkillError("%s 不是普通文件(设备/管道/FIFO 不收)" % rel)
            out.append((rel, p, st.st_size))
    return out


def _check_no_escape(skill_dir, boundary):
    """每个符号链接的真实落点必须仍在**下载下来的这棵仓库树**内。

    边界取 `boundary`(仓库根)而不是技能目录本身:像 Auto-Redbook-Skills 这种仓库,
    `skills/<name>/SKILL.md` 是 `../../SKILL.md` 的软链 —— 落在技能目录外、但仍在
    仓库内,是**合法**的(解引用后正文就进技能目录了)。真正要拒的是
    `../../../../etc/passwd` 那一类:复制是解引用(copytree symlinks=False),
    它会在复制时被悄悄抄进技能目录,而链接名看起来完全无害 —— 所以必须**复制之前**判。
    """
    real_root = os.path.realpath(boundary)
    for dirpath, dirnames, filenames in os.walk(skill_dir, followlinks=False):
        for name in list(dirnames) + list(filenames):
            p = os.path.join(dirpath, name)
            if not os.path.islink(p):
                continue
            real = os.path.realpath(p)
            if not (real == real_root or real.startswith(real_root + os.sep)):
                raise SkillError(
                    "技能里有符号链接指向来源目录之外:%s -> %s。已拒(复制会把它背后的"
                    "真实文件抄进技能目录,而链接名看起来完全无害)"
                    % (os.path.relpath(p, skill_dir), real))


def inspect_skill(d, root, max_bytes=MAX_BYTES, max_files=MAX_FILES, excludes=(),
                  name_override=""):
    """校验一个技能目录,返回可打印/可落盘的信息。任何一条不过就抛 SkillError。"""
    _check_no_escape(d, root)
    files = _walk_files(d, excludes)
    if not files:
        raise SkillError("技能目录是空的:%s" % d)
    total = sum(f[2] for f in files)
    if len(files) > max_files:
        raise SkillError("文件数 %d 超过上限 %d(多半是把 node_modules/ 也带进来了);"
                         "可用 --subdir 只装技能子目录" % (len(files), max_files))
    if total > max_bytes:
        raise SkillError("体积 %.1f MB 超过上限 %.1f MB;可用 --subdir 只装技能子目录"
                         % (total / 1048576.0, max_bytes / 1048576.0))
    name_raw, desc = _frontmatter(os.path.join(d, "SKILL.md"))
    # `--name` 既能把名字改短,也能**救回** frontmatter 里不合法的名字(报错里就是这么
    # 建议的)。给了就以它为准,不再校验原名字 —— 否则那条建议等于没用。
    name = norm_name(name_override) if name_override \
        else norm_name(name_raw or os.path.basename(os.path.normpath(d)))
    if not desc:
        raise SkillError(
            "技能 %s 的 SKILL.md 没有 description。description 是**唯一的触发面**"
            "(系统提示里常驻的就这一行),缺了它技能永远不会被读到 —— 装了等于没装,"
            "所以这里直接拒" % name)
    # description 预算:与 scan_skills 同一条口径(SKILL_DESC_MAX 按字节算,汉字 3 字节)
    try:
        dmax = int(os.environ.get("SKILL_DESC_MAX", "200"))
    except ValueError:
        dmax = 200
    desc_bytes = len(desc.encode("utf-8"))
    deps = [x for x in _DEPS if os.path.isfile(os.path.join(d, x))]
    return {
        "name": name,
        "name_raw": name_raw,
        "description": desc,
        "desc_bytes": desc_bytes,
        "desc_budget": dmax,
        "desc_truncated": desc_bytes > dmax,
        "dir": d,
        "files": files,
        "count": len(files),
        "bytes": total,
        "deps": deps,
        "root": root,
        "excludes": list(excludes or ()),
        "dir_bytes": _dir_bytes(files),
    }


# ─────────────────────────────────────────────────────────── 计划与落盘
def skills_home():
    """目标目录。**与 seed.skills_dest() 同源**,不另立一份。"""
    from wchat_py.seed import skills_dest
    return skills_dest()


def plan(spec, info, commit=""):
    """把"要装什么"摊成人话(装之前给人看的就这一段)。"""
    lines = ["", "来源: %s  %s" % (spec["url"] + ("#" + spec["ref"] if spec.get("ref") else ""),
                                ("· commit " + commit) if commit else ""),
             "技能: %s" % info["name"]]
    if info["name_raw"] and info["name_raw"] != info["name"]:
        lines.append("      (frontmatter 里写的是 %r,已按小写归一到 %s)"
                     % (info["name_raw"], info["name"]))
    lines.append("描述: %s" % info["description"])
    tag = "共 %d 字 / %d 字节" % (len(info["description"]), info["desc_bytes"])
    if info["desc_truncated"]:
        tag += " —— ⚠ 超过 SKILL_DESC_MAX=%d,注入时**尾部会被截断**," \
               "触发词请放在前 30 个字内" % info["desc_budget"]
    lines.append("      %s" % tag)
    scripts = [f[0] for f in info["files"] if f[0].endswith((".py", ".sh", ".js"))]
    lines.append("文件: %d 个 / %.1f MB%s"
                 % (info["count"], info["bytes"] / 1048576.0,
                    ("(含 %d 个脚本: %s)" % (len(scripts), ", ".join(scripts[:3])))
                    if scripts else ""))
    big = [(k, v) for k, v in info["dir_bytes"][:3] if v >= 512 * 1024]
    if big:
        lines.append("      占地最大: %s"
                     % ", ".join("%s %.1f MB" % (k, v / 1048576.0) for k, v in big))
        lines.append("      想省体积: --exclude %s(逗号分隔的相对路径)"
                     % ",".join(k for k, _ in big if not k.startswith("(")))
    if info["deps"]:
        lines.append("依赖: 检测到 %s —— **不会自动安装**,装完请自己跑一次"
                     % ", ".join(info["deps"]))
        if "requirements.txt" in info["deps"]:
            lines.append("        pip install -r %s/requirements.txt"
                         % os.path.join(skills_home(), info["name"]))
        if "package.json" in info["deps"]:
            lines.append("        cd %s && npm install"
                         % os.path.join(skills_home(), info["name"]))
    lines.append("目录: %s" % os.path.join(skills_home(), info["name"]))
    return "\n".join(lines)


def _dir_bytes(files):
    """按第一层子目录汇总体积,给 `plan()` 用(让人一眼看出"大的是哪块")。"""
    agg = {}
    for rel, _p, size in files:
        top = rel.split(os.sep)[0] if os.sep in rel else "(根目录文件)"
        agg[top] = agg.get(top, 0) + size
    return sorted(agg.items(), key=lambda kv: -kv[1])


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def install_one(info, spec, commit="", force=False, dry_run=False):
    """真正落盘。返回 (目标目录, 备份目录或空串)。"""
    dest_root = skills_home()
    dest = os.path.join(dest_root, info["name"])
    if os.path.exists(dest) and not force:
        raise SkillError(
            "技能 %s 已经存在: %s。要覆盖请加 --force(旧目录会改名备份,不会被删)"
            % (info["name"], dest))
    if dry_run:
        return dest, ""
    os.makedirs(dest_root, exist_ok=True)
    backup = ""
    if os.path.exists(dest):
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        # 以 "." 开头是**必须的**:`scan_skills` 用 `<dir>/*/SKILL.md` 扫,而 glob 不匹配
        # 点开头的名字。写成 `name.bak-ts` 会让备份目录被当成第二个同名技能一起注入 ——
        # 表现为"技能列表里同一个技能出现两次",而两行描述一模一样,极难发现。
        backup = os.path.join(dest_root, ".bak-%s-%s" % (info["name"], ts))
        os.rename(dest, backup)      # 只改名,不删
    tmp = os.path.join(dest_root, ".%s.tmp-%d" % (info["name"], os.getpid()))
    try:
        # symlinks=False = **解引用**:见文件头铁律 4
        shutil.copytree(info["dir"], tmp, symlinks=False,
                        ignore=_ignore_factory(info["dir"], info.get("excludes")))
        meta = {
            "source": spec["url"],
            "ref": spec.get("ref") or "",
            "commit": commit,
            "subdir": spec.get("subdir") or "",
            "installed_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "bytes": info["bytes"],
            "files": [{"path": rel, "sha256": _sha256(p)} for rel, p, _ in info["files"]],
        }
        with open(os.path.join(tmp, ".source.json"), "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        os.rename(tmp, dest)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        if backup and not os.path.exists(dest):
            os.rename(backup, dest)     # 装失败要把旧的放回去
        raise
    return dest, backup


# ─────────────────────────────────────────────────────────── CLI
def _ask(question, ask="是否继续"):
    """yes/no 提问(装技能)。统一走 render.confirm_block,不再自带自己的语法。

    非 tty(管道/CI)一律当"否" —— fail-closed,不能因为读不到人就把东西装进去。
    """
    if not sys.stdin.isatty():
        return False
    from wchat_py.render import confirm_block
    return confirm_block(question.rstrip("?？"), ask=ask)


# ── 多技能:选出要装哪些(菜单 / --skill / --all)────────────────────
def _relposix(path, root):
    try:
        rel = os.path.relpath(path, root)
    except ValueError:
        rel = path
    return rel.replace(os.sep, "/")


def _skill_label(base, root):
    """给人看 / 给 --skill 用的标签:去掉开头的 `skills/`,如 `01-内容创作/audio-processing`。"""
    rp = _relposix(base, root)
    if rp.startswith("skills/"):
        rp = rp[len("skills/"):]
    return rp


def _parse_selection(line, idx):
    """解析菜单输入:`all`/`*` 全选,`1,3-5` 逗号/范围。返回 [(dir,name)];非法抛 ValueError。"""
    s = line.strip()
    if not s:
        return []
    if s.lower() in ("all", "*", "全部"):
        return list(idx.values())
    picked = []
    for part in re.split(r"[\s,]+", s):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)\s*[-~]\s*(\d+)", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > b:
                a, b = b, a
            rng = range(a, b + 1)
        elif part.isdigit():
            rng = [int(part)]
        else:
            raise ValueError("看不懂的选择 %r" % part)
        for k in rng:
            if k not in idx:
                raise ValueError("编号 %d 超出范围(1-%d)" % (k, len(idx)))
            picked.append(idx[k])
    out, seen = [], set()
    for c in picked:
        if id(c) not in seen:
            seen.add(id(c))
            out.append(c)
    return out


def _menu_choose(cands, root):
    """交互式按编号选技能(tty 专用)。返回选中的 [(dir,name)];空行取消=[];非 tty=None。"""
    if not sys.stdin.isatty():
        return None
    idx, groups, order = {}, {}, []
    for c in cands:
        label = _skill_label(c[0], root)
        cat, _, leaf = label.rpartition("/")
        cat = cat or "(根目录)"
        if cat not in groups:
            groups[cat] = []
            order.append(cat)
        num = len(idx) + 1
        idx[num] = c
        groups[cat].append((num, leaf or label))
    sys.stdout.write(
        "这个来源里有 %d 个技能。技能会常驻系统提示,启动时默认只注入最新 %s 个"
        "(SKILL_MAX_INJECT)。装哪些?\n" % (len(idx), os.environ.get("SKILL_MAX_INJECT", "30")))
    for cat in order:
        sys.stdout.write("  [%s]\n" % cat)
        for num, leaf in groups[cat]:
            sys.stdout.write("    %4d) %s\n" % (num, leaf))
    sys.stdout.write("输入编号(空格或逗号分隔,支持 1-5 范围)、all 装全部、直接回车取消: ")
    sys.stdout.flush()
    for _ in range(3):
        try:
            line = (sys.stdin.readline() or "").strip()
        except (KeyboardInterrupt, EOFError):
            line = ""
        if not line:
            sys.stdout.write("\n")
            return []
        try:
            sel = _parse_selection(line, idx)
        except ValueError as e:
            sys.stdout.write("  ✗ %s,请重输(回车取消): " % e)
            sys.stdout.flush()
            continue
        sys.stdout.write("\n")
        return sel
    sys.stdout.write("\n")
    return []


def _match_skills(tokens, cands, root):
    """把 `--skill` 名字解析成候选。支持 完整相对路径 / `分类/名字` / 唯一 basename。

    重名(本仓库就有 `competitor-analysis`、`customer-service` 跨分类重名)必须报歧义,
    不能静默挑一个 —— 那会让用户以为装的是另一类里的同名技能。
    """
    out, seen = [], set()
    for tok in tokens:
        t = tok.strip().replace("\\", "/").strip("/")
        if t.startswith("./"):
            t = t[2:]
        hit = []
        for c in cands:
            label = _skill_label(c[0], root)          # 01-内容创作/audio-processing
            bn = os.path.basename(os.path.normpath(c[0]))
            full = _relposix(c[0], root)              # skills/01-内容创作/audio-processing
            if t in (label, bn, full) or label.endswith("/" + t) or full.endswith("/" + t):
                hit.append(c)
        if not hit:
            raise SkillError(
                "--skill 里找不到技能 %r。先不带 --skill 跑一次,可看到全部候选名" % tok)
        if len(hit) > 1:
            raise SkillError(
                "--skill %r 匹配到多个技能,请用带分类的路径区分:\n%s"
                % (tok, "\n".join("    " + _skill_label(c[0], root) for c in hit)))
        c = hit[0]
        if id(c) not in seen:
            seen.add(id(c))
            out.append(c)
    return out


def _install_many(chosen, spec, commit, root, excludes, force, dry, yes):
    """多技能:逐个校验 → 汇总预览 → 确认一次 → 逐个落盘(单个失败不影响其余)。

    返回 ("many", "", None) 表示流程走完;取消 / 全不合格时返回 (None, "", None)。
    """
    valid, failed = [], []
    for c in chosen:
        label = _skill_label(c[0], root)
        try:
            info = inspect_skill(c[0], root, excludes=excludes)
        except SkillError as e:
            failed.append((label, str(e)))
            continue
        valid.append((label, c, info))

    # 同一批里两个技能若落到同一个 <SKILLS>/<name>,后一个会覆盖前一个 —— 提前揪出来。
    used, dedup = {}, []
    for label, c, info in valid:
        nm = info["name"]
        if nm in used:
            failed.append((label, "与 %s 的目标名 %s 重名,已跳过(可单独用 --name 装)"
                           % (used[nm], nm)))
            continue
        used[nm] = label
        dedup.append((label, c, info))
    valid = dedup

    if not valid:
        raise SkillError("选中的技能全部未通过校验:\n%s"
                         % "\n".join("    %s:%s" % (l, m) for l, m in failed))

    try:
        mi = int(os.environ.get("SKILL_MAX_INJECT", "30"))
    except (TypeError, ValueError):
        mi = 30
    tot_files = sum(i["count"] for _, _, i in valid)
    tot_bytes = sum(i["bytes"] for _, _, i in valid)
    sys.stdout.write("\n来源: %s  %s\n本次安装 %d 个技能(共 %d 文件 / %.1f MB):\n"
                     % (spec["url"] + ("#" + spec["ref"] if spec.get("ref") else ""),
                        ("· commit " + commit) if commit else "",
                        len(valid), tot_files, tot_bytes / 1048576.0))
    deps_all, trunc = set(), 0
    for label, _c, info in valid:
        line = "  · %-44s %4d 文件 / %5.1f MB" % (
            label, info["count"], info["bytes"] / 1048576.0)
        if info["deps"]:
            line += "  依赖:" + ",".join(info["deps"])
            deps_all.update(info["deps"])
        if info["desc_truncated"]:
            trunc += 1
        sys.stdout.write(line + "\n")
    for label, msg in failed:
        sys.stdout.write("  ✗ %-44s 跳过:%s\n" % (label, msg))
    if trunc:
        sys.stdout.write("⚠ %d 个技能的 description 超出 SKILL_DESC_MAX,注入时尾部会被截断\n"
                         % trunc)
    if deps_all:
        sys.stdout.write("⚠ 检测到依赖: %s —— 不会自动安装,装完请自己跑一次\n"
                         % ", ".join(sorted(deps_all)))
    if len(valid) > mi:
        # 关键诚实点:装进去 ≠ 生效。scan_skills 只注入最新 max_inject 个。
        sys.stdout.write(
            "⚠ 你选了 %d 个,但启动时只注入最新 %d 个(SKILL_MAX_INJECT=%d),"
            "其余不会出现在可用技能列表,模型也就用不到。\n"
            "   要全部生效:export SKILL_MAX_INJECT=%d(会增加系统提示体积/成本)\n"
            % (len(valid), mi, mi, len(valid)))

    if dry:
        return "many", "", None
    if not yes:
        if not _ask("安装这 %d 个技能?" % len(valid)):
            sys.stdout.write("已取消,未改动任何文件\n")
            return None, "", None

    ok, bad = [], []
    for label, _c, info in valid:
        try:
            dest, backup = install_one(info, spec, commit, force=force, dry_run=False)
            ok.append((label, dest, backup))
        except SkillError as e:
            bad.append((label, str(e)))
        except Exception as e:
            bad.append((label, "%s: %s" % (type(e).__name__, e)))
    sys.stdout.write("\n✅ 已安装 %d 个:\n" % len(ok))
    for label, dest, backup in ok:
        sys.stdout.write("  · %s -> %s%s\n"
                         % (label, dest, ("(旧版已备份 %s)" % backup) if backup else ""))
    if bad:
        sys.stdout.write("⚠ 跳过 %d 个:\n" % len(bad))
        for label, msg in bad:
            sys.stdout.write("  · %s:%s\n" % (label, msg))
    return "many", "", None


def cmd_add(argv):
    src = ""
    name_override = ref_override = subdir_override = ""
    excludes, skill_sel = [], []
    dry = force = yes = all_flag = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"):
            sys.stdout.write(
                "用法: wchat skills add <来源> [--name N] [--ref R] [--subdir D]"
                " [--skill A,B] [--all] [--dry-run] [--force] [--yes]\n"
                "  来源: owner/repo | https://host/o/r[/tree/分支/子目录] | 本地目录 | .zip/.tar.gz\n"
                "  --dry-run  只打印计划,不落盘\n"
                "  --exclude  逗号分隔的相对路径,安装时跳过(如 demos,docs)\n"
                "  --name     指定安装后的名字(只能用于单个技能;能救回不合法的名字)\n"
                "  --skill    只装这些技能(逗号分隔,用 分类/名字 或唯一名字),可多次给\n"
                "  --all      装来源里的全部技能(数量多时留意上下文预算 SKILL_MAX_INJECT)\n"
                "  --force    已存在时覆盖(旧目录改名为 .bak-<名字>-<时间戳>,不删除)\n")
            return 0
        elif a == "--name" and i + 1 < len(argv):
            name_override = argv[i + 1]; i += 2; continue
        elif a == "--ref" and i + 1 < len(argv):
            ref_override = argv[i + 1]; i += 2; continue
        elif a == "--subdir" and i + 1 < len(argv):
            subdir_override = argv[i + 1]; i += 2; continue
        elif a == "--skill" and i + 1 < len(argv):
            skill_sel += [x for x in re.split(r"[\s,]+", argv[i + 1]) if x.strip()]
            i += 2
            continue
        elif a == "--exclude" and i + 1 < len(argv):
            excludes += [x for x in argv[i + 1].split(",") if x.strip()]
            i += 2
            continue
        elif a == "--dry-run":
            dry = True
        elif a == "--force":
            force = True
        elif a == "--all":
            all_flag = True
        elif a == "--yes" or a == "-y":
            yes = True
        elif not src:
            src = a
        else:
            sys.stderr.write("⚠ 多余的参数已忽略: %r\n" % a)
        i += 1
    if not src:
        sys.stderr.write("用法: wchat skills add <owner/repo | URL | 本地目录>\n")
        return 2
    if all_flag and skill_sel:
        sys.stderr.write("❌ --all 与 --skill 不能同时用(二选一)\n")
        return 2
    tmpdir = tempfile.mkdtemp(prefix="wchat_skill_add_")
    try:
        try:
            spec = parse_source(src)
            if ref_override:
                spec["ref"] = ref_override
            if subdir_override:
                spec["subdir"] = subdir_override
            root, meta = fetch_source(spec, tmpdir)
            commit = meta.get("commit", "")
            if spec.get("subdir"):
                root = os.path.realpath(os.path.join(root, spec["subdir"]))
                if not os.path.isdir(root):
                    raise SkillError("仓库里没有子目录 %r" % spec["subdir"])
            cands = discover(root)
            if not cands:
                raise SkillError(
                    "在这个来源里没找到技能。找过这些位置:"
                    ".claude-plugin/marketplace.json 指向的目录、skills/*/SKILL.md、"
                    ".claude/skills/*/SKILL.md、根 SKILL.md。\n"
                    "  → 如果技能在某个子目录里,请加 --subdir <子目录>\n"
                    "  → 目录开头:%s" % ", ".join(sorted(os.listdir(root))[:12]))

            # ── 决定装哪些:--all / --skill / 单技能 / 交互菜单 ──
            if all_flag:
                chosen = list(cands)
            elif skill_sel:
                chosen = _match_skills(skill_sel, cands, root)
            elif len(cands) == 1:
                chosen = cands
            else:
                chosen = None
                if not yes and not dry:      # 交互才弹菜单;--yes/--dry-run 属脚本场景,不猜
                    chosen = _menu_choose(cands, root)
                if chosen is None:
                    bycat = {}
                    for c in cands:
                        cat = _skill_label(c[0], root).rpartition("/")[0] or "(根目录)"
                        bycat[cat] = bycat.get(cat, 0) + 1
                    summary = ", ".join("%s %d 个" % (k, bycat[k]) for k in bycat)
                    raise SkillError(
                        "这个来源里有 %d 个技能(%s),装哪些请明确指定:\n"
                        "  · --all               全部(注意:启动时默认只注入最新 %s 个)\n"
                        "  · --skill 名1,名2      按名选(重名可用 分类/名字 区分)\n"
                        "  · --subdir <分类目录>  只装某一类\n"
                        "  · 直接交互运行(别在脚本里加 --yes)会弹出编号菜单\n"
                        "候选(前 20 个):\n%s"
                        % (len(cands), summary, os.environ.get("SKILL_MAX_INJECT", "30"),
                           "\n".join("    " + _skill_label(c[0], root) for c in cands[:20])))
                if not chosen:
                    sys.stdout.write("已取消,未改动任何文件\n")
                    return 0

            if len(chosen) > 1 and name_override:
                raise SkillError("--name 只能用于单个技能;要装多个请去掉 --name")

            if len(chosen) == 1:
                info = inspect_skill(chosen[0][0], root, excludes=excludes,
                                     name_override=name_override)
                sys.stdout.write(plan(spec, info, commit) + "\n")
                if info["desc_truncated"]:
                    sys.stdout.write("⚠ description 超出预算:注入时会从尾巴砍,"
                                     "请把最可能被搜到的词放前 30 个字\n")
                if not yes and not dry:
                    if not _ask("继续安装?"):
                        sys.stdout.write("已取消,未改动任何文件\n")
                        return 0
                dest, backup = install_one(info, spec, commit, force=force, dry_run=dry)
            else:
                dest, backup, info = _install_many(
                    chosen, spec, commit, root, excludes, force, dry, yes)
                if dest is None:             # 取消 / 全部不合格
                    return 0
        except SkillError as e:
            sys.stderr.write("❌ %s\n" % e)
            return 1
        except Exception as e:      # 兜底:磁盘满/权限/git 崩溃等,给一句人话 + 类型
            sys.stderr.write("❌ 安装失败(%s): %s\n"
                             "   技能目录未改动;若是权限问题,检查 %s 的属主\n"
                             % (type(e).__name__, e, skills_home()))
            return 1
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    if dry:
        sys.stdout.write("(dry-run)未落盘。去掉 --dry-run 才会真正安装。\n")
        return 0
    if len(chosen) == 1:
        sys.stdout.write("✅ 已安装: %s\n" % dest)
        if backup:
            sys.stdout.write("   旧版本已备份为: %s(未删除)\n" % backup)
        if info["deps"]:
            sys.stdout.write("⚠ 依赖还没装 —— 按上面那两行命令自己跑一次,"
                             "然后新开会话确认它能被触发。\n")
    sys.stdout.write("   验证: 新开一个会话,用自然说法问一句,看它是否出现在可用技能列表里\n")
    return 0


def cmd_remove(argv):
    name = ""
    for a in argv:
        if a in ("-h", "--help"):
            sys.stdout.write("用法: wchat skills remove <名字>\n"
                             "  不会删除:目录被移进 <技能目录>/.trash/<名字>-<时间戳>/\n")
            return 0
        if not name:
            name = a
    if not name:
        sys.stderr.write("用法: wchat skills remove <名字>\n")
        return 2
    try:
        n = norm_name(name)
    except SkillError as e:
        sys.stderr.write("❌ %s\n" % e)
        return 1
    root = skills_home()
    dest = os.path.join(root, n)
    if not os.path.isdir(dest):
        sys.stderr.write("❌ 没有这个技能: %s\n" % dest)
        return 1
    trash = os.path.join(root, ".trash")
    os.makedirs(trash, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    tgt = os.path.join(trash, "%s-%s" % (n, ts))
    os.rename(dest, tgt)
    sys.stdout.write("✅ 已移出: %s -> %s(要恢复就 mv 回去)\n" % (dest, tgt))
    return 0


def main(argv):
    if not argv:
        sys.stderr.write("用法: wchat skills add <来源> | wchat skills remove <名字>\n")
        return 2
    if argv[0] == "add":
        return cmd_add(argv[1:])
    if argv[0] in ("remove", "rm", "uninstall"):
        return cmd_remove(argv[1:])
    sys.stderr.write("skillinstall: 未知子命令 %r\n" % argv[0])
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))