#!/bin/bash

# api key
DEEPSEEK_API_KEY="sk-43c796fe3c924743a38e9c5aec6c077a"
# 保存当前对话的标题，默认空字符串，首次保存时由模型生成
chat_title=""

# 最新一次用户输入的内容
content=""

# 记录所有的对话内容，包含用户和助手的消息
messages=$(jq -n '[{role: "system", content: "You are a helpful assistant."}]')

#辅助权限判断助手消息
# 
auth_message=$(jq -n '[{role: "system", content: "你是一个bash命令判断专家，用户给你一个bash命令，你会根据上下文/bash命令本身，判断这个bash命令是否存在危险性，判断标准是：涉及写操作/bash命令存在严重的威胁用户电脑安全问题/可能存在让用户丢失数据的bash命令/存在模糊的，可能的攻击命令。如果存在以上这些命令，你总是回复N,否则，你会回复Y。注意，你不会调用任何bash命令，只是做判断，并且只能回复【Y/N】,不会回复多余文字。"}]')


# 是否思考,默认关闭
thinking="disabled"

#模型，默认是 deepseek-v4-flash
model="deepseek-v4-flash"

# 最大工具调用轮次：防止模型失控时无限递归调用工具（每轮 = 一次真实 API 请求）
# MAX_TOOL_ROUNDS=10

# 历史记录集合
history_list=()

# 不要读写历史记录
forget_history=0

# ---------- 上下文自动压缩(compaction)配置 ----------
# 最近一次请求返回的真实 prompt_tokens(来自 usage)。每次请求都把全量 messages
# 发给 API,所以 prompt_tokens 就是"当前上下文总 token 数",无需估算或累计。
# 0 表示尚无精确值(刚启动/刚加载历史),此时不触发自动压缩。
last_prompt_tokens=0

# 触发自动压缩的阈值。业界实践(Claude Code/Aider)多在接近窗口上限才压,
# 这里按"控制每轮成本与首字延迟"主动提前到 10 万 token(约占 1M 窗口的 10%)。
TOKEN_THRESHOLD=100000

# 压缩后保留最近多少条消息原文(一轮工具链约 4 条:user + assistant(tool_calls)
# + tool + assistant(最终回复),16 条 ≈ 最近 4 轮,足够模型衔接上下文)
KEEP_MSGS=16

# 摘要消息的前缀标记:让用户在 -msgs / 历史渲染时能一眼认出"这是压缩过的历史"
SUMMARY_PREFIX="【历史对话摘要】"

# 工具输出(tool 角色消息)是上下文里最肥的部分:超长命令输出保留头尾,
# 中间只留行数标记(开头=表头/摘要,结尾=报错/结果,中间多为重复日志)
TOOL_OUTPUT_MAX_LINES=400
TOOL_OUTPUT_MAX_COL=2000
# ----------------------------------------------------

# 用户输入的内容
input=""
# 保存当前终端设置
stty_save=""

# 窗口行数
WIN=10
# 重画节流间隔（微秒）
THROTTLE=50000

options_list=("-l" "-msgs" "-h" "-c" "-u")

# 优先使用 Homebrew bash 5.x（readline 8.x 原生支持括号粘贴 + 中文编辑）
if [[ -z "$BREW_BASH_CHECKED" ]]; then
    for brew_bash in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        if [[ -x "$brew_bash" ]]; then
            export BREW_BASH_CHECKED=1
            exec "$brew_bash" "$0" "$@"
        fi
    done
fi


# 关闭 readline 括号粘贴：多行粘贴时首行立即被 read -e 接受，
# 剩余字节留在 tty 输入队列里，可用不带 -e 的非阻塞 read 排干拿到全文
# （若保持开启，bash 会把首行之后的内容直接丢弃）
_ct_inputrc=$(mktemp)
[[ -f "$HOME/.inputrc" ]] && printf '$include %s\n' "$HOME/.inputrc" > "$_ct_inputrc"
printf 'set enable-bracketed-paste off\n' >> "$_ct_inputrc"
export INPUTRC="$_ct_inputrc"

trap handle_ctrl_c INT

# 任何退出路径都恢复光标显示 + 自动换行
trap 'printf "\033[?25h\033[?7h"' EXIT


render_markdown() {
    local text="$1"
    # glow 是 markdown 渲染器，会把「行尾反斜杠 + 换行」当作 hard break（硬换行）
    # 语法而隐藏反斜杠，导致用户输入的 \\ 只保留的一个 \ 看起来也被删了。
    # 先用 sed 把行尾单个 \ 转义成 \\，glow 就能原样显示它。
    if command -v glow >/dev/null 2>&1; then
        echo "$text" | sed 's/\\$/\\\\/' | glow -
    elif command -v mdcat >/dev/null 2>&1; then
        echo "$text" | sed 's/\\$/\\\\/' | mdcat -
    else
        echo "$text"
    fi
}

save_history() {
    [[ -z "$chat_title" ]] && return 0
    [[ -z "$messages" ]] && return 0
    [[ $forget_history == 1 ]] && return 0
    local history

    # 替换斜杠
    # name=$(echo "${chat_title%.json}" | tr '/' '_')
    # 替换其他危险字符（如空格、特殊符号）
    name=$(echo "$chat_title" | tr '[:space:]' '_')
    name=$(echo "$name" | tr -d '\\/:*?"<>|')
    name="${name%.json}" 

    [ -f "${HOME}/.ct/${name}.json" ] || touch "${HOME}/.ct/${name}.json"
    history=$(jq -n --argjson msgs "$messages" --arg model "$model" --arg thinking "$thinking" \
    '{
        model: $model,
        messages: $msgs,
        thinking: {type: $thinking},
        reasoning_effort: "high",
        stream: true
    }' 2>&1)

    echo "保存记录 ${HOME}/.ct/${name}.json"

    echo "$history" > "${HOME}/.ct/${name}.json"
}

