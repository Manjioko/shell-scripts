#!/usr/bin/env python3
"""`wchat model` / `wchat mcp` / `wchat skills` 三个子命令的闸门。

这三件事各自守一条"看起来能用、其实会伤到人"的风险:

1. **model**:实现只有一份。`bin/ctmodel` 是壳,`wchat model` 是本体;
   两边必须改同一个文件、同一个组,不允许各算各的。
2. **mcp**:清单从上游默认位收进 `~/.ct/`。风险是"老用户升级后 server 全没了"
   (那不是没配、是我们换了读取位置),所以要有一次性迁移。
   另一条更硬:清单里的 env 可能是真凭据,必须进 H6/P1 名单 —— 由别的闸门守。
3. **skills**:随包技能只在**目标不存在**时播种;已有技能一个字都不许改。
   这一条是本文最重要的断言:那个目录里丢过 5 个技能文件且不可恢复。
"""
import json
import os
import shutil
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
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


_TMPDIRS = []


def fresh():
    d = tempfile.mkdtemp(prefix="cmds_", dir=REPO)
    _TMPDIRS.append(d)
    os.environ["HOME"] = os.path.join(d, "home")
    os.environ["CT_HOME"] = os.path.join(d, "ct")
    os.makedirs(os.environ["HOME"])
    os.makedirs(os.environ["CT_HOME"])
    for k in ("MCP_CONFIG", "SKILLS_DIR", "SKILLS_DIR_DEFAULT", "SKILL_CONFIG"):
        os.environ.pop(k, None)
    return d


# ---------------- 1) wchat model 与 ctmodel 壳是同一个实现 ----------------
print("=== 1. model 子命令 ===")
d = fresh()
from wchat_py import setup                      # noqa: E402

cfg = setup.config_path()
open(cfg, "w").write(
    'BASE_URL="https://a/v1/chat/completions" # @model deepseek\n'
    'PRO_MODEL="ds" # @model deepseek\n'
    '# BASE_URL="https://q/v1/chat/completions" # @model qwen\n'
    '# PRO_MODEL="qw" # @model qwen\n')
os.chmod(cfg, 0o600)

ck("无参显示当前组", (setup.cmd_model([]) == 0) and setup.active_group() == "deepseek")
ck("-l 列出两组", setup.model_groups() == ["deepseek", "qwen"], repr(setup.model_groups()))
ck("切换成功", setup.cmd_model(["qwen"]) == 0 and setup.active_group() == "qwen")
ck("切换后读到的值也换了", setup.read_config().get("PRO_MODEL") == "qw",
   repr(setup.read_config()))
ck("切换留了备份", os.path.isfile(cfg + ".bak"))
ck("备份权限 600", (os.stat(cfg + ".bak").st_mode & 0o777) == 0o600)
ck("undo 回滚", setup.cmd_model(["undo"]) == 0 and setup.active_group() == "deepseek")
ck("未知组返回需要人处理(非 0)", setup.cmd_model(["nosuch"]) != 0)
ck("-n 只预览不落盘", setup.cmd_model(["qwen", "-n"]) == 0 and setup.active_group() == "deepseek")

# 壳 = 同一个实现:直接跑 configs/ctmodel.py,改的必须是同一份文件
import subprocess                                # noqa: E402
r = subprocess.run([sys.executable, os.path.join(REPO, "configs", "ctmodel.py"), "qwen"],
                   capture_output=True, text=True,
                   env=dict(os.environ, CT_HOME=os.environ["CT_HOME"]))
ck("ctmodel 壳切换成功", r.returncode == 0, (r.stdout + r.stderr)[:200])
ck("壳与本体改同一文件", setup.active_group() == "qwen", repr(setup.active_group()))
# 结构锁:壳里不许再有第二套逻辑(只允许转调)
_shell = open(os.path.join(REPO, "configs", "ctmodel.py"), encoding="utf-8").read()
_shell_body = "\n".join(ln for ln in _shell.split("\n") if not ln.strip().startswith("#"))
ck("壳里没有第二套实现", "cmd_model(" in _shell_body and "def cmd_switch" not in _shell_body)

# ---------------- 2) mcp 清单在 ~/.ct/ 下 + 一次性迁移 ----------------
print("=== 2. mcp 子命令 ===")
from wchat_py import seed                        # noqa: E402
from wchat_py import mcp_bridge                  # noqa: E402

d = fresh()
ck("默认路径在 ~/.ct 下",
   mcp_bridge.config_path() == os.path.join(os.environ["CT_HOME"], "mcporter.json"),
   mcp_bridge.config_path())
ck("没清单时 status 不炸", seed.cmd_mcp([]) == 0)
# 唯一入口:ensure_manifest 负责"新用户从模板生成"
_p, _why = seed.ensure_manifest()
ck("新用户:自动从模板生成", _p == seed.mcp_dest(), repr((_p, _why)))
ck("生成时说清了来源", "模板" in _why, _why)
ck("生成的清单权限 600", (os.stat(seed.mcp_dest()).st_mode & 0o777) == 0o600)
ck("只默认开 chrome",
   mcp_bridge.config_servers() == ["chrome"], repr(mcp_bridge.config_servers()))
