"""filefmt —— 文件类工具的**纯格式化**(迁移自 wchat.sh 的 tool_read_file 输出段)。

只做"给定已解析路径 + 分页/截断参数 → 带行号的展示内容",不做任何权限裁决
(`resolve_path`/`path_access` 留 bash)。

退出码(见 `_contract`):
  0  成功,单行 JSON 写 stdout:`{"content": "...", "lines": N}`
  1  未知子命令

用法:
  python3 -m wchat_py.filefmt read <resolved> <display_path> <offset> <limit> <read_max>
"""

from __future__ import annotations

import json
import sys

from . import _contract


def _int(s, default=0) -> int:
    try:
        return int(s)
    except (TypeError, ValueError):
        return default


def read_fmt(resolved: str, path: str, offset, limit, read_max) -> dict:
    """带行号输出。行号供 edit_file 的 old_string 定位与沟通引用。

    · limit>0 → 取 [offset, offset+limit-1] 窗口;
    · 否则若字符数 > read_max → 按行累计字符数截断(不切断多字节字符);
    · 否则全量。
    `out` 与 bash 的 `$(...)` 一致**剥掉结尾换行**(命令替换会吃掉)。
    """
    with open(resolved, "rb") as fh:
        text = fh.read().decode("utf-8", "replace")
    total_lines = text.count("\n")          # 对齐 wc -l
    total_chars = len(text)                 # 对齐 wc -m
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    offset = _int(offset, 1)
    limit = _int(limit, 0)
    read_max = _int(read_max, 0)
    if limit > 0:
        window = lines[offset - 1: offset - 1 + limit]
        out = "".join("%6d│ %s\n" % (offset + i, ln) for i, ln in enumerate(window))
        # ⚠ 头部用**字面** `\n`(反斜杠+n):复刻 bash 双引号串 `"...:\n\n$out"` ——
        #   bash 双引号里 `\n` 是两个字符。原实现即如此(疑似历史笔误,应为 $'\n'),
        #   迁移只做等价、不改行为;要修另开一刀。
        content = "📄 %s · 第 %d-%d 行(共 %d 行):\\n\\n%s" % (
            path, offset, offset + limit - 1, total_lines, out.rstrip("\n"))
    elif total_chars > read_max:
        parts = []
        acc = 0
        for i, ln in enumerate(lines, 1):
            acc += len(ln) + 1
            if acc > read_max:
                break
            parts.append("%6d│ %s\n" % (i, ln))
        content = ("📄 %s · 共 %d 行 / %d 字符,文件过大仅返回前半部分。"
                   "请用 offset/limit 参数继续分段读取:\\n\\n%s"
                   % (path, total_lines, total_chars, "".join(parts).rstrip("\n")))
    else:
        out = "".join("%6d│ %s\n" % (i, ln) for i, ln in enumerate(lines, 1))
        content = "📄 %s · 共 %d 行:\\n\\n%s" % (path, total_lines, out.rstrip("\n"))
    return {"content": content, "lines": total_lines}


def cmd_read(argv) -> int:
    a = list(argv) + ["", "", "", "", ""]
    out = read_fmt(a[0], a[1], a[2], a[3], a[4])
    _contract.write_text(json.dumps(out, ensure_ascii=False) + "\n")
    return _contract.EXIT_OK


_COMMANDS = {
    "read": cmd_read,
}


def main(argv) -> int:
    if not argv or argv[0] not in _COMMANDS:
        sys.stderr.write(
            "filefmt: 未知子命令 %r(可用: %s)\n"
            % (argv[0] if argv else "", ", ".join(sorted(_COMMANDS)))
        )
        return _contract.EXIT_FAIL
    return _COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))