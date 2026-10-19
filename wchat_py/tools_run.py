#!/usr/bin/env python3
"""B 阶段 1d：工具实现的 Python 版（**逐字复刻** bash 的输出，因为工具输出会进下一轮上下文）。

第一个工具：`list_dir`。bash 原文（`wchat.sh` 6031–6044）：
```bash
resolved=$(resolve_path "$path") || resolved=""
path_access "$path" r "列出目录" || return 0          # ← 授权, 留 bash
[[ -d "$resolved" ]] || { content="错误:目录不存在: $path"; return 0; }
files=$(find "$resolved" -maxdepth 1 -type f -exec basename {} \; | sort)
dirs=$(find "$resolved" -maxdepth 1 -mindepth 1 -type d -exec basename {} \; | sort)
content="目录 $path:\n[文件]\n${files:-"(空)"}\n\n[子目录]\n${dirs:-"(空)"}"
printf ' 已列出 %s\n' "$path"
```

**边界**：授权（`resolve_path`/`path_access`）留在 bash —— 它要弹确认；
本模块只负责"给定已解析的目录，产出 content 与状态行"。
"""
import os


def list_dir(resolved, display_path):
    """返回 (content, status_line)。`resolved` 必须是已通过授权的绝对路径。

    ⚠ 输出格式与 bash **逐字一致**（含 `\\n` 的位置、`(空)` 占位、状态行前导空格）。
    顺序：bash 是 `find … | sort`（字典序）；这里用 `sorted()`（码点序）。
    两者在纯 ASCII 下相同；中文名可能因 locale 差异而不同 —— 若 parity 红，先查这个。
    """
    if not os.path.isdir(resolved):
        return ("错误:目录不存在: %s" % display_path, None)
    try:
        names = os.listdir(resolved)
    except OSError:
        names = []
    files = sorted(n for n in names if os.path.isfile(os.path.join(resolved, n)))
    dirs = sorted(n for n in names if os.path.isdir(os.path.join(resolved, n)))
    content = "目录 %s:\n[文件]\n%s\n\n[子目录]\n%s" % (
        display_path,
        "\n".join(files) if files else "(空)",
        "\n".join(dirs) if dirs else "(空)")
    return (content, " 已列出 %s" % display_path)

