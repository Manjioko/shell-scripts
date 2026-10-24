#!/usr/bin/env python3
"""ctmodel —— 切换 ~/.ct/config 生效的模型(对应原 configs/ctmodel.sh)。

原理不变:配置里每条"跟着模型走"的赋值行,行尾带标记 `# @model <名字>`。
切到 X = 标记为 X 的行去掉注释、标记为其他模型的行加注释;不改任何值、不动顺序。
用法:
  ctmodel                 查看当前生效模型
  ctmodel <名字>          切换(切换前自动备份 config.bak)
  ctmodel <名字> -n       只预览 diff,不落盘
  ctmodel -l|--list       列出所有模型
  ctmodel check [--fix]   校验配置(--fix 顺手修权限 600)
  ctmodel undo            回滚到上一次切换前
"""
import os
import re
import shutil
import sys

CONFIG = os.environ.get("CT_CONFIG_FILE") or os.path.expanduser(os.path.join("~", ".ct", "config"))
MARK = re.compile(r"#\s*@model\s+(\S+)\s*$")
ASSIGN = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)=(.*)$')


def _read():
    with open(CONFIG, encoding="utf-8", errors="replace") as f:
        return f.read().splitlines()


def _models(lines):
    out = []
    for ln in lines:
        m = MARK.search(ln)
        if m and m.group(1) not in out:
            out.append(m.group(1))
    return out


def _current(lines):
    for ln in lines:
        if ln.lstrip().startswith("#"):
            continue
        m = MARK.search(ln)
        if m:
            return m.group(1)
    return ""


def _set_active(ln, active):
    """活动行原样;非活动行整体注释(去掉一个前导 '# ')。"""
    lead = len(ln) - len(ln.lstrip())
    pad, body = ln[:lead], ln[lead:]
    if active:
        if body.startswith("# ") and MARK.search(body[2:]):
            return pad + body[2:]
        return ln
    if body.startswith("#"):
        return ln
    return pad + "# " + body


def cmd_switch(name, dry):
    lines = _read()
    models = _models(lines)
    if name not in models:
        sys.stderr.write("未知模型: %s(可用: %s)\n" % (name, " ".join(models) or "(无)"))
        return 2
    new = []
    changed = []
    for ln in lines:
        m = MARK.search(ln)
        if not m:
            new.append(ln); continue
        want = (m.group(1) == name)
        upd = _set_active(ln, want)
        if upd != ln:
            changed.append((ln, upd))
        new.append(upd)
    if not changed:
        print("已经是 %s,无需切换" % name)
        return 0
    if dry:
        for old, upd in changed:
            print("- " + old)
            print("+ " + upd)
        return 0
    try:
        shutil.copyfile(CONFIG, CONFIG + ".bak")
        with open(CONFIG, "w", encoding="utf-8") as f:
            f.write("\n".join(new) + "\n")
    except OSError as e:
        sys.stderr.write("写入失败: %s\n" % e)
        return 1
    print("已切换到 %s(备份: %s.bak)" % (name, CONFIG))
    return 0


def cmd_check(fix):
    try:
        lines = _read()
    except OSError as e:
        sys.stderr.write("读不到配置: %s\n" % e)
        return 1
    bad = 0
    active = {}
    for i, ln in enumerate(lines, 1):
        s = ln.strip()
        if not s or s.startswith("#") or MARK.search(s) and s.startswith("#"):
            continue
        m = ASSIGN.match(s)
        if not m:
            print("第 %d 行不是 KEY=value: %s" % (i, ln)); bad += 1
            continue
        k = m.group(1)
        if k in active:
            print("变量 %s 有多条生效行(第 %d 与第 %d 行)" % (k, active[k], i)); bad += 1
        active[k] = i
    if fix:
        try:
            os.chmod(CONFIG, 0o600)
            print("已修权限: 600")
        except OSError as e:
            print("修权限失败: %s" % e); bad += 1
    print("校验通过" if not bad else "发现 %d 个问题" % bad)
    return 0 if not bad else 1


def main(argv):
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if not argv:
        if not os.path.isfile(CONFIG):
            sys.stderr.write("配置不存在: %s\n" % CONFIG)
            return 1
        print("当前模型: %s" % (_current(_read()) or "(无生效行)"))
        return 0
    a = argv[0]
    if a in ("-l", "--list"):
        print("\n".join(_models(_read())))
        return 0
    if a == "check":
        return cmd_check("--fix" in argv[1:])
    if a == "undo":
        bak = CONFIG + ".bak"
        if not os.path.isfile(bak):
            sys.stderr.write("没有备份: %s\n" % bak)
            return 1
        shutil.copyfile(bak, CONFIG)
        print("已回滚: %s <- %s" % (CONFIG, bak))
        return 0
    if a.startswith("-"):
        sys.stderr.write("未知选项: %s\n" % a)
        return 2
    if not os.path.isfile(CONFIG):
        sys.stderr.write("配置不存在: %s(可先运行 setup.py 或从 configs/config.example 复制)\n" % CONFIG)
        return 1
    return cmd_switch(a, "-n" in argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))