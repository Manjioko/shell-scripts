#!/usr/bin/env python3
"""REPL 元命令分发骨架（B 阶段主循环迁移第一刀）。

把 `handle_cmd_line` 的"line → 该做哪件事"抽成**纯分类**（无副作用），供 Python 主循环后续
按动作去调用真正实现。副作用（切模型探图/压缩/存盘/清屏…）仍在 bash，逐步迁。

`classify(line)` 返回 `(action, arg)`：
  · 精确 token → 固定动作（见 _EXACT）
  · `-auto[=tier]` / `-img <path>` 前缀 → ("auto_on", tier) / ("image", path)
  · 其它 → ("text", line)（按普通正文发给模型）
"""
_EXACT = {
    "-q": "quit", "-l": "list_history", "-msgs": "show_msgs", "-c": "compact",
    "-skill": "skill_save", "-u": "usage", "-h": "help", "-n": "new_session",
    "-cl": "clear", "-stp": "model_pro", "-stt": "thinking_on", "-stf": "model_flash",
    "-noauto": "auto_off", "-fs": "save_history", "-md": "model_status",
}


def classify(line):
    s = line or ""
    if s in _EXACT:
        return _EXACT[s], ""
    if s == "-auto":
        return "auto_on", ""
    if s.startswith("-auto="):
        return "auto_on", s[len("-auto="):]
    if s == "-img" or s.startswith("-img "):
        return "image", s[len("-img"):].lstrip(" ")
    return "text", s


if __name__ == "__main__":
    import sys
    for ln in sys.stdin:
        print("%s\t%s" % classify(ln.rstrip("\n")))

