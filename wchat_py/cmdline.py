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
    # `-tf` / `-tf=on` / `-tf=off`:思考文本展示档位(整段 / 两行)。
    # `-tf` 同时管思考文本与 shell 输出箱(用户要求:同一件事不要两个开关),
    # 所以这里不再有 `-sf`。裸 `-tf` 按 on 处理(与会话里敲 `-tf` 的直觉一致)。
    if s == "-tf" or s.startswith("-tf="):
        return "think_full", (s.split("=", 1)[1] if "=" in s else "on")
    # `-sf` 已删除,但**不能当正文**:那样用户敲 `-sf=on` 会把字面串发给模型,
    # 却以为开关生效了。单独给它一个"已废弃"动作,由 repl 明说一句。
    if s == "-sf" or s.startswith("-sf="):
        return "deprecated_sf", (s.split("=", 1)[1] if "=" in s else "")
    if s == "-img" or s.startswith("-img "):
        return "image", s[len("-img"):].lstrip(" ")
    return "text", s


if __name__ == "__main__":
    import sys
    for ln in sys.stdin:
        print("%s\t%s" % classify(ln.rstrip("\n")))