# 幂等:再调不覆盖
_before = open(seed.mcp_dest(), encoding="utf-8").read()
_p2, _why2 = seed.ensure_manifest()
ck("再 ensure 不覆盖", _p2 is None and _why2 == "已存在", repr((_p2, _why2)))
ck("再 ensure 一个字都没改",
   open(seed.mcp_dest(), encoding="utf-8").read() == _before)
# 结构锁:**不许再有 init 子命令**。两套机制并存正是这次要消灭的东西,
# 所以直接查源码里没有 "init" 这个分派,而不是查它的输出(输出措辞会变)。
_src = open(os.path.join(REPO, "wchat_py", "seed.py"), encoding="utf-8").read()
_help = open(os.path.join(REPO, "wchat_py", "seed.py"), encoding="utf-8").read()
ck("seed 里不再有 init 分派", 'in ("init", "--init")' not in _src)
ck("help 文本不再教人 init", "init:" not in _help)

# 迁移:目标不存在 + 老位置有 -> 复制一份过去
d = fresh()
legacy_dir = os.path.join(os.environ["HOME"], ".mcporter")
os.makedirs(legacy_dir)
legacy = os.path.join(legacy_dir, "mcporter.json")
with open(legacy, "w", encoding="utf-8") as f:
    json.dump({"imports": [], "mcpServers": {"old": {"command": "x"}}}, f)
ck("迁移成功", seed.migrate_from_legacy() == seed.mcp_dest())
ck("迁移后能读到老 server", mcp_bridge.config_servers() == ["old"],
   repr(mcp_bridge.config_servers()))
ck("迁移不删原件", os.path.isfile(legacy))
# 目标已存在时不再迁移(不覆盖用户在 ~/.ct 里的新配置)
with open(seed.mcp_dest(), "w", encoding="utf-8") as f:
    json.dump({"imports": [], "mcpServers": {"mine": {}}}, f)
ck("已有新清单时不迁移", seed.migrate_from_legacy() is None)
ck("新清单未被老清单盖掉", mcp_bridge.config_servers() == ["mine"],
   repr(mcp_bridge.config_servers()))

# 模板里不许有明文密钥(只查形态:env 值必须是 ${...} 或空)
_tpl = json.load(open(seed.mcp_template(), encoding="utf-8"))
_leak = []
for sname, s in (_tpl.get("mcpServers") or {}).items():
    for k, v in (s.get("env") or {}).items():
        if isinstance(v, str) and v and not (v.startswith("${") and v.endswith("}")):
            _leak.append("%s.%s" % (sname, k))
ck("模板里没有明文密钥", not _leak, repr(_leak))

# ---------------- 3) 技能只补缺、不覆盖 ----------------
print("=== 3. skills 子命令 ===")
d = fresh()
dest = seed.skills_dest()
os.makedirs(os.path.join(dest, "my-own"), exist_ok=True)
with open(os.path.join(dest, "my-own", "SKILL.md"), "w", encoding="utf-8") as f:
    f.write("---\nname: my-own\ndescription: 我自己写的\n---\n\n正文\n")
_mine_before = open(os.path.join(dest, "my-own", "SKILL.md"), encoding="utf-8").read()

added, skipped, msg = seed.seed_skills()
ck("首次播种有新增", len(added) >= 7, msg)
ck("播种保留了用户自己的技能",
   open(os.path.join(dest, "my-own", "SKILL.md"), encoding="utf-8").read() == _mine_before)
ck("播种不把用户技能算成自己的", "my-own" not in added)

# 改一个已播种的技能,再播一次:必须一个字都不改(这条是最硬的)
_some = added[0]
_target = os.path.join(dest, _some, "SKILL.md")
with open(_target, "a", encoding="utf-8") as f:
    f.write("\n## 我本地改的行\n")
_edited = open(_target, encoding="utf-8").read()
seed.seed_skills(force=True)
ck("再次播种:改过的技能不被覆盖",
   open(_target, encoding="utf-8").read() == _edited)

rows = dict((n, (src, dirty)) for n, src, dirty in seed.skills_status())
ck("状态能认出本地改过的", rows.get(_some, ("", False))[1] is True, repr(rows.get(_some)))
ck("状态能认出自己写的", rows.get("my-own", ("",))[0] == "local", repr(rows.get("my-own")))
ck("播种留了 marker", os.path.isfile(os.path.join(os.environ["CT_HOME"], ".seeded.json.skills")))

# 随包技能目录本身必须存在(否则发布包少东西,播种静默失效)
ck("包里有 skills 源目录", os.path.isdir(seed.skills_src()), seed.skills_src())
ck("源目录里技能数 > 0", len(os.listdir(seed.skills_src())) > 0)

# 清理必须在**断言之后、打印结论之前**:以前只清了最后一节的目录,
# 前面每节 fresh() 建的都留在仓库根,跑几次就是几十个 cmds_xxx 垃圾目录。
for _d in _TMPDIRS:
    shutil.rmtree(_d, ignore_errors=True)
print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
sys.exit(0 if F == 0 else 1)

