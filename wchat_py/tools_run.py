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



def read_file(resolved, display_path, offset, limit, read_max, work_root):
    """等价 bash `tool_read_file` 的格式化后半（授权 `path_access` 在 bash）。

    返回 (content, status_line)。status_line 是印到屏幕的进度行，**不进上下文**。
    · 非文件 → 错误 content（含解析结果与工作根）+ ` ⛔ 文件不存在:` 状态行
    · 是文件 → filefmt.read_fmt 出 content + ` 已读取 X(N 行)` 状态行
    """
    if not os.path.isfile(resolved):
        return ("错误:文件不存在: %s(解析为 %s;工作根 %s)" % (display_path, resolved, work_root),
                " ⛔ 文件不存在:%s" % resolved)
    from wchat_py.filefmt import read_fmt
    j = read_fmt(resolved, display_path, offset, limit, read_max)
    return (j["content"], " 已读取 %s(%s 行)" % (display_path, j["lines"]))


def grep(pattern, path, max_matches, max_col, work_root):
    """等价 bash `tool_grep`（授权 `path_access` 在 bash）。

    grep 本体走 subprocess（宪章 §2 豁免 1：外部二进制；不经 shell 解析）。
    返回 (content, status_line)。`path` 空 ⇒ 搜 work_root。
    """
    import subprocess
    from wchat_py.textutil import clip_bytes
    if not pattern:
        return ("错误:pattern 不能为空", None)
    if path:
        resolved = os.path.expanduser(path)
        if not os.path.isabs(resolved):
            resolved = os.path.join(work_root, resolved)
    else:
        resolved = work_root
    if not os.path.exists(resolved):
        return ("错误:路径不存在: %s" % path, None)
    cmd = ["grep", "-rnIE", "--exclude-dir=.git", "--exclude-dir=node_modules",
           "--exclude-dir=.venv", "-e", pattern, resolved]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    lines = p.stdout.split(b"\n")
    if lines and lines[-1] == b"":
        lines = lines[:-1]
    sel = lines[:max_matches]
    joined = b"\n".join(sel) + (b"\n" if sel else b"")
    cl = clip_bytes(joined, max_col, "…(过长已截断)".encode())
    out = cl.decode("utf-8", "replace").rstrip("\n")
    if not out:
        return ('未找到匹配 "%s" 的内容。' % pattern, " 已搜索:%s" % pattern)
    return ('搜索 "%s" 的结果:\\n%s' % (pattern, out), " 已搜索:%s" % pattern)