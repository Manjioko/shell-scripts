#!/usr/bin/env python3
"""deb 打包闸门:**快检查**(不构建、不装),守住 deb 的形态契约。

真构建 + 真装(dpkg -i + 跑 wchat)在 `packaging/linux/{build-deb,test-deb}.sh` 里,
需要 docker 与几分钟,不适合进闸门表。这里钉的是**那些一改就悄悄坏掉的约定**:

  1. control 字段齐全(少了 Depends: python3-venv,装完就是"venv 建不出来");
  2. postinst 必须 `--no-index`(运行期无网,联网 pip 会让 apt install 挂住);
  3. postinst 幂等(apt 升级会再调一次,不能每次重装依赖);
  4. prerm 删 venv(venv 的 shebang 绑死当前 Python,升级后是僵尸态);
  5. wrapper 指向 `venv/bin/python`,不是 `/usr/bin/env python3`
     (后者会用系统 python,依赖都不在);
  6. make_deb 与 make_release **共用同一份文件清单**(两份实现必漂);
  7. 验收脚本**不把仓库源码挂成可写工作目录**(挂了就测不出"包自足")。

这些判据都来自"装出来才发现"的真实形状,不是想象。
"""
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = F = 0


def ck(name, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  ok   %s" % name)
    else:
        F += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


def read(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return f.read()


SRC = read("packaging/linux/make_deb.py")
ACC = read("packaging/linux/deb-acceptance.sh")
BUILD = read("packaging/linux/build-deb.sh")

print("=== 1. control 字段 ===")
ctrl = SRC[SRC.index("CONTROL = "):SRC.index("POSTINST = ")]
for field in ("Package:", "Version:", "Architecture:", "Depends:", "Description:"):
    ck("control 有 %s" % field, field in ctrl, ctrl[:200])
# python3-venv 不在 Depends 里 → postinst 那句 `python3 -m venv` 会失败,
# 而报错是 ensurepip 的一行提示,很难第一眼看懂。
ck("Depends 含 python3-venv", "python3-venv" in ctrl, ctrl[:300])
ck("Depends 有 python3 版本下限", re.search(r"python3 \(>= 3\.9\)", ctrl) is not None, ctrl[:300])

print("=== 2. postinst:离线 + 幂等 + 失败要停 ===")
post = SRC[SRC.index("POSTINST = "):SRC.index("PRERM = ")]
ck("pip 走 --no-index(运行期无网)", "--no-index" in post, post[:400])
ck("pip 指向包内 wheels", "--find-links" in post and "wheels" in post, post[:400])
ck("装依赖前先探一次 import(幂等,升级不重装)",
   "import openai, httpx, prompt_toolkit, rich, PIL" in post, post[:400])
ck("建 venv 失败要 exit 1(不许带着半成品继续)", "exit 1" in post)
ck("没有裸 pip install(会去网上下)", re.search(r"pip\"?\s+install(?![^\n]*--no-index)", post) is None,
   "postinst 里有不带的 --no-index 的 pip install")

print("=== 3. prerm:卸载/升级前删 venv ===")
prerm = SRC[SRC.index("PRERM = "):SRC.index("WRAPPER = ")]
ck("prerm 删 venv", "rm -rf /usr/lib/wchat/venv" in prerm)
ck("prerm 覆盖 upgrade 与 remove",
   'upgrade' in prerm and 'remove' in prerm, prerm[:300])

print("=== 4. wrapper 必须显式指定 venv 解释器 ===")
wr = SRC[SRC.index("WRAPPER = "):SRC.index("CTMODEL_WRAPPER = ")]
ck("wrapper exec 到 venv 的 python", "/usr/lib/wchat/venv/bin/python" in wr, wr[:200])
# 只看**非注释行**:wr 里那句注释本身就是"不用 env python3"的说明,
# 拿整段做子串判断会把说明算成违规(第一版就误报了)。
_wr_code = "\n".join(ln for ln in wr.splitlines() if not ln.lstrip().startswith("#"))
ck("wrapper 代码里不用 env python3(会用系统 python,依赖全不在)",
   "env python3" not in _wr_code, _wr_code[:200])
ck("ctmodel 壳转成 `wchat model`", '"model"' in SRC or "model" in SRC, "")
ctm = SRC[SRC.index("CTMODEL_WRAPPER = "):]
ck("ctmodel 是转调而不是第二份实现", "/usr/lib/wchat/wchat\" model" in ctm or 'model "$@"' in ctm,
   ctm[:200])

print("=== 5. 文件清单与 brew 包同源(结构锁) ===")
ck("make_deb 从 make_release import 清单",
   "from make_release import" in SRC and "INCLUDE" in SRC, "")
ck("没有自己抄一份 INCLUDE 列表",
   not re.search(r"^INCLUDE\s*=\s*\[", SRC, re.M), "make_deb.py 里出现了第二份 INCLUDE")
ck("剔除规则也复用(SKIP_DIRS/SKIP_NAMES/SKIP_SUFFIX)",
   all(k in SRC for k in ("SKIP_DIRS", "SKIP_NAMES", "SKIP_SUFFIX")))

print("=== 6. 验收脚本必须真的干净(不挂源码) ===")
# 挂了源码 → 包里的东西缺了也测不出来(容器里 cwd 就有那份代码)。
ck("验收里没有把 /src 当工作目录 cd 进去", "cd /src" not in ACC, "")
ck("验收在 /tmp 跑命令(验证不依赖 cwd)", "cd /tmp" in ACC)
ck("验收检查装前无 wchat(否则测不出东西)", "/usr/bin/wchat" in ACC and "装前" in ACC)
ck("验收检查卸载后 venv 已删", "venv 还在" in ACC)
ck("验收用 [ -e 路径 ] 判残留,不用 command -v",
   "[ -e /usr/bin/wchat ]" in ACC and "command -v wchat" not in ACC,
   "dash 会缓存命令路径,command -v 在文件删掉后仍报旧值 —— 实测假红过一次")
ck("验收有唯一的结论行(没有它就分不清跑完还是崩了)", "DEB-ACCEPTANCE-OK" in ACC)

print("=== 7. 构建脚本:wheel 必须在**有网**的那一侧取 ===")
ck("build-deb 分两步(先取 wheel 再打 deb)", "wheelhouse" in BUILD and "make_deb.py" in BUILD,
   BUILD[:300])
ck("Dockerfile.deb 用 --only-binary(避免了现场编译)", "--only-binary=:all:" in
   read("packaging/linux/Dockerfile.deb"),
   "需要编译的依赖会让离线安装变成天方夜谭")

print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
sys.exit(0 if F == 0 else 1)