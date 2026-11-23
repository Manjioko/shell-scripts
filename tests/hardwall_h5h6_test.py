#!/usr/bin/env python3
"""H5(dotfile 写)/ H6(凭据)误判修复回归闸门。

2026-11 用户实报 + 生产活体复现:H5/H6 判定前不剥注释、且 H5 把**任何 `>`** 都当重定向,
于是纯只读命令被误判、命中碎成 `1`/`>`/`$b`(截图里的 `命中:1`/`命中:+`)。
修复:判定前 `strip_comments` 剥 `#` 注释;`_write_view` 只把**真 shell 重定向**算写
(引号内 / `[[ ]]` / `(( ))` / 非 shell heredoc 正文 里的 `>` 不算)。
本闸门钉死:这些误判场景必须放行(NP)、真写/真读必须照拦(H5/H6),且 H5 命中是目标/动词、非碎片。
全离线。末行 RESULT …。
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from wchat_py import hardwall as H  # noqa: E402

P = F = 0


def ck(name, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  ok   %s" % name)
    else:
        F += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


def _r(c):
    return H.rule(c, work_root="/tmp/proj", home="/Users/x")


def expect(c, want, tag):
    rid, _ = _r(c)
    got = rid or "NP"
    ck("%s: %r → %s" % (tag, c, want), got == want, "got=%r" % (rid,))


# ── 误判场景:应放行(NP) ──
expect("ls ~/.zshrc  # 只是看看 > 好不好", "NP", "注释里的 > 不算写")
expect("cat notes.md  # 文档提到 ~/.ssh/id_rsa", "NP", "注释里的凭据路径不算读")
expect("cp a.txt b.txt  # 之后同步 ~/.ssh/config", "NP", "注释触发 H6")
expect("cat ~/.bashrc  # 大括号 { } 只是注释", "NP", "注释里的 { }")
expect('python3 -c "print(1>0)"; cat ~/.bashrc', "NP", "python -c 里 > 是语言比较")
expect("grep 'x>y' ~/.bashrc", "NP", "单引号里的 >")
expect("sed 's/a>b/c/' ~/.bashrc", "NP", "sed 替换里的 >")
expect("[[ $a > $b ]] && cat ~/.bashrc", "NP", "[[ ]] 里 > 是比较")
expect("(( x > 1 )) && cat ~/.bashrc", "NP", "(( )) 里 > 是算术比较")
expect("awk '{if($1>$2) print}' ~/.bashrc", "NP", "awk 程序里的 >")
expect("cat > /tmp/x <<'EOF'\necho a > ~/.bashrc\nEOF", "NP", "cat heredoc 正文是数据")
expect("python3 - <<'PY'\nprint(1>0)\nPY", "NP", "python heredoc 正文是代码非 shell")

# ── 真阳性:必须仍拦 ──
expect("echo hi > ~/.bashrc", "H5", "真写 dotfile")
expect("echo x >> ~/.zshrc", "H5", "真追写 dotfile")
expect('echo "done" >> ~/.bashrc', "H5", "引号+真追写")
expect("cat src > ~/.zshrc", "H5", "真写 dotfile")
expect("echo y > ~/.ct/config", "H5", "写 .ct/config")
expect("echo z > ~/.config/gh/hosts", "H5", "写 dotfile_cfg")
expect("cp a ~/.bashrc", "H5", "写动词 cp")
expect("tee ~/.bashrc", "H5", "写动词 tee")
expect("cat ~/.aws/credentials", "H6", "真读凭据")
expect("cat ~/.ssh/id_rsa", "H6", "真读私钥")

# ── H5 命中卫生:必须给目标/写动词,不能是碎片(`>`/`1`/`$b`/`+`) ──
for c, want_frag in [("echo hi > ~/.bashrc", "~/.bashrc"),
                     ('echo "done" >> ~/.zshrc', "~/.zshrc"),
                     ("cat src > ~/.ct/config", "~/.ct/config")]:
    rid, _ = _r(c)
    frag = H._frag(rid, c) if rid else ""
    ck("H5 命中=目标 %r → %r" % (c, want_frag),
       frag == want_frag and frag not in ("", ">", "1", "b/c/", "$b", "+"),
       "frag=%r" % (frag,))

# ── H11(系统路径重定向)同样曾扫注释/heredoc 里的 `>`(用户实报"命中:第7行 > /etc/hosts") ──
expect("echo hi # > /etc/hosts", "NP", "H11 注释里的 > 不误判")
expect("cat notes.md # 涉及 > /etc/hosts", "NP", "H11 注释提系统重定向")
expect("python3 - <<'PY'\nx=[('echo # > /etc/hosts',0)]\nPY", "NP", "H11 python heredoc 正文里的 >")
expect("echo 1.1.1.1 x >> /etc/hosts", "H11", "H11 真写系统路径")
expect("echo x > /usr/bin/evil", "H11", "H11 真写 /usr/bin")

print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
sys.exit(0 if F == 0 else 1)