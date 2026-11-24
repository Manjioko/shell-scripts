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
    # MCP 会话内开关。`-mcp` 与命令行 `-mcp` 走**同一条** enable_now():
    # 两套实现必然漂移,而"启动时能连上、中途敲同一条命令连不上"是最难查的那种漂移。
    "-mcp=off": "mcp_off", "-no-mcp": "mcp_off", "-mcp=on": "mcp_on",
}

# 会话内命令目录(**单一真源**):TUI 的 `/` 弹出菜单文案、二级枚举项都从这里取,
# 不再在 tui.py 手抄一份(手抄必然与 classify 漂移)。每项 == (命令, 一句话描述, kind, options):
#   kind="run"  : 无参,选中即执行(直接走 classify → 对应动作)
#   kind="enum" : 带固定选项 → 进二级菜单选一个再执行(见 options)
#   kind="file" : 特殊(如 /img),选中后弹原生文件选择器,不自动发送
#   options     : [(值, 选项标签, 说明)],仅 enum 用
# **刻意不含** `-sf`(已废弃,敲到才提示)、也不含启动专用项(-t/-st/-p/-f/--allow…)。
SLASH_COMMANDS = [
    ("/q",      "退出程序(Ctrl+C 不退出,这是唯一退出方式)", "run", None),
    ("/n",      "新对话(清空历史并清屏)", "run", None),
    ("/cl",     "清屏(保留对话历史)", "run", None),
    ("/c",      "手动压缩对话历史", "run", None),
    ("/u",      "查看当前上下文 token 用量", "run", None),
    ("/h",      "显示帮助信息", "run", None),
    ("/l",      "查看历史记录并选择加载", "run", None),
    ("/md",     "查看当前模型/思考/标题信息", "run", None),
    ("/msgs",   "显示当前消息记录", "run", None),
    ("/skill",  "从当前对话提炼技能并保存", "run", None),
    ("/fs",     "立即保存当前对话历史", "run", None),
    ("/stp",    "切换到 pro 模型", "run", None),
    ("/stf",    "恢复默认(flash 模型,关闭思考)", "run", None),
    ("/stt",    "开启思考模式", "run", None),
    ("/noauto", "关闭 auto,恢复逐次人工确认", "run", None),
    ("/mcp",    "立刻连接 MCP server(按需模式下平时不连接)", "run", None),
    ("/mcp=off", "本次会话关闭 MCP", "run", None),
    ("/tf",     "思考/输出展示档位", "enum", [
        ("0", "极简(隐藏思考/工具,只留正文+状态行)", "默认"),
        ("1", "两行折叠(思考两行 + shell 折叠)", ""),
        ("2", "整段展开(思考整段 + shell 展开)", ""),
    ]),
    ("/auto",   "自动权限档位(选中后需重新签字)", "enum", [
        ("read", "只自动放行越界读取", ""),
        ("sandbox", "工作目录内全自动", "默认"),
        ("full", "再加 --allow-write 白名单", ""),
    ]),
    ("/img",    "发送图片(选文件后插入占位,可与问题一起发)", "file", None),
]


def classify(line):
    s = line or ""
    # `/` 与会话内 `-` **同一套命令**:把最前面的 `/` 换成 `-` 复用下面的全部分支。
    # 这样两种前缀不会各写一份逻辑(必然漂移)。注意正文兜底必须返回**原串**(`orig`),
    # 否则 `/usr/bin` 这类以 / 开头的普通消息会被改写。
    orig = s
    if s.startswith("/"):
        s = "-" + s[1:]
    if s in _EXACT:
        return _EXACT[s], ""
    if s == "-mcp":
        return "mcp_on", ""
    if s.startswith("-mcp add") or s.startswith("-mcp remove"):
        # 会话内加/删 server:直接转给 mcp_cmd(cli 子命令与它共用一份实现)。
        # 必须先于 startswith("-mcp=") 判:否则 "-mcp add …" 会被当正文发给模型。
        return "mcp_edit", s[len("-mcp "):]
    if s.startswith("-mcp="):
        # -mcp=off 已在 _EXACT 里;其余按"只启用这些 server"处理(-mcp=git,figma)
        return "mcp_on", s[len("-mcp="):]
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
    return "text", orig


if __name__ == "__main__":
    import sys
    for ln in sys.stdin:
        print("%s\t%s" % classify(ln.rstrip("\n")))