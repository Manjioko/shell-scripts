#!/usr/bin/env python3
"""工具执行的 Python 可调用 seam（B 阶段 §12 影子分发的后端）。

用法：`python3 -m wchat_py.toolcall <tool_name>`，stdin 为 args JSON，stdout 输出
`{"status": "...", "content": "..."}`：
- `status` 是屏幕进度行（如 ` 已列出 .`），**不进 content**；
- `content` 是进下一轮上下文的工具结果。
bash 侧接 seam 时：status 打到终端、content 进消息——这正是 §12 里点名的坑。

只接纯格式化工具 + run_shell_command；授权 `path_access` 仍由 bash 在调用前完成。
"""
import json
import os
import sys

from wchat_py.tools_run import list_dir, read_file, grep, write_file, edit_file


def _resolve(path, wr):
    if not path:
        return wr
    p = os.path.expanduser(path)
    return p if os.path.isabs(p) else os.path.join(wr, p)


def run(name, a, work_root, home):
    """返回 (content, status)。status 可为 None。"""
    if name == "list_dir":
        path = a.get("path") or "."
        return list_dir(_resolve(path, work_root), path)
    if name == "read_file":
        path = a.get("path") or ""
        return read_file(_resolve(path, work_root), path, a.get("offset"), a.get("limit"),
                         int(os.environ.get("READ_MAX_CHARS", "30000")), work_root)
    if name == "grep":
        return grep(a.get("pattern") or "", a.get("path") or "",
                    int(os.environ.get("GREP_MAX_MATCHES", "200")),
                    int(os.environ.get("GREP_MAX_COL", "500")), work_root)
    if name == "write_file":
        path = a.get("path") or ""
        return write_file(_resolve(path, work_root), path, a.get("content") or "", work_root)
    if name == "edit_file":
        path = a.get("path") or ""
        return edit_file(_resolve(path, work_root), path,
                         a.get("old_string") or "", a.get("new_string") or "", work_root)
    raise SystemExit("toolcall: 不支持的工具 %r" % name)


def main(argv):
    if len(argv) < 1:
        raise SystemExit("用法: python3 -m wchat_py.toolcall <tool_name>  (stdin=args JSON)")
    name = argv[0]
    raw = sys.stdin.read()
    try:
        args = json.loads(raw) if raw.strip() else {}
    except Exception:
        args = {}
    wr = os.environ.get("WORK_ROOT") or os.getcwd()
    home = os.environ.get("HOME") or ""
    content, status = run(name, args, wr, home)
    sys.stdout.write(json.dumps({"status": status or "", "content": content}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