get_history() {
    [[ -z $1 ]] && return 0
    local history
    chat_title=$(basename "$1")
    messages=$(jq '.messages' <<< "$(<"$1")")
    # 加载的是新对话:旧的 token 计数作废,等下一次请求由 usage 刷新精确值
    last_prompt_tokens=0
    thinking=$(jq -r '.thinking.type' <<< "$(<"$1")")
    model=$(jq -r '.model' <<< "$(<"$1")")
    jq -c '. [1:][]' <<< "$messages" | while IFS= read -r line; do
        role=$(jq -r '.role' <<< "$line")
        content=$(jq -r '.content' <<< "$line")
        render_markdown "## $role"
        echo "----------------------------------------------"
        render_markdown "$content"
    done
}

handle_ctrl_c() {
    local name=""
    # 恢复默认 SIGINT，防止 overview 卡住时无法强制退出
    trap - INT
    [ -d "${HOME}/.ct" ] || mkdir "${HOME}/.ct"
    save_history
    exit 130
}


history_handler() {
    local tmpList=()
    echo "请从以下编号中选择你要加载的历史记录："
    # shellcheck disable=SC2207
    tmpList=($(ls -t "$HOME"/.ct/*.json 2>/dev/null))
    history_list=()
    for i in "${!tmpList[@]}"; do
        echo "$i. $(basename "${tmpList[$i]}")"
        history_list+=("$i. ${tmpList[$i]}")
    done
    local filePath=""
    local isSelect=0
    IFS= read -r -p "_>" num
    [[ -z "$num"  &&  "${history_list[0]}" ]] && get_history "${history_list[0]#0. }" && return 0
    [[ -z "$num" ]] && echo "请输入编号" && return 1
    [[ ! "$num" =~ ^[0-9]+$ ]] && echo "请输入正确的编号" && return 1
    [[ "$num" -lt 0 || "$num" -ge "${#history_list[@]}" ]] && echo "请选择正确的编号" && return 1
    echo "选中的数字: $num"
    for ((i = 0; i < "${#history_list[@]}"; i++)); do
        if [[ ${history_list[i]} =~ ^${num} ]]; then
            echo "切换到 ${history_list[i]#${i}. }"
            filePath="${history_list[i]#${i}. }"
            isSelect=1

            [[ -n "$filePath" ]] && reset && get_history "$filePath"
            break
        else
            isSelect=0
        fi
    done
    [[ "$isSelect" == 0 ]] && echo "请选择正确的编号"

}


help_user() {
    echo "使用方法: ct [选项] [内容]"
    echo "选项:"
    echo "  -t    启用思考模式"
    echo "  -p    使用 deepseek-v4-pro 模型"
    echo "  -dp   启用思考模式并使用 deepseek-v4-pro 模型"
    echo "  -l    查看历史记录并选择加载"
    echo "  -f    忽略历史记录"
    echo "  -tr   翻译任务"
    echo "  -to   词源词根解释任务"
    echo "  -c    手动压缩对话历史"
    echo "  -u    查看当前上下文 token 用量"
    echo "  -h    显示帮助信息"
    echo
    echo "内容交互时，也可使用以下选项:"
    echo "  -msgs 显示当前消息记录"
    echo "  -l    查看历史记录并选择加载"
    echo "  -c    手动压缩对话历史"
    echo "  -u    查看当前上下文 token 用量"
    echo "  -h    显示帮助信息"
}

parse_options() {
    for ((i=1; i<=$#; i++)); do
        if [[ "${!i}" != "-"* ]]; then
            content+="${!i}"
        fi
        if [[ "${!i}" == "-tr" ]]; then
            prectx="**注意：翻译任务不需要调用任何工具**。请翻译以下内容为中文，并拆解其语法的重点难点,其输入的格式如下：\
            原文：（原文内容显示在此处,原文可适当按照自己的理解进行格式化美化，然后输出并翻译）\
            译文：（译文内容显示在此处,注意，这里译文可以按照自己格式化美化后的文本进行翻译，因为用户提供的翻译文本很多都存在乱换行，结构松散的问题。）\
            语法分析：（语法分析在此处，尽量保持行文简洁,同样使用markdown格式标注出讲解的重点）\
            总结：（将以上内容做一个通俗易懂的总结，让用户将整个翻译串联起来，加深记忆）

            以上是格式，需要翻译的文本如下 "
            if [[ -z "$content" ]]; then
                    content="${prectx}: $(pbpaste)"
            else
                    content="${prectx}: $content"
            fi
            continue
        fi
        if [[ "${!i}" == "-to" ]]; then
            prectx="**注意：翻译任务不需要调用任何工具**。请解释该词的词源词根，以及由此延伸的词普遍都有哪些意思，并且给出一些常用的例句，注意例句要尽量简短，方便记忆。其输入格式如下：\
            词源词根：（在此处解释该词的词源词根）\
            延伸意思：（在此处解释由该词源词根延伸出来的意思）\
            常用例句：（在此处给出一些常用的例句，注意例句要尽量简短，方便记忆）\
            以上是格式，需要解释的词如下"
            if [[ -z "$content" ]]; then
                    content="${prectx}: $(pbpaste)"
            else
                    content="${prectx}: $content"
            fi
            continue
        fi

        # 单纯使用思考模式
        if [[ "${!i}" == "-t" ]]; then
            thinking="enabled"
            continue
        fi

        # 关闭深入思考，使用pro模型
        if [[ "${!i}" == "-p" ]]; then
            model="deepseek-v4-pro"
            continue
        fi

        # 深入思考
        if [[ "${!i}" == "-dp" ]]; then
            thinking="enabled"
            model="deepseek-v4-pro"
            continue
        fi

        if [[ "${!i}" == "-l" ]]; then
            history_handler
            break
        fi
        if [[ "${!i}" == "-f" ]]; then
            forget_history=1
            break
        fi

        if [[ "${!i}" == "-c" ]]; then
            compact_messages
            break
        fi

        if [[ "${!i}" == "-u" ]]; then
            print_usage
            break
        fi

        if [[ "${!i}" == "-h" ]]; then
            help_user
            break
        fi

    done
}

# 递归杀掉一个进程及其全部后代（TERM）。必须整树杀干净：
# 任何一个后代还攥着管道写端，read 就永远等不到 EOF，循环退不出去
kill_tree() {
    local pid=$1 child
    for child in $(pgrep -P "$pid" 2>/dev/null); do
        kill_tree "$child"
    done
    kill "$pid" 2>/dev/null
}

# 窗口模式运行命令
run_with_window() {
    local cmd="$1"
    local -a buf=()
    local total=0 drawn=0
    local now last_paint=0 dirty=0
    local chunk rc line pending="" partial=""
    local cmd_pid="" interrupted=0 old_trap="" win_fd="" win_tmpdir=""
    # 全量输出通过全局变量回传给调用方（窗口只显示末尾 WIN 行）
    window_output=""

    shopt -s extglob
    # 藏光标防闪烁；关自动换行 → 超宽行（含中文宽字符）在右边缘直接裁剪，
    # 光标不会掉到下一行，"上移 drawn 行"的算术就永远是对的
    printf '\033[?25l\033[?7l'

    # 剥掉 \r 和 CSI 转义序列（子命令的进度条/颜色码会画烂窗口）
    clean_line() {
        line=${line//$'\r'/}
        line=${line//$'\e'\[*([0-9;?])[[:alpha:]]/}
    }

    # drawn = 已画的完整行数；光标始终停在窗口底行（partial 行）上
    paint() {
        local i
        printf '\r'
        (( drawn > 0 )) && printf '\033[%dA' "$drawn"
        for i in "${buf[@]}"; do
            # \033[2K 整行擦除，清掉上一帧更长内容的残留
            printf '\033[2K\033[2m│\033[0m %s\n' "$i"
        done
        # 底行：交互提示等无换行输出画在这里，光标停在其末尾方便用户应答
        printf '\033[2K'
        if [[ -n "$partial" ]]; then
            printf '\033[2m│\033[0m \033[33m%s\033[0m\033[?25h' "$partial"
        else
            printf '\033[?25l'
        fi
        drawn=${#buf[@]}
    }

    # 用 FIFO + 后台任务代替进程替换 <(...)：$! 能拿到子 shell PID，
    # 卡死时 Ctrl+C 可精准杀掉子命令。
    # 不用 coproc 的原因：coproc 一结束 bash 会 unset 其数组并关掉读端 fd，
    # read 会撞上 "invalid file descriptor"；FIFO 读端由我们自己控制，EOF 语义干净。
    win_tmpdir=$(mktemp -d)
    mkfifo "$win_tmpdir/fifo" || return 1
    eval "$cmd" > "$win_tmpdir/fifo" 2>&1 &
    cmd_pid=$!
    exec {win_fd}<"$win_tmpdir/fifo"   # 读端与子命令的写端 rendezvous，不会死锁

    # 备份原 INT trap（通常是 handle_ctrl_c），窗口期间换成"只杀子命令"：
    # - 第一次 Ctrl+C：kill_tree 用 TERM 杀掉整个子进程树（子命令忽略 SIGINT，
    #   直接发 INT 没用），管道写端全部关闭 → read 收到 EOF → 窗口正常收尾，主脚本继续；
    # - 第二次 Ctrl+C：兜底恢复原 trap 并重新触发，走 handle_ctrl_c 整体退出。
    # trap 能在 0.1s 内执行的前提是下面 read 的 -t 轮询超时；无限阻塞的 read 会
    # 把 trap 推迟到 read 完成才执行（死锁），这里不存在。
    old_trap=$(trap -p INT)
    trap 'if (( interrupted )); then eval "$old_trap"; kill -INT $$; else interrupted=1; kill_tree "$cmd_pid"; fi' INT

    while :; do
        chunk=""
        # 按块读而非按行读：交互提示通常不带换行，按行 read 永远等不到 \n，
        # 用户只能面对空窗口；-N+超时能把无换行的残段也取出来实时展示
        IFS= read -r -t 0.1 -N 65536 -u "$win_fd" chunk
        rc=$?
        if [[ -n "$chunk" ]]; then
            pending+="$chunk"
            while [[ "$pending" == *$'\n'* ]]; do
                line="${pending%%$'\n'*}"
                pending="${pending#*$'\n'}"
                clean_line
                window_output+="$line"$'\n'
                buf+=("$line")
                (( ${#buf[@]} > WIN )) && buf=("${buf[@]:1}")
                (( total++ ))
            done
            line="$pending"; clean_line; partial="$line"
            dirty=1
        fi
        # 超时退出码 >128（继续等）；1~128 是 EOF（命令结束）
        (( rc > 0 && rc <= 128 )) && break
        now=${EPOCHREALTIME/./}
        if (( dirty && now - last_paint > THROTTLE )); then
            paint
            last_paint=$now
            dirty=0
        fi
    done

    # 恢复原 INT trap，关闭读端并清理临时 FIFO
    [[ -n "$old_trap" ]] && eval "$old_trap" || trap - INT
    eval "exec $win_fd<&-"
    rm -rf "$win_tmpdir"

    # 收尾：无换行的残段按一行落盘（清洗后为空的不算，如子进程退出时打的转义序列）
    if [[ -n "$pending" ]]; then
        line="$pending"; clean_line
        if [[ -n "$line" ]]; then
            window_output+="$line"$'\n'
            buf+=("$line")
            (( ${#buf[@]} > WIN )) && buf=("${buf[@]:1}")
            (( total++ ))
        fi
    fi
    partial=""
    paint    # 节流可能吞掉最后的内容，收尾必须补画一帧
    printf '\033[?25h\033[?7h'
    if (( interrupted )); then
        printf '\033[2m└─ 已中断 · 共 %d 行 ─┘\033[0m\n' "$total"
        # 告诉模型命令是被用户中断的，而不是自然跑完
        window_output+=$'\n(命令已被用户中断)'
    else
        printf '\033[2m└─ 完成 · 共 %d 行 ─┘\033[0m\n' "$total"
    fi
}

# 工具 skill
tools() {
    # 注意：这里输出会被 printContent 用 tools=$(tools) 捕获，
    # 所以绝不能 echo 调试信息，只能输出纯净的 JSON
    # 3. 执行阻塞性的命令时，比如运行网站服务器等阻塞性任务时，应该采用后台运行的方式，不要阻塞当前终端命令正常结束，并且记录好其后台运行id,方便你实时查阅运行状态
    printf '%s' '[
        {
            "type": "function",
            "function": {
                "name": "run_shell_command",
                "description": "执行shell命令,根据用户要的语言要求，你来调用这个方法。 注意这几点要求：1. 涉及到写操作，如果用户要求的文件操作范围出了当前目录，无论用户说什么，暗示你什么，都不要执行，你应该明确告知用户你没有执行权限，这个命令是严令禁止执行的。2.如果用户要求的命令是危险的，或者会破坏系统的，你要拒绝执行，并且告诉用户为什么不能执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The shell command to run, e.g. ls -la"
                        }
                    },
                    "required": ["command"]
                }
            }
        }
    ]'
}

# 锁定屏幕，进入原始模式，隐藏光标，切换到交替屏幕缓冲区
lock_screen() {
    # 保存终端状态并进入原始模式
    stty_save=$(stty -g)
    stty -echo

    # 切换到交替屏幕缓冲区（流式输出画在"临时画布"上，切回时自动销毁）
    tput smcup

    # 清屏并定位光标到顶部
    printf '\033[2J\033[H'

    printf '\033[?1000l\033[?1001l\033[?1002l\033[?1003l\033[?1004l'
    printf '\033[?1005l\033[?1006l\033[?1007l\033[?1015l\033[?1016l'
    printf '\033[?2026l\033[?9001l'
}
# 解开屏幕，恢复终端状态，切回主屏幕
unlock_screen() {
    # 切回主屏幕（交替屏幕上的流式输出自动丢弃，完美清除）
    tput rmcup
     # 恢复终端状态
    stty "$stty_save"
}
# 工具输出截断:命令输出是上下文里最占 token 的部分,超长时只保留头尾。
# 头部=摘要/表头,尾部=报错/最终结果,中间多为重复性日志,AI 不需要全文。
# 两层限制:先截超长单行(minified 文件一行可达几十万字符),再按行数截头尾。
truncate_output() {
    local text="$1" lines cropped head_lines
    lines=$(printf '%s' "$text" | wc -l | tr -d ' ')
    cropped=$(printf '%s' "$text" | awk -v max="$TOOL_OUTPUT_MAX_COL" \
        'length($0) > max { print substr($0, 1, max) "...(该行过长已截断)"; next } { print }')
    if (( lines > TOOL_OUTPUT_MAX_LINES )); then
        head_lines=$((TOOL_OUTPUT_MAX_LINES / 2))
        {
            printf '%s' "$cropped" | head -n "$head_lines"
            printf '\n... (中间 %d 行已省略) ...\n\n' "$((lines - TOOL_OUTPUT_MAX_LINES))"
            printf '%s' "$cropped" | tail -n "$head_lines"
        }
    else
        printf '%s' "$cropped"
    fi
}

# 自动压缩(compaction):把超出保留范围的旧消息浓缩成一条摘要消息,
# 结构重组为 system + [摘要] + 最近 KEEP_MSGS 条原文。
# 业界标准做法(Claude Code / Aider):
#   - 压缩请求用便宜的 flash 模型、stream:false 静默一次拿完,不渲染;
#   - 摘要作为普通 user 消息插入,历史可被递归再压缩;
#   - 压缩失败时兜底硬截断(保留 system + 最近原文),绝不让脚本卡死;
#   - 只在"完整一轮的边界"调用(printContent 开头),工具调用链中途调用会
#     破坏 tool_call_id 配对,API 会直接报错。
compact_messages() {
    local total cut system_msg tail_msgs to_compact summary request_body
    total=$(echo "$messages" | jq 'length')
    (( total <= KEEP_MSGS )) && echo "消息数量未达到压缩门槛(当前 ${total} 条)" && return 0

    cut=$((total - KEEP_MSGS))
    system_msg=$(echo "$messages" | jq '.[0]')
    tail_msgs=$(echo "$messages" | jq --argjson cut "$cut" '.[$cut:]')

    # 中间段转成 "[role] content" 可读文本,减少压缩请求的输入 token;
    # assistant 带 tool_calls 的消息 content 为 null,单独转成一行描述,
    # 保证"模型调用了哪个工具、什么参数"这一关键信息不丢
    to_compact=$(echo "$messages" | jq -r --argjson cut "$cut" '
        .[1:$cut][] |
        if .tool_calls != null then
            "[assistant → 工具调用] " + (.tool_calls | tostring)
        else
            "[" + .role + "] " + (.content // "")
        end')

    if (( last_prompt_tokens > 0 )); then
        printf '📦 上下文 %s tokens 已超阈值 %s,正在压缩历史…\n' "$last_prompt_tokens" "$TOKEN_THRESHOLD"
    else
        printf '📦 正在压缩历史…\n'
    fi

    request_body=$(jq -n --arg compact_text "$to_compact" '{
        model: "deepseek-v4-flash",
        messages: [
            {role: "system", content: "你是对话历史压缩助手。任务:把用户提供的旧对话记录压缩成一段精炼的摘要,替代原始记录用于后续继续对话。要求:1. 保留用户的整体目标与意图、关键决策与结论、已执行的命令及其重要结果、出现的错误与最终修复方式、涉及的重要文件路径、用户的个人偏好与约束;2. 丢弃客套话、重复内容与冗长的命令输出;3. 用中文输出,不超过 500 字,只输出摘要正文,不要任何前缀或解释。"},
            {role: "user", content: $compact_text}
        ],
        thinking: {type: "disabled"},
        reasoning_effort: "high",
        stream: false
    }')

    # 同步一次拿完。--max-time 兜底防卡死;失败时 summary 为空,走硬截断分支
    summary=$(curl -s --max-time 120 https://api.deepseek.com/chat/completions \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $DEEPSEEK_API_KEY" \
        -d "$request_body" | jq -r '.choices[0].message.content // empty' 2>/dev/null)

    if [[ -z "$summary" ]]; then
        messages=$(echo "$messages" | jq --argjson cut "$cut" '.[0:1] + .[$cut:]')
        printf '⚠️  压缩失败(网络/接口错误),已回退:保留 system + 最近 %s 条原文\n' "$KEEP_MSGS"
    else
        messages=$(jq -n --argjson sys "$system_msg" --arg prefix "$SUMMARY_PREFIX" \
            --arg summary "$summary" --argjson tail "$tail_msgs" \
            '[ $sys, {role: "user", content: ($prefix + "\n" + $summary)}, $tail[] ]')
        printf '✅ 压缩完成:%s 条消息 → 1 条摘要 + %s 条原文\n' "$total" "$KEEP_MSGS"
    fi
    # 重置计数:下一轮正常请求会带回新 messages 的精确 prompt_tokens
    last_prompt_tokens=0
}

# 查看当前上下文用量
print_usage() {
    local est pct
    est=$(printf '%s' "$messages" | wc -c | tr -d ' ')
    est=$((est * 2 / 5))
    echo "消息条数: $(echo "$messages" | jq length)"
    if (( last_prompt_tokens > 0 )); then
        pct=$((last_prompt_tokens * 100 / TOKEN_THRESHOLD))
        echo "上下文用量: ${last_prompt_tokens} tokens / 阈值 ${TOKEN_THRESHOLD}(已用 ${pct}%)"
    else
        echo "上下文用量: 约 ${est} tokens(字节粗算;精确值在下次请求后显示,阈值 ${TOKEN_THRESHOLD})"
    fi
}

# 追加一条 user 消息到 messages 列表（必须在父 shell 调用，不能放 $() 里）
add_message() {
    local addctx="$1"
    local role="$2"
    local tool_call_id="$3"
    local tool_call_name="$4"
    local tool_call_args="$5"
    if [[ -z "$role" ]]; then
        # 默认 user 消息
        messages=$(echo "$messages" | jq --arg content "$addctx" '. + [{role: "user", content: $content}]')
    elif [[ "$role" == "assistant" && -n "$tool_call_name" ]]; then
        # assistant 发起工具调用：必须记录 tool_calls。若本轮已有正文输出，
        # 也要把正文写入 content，否则工具调用前的正文会丢失，messages 不完整。
        if [[ -z "$addctx" ]]; then
            messages=$(echo "$messages" | jq --arg id "$tool_call_id" --arg name "$tool_call_name" --arg args "$tool_call_args" \
                '. + [{role: "assistant", content: null, tool_calls: [{id: $id, type: "function", function: {name: $name, arguments: $args}}]}]')
        else
            messages=$(echo "$messages" | jq --arg content "$addctx" --arg id "$tool_call_id" --arg name "$tool_call_name" --arg args "$tool_call_args" \
                '. + [{role: "assistant", content: $content, tool_calls: [{id: $id, type: "function", function: {name: $name, arguments: $args}}]}]')
        fi
    elif [[ -n "$tool_call_id" ]]; then
        # tool 角色：工具执行结果，带 tool_call_id
        messages=$(echo "$messages" | jq --arg content "$addctx" --arg role "$role" --arg tool_call_id "$tool_call_id" '. + [{role: $role, content: $content, tool_call_id: $tool_call_id}]')
    else
        # 普通角色（assistant 文本回复等）
        messages=$(echo "$messages" | jq --arg content "$addctx" --arg role "$role" '. + [{role: $role, content: $content}]')
    fi
}
auth_add_message() {
    local addctx="$1"
    local role="${2:-user}"
    # 基于 auth_message 自身拼接（原实现误用了 $messages，会丢掉对话历史）
    auth_message=$(echo "$auth_message" | jq --arg content "$addctx" --arg role "$role" '. + [{role: $role, content: $content}]')
}

# 工具调用处理器：解析流式分片的工具调用 JSON，输出标准化的 fn_name/fn_args/fn_id
tool_handler() {
    local tool_chunk="$1"

    local chunk_name chunk_args chunk_id
    chunk_name=$(printf '%s' "$tool_chunk" | jq -r '.[0].function.name // empty' 2>/dev/null)
    chunk_args=$(printf '%s' "$tool_chunk" | jq -r '.[0].function.arguments // empty' 2>/dev/null)
    chunk_id=$(printf '%s' "$tool_chunk" | jq -r '.[0].id // empty' 2>/dev/null)

    # 用 jq 构造 JSON，自动转义 fn_args 里的引号/反斜杠。
    # 绝不能手工拼字符串：arguments 分片里到处是 "，会直接破坏 JSON 语法。
    jq -n --arg name "$chunk_name" --arg args "$chunk_args" --arg id "$chunk_id" \
        '{fn_name: $name, fn_args: $args, fn_id: $id}'
}

auth_think() {
    local cmd=$1
    local request_body
    auth_add_message "$cmd"
    request_body=$(jq -n \
            --argjson msgs "$auth_message" \
            '{
            model: "deepseek-v4-flash",
            messages: $msgs,
            thinking: {type: "disabled"},
            reasoning_effort: "high",
            stream: true,
            stream_options: {include_usage: true}
        }')
    # 结果通过全局 full_response 传回（ask_ai 会重置它），这里不再 printf。
    # 本函数绝不能放进 $(...) 里调用：ask_ai 的流式输出会塞满命令替换的
    # 管道缓冲（16K~64K），写端阻塞而父 shell 又在等它结束，直接死锁。
    # 第三个参数 0:auth 消息极小,不把它的 usage 记入 last_prompt_tokens
    ask_ai "$request_body" 0 0

    sleep 0.5

    auth_add_message "$full_response" "assistant"
}
# 工具调用执行器：根据 fn_name/fn_args/fn_id 执行工具，并把结果喂给模型继续推理
tool_run_and_parse() {
    local id="$1"
    local tools
    local request_body
    
    add_message "$full_response" "assistant" "$fn_id" "$fn_name" "$fn_args"

    if [[ -n "$fn_name" && -n "$fn_args" && -n "$fn_id" ]]; then

        if [[ "$fn_name" == "run_shell_command" ]]; then
            render_markdown "$full_response"
            command=$(jq -r '.command' <<< "$fn_args")
            # tool_use_response="**工具调用: $command**"
            # render_markdown "$tool_use_response"
            printf " \033[43;30;1m  工具调用：${command}  \033[0m"
            echo $'\n\n'

            # 冲掉流式回复期间积压在 tty 队列的字节（粘贴/误触），
            # 防止残留的 "y" 自动确认执行命令（粘贴注入防护）
            local _junk=""
            while IFS= read -r -t 0.01 -N 1000000 _junk; [[ -n "$_junk" ]]; do _junk=""; done


            fn_name=""
            fn_args=""
            fn_id=""

            # echo "审判开始；；；；"
            # 不能写 ai_auth=$(auth_think ...)：命令替换会捕获 auth_think 的
            # 全部 stdout，流式输出一旦超过管道缓冲就死锁。改为直接调用，
            # 结果从全局变量 full_response 取。
            auth_think "$command"
            ai_auth=$(echo -e "$full_response")
            
            if [[ "$ai_auth" == "N" ]]; then
                printf '\033[2K\033[1;41;37m是否执行该命令？(y/n): \033[0m'
                IFS= read -r answer

            fi
            # echo "用户输入: $answer"
            if [[ "$ai_auth" == "Y" || "$answer" == "y" ]]; then
                # 窗口内执行命令；完整输出在 window_output，作为工具结果喂给 AI
                # 先截断:超长输出保留头尾,砍掉重复性日志,控制上下文体积
                run_with_window "$command"
                content=$(truncate_output "$window_output")
                sleep 2
            else
                content="用户拒绝执行命令: $command"
            fi
        fi

        add_message "$content" "tool" "$id"

        # id="$fn_id"

        fn_name=""
        fn_args=""
        fn_id=""

        tools=$(tools)
        request_body=$(jq -n \
            --arg model "$model" \
            --argjson msgs "$messages" \
            --arg thinking "$thinking" \
            --argjson tool "$tools" \
            '{
            model: $model,
            messages: $msgs,
            thinking: {type: $thinking},
            reasoning_effort: "high",
            stream: true,
            tools: $tool,
            stream_options: {include_usage: true}
        }')

        ask_ai "$request_body" 1

        # render_markdown "$thinking_response"
        # render_markdown "$full_response"
    fi
}
# 核心函数：向模型发起请求，流式接收输出，处理工具调用
# 参数:$1 请求体  $2 是否锁屏  $3 是否记录 usage(默认 1;auth 等旁路请求传 0,
#      避免把小请求的 prompt_tokens 误记为上下文真实大小)
ask_ai() {
    
    # 拦截以选项开头的内容（如 "-l xxx"），避免被当成普通提问发给模型
    for option in "${options_list[@]}"; do
        [[ "$content" == "$option" || "$content" == "$option "* ]] && content="" && return 0
    done

    local think_start=0
    local think_end=0
    local chunk=""
    local json=""
    local usage_pt=""
    local request_body=$1
    local is_lock_screen=$2
    local is_record_usage=${3:-1}

    full_response=""
    thinking_response=""

    [[  "$is_lock_screen" == 1 ]] && lock_screen

    while IFS= read -r line; do
        if [[ "$line" =~ ^data:\  ]]; then
            json="${line#data: }"
            [[ "$json" == "[DONE]" ]] && break

            # stream_options.include_usage:流式最后一个 chunk 的 choices 为空、
            # 只带 usage。prompt_tokens = 本次请求发送的全量 messages 的 token 数,
            # 即"当前上下文大小"的精确值,直接覆盖旧值,无需累计
            if (( is_record_usage )); then
                usage_pt=$(printf '%s' "$json" | jq -r '.usage.prompt_tokens // empty' 2>/dev/null)
                [[ -n "$usage_pt" ]] && last_prompt_tokens=$usage_pt
            fi

            # 思考部分的输出也要显示出来
            thinking_chunk="$(printf '%s' "$json" | jq -j '.choices[0].delta.reasoning_content // empty' 2>/dev/null; echo x)"
            thinking_chunk="${thinking_chunk%x}"
            if [[ -n "$thinking_chunk" ]]; then
                if [[ $think_start -eq 0 ]]; then
                    think_start=1
                    printf '\n\033[33m----------思考开始------------\033[0m\n'
                    thinking_response+=$'\n\n----------思考开始------------\n\n\n'
                fi
                printf '%s' "$thinking_chunk"
            fi
            [[ -n "$thinking_chunk" ]] && thinking_response+="$thinking_chunk"


            # jq -j 不自动加末尾换行 + echo x 技巧保留内容中的 \n（$() 会截掉末尾换行符）
            chunk="$(printf '%s' "$json" | jq -j '.choices[0].delta.content // empty' 2>/dev/null; echo x)"
            chunk="${chunk%x}"

            if [[ $think_end -eq 0 && $think_start -eq 1 ]]; then
                if [[ -z "$thinking_chunk" && -n "$chunk" ]]; then
                    think_end=1
                    printf '\n\033[33m----------思考结束------------\033[0m\n'
                    thinking_response+=$'\n\n----------思考结束----------\n\n\n'
                fi
            fi

            if [[ -n "$chunk" ]]; then
                printf '%s' "$chunk"
            fi
            [[ -n "$chunk" ]] && full_response+="$chunk"

            
            tool_chunk="$(printf '%s' "$json" | jq -j '.choices[0].delta.tool_calls // empty')"
            if [[ -n "$tool_chunk" ]]; then
                # echo "==: $tool_chunk"
                fn_object=$(tool_handler "$tool_chunk")
                # echo "工具调用更新==: $fn_object"
                # 流式分片：函数名/id 只在首片出现，非空才赋值，避免被空分片冲掉；
                # arguments 是分片到达的，必须累加 fn_args+="$chunk"，不能覆盖。
                chunk_name=$(jq -r '.fn_name' <<< "$fn_object")
                chunk_args=$(jq -r '.fn_args' <<< "$fn_object")
                chunk_id=$(jq -r '.fn_id' <<< "$fn_object")
                [[ -n "$chunk_name" ]] && fn_name="$chunk_name"
                [[ -n "$chunk_args" ]] && fn_args+="$chunk_args"
                [[ -n "$chunk_id" ]] && fn_id="$chunk_id"
            fi

            # chunk_end="$(printf '%s' "$json" | jq -j '.choices[0].finish_reason // empty')"
            # [[ "$chunk_end" == "tool_calls" ]] && tool_handler 0
        fi
    done < <(
        curl -sN https://api.deepseek.com/chat/completions \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer $DEEPSEEK_API_KEY" \
            -d "$request_body"\
        2>&1
    )

    [[ "$is_lock_screen" == 1 ]] && unlock_screen

    [[ -n "$fn_id" ]] && tool_run_and_parse "$fn_id"
}

# 打印内容函数
printContent() {

    add_message "$content"

    # 上下文超阈值时先压缩再发送。只在"完整一轮的边界"(这里)触发:
    # 工具调用链中途压缩会破坏 tool_call_id 配对,API 直接报错。
    (( last_prompt_tokens > TOKEN_THRESHOLD )) && compact_messages

    # 构建请求体
    # messages 是 JSON 数组，必须用 --argjson 而不是 --arg
    # thinking 为空时不应发送 thinking 字段
    local tools=$(tools)
    local request_body=$(jq -n \
        --arg model "$model" \
        --argjson msgs "$messages" \
        --arg thinking "$thinking" \
        --argjson tool "$tools" \
        '{
        model: $model,
        messages: $msgs,
        thinking: {type: $thinking},
        reasoning_effort: "high",
        stream: true,
        tools: $tool,
        stream_options: {include_usage: true}
    }')

    
    ask_ai "$request_body" 1
    

    # glow 是 markdown 渲染器，会把「行尾反斜杠 + 换行」当作 hard break（硬换行）
    # 语法而隐藏反斜杠，导致用户输入的 \\ 只保留的一个 \ 看起来也被删了。
    # 先用 sed 把行尾单个 \ 转义成 \\，glow 就能原样显示它。
    render_markdown "$thinking_response"
    render_markdown "$full_response"
    # echo "$messages"

    [[ -n "$full_response" ]] && add_message "$full_response" "assistant"

}

# 根据聊天记录生成标题
overview() {

    local chunk=""
    # local res=""
    local msg
    local request_body

    # echo "记录是: $messages"
    msg=$(echo "$messages" | jq '. + [{role: "user", content: "根据我们的聊天对话，总结一个恰当的标题。输入格式如下: 标题内容。注意：除了按格式输出外，不可以有多余文字，标题内容不可有特殊字符换行符号等。"}]')

    request_body=$(jq -n  --argjson msgs "$msg" '{
        model: "deepseek-v4-flash",
        messages: $msgs,
        thinking: {type: "disabled"},
        reasoning_effort: "high",
        stream: true,
        stream_options: {include_usage: true}
    }')

    ask_ai "$request_body"

    printf "%s" "$full_response"
}

# 检测当前运行环境：macos / wsl / linux
detect_os() {
    if [[ "$(uname -s)" == "Darwin" ]]; then
        echo "macos"
    elif [[ -n "$WSL_DISTRO_NAME" ]] || grep -qi microsoft /proc/version 2>/dev/null; then
        echo "wsl"
    else
        echo "linux"
    fi
}

# WSL 下选择文件：优先 WSLg 的 zenity，否则用 Windows 原生对话框（PowerShell + WinForms），
# 返回的 Windows 路径用 wslpath 转回 Linux 路径
wsl_choose_file() {
    local winpath=""

    # 有 WSLg 图形环境时直接用 Linux GUI 选择器
    if [[ -n "$DISPLAY" ]] && command -v zenity >/dev/null 2>&1; then
        local pick
        pick=$(zenity --file-selection --title="选择文件" 2>/dev/null) || { echo "CANCELLED"; return 0; }
        echo "$pick"
        return 0
    fi

    # 退回 Windows 原生对话框（不依赖图形环境/发行版内软件）
    # shellcheck disable=SC2016
    winpath=$(powershell.exe -NoProfile -NonInteractive -Command '
        [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
        Add-Type -AssemblyName System.Windows.Forms
        $dlg = New-Object System.Windows.Forms.OpenFileDialog
        $dlg.Title = "选择文件"
        $dlg.Multiselect = $false
        if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
            Write-Output $dlg.FileName
        }
    ' 2>/dev/null | tr -d '\r')

    if [[ -n "$winpath" ]]; then
        wslpath -u "$winpath" 2>/dev/null
    else
        echo "CANCELLED"
    fi
}

# Linux 下选择文件：依次尝试 zenity / kdialog / yad，都没有则退回 fzf
linux_choose_file() {
    local pick=""
    if command -v zenity >/dev/null 2>&1; then
        pick=$(zenity --file-selection --title="选择文件" 2>/dev/null)
    elif command -v kdialog >/dev/null 2>&1; then
        pick=$(kdialog --getopenfilename 2>/dev/null)
    elif command -v yad >/dev/null 2>&1; then
        pick=$(yad --file-selection --title="选择文件" 2>/dev/null)
    elif command -v fzf >/dev/null 2>&1; then
        pick=$(fzf --prompt="选择文件> ")
    else
        echo "未找到可用的文件选择器，请安装 zenity / kdialog / yad 或 fzf" >&2
    fi
    [[ -z "$pick" ]] && pick="CANCELLED"
    echo "$pick"
}

# 读取文件内容并追加到输入中
read_file() {

    [[ "$forget_history" == 1 ]] && return 0

    filePath=""
    fileContent=""
    case "$(detect_os)" in
        macos)
            filePath=$(osascript -e 'try' -e 'POSIX path of (choose file)' -e 'on error' -e 'return "CANCELLED"' -e 'end try')
            ;;
        wsl)
            filePath=$(wsl_choose_file)
            ;;
        linux)
            filePath=$(linux_choose_file)
            ;;
    esac
	echo "[文件] $filePath"
    if [[ -n "$filePath" && "$filePath" != "CANCELLED" && -f "$filePath" ]]; then
        fileContent=$(<"$filePath")
        input+="$fileContent"$'\n'
    elif [[ "$filePath" == "CANCELLED" ]]; then
        # 取消选择时静默返回（错误提示已由各选择器打印）
        :
    fi
    normal_line 0
}

# 读取用户输入的多行内容，支持续行、粘贴、文件导入等
normal_line() {
    local line

    local prompt
    if [[ "$1" == 0 ]]; then
        prompt="..  "
    else
        prompt="> "
    fi
    while IFS= read -e -r -p "$prompt" line; do
        # 多行粘贴：括号粘贴已关闭，首行被 readline 接受后剩余字节还在 tty 队列里。
        # 一次性非阻塞大块读取（-N 走非规范模式，末尾没换行的残段超时后也会存入变量）；
        # 排干期间关掉回显，避免 macOS 在 tty 模式切换时重复回显残余队列
        local rest=""
        local chunk=""
        local stty_save
        stty_save=$(stty -g)
        stty -echo
        # 循环排干：慢速粘贴（SSH/分块粘贴）可能跨越多个超时窗口
        while IFS= read -r -t 0.05 -N 1000000 chunk; [[ -n "$chunk" ]]; do
            rest+="$chunk"
            chunk=""
        done
        stty "$stty_save"
        if [[ -n "$rest" ]]; then
            # 粘贴不直接提交：转入续行模式，用户可继续补充，真正按回车才结束
            rest="${rest%$'\n'}"
            # 排干时回显是关的，补印粘贴的剩余行让用户确认内容
            printf '%s\n' "$rest"
            line+=$'\n'"$rest"
            input+="$line"$'\n'
            prompt=".. "
            continue
        fi
        if [[ "$line" == "@" ]]; then
            read_file
            break
        fi
        if [[ "${line: -1}" == "@" ]]; then
            line="${line%@}"
            input+="$line"$'\n'
            read_file
        fi
        if [[ "$line" == *\\ ]]; then
            # 行尾有反斜杠 → 续行。只删掉最后一个字符（即那个反斜杠），
            # 用 ${line%?}（删除任意一个字符）而不是 ${line%\\}，
            # 避免不同 bash 版本对 pattern 中反斜杠转义的解析差异导致删多了。
            line="${line%?}"
            input+="$line"$'\n'
            prompt=".. "
            continue
        fi
        if [[ "$line" == "-l" ]]; then
            history_handler
            break
        fi
        if [[ "$line" == "-msgs" ]]; then
            echo "$messages"
            break
        fi
        if [[ "$line" == "-c" ]]; then
            compact_messages
            break
        fi
        if [[ "$line" == "-u" ]]; then
            print_usage
            break
        fi
        if [[ "$line" == "-h" ]]; then
            help_user
            break
        fi

        input+="$line"$'\n'
        prompt=".. "

        break
    done
    input="${input%$'\n'}"
}




# 解析命令行参数
parse_options "$@"
# 是否是第一次进入
first_time=1
if [[ -n "$content" ]]; then
    printContent
    [[ -z "$chat_title"  ]] && chat_title=$(overview) && first_time=0
fi

# 交互式输入循环
while true; do
    input=""
    normal_line 1
    content="$input"

    [[ -z "$content" ]] && continue

    printContent

    if [[ -z "$chat_title" && "$first_time" -eq 1 ]]; then
        chat_title=$(overview) && first_time=0
    fi
done



