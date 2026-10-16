"""skill —— 技能目录扫描与前置解析(迁移自 wchat.sh 的 scan_skills)。

只负责"扫目录 + 解析 frontmatter → 拼出 `- 名: 描述` 列表"这一层(纯文件 IO),
**不含**注入(改 messages 的 inject_skills 留 bash)。

解析口径(与 bash 旧实现逐条对齐,tests/py_channel_test.sh 的 parity 钉住):
  · 只认 `*/SKILL.md`;`ls -t` 语义 = 按 mtime 降序(并列按路径升序,近似 ls 的 tie);
  · frontmatter 在首对 `---` 之间;`name:`/`description:` 后**紧跟**的缩进行并入 description;
  · name 缺失用**目录名**兜底;description 缺失(没有检索价值)**跳过注入**;
  · description 先压空白成单空格再 strip,最后按 SKILL_DESC_MAX 做字节安全截断;
  · SKILLS_DIR_EXTERNAL=1 时,SKILL.md 或其目录是符号链接的条目一律跳过
    (列表侧与 tool_read_skill 的读全文侧必须一起堵,只堵一侧 = 凭记忆还能读)。

退出码(见 `_contract`):
  0  成功,列表写 stdout
  1  未知子命令

用法:
  python3 -m wchat_py.skill scan <skills_dir> <max_inject> <desc_max> <external 0|1>
  python3 -m wchat_py.skill read <name> <skills_dir> <read_max_chars> <external 0|1>  # -> {content,status}
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys

from . import _contract
from .textutil import clip_bytes

# POSIX [:space:] 的 ASCII 面;行内不会有 \n。
_WS = " \t\r\f\v"
_WS_RUN = re.compile("[" + _WS + "]+")
_INDENT = re.compile("[" + _WS + "]")
_MARK = "\u2026".encode("utf-8")


def _strip(s: str) -> str:
    return s.strip(_WS)


def _squeeze(s: str) -> str:
    return _WS_RUN.sub(" ", s).strip(_WS)


def _read_lines(path: str):
    with open(path, "rb") as fh:
        text = fh.read().decode("utf-8", "replace")
    if text == "":
        return []
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]           # 末尾换行不产生额外一行(对齐 while read)
    return lines


def _parse_frontmatter(path: str):
    name = ""
    desc = ""
    in_front = False
    in_desc = False
    for line in _read_lines(path):
        if not in_front:
            if line == "---":
                in_front = True
            continue
        if line == "---":
            break
        if line.startswith("name:"):
            name = line[len("name:"):]
            in_desc = False
        elif line.startswith("description:"):
            desc = line[len("description:"):]
            in_desc = True
        elif in_desc and _INDENT.match(line):
            desc = desc + " " + line
        else:
            in_desc = False
    return _strip(name), _squeeze(desc)


def scan_skills(skills_dir: str, max_inject: str, desc_max: str, external: str) -> str:
    external = str(external) == "1"
    try:
        n = int(max_inject)
    except (TypeError, ValueError):
        n = 0
    try:
        dmax = int(desc_max)
    except (TypeError, ValueError):
        dmax = 0
    files = [f for f in glob.glob(os.path.join(skills_dir, "*", "SKILL.md"))
             if os.path.isfile(f)]
    files.sort()                                        # 并列 mtime 的确定次序
    try:
        files.sort(key=os.path.getmtime, reverse=True)  # ls -t: mtime 降序(稳定)
    except OSError:
        pass
    files = files[:n] if n > 0 else []
    out = []
    for f in files:
        if external and (os.path.islink(f) or os.path.islink(os.path.dirname(f))):
            continue
        name, desc = _parse_frontmatter(f)
        if not name:
            name = os.path.basename(os.path.dirname(f))
        if not desc:
            continue
        clipped = clip_bytes(desc.encode("utf-8"), dmax, _MARK)
        desc = clipped.decode("utf-8", "replace").rstrip("\n")   # 对齐 bash 的 $(...) 剥尾换行
        out.append("- %s: %s\n" % (name, desc))
    return "".join(out)


def cmd_scan(argv) -> int:
    skills_dir = argv[0] if len(argv) > 0 else ""
    max_inject = argv[1] if len(argv) > 1 else ""
    desc_max = argv[2] if len(argv) > 2 else ""
    external = argv[3] if len(argv) > 3 else "0"
    _contract.write_text(scan_skills(skills_dir, max_inject, desc_max, external))
    return _contract.EXIT_OK


def _truncate_by_lines(text: str, maxc: int) -> str:
    """复刻 awk 'acc += u8len($0)+1; if (acc>max) exit; print'(按**行**截,每行算字符数+1)。"""
    parts = text.split("\n")
    if parts and parts[-1] == "":
        parts = parts[:-1]
    acc = 0
    out = []
    for line in parts:
        acc += len(line) + 1
        if acc > maxc:
            break
        out.append(line)
    return "\n".join(out)


def read_skill(name: str, skills_dir: str, read_max: str, external: str) -> dict:
    """读取技能全文(带 name 白名单 + 外置符号链接守卫)。返回 {content, status}。"""
    if not name:
        return {"content": "错误:技能名(name)不能为空", "status": ""}
    if "/" in name or ".." in name:
        return {"content": "错误:非法技能名: " + name,
                "status": " ⛔ 非法技能名:" + name}
    path = os.path.join(skills_dir, name, "SKILL.md")
    if str(external) == "1" and (os.path.islink(path)
                                 or os.path.islink(os.path.join(skills_dir, name))):
        return {"content": "错误:技能 %s 的目录或 SKILL.md 是符号链接,已拒绝读取"
                           "(外置技能目录里的链接可指向工作目录之外)" % name,
                "status": " ⛔ 技能为符号链接,已拒绝:" + name}
    if not os.path.isfile(path):
        return {"content": "错误:技能不存在: %s。可用技能见系统消息中的可用技能列表,"
                           "名称以列表中短横线后的名字为准。" % name,
                "status": " ⛔ 技能不存在:" + name}
    with open(path, "rb") as fh:
        text = fh.read().decode("utf-8", "replace")
    try:
        rmax = int(read_max)
    except (TypeError, ValueError):
        rmax = 0
    if len(text) > rmax:
        out = _truncate_by_lines(text, rmax)
        # ⚠ 这里刻意用**字面** `\n`(反斜杠+n),不是换行:复刻 bash 双引号串
        #   `"...:\n\n$out"` —— bash 双引号里 `\n` 是两个字符。原实现即如此(疑似历史
        #   笔误,应为 $'\n'),迁移只做等价、不改行为;要修另开一刀。
        content = "📖 技能 %s(SKILL.md,共 %d 字符,过长已截断):\\n\\n%s" % (name, len(text), out)
    else:
        content = "📖 技能 %s(SKILL.md)全文:\\n\\n%s" % (name, text.rstrip("\n"))
    return {"content": content, "status": " 已读取技能 %s" % name}


def cmd_read(argv) -> int:
    name = argv[0] if len(argv) > 0 else ""
    skills_dir = argv[1] if len(argv) > 1 else ""
    read_max = argv[2] if len(argv) > 2 else ""
    external = argv[3] if len(argv) > 3 else "0"
    out = read_skill(name, skills_dir, read_max, external)
    _contract.write_text(json.dumps(out, ensure_ascii=False) + "\n")
    return _contract.EXIT_OK


_COMMANDS = {
    "scan": cmd_scan,
    "read": cmd_read,
}


def main(argv) -> int:
    if not argv or argv[0] not in _COMMANDS:
        sys.stderr.write(
            "skill: 未知子命令 %r(可用: %s)\n"
            % (argv[0] if argv else "", ", ".join(sorted(_COMMANDS)))
        )
        return _contract.EXIT_FAIL
    return _COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))