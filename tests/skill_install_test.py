#!/usr/bin/env python3
"""技能安装闸门:`wchat skills add/remove` 的六条铁律各一条断言。

**全离线**:所有"仓库"都是临时目录里现造的(`git init` + 提交),不联网、不装依赖。
只有这样才能进 `run_gates.sh` —— 一道需要网络的闸门等于一道随时会假红的闸门。

钉住的东西(每条都对应一个真实会踩的坑,见 `wchat_py/skillinstall.py` 文件头):
  1. 检出后**只装不跑**:装进来的 .py 里那句 `raise`/写文件绝不执行;
  2. 装之前必须能拿到"来源/文件数/依赖",`--dry-run` 不落盘;
  3. 已存在默认拒绝;`--force` 只改名备份,**旧内容一个字都不能少**;
  4. 软链一律**解引用成实体文件** —— 这条是变异目标:改回 `symlinks=True` 必须红;
  5. 指向仓库外的符号链接必须**在复制之前**被拒(否则 /etc/passwd 会被抄进技能目录);
  6. 落盘后 `.source.json` 有来源/commit/逐文件 sha256;`remove` 只移不删。

另钉三条"装完真能用":
  · 装完 `scan_skills(external=1)` 能看到它(软链仓库的经典故障:装上了但看不见);
  · `read_skill` 回给模型的正文里带**技能目录**(否则带脚本的技能必然跑不起来);
  · 依赖只打印、不安装。

用法:`python3 tests/skill_install_test.py`(可用 MODULE=… 指向变异体)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

MODULE = os.environ.get("MODULE") or "wchat_py.skillinstall"
import importlib  # noqa: E402
SI = importlib.import_module(MODULE)

P = F = 0


def ck(name, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  ok   %s" % name)
    else:
        F += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


W = tempfile.mkdtemp(prefix="skill_install_gate_")
os.environ["CT_HOME"] = os.path.join(W, "ct")
SKILLS = os.path.join(W, "ct", "skills")
os.environ["SKILLS_DIR_DEFAULT"] = SKILLS
os.environ["WCHAT_NO_SETUP"] = "1"


def mk_skill(path, name="demo", desc="演示技能,用于闸门"):
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "SKILL.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: %s\ndescription: %s\n---\n\n正文\n" % (name, desc))
    return path


def git_repo(path):
    subprocess.run(["git", "init", "-q", path], check=True)
    subprocess.run(["git", "-C", path, "add", "-A"], check=True)
    subprocess.run(["git", "-C", path, "-c", "user.email=a@b", "-c", "user.name=a",
                    "commit", "-qm", "init"], check=True)
    return path


def run(argv):
    """跑 CLI,返回 (rc, stdout+stderr)。"""
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        rc = SI.main(list(argv))
    return rc, buf.getvalue()


print("=== 1. 发现:四种仓库形态都要认得 ===")
# 1a 根 SKILL.md(单技能仓库)
r_root = git_repo(mk_skill(os.path.join(W, "r_root"), "root-skill"))
cands = SI.discover(r_root)
ck("根 SKILL.md 被认出", [os.path.basename(c[0]) for c in cands] == ["r_root"], repr(cands))
# 1b skills/* 多技能
r_multi = os.path.join(W, "r_multi")
mk_skill(os.path.join(r_multi, "skills", "aa"), "aa")
mk_skill(os.path.join(r_multi, "skills", "bb"), "bb")
ck("skills/* 多技能都被认出", len(SI.discover(r_multi)) == 2, repr(SI.discover(r_multi)))
# 1c .claude-plugin/marketplace.json
r_plug = os.path.join(W, "r_plug")
mk_skill(os.path.join(r_plug, "skills", "cc"), "cc")
os.makedirs(os.path.join(r_plug, ".claude-plugin"), exist_ok=True)
with open(os.path.join(r_plug, ".claude-plugin", "marketplace.json"), "w") as fh:
    json.dump({"plugins": [{"name": "p", "source": "./"}]}, fh)
ck("marketplace.json 指向的目录被扫到", len(SI.discover(r_plug)) == 1, repr(SI.discover(r_plug)))
# 1d 软链样式(Auto-Redbook-Skills 那种):技能实体在链接目标那一层
r_link = os.path.join(W, "r_link")
mk_skill(r_link, "linked-skill")
os.makedirs(os.path.join(r_link, "skills", "linked-skill"), exist_ok=True)
os.symlink("../../SKILL.md", os.path.join(r_link, "skills", "linked-skill", "SKILL.md"))
os.makedirs(os.path.join(r_link, "scripts"), exist_ok=True)
open(os.path.join(r_link, "scripts", "run.py"), "w").write("print(1)\n")
cands = SI.discover(r_link)
ck("软链式技能被认出且只认一次(同一份 SKILL.md 不重复)",
   len(cands) == 1 and os.path.realpath(cands[0][0]) == os.path.realpath(r_link),
   repr(cands))

print("=== 2. 校验:不合格的必须在落盘前拒掉 ===")
bad = os.path.join(W, "bad_nodesc")
os.makedirs(bad, exist_ok=True)
open(os.path.join(bad, "SKILL.md"), "w").write("---\nname: nodesc\n---\n\nbody\n")
try:
    SI.inspect_skill(bad, bad)
    ck("缺 description → 拒", False, "没拒绝")
except SI.SkillError as e:
    ck("缺 description → 拒(它是唯一触发面,缺了等于没装)", "description" in str(e), str(e)[:80])

bad2 = os.path.join(W, "bad_name")
os.makedirs(bad2, exist_ok=True)
open(os.path.join(bad2, "SKILL.md"), "w").write(
    "---\nname: Bad Name Here\ndescription: d\n---\n\nb\n")
try:
    SI.inspect_skill(bad2, bad2)
    ck("非法名字 → 拒(不静默改名)", False, "没拒绝")
except SI.SkillError as e:
    ck("非法名字 → 拒(不静默改名)", "合法标识" in str(e), str(e)[:80])

big = os.path.join(W, "big")
mk_skill(big, "bigone")
open(os.path.join(big, "huge.bin"), "wb").write(b"x" * (64 * 1024))
try:
    SI.inspect_skill(big, big, max_bytes=1024)
    ck("超体积 → 拒", False, "没拒绝")
except SI.SkillError as e:
    ck("超体积 → 拒(否则 node_modules 会被搬进 ~/.ct/skills)", "上限" in str(e), str(e)[:80])

# `--name` 必须能救回不合法名字(报错文案就是这么建议的,不能建议了却做不到)
resc = os.path.join(W, "rescue")
os.makedirs(resc, exist_ok=True)
open(os.path.join(resc, "SKILL.md"), "w").write(
    "---\nname: Bad Name\ndescription: d\n---\n\nb\n")
_info = SI.inspect_skill(resc, resc, name_override="rescued-name")
ck("--name 能救回 frontmatter 里不合法的名字", _info["name"] == "rescued-name",
   repr(_info["name"]))

print("=== 3. 逃逸链接:复制之前就要拒(否则系统文件被抄进技能目录) ===")
esc = os.path.join(W, "esc")
mk_skill(esc, "escape")
open(os.path.join(W, "secret.txt"), "w").write("TOPSECRET\n")
os.symlink(os.path.join(W, "secret.txt"), os.path.join(esc, "leak.txt"))
try:
    SI.inspect_skill(esc, esc)
    ck("指向仓库外的符号链接 → 拒", False, "没拒绝")
except SI.SkillError as e:
    ck("指向仓库外的符号链接 → 拒", "符号链接" in str(e), str(e)[:100])
ck("被拒时目标目录没被创建", not os.path.exists(os.path.join(SKILLS, "escape")))

print("=== 4. 安装:解引用 / 记账 / 目录可见 ===")
src_link = os.path.join(W, "inst_link")
mk_skill(src_link, "installed-demo", "装机演示技能")
os.makedirs(os.path.join(src_link, "scripts"), exist_ok=True)
open(os.path.join(src_link, "scripts", "boom.py"), "w").write(
    "open(%r, 'w').write('EXECUTED')\nraise SystemExit('must not run')\n" % os.path.join(W, "ran.txt"))
open(os.path.join(src_link, "requirements.txt"), "w").write("playwright\n")
# 仓库里"资源文件用软链共享"很常见(真实例子:SKILL.md 本身、assets/themes/*.css)。
# 装进来必须是**实体副本**:保留链接的话,它在技能目录里指向一个不存在的相对路径,
# 表现为"技能装上了但样式/模板读不到"。
os.makedirs(os.path.join(src_link, "assets"), exist_ok=True)
os.makedirs(os.path.join(src_link, "shared"), exist_ok=True)
open(os.path.join(src_link, "shared", "theme.css"), "w").write("body{color:red}\n")
os.symlink("../shared/theme.css", os.path.join(src_link, "assets", "theme.css"))
git_repo(src_link)
rc, out = run(["add", src_link, "--yes"])
ck("安装成功 rc=0", rc == 0, out[-300:])
dest = os.path.join(SKILLS, "installed-demo")
ck("落在 <SKILLS>/<name>/SKILL.md", os.path.isfile(os.path.join(dest, "SKILL.md")))
ck("SKILL.md 是**实体文件**(不是软链:外置模式会跳过软链条目)", 
   not os.path.islink(os.path.join(dest, "SKILL.md")))
ck("**没有执行**仓库里的脚本(装 ≠ 跑)", not os.path.exists(os.path.join(W, "ran.txt")))
_theme = os.path.join(dest, "assets", "theme.css")
ck("仓库内的软链资源被**解引用成实体文件**(不是断链)",
   os.path.isfile(_theme) and not os.path.islink(_theme)
   and "color:red" in open(_theme, encoding="utf-8").read(),
   "isfile=%r islink=%r" % (os.path.isfile(_theme), os.path.islink(_theme)))
meta_p = os.path.join(dest, ".source.json")
ck(".source.json 存在", os.path.isfile(meta_p))
meta = json.load(open(meta_p, encoding="utf-8"))
ck(".source.json 有 source/installed_at/files",
   meta.get("source") and meta.get("installed_at") and meta.get("files"), repr(meta)[:160])
ck(".source.json 逐文件带 sha256",
   all(len(f.get("sha256", "")) == 64 for f in meta["files"]), repr(meta["files"][:1]))
ck("依赖文件被原样复制(说明只拷贝、没有解析安装)",
   os.path.isfile(os.path.join(dest, "requirements.txt")))
ck("装完没有多出 site-packages/node_modules(真的没装依赖)",
   not any(x in os.listdir(dest) for x in ("site-packages", "node_modules")))
ck("依赖清单出现在提示里", "requirements.txt" in out and "不会自动安装" in out, out[-260:])
# 装完必须能被注入侧看到
from wchat_py.skill import scan_skills, read_skill  # noqa: E402
_inject = scan_skills(SKILLS, "30", "200", "1")
ck("装完 scan_skills(external=1) 能看到它", "installed-demo" in _inject, _inject[:200])
_rd = read_skill("installed-demo", SKILLS, "30000", "1")
ck("read_skill 正文里带技能目录(否则带脚本的技能必然跑不起来)",
   os.path.join(SKILLS, "installed-demo") in _rd["content"], _rd["content"][:160])

print("=== 5. 已存在:默认拒;--force 只备份不删 ===")
rc, out = run(["add", src_link, "--yes"])
ck("重复安装被拒 rc≠0", rc != 0 and "已经存在" in out, out[-200:])
old = open(os.path.join(dest, "SKILL.md"), encoding="utf-8").read()
with open(os.path.join(dest, "LOCAL-EDIT.txt"), "w") as fh:
    fh.write("我自己加的\n")
rc, out = run(["add", src_link, "--yes", "--force"])
ck("--force 装成功", rc == 0, out[-200:])
baks = [d for d in os.listdir(SKILLS) if d.startswith(".bak-installed-demo-")]
ck("旧目录被改名备份(不是删除)", len(baks) == 1, repr(baks))
ck("备份名以 . 开头(否则 scan_skills 的 glob 会把它当成第二个同名技能)",
   all(b.startswith(".") for b in baks), repr(baks))
from wchat_py.skill import scan_skills as _scan  # noqa: E402
_now = _scan(SKILLS, "30", "200", "1")
ck("--force 之后注入列表里这个技能只出现一次(备份不混进来)",
   _now.count("installed-demo:") == 1, _now[:300])
ck("备份里保留了我改过的内容(一个字都没少)",
   os.path.isfile(os.path.join(SKILLS, baks[0], "LOCAL-EDIT.txt"))
   and open(os.path.join(SKILLS, baks[0], "SKILL.md"), encoding="utf-8").read() == old)

print("=== 6. --dry-run 不落盘;remove 只移不删 ===")
shutil.rmtree(os.path.join(SKILLS, "installed-demo"))
for d in baks:
    shutil.rmtree(os.path.join(SKILLS, d), ignore_errors=True)
src2 = os.path.join(W, "dry")
mk_skill(src2, "dry-run-demo")
rc, out = run(["add", src2, "--dry-run"])
ck("--dry-run 打印计划", rc == 0 and "dry-run" in out and "dry-run-demo" in out, out[-160:])
ck("--dry-run 真的没落盘", not os.path.exists(os.path.join(SKILLS, "dry-run-demo")))
run(["add", src2, "--yes"])
rc, out = run(["remove", "dry-run-demo"])
ck("remove rc=0", rc == 0, out[-160:])
ck("remove 之后技能目录里没有了", not os.path.exists(os.path.join(SKILLS, "dry-run-demo")))
trash = [d for d in os.listdir(os.path.join(SKILLS, ".trash")) if d.startswith("dry-run-demo-")]
ck("但内容在 .trash/ 里(不删除,可恢复)", len(trash) == 1
   and os.path.isfile(os.path.join(SKILLS, ".trash", trash[0], "SKILL.md")))

print("=== 7. 不支持来源/多技能仓库:说清怎么办,不猜 ===")
try:
    SI.parse_source("/nonexistent/xyz")
    ck("不存在的本地路径 → 拒", False, "没拒绝")
except SI.SkillError as e:
    ck("不存在的本地路径 → 拒", "看不懂" in str(e) or "不是目录" in str(e), str(e)[:80])
rc, out = run(["add", r_multi, "--yes"])
ck("多技能仓库不给 --subdir 时报错并列出候选", rc != 0 and "aa" in out and "--subdir" in out,
   out[-300:])

print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
shutil.rmtree(W, ignore_errors=True)
sys.exit(0 if F == 0 else 1)

