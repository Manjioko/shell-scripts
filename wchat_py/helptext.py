#!/usr/bin/env python3
"""`-h` 帮助正文(纯文本)。

为什么单独一个模块:入口 `cli.py` 有**行数上限**(`tests/layout_test.py` 守 1250),
帮助文案是"一屏静态文本",既不是业务逻辑也不该把入口撑大。这里只返回字符串列表,
由 `cli.help_user()` 负责加 `│ ` 前缀上屏 —— 渲染口径仍然只有一处。
"""
import os

MC_CONFIG_DEFAULT = os.path.join(".ct", "mcporter.json")


def help_lines():
    """返回帮助正文的每一行(空串 = 空行)。调用时读环境,保证 `-h` 显示当前值。"""
    from wchat_py.runtime import _skills_dir_default

    # 默认位与 mcp_bridge.config_path() 必须同源 —— 猜错会让 help 基线的 bytes 比对
    # 立刻红,这正是 bytes 级判据的价值。
    mcp_cfg = os.environ.get("MCP_CONFIG") or os.path.join(
        os.environ.get("HOME", ""), MC_CONFIG_DEFAULT)
    skills_dir = os.environ.get("SKILLS_DIR") or _skills_dir_default()
    sd_default = _skills_dir_default()

    out = [
        "使用方法: ct [选项] [内容]",
        "选项:",
        "  -t    启用思考模式",
        "  -tf=on|-off  展示档位:on=思考整段 + shell 输出多行;off=思考两行 + shell 只留收尾行(默认)",
        "  -p    使用 deepseek-v4-pro 模型",
        "  -dp   启用思考模式并使用 deepseek-v4-pro 模型",
        "  -l    查看历史记录并选择加载",
        "  -f    忽略历史记录",
        "  -st   单轮模式:执行一轮对话后退出,不进交互循环(内容为空时在提示符输入一轮)",
        "  -auto  敏感权限全自动(免逐次确认)。可接档位: -auto / -auto=read|sandbox|full",
        "         read=只自动放行越界读取 | sandbox(默认)=工作目录内全自动 | full=再加 --allow-write 名单",
        "         硬墙(提权/系统路径/凭据/越界写/强推/下载即执行)任何档位都不放行;启动需输入 AUTO 签字",
        "  --allow 路径 / --allow-write 路径  越界只读/可写白名单(可多次,配合 -auto)",
        "  --allow-install [包名,逗号]  允许 auto 安装这些包(配额 AUTO_MAX_INSTALLS,默认 5);不带包名 = 全开并警告。换源一律拒",
        "  -mcp [服务器,逗号]  启用 MCP 外部能力(mcp_tools/mcp_schema/mcp_call 三条通道)",
        "         不带 -mcp 时:**按需模式**(仅当这台机器此前成功用过 MCP)。工具已登记但不连接,",
        "         首次真正用到时才探测 —— 启动不再等它。想立刻就绪就带 -mcp(启动时当场连接并摊开暴露面)。",
        "         会话内也可用: -mcp 立刻连接 | -mcp=off 本次关闭 | -no-mcp 启动即关闭",
        "         多个 wchat 并存时按 server 分座:被别的会话占着的那个会明说是哪个 pid,其余照常",
        "         清单来自 %s;**必须在该文件里写 \"imports\": []**,否则会带进" % mcp_cfg,
        "         Codex/VS Code 等其它程序声明的 server(wchat 会主动报出并排除,但别依赖这个)",
        "         ⚠ 这是**能力开关,不是安全边界**:不带 -mcp 只表示我们不提供这三条通道,",
        "           模型仍可用 run_shell_command 自行调用 mcporter。无人值守 + auto 时必须点名",
        "         启动时把每个 server **实际拿到的命令行原样摊开**;我们不解释也不替你改第三方参数",
        "         (第三方参数会随版本改名,写死在我们代码里只会静默失效)。遥测、脱敏头这类",
        "         开关请按各 server 自己的文档设;启动输出里那行就是它实际拿到的参数。",
        "  -img  发送图片(后接图片路径,自动转述为文字)",
        "  -skill 从当前对话提炼技能,确认后保存到本次生效的技能目录(见下)",
        "         当前技能目录: %s(未设 SKILLS_DIR 时为 %s;外置值须过校验)" % (skills_dir, sd_default),
        "  -c    手动压缩对话历史",
        "  -u    查看当前上下文 token 用量",
        "  -q    退出程序(Ctrl+C 不会退出,这是唯一退出方式)",
        "  -h    显示帮助信息",
        "",
        "不开对话、只做配置类动作:",
        "  wchat -config        逐项填写配置(接口地址/key/模型),写 ~/.ct/config",
        "  wchat model [名字]   看/切换模型组(等价原来的 ctmodel);-l 列组,check 校验,undo 回滚",
        "  wchat mcp            看 MCP 清单位置与 server(首次启动自动生成 ~/.ct/mcporter.json)",
        "  wchat skills          看技能来源;--seed 补随包技能;add <来源> 装第三方技能;remove <名> 移出",
        "  wchat memory [list|show|prune]  看/清理本项目的记忆(跨会话的坑/事实/偏好)",
        "",
        "内容交互时，也可使用以下选项:",
        "  -msgs 显示当前消息记录",
        "  -l    查看历史记录并选择加载",
        "  -skill 从当前对话提炼技能,确认后保存到 %s" % skills_dir,
        "  -c    手动压缩对话历史",
        "  -n    新对话(清空历史并清屏)",
        "  -cl   清屏(保留对话历史)",
        "  -stp  切换到 pro 模型",
        "  -auto[=read|sandbox|full]  会话中途开启/切换 auto 档位(需重新签字)",
        "  -mcp  立刻连接 MCP server(按需模式下平时不连接,敲它才连;失败会说原因与恢复命令)",
        "  -mcp=off  本次会话关闭 MCP(mcp_* 工具从模型视野移除);-mcp=git,figma 只启用点名的",
        "  -noauto  关闭 auto,恢复逐次人工确认",
        "  -stt  开启思考模式",
        "  -stf  恢复默认(flash 模型,关闭思考)",
        "  -tf=on|-off  展示档位切换:思考整段/shell 多行 ↔ 思考两行/shell 收尾行(默认后者)",
        "  -fs   立即保存当前对话历史(-f 模式下静默跳过)",
        "  -md   查看当前模型/思考/标题信息",
        "  -u    查看当前上下文 token 用量",
        "  -img <路径> 发送图片(自动转述为文字)",
        "  Ctrl+F 选择文件(图片自动转述;文本传路径,由 AI 用工具按需读取)",
        "  Ctrl+G/Ctrl+V 粘贴剪贴板图片(截图后直接附加,自动转述;部分终端下直接粘贴亦可)",
        "  -q    退出程序(询问是否保存,默认保存;N/n 不保存;-f 不询问)",
        "  -h    显示帮助信息",
        "",
        "输入规则:",
        "  - 回车 = 发送整段(缓冲区为空时不动)",
        "  - 想换行:把行尾写成 \\ 再回车(反斜杠会被吃掉,不发送,继续输入)",
        "  - 粘贴多行文本会自动折叠成一行占位,回车发送时原样展开",
        "  - -x 命令仅在单行输入时生效;多行/续行中输入 -x 按正文处理",
        "  - Ctrl+C 清空当前行,Ctrl+R 整段清空本轮输入",
    ]
    return out