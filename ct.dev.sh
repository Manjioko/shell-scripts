#!/bin/bash

# 该脚本支持mimo-2.5/deepseek-v4
# ================切换模型需要改这里👇👇=======================
#deepseek
# https://api.deepseek.com/chat/completions
BASE_URL="https://api.xiaomimimo.com/v1/chat/completions"
# api key
DEEPSEEK_API_KEY="sk-czmvdt29sr03q4jaa7fonxycos40y02ipzvq6g34m73fewai"
#"deepseek-v4-flash"
#"deepseek-v4-pro"
flash_model="mimo-v2.5"
pro_model="mimo-v2.5-pro"
# ===============================👆👆========================


# 保存当前对话的标题，默认空字符串，首次保存时由模型生成
chat_title=""

# 最新一次用户输入的内容
content=""

# 记录所有的对话内容，包含用户和助手的消息
messages=$(jq -n '[{role: "system", content: "You are a helpful assistant."}]')

#辅助权限判断助手消息
# 
auth_message=$(jq -n '[{role: "system", content: "你是一个bash命令判断专家，用户给你一个bash命令，你会根据上下文/bash命令本身，判断这个bash命令是否存在危险性，判断标准是：涉及写操作/bash命令存在严重的威胁用户电脑安全问题/可能存在让用户丢失数据的bash命令/存在模糊的，可能的攻击命令。如果存在以上这些命令，你总是回复N,否则，你会回复Y。注意，你不会调用任何bash命令，只是做判断，并且只能回复【Y/N】,不会回复多余文字。同时存在白名单机制:【1. /tmp/*】-> 遇到读写白名单内的目录/文件，应该放行(回复Y)。"}]')


# 是否思考,默认关闭
thinking="disabled"

#模型，默认是 deepseek-v4-flash
model="$flash_model"

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

# 触发自动压缩的阈值。注意:本脚本默认跑 mimo-v2.5(小米)接口,实际窗口
# 远小于 deepseek 的 1M——实测约 4 万 token 就会越界断连。按 32K 窗口的
# 75% 取 24000,留出安全余量;换更大窗口的模型时再按比例调高。
# 如果是deepseek-v4,可改成 100000
TOKEN_THRESHOLD=24000

# 压缩后保留最近多少条消息原文(一轮工具链约 4 条:user + assistant(tool_calls)
# + tool + assistant(最终回复),16 条 ≈ 最近 4 轮,足够模型衔接上下文)
KEEP_MSGS=16

# 摘要消息的前缀标记:让用户在 -msgs / 历史渲染时能一眼认出"这是压缩过的历史"
SUMMARY_PREFIX="【历史对话摘要】"

# 工具输出(tool 角色消息)是上下文里最肥的部分:超长命令输出保留头尾,
# 中间只留行数标记(开头=表头/摘要,结尾=报错/结果,中间多为重复日志)
TOOL_OUTPUT_MAX_LINES=400
TOOL_OUTPUT_MAX_COL=2000

# ---------- 文件类工具(read_file/write_file/edit_file/list_dir/grep)配置 ----------
# 工具可访问的根目录(物理路径,启动时锁定)。所有文件类工具的路径
# 必须解析后落在此目录内,越界直接拒绝——权限在代码层强制,不靠提示词。
WORK_ROOT=$(pwd -P)

# read_file 单次返回的字符上限:超过则截断并提示用 offset/limit 分段读。
# 按行粒度截断(awk 逐行累计),不会把多字节字符切两半。
# 代码必须完整,所以阈值给得比 shell 输出(400 行)宽得多,且由 AI 自主分段。
READ_MAX_CHARS=30000

# grep 单次最多返回的匹配行数,超出只保留前 N 条并提示
GREP_MAX_MATCHES=200

# grep 结果单行截断宽度,防止命中 minified 长行污染上下文
GREP_MAX_COL=500

# web_fetch 单次返回网页纯文本的字符上限,超出按行截断并提示
WEB_MAX_CHARS=20000
# ----------------------------------------------------

# 用户输入的内容
input=""
# Ctrl+C 状态标志(配合 handle_ctrl_c):
#   ai_active=1     正处于 ask_ai 流式输出阶段 → Ctrl+C 打断本轮输出(ai_abort)
#   ai_active=0     主屏幕(输入行的 Ctrl+C 由 readline 清行;普通 read 被打断
#                   时置 input_aborted),任何情况都不退出
#   round_aborted   本轮 AI 输出是否被 Ctrl+C 打断(printContent 置位,主循环据此跳过标题生成)
ai_active=0
ai_abort=0
input_aborted=0
round_aborted=0
# 保存当前终端设置
stty_save=""

# thinking_response 已渲染到主屏幕的字符位置。思考随流只画在交替屏幕上,
# 解锁即被丢弃;工具链每轮结束后由 flush_thinking 把"增量思考"渲染到
# 主屏幕(工具横幅之前),主屏幕时间线才与真实流程一致:
#   思考1 → 工具1 → 思考2 → 工具2 → ... → 正文
thinking_rendered_len=0

# 窗口行数
WIN=10
# 重画节流间隔（微秒）
THROTTLE=50000

# 显示层裁剪宽度:minified CSS/JS 一行可达数 MB。若超长行全量进显示缓冲和
# 底行 partial,每轮循环都要对不断增长的 pending 做全量字符串清洗+复制(O(n)
# 且越积越大),bash 卡在字符串操作中不检查信号,Ctrl+C 被无限推迟。
# 显示层只保留可视宽度;完整内容照旧进 window_output(喂给 AI,由截断兜底)。
DISP_COL=300

# run_with_window 兜底:命令主体已退出,但输出管道(FIFO 写端)仍被其后台子进程
# 握着(AI 在命令里误用 & 启动服务),EOF 永远不来。主进程已死且静默超过该时长
# 就提前收尾,防止窗口永久挂死。仅当主进程已退出才计时,不影响正常慢命令。
BG_SILENT_TIMEOUT=3000000   # 3 秒(微秒)

# 用户按下 Ctrl+C 后的强制收尾时限:第一次 INT 触发杀组后,若 EOF 仍不来
# (杀组失败/孤儿写端等极端情况),静默超过该时长直接强制收尾。
# 保证"按一次 Ctrl+C 最多卡 2 秒"的硬承诺,不依赖杀组是否成功。
INT_FORCE_TIMEOUT=2000000   # 2 秒(微秒)

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

# 并行工具调用收集区:DeepSeek 一次响应可返回多个 tool_calls(按 index 区分)。
# 按 index 存入关联数组,arguments 分片按 index 各自累加。
# 历史 bug:旧实现只取 .[0] 且把所有 index 的 arguments 无区分拼接,
# 导致多个调用的 JSON 参数粘在一起,jq 解析出 null → eval "null" 报错。
# 注意:declare -A 是 bash 4+ 语法,必须放在上面的 exec 切换 bash 5 之后,
# 否则 macOS 自带 bash 3.2 会报 "declare: -A: invalid option" 并退出。
declare -A tool_ids=() tool_names=() tool_args=()


# pty 包装探测(启动时一次):裸 FIFO 会让子命令的 stdio 切到全缓冲,
# python/node/编译器等程序的输出会憋到进程退出才一次性涌出,窗口失去实时性。
# 套一层伪终端(pty)让子命令以为自己面对终端,恢复行缓冲。
# script 几乎是所有 Unix 标配,但有两个语法家族:
#   util-linux(Linux): script -qec "cmd" /dev/null,支持 --version
#   BSD(macOS/FreeBSD): script -q /dev/null cmd...,不支持 --version(借此区分)
# 都没有时用 python3 的 pty 模块兜底;再没有退回裸 FIFO(仅损失实时性,功能不变)
if command -v script >/dev/null 2>&1; then
    if script --version 2>/dev/null | grep -qi util-linux; then
        PTY_STYLE="util-linux"
    else
        PTY_STYLE="bsd"
    fi
elif command -v python3 >/dev/null 2>&1; then
    PTY_STYLE="python"
else
    PTY_STYLE="none"
fi

# 关闭 readline 括号粘贴：多行粘贴时首行立即被 read -e 接受，
# 剩余字节留在 tty 输入队列里，可用不带 -e 的非阻塞 read 排干拿到全文
# （若保持开启，bash 会把首行之后的内容直接丢弃）
_ct_inputrc=$(mktemp)
[[ -f "$HOME/.inputrc" ]] && printf '$include %s\n' "$HOME/.inputrc" > "$_ct_inputrc"
printf 'set enable-bracketed-paste off\n' >> "$_ct_inputrc"
export INPUTRC="$_ct_inputrc"

# 主屏输入行(read -e)按 Ctrl+C 立即清空当前行:readline 读取期间会强制打开
# ISIG,所以单靠 trap 拦不住——SIGINT 打断不了 readline 的 read,旧内容会留在
# 缓冲区里"复活"。做法:normal_line 在每次 read 前临时 stty intr undef(驱动层
# 解除 Ctrl+C→SIGINT 映射),0x03 变成普通按键交给 readline 分派,这里绑定为
# unix-line-discard(等价 C-u,清到行首),行被即时清空、提示符保留,继续输入
# 不受影响;读完后 normal_line 立即恢复映射,其余阶段(AI 输出/工具窗口/历史
# 选择)的 Ctrl+C 仍走 SIGINT 打断流程。
bind '"\C-c": unix-line-discard' 2>/dev/null

trap handle_ctrl_c INT

# 任何退出路径都恢复光标显示 + 自动换行 + 回显 + 中断键映射(窗口期间两次 Ctrl+C 会直接退出)
trap 'printf "\033[?25h\033[?7h"; stty echo isig 2>/dev/null; stty intr "^C" 2>/dev/null' EXIT


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

    # 清洗标题为合法文件名。注意两点历史 bug:
    # 1. 用 printf '%s' 而非 echo——echo 会自动追加换行,该换行又被 tr 转成
    #    '_',导致每个文件名末尾都多一个下划线;
    # 2. 先 trim 首尾空白(模型生成的标题末尾常带换行),避免末尾残留 '_'。
    name=$(printf '%s' "$chat_title" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
    name=$(printf '%s' "$name" | tr '[:space:]' '_')
    name=$(printf '%s' "$name" | tr -d '\\/:*?"<>|')
    name="${name%.json}"
    # 清洗后无有效字符(如全空白标题)时给兜底名,避免保存成 ".json"
    [[ -z "$name" ]] && name="chat"

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
    # 防御性清理:旧版本压缩可能把截断点切在工具链中间,产生"孤儿" tool
    # 消息(前一条不是含其 tool_call_id 的 assistant)。这种消息会让 API 400
    # 且无任何输出。加载时自动剔除,保证历史文件永远可继续对话。
    # 注意:reduce 本身产出数组,外面不能再包一层 []。
    local orphan
    orphan=$(echo "$messages" | jq '
        reduce .[] as $m ([];
            if $m.role == "tool" then
                if (.[-1].role == "assistant" and (.[-1].tool_calls // [] | any(.id == $m.tool_call_id)))
                then . + [$m]
                else .
                end
            else . + [$m]
            end)')
    messages=$orphan
    # thinking 模式下,DeepSeek 要求回传上一轮 assistant 的 reasoning_content。
    # 旧历史(无 reasoning_content 字段)直接续聊会 400,加载时自动降级为不思考
    if [[ "$(jq -r '.thinking.type // empty' <<< "$(<"$1")")" == "enabled" ]]; then
        has_reasoning=$(echo "$messages" | jq '[.[] | select(.role == "assistant")] | last | has("reasoning_content")')
        if [[ "$has_reasoning" != "true" ]]; then
            thinking="disabled"
            echo "⚠️ 该历史缺少思考内容,已自动关闭思考模式(thinking 要求回传 reasoning_content)"
        fi
    fi
    # 加载的是新对话:旧的 token 计数作废,等下一次请求由 usage 刷新精确值
    last_prompt_tokens=0
    thinking=$(jq -r '.thinking.type' <<< "$(<"$1")")
    model=$(jq -r '.model' <<< "$(<"$1")")
    jq -c '. [1:][]' <<< "$messages" | while IFS= read -r line; do
        role=$(jq -r '.role' <<< "$line")
        content=$(jq -r '.content // empty' <<< "$line")
        reasoning=$(jq -r '.reasoning_content // empty' <<< "$line")
        # 思考内容单独渲染:thinking 模式下 assistant 消息的 reasoning_content
        # 是历史正文的一部分,旧版只渲染 content,思考内容完全丢失
        if [[ -n "$reasoning" ]]; then
            render_markdown "### 💭 思考"
            echo "----------------------------------------------"
            render_markdown "$reasoning"
        fi
        render_markdown "## $role"
        echo "----------------------------------------------"
        if [[ "$role" == "tool" ]]; then
            # 工具输出渲染上限:超过 5 行只显示前 5 行并提示省略,避免
            # 长输出干扰主要信息阅读(仅影响显示,messages 保持完整)。
            # 历史数据有三个坑,朴素按真实换行统计会失灵:
            # 1. 工具函数用双引号拼接 \n,bash 不解析,历史里存的是字面
            #    "反斜杠+n"——先还原成真实换行,否则逻辑上多行被当成一行,
            #    明明超过 5 行却不省略;
            # 2. 内容可能以 BOM/空行开头——前导空白行会吃掉 5 行的预算,
            #    出现"什么都没看到就被省略"。先剔除 BOM 和空行/纯空白行;
            # 3. 每行限宽 200 字符,防 minified 长行刷屏(仅显示层截断)。
            disp=${content//"\\n"/$'\n'}
            disp=${disp/#$'\xef\xbb\xbf'/}
            disp=$(printf '%s' "$disp" | grep -vE '^[[:space:]]*$')
            nlines=$(printf '%s' "$disp" | awk 'END{print NR}')
            shown=$(printf '%s' "$disp" | awk -v w=200 'NR<=5 { if (length($0)>w) print substr($0,1,w) "…(该行过长已截断)"; else print }')
            if (( nlines > 5 )); then
                render_markdown "$shown"$'\n'"…(其余 $((nlines - 5)) 行已省略)"
            else
                render_markdown "$shown"
            fi
        else
            # assistant 的 tool_calls 消息 content 为 null,不再渲染出字面 "null"
            [[ -n "$content" ]] && render_markdown "$content"
        fi
    done
}

# Ctrl+C 不再退出程序:
# - AI 输出阶段(ask_ai 流式期间,副屏):置 ai_abort,由 ask_ai 打断流、
#   丢弃本轮输出、返回主屏幕继续对话;
# - 主屏幕:输入行(read -e)的 Ctrl+C 已在 readline 层直接清行(见 bind 配置),
#   不会走到本函数;历史选择/命令确认等普通 read 被打断时置 input_aborted,
#   由调用处清空输入后重新提示。
# 唯一退出方式是明确输入 -q(命令行参数或交互输入)。
handle_ctrl_c() {
    if (( ai_active )); then
        ai_abort=1
    else
        input_aborted=1
    fi
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
    local filePath="" rc
    local isSelect=0
    # 普通 read 在 trap 存在时不会被 SIGINT 打断(实测:trap 执行后 read 继续
    # 等输入,只是驱动缓冲区被 VINTR 清空,屏幕残留已输入字符)。改成短超时
    # 轮询:检测到 input_aborted 立即退出选择器。读取期间关 ECHOCTL 避免 ^C
    # 回显;数字输入的正常回显不受影响。
    stty -echoctl 2>/dev/null
    printf '_>'
    num=""
    while :; do
        IFS= read -r -t 0.2 num
        rc=$?
        (( input_aborted )) && break
        (( rc <= 128 )) && break
    done
    stty echoctl 2>/dev/null
    # Ctrl+C 打断编号输入:擦掉屏幕残留行,不把空编号误判成"默认加载最新历史"
    if (( input_aborted )); then
        input_aborted=0
        printf '\r\033[2K\n'
        return 0
    fi
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
    echo "  -q    退出程序(Ctrl+C 不会退出,这是唯一退出方式)"
    echo "  -h    显示帮助信息"
    echo
    echo "内容交互时，也可使用以下选项:"
    echo "  -msgs 显示当前消息记录"
    echo "  -l    查看历史记录并选择加载"
    echo "  -c    手动压缩对话历史"
    echo "  -u    查看当前上下文 token 用量"
    echo "  -q    退出程序"
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
            model="$pro_model"
            continue
        fi

        # 深入思考
        if [[ "${!i}" == "-dp" ]]; then
            thinking="enabled"
            model="$pro_model"
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

        if [[ "${!i}" == "-q" ]]; then
            # 唯一退出方式:退出前保存历史(沿用原 Ctrl+C 退出的保存逻辑)
            save_history
            exit 0
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
    local cmd_stat="" last_data=0 bg_leak=0 forced=0 win_pgid=""
    local in_fd="" key="" tty_save="" tty_cols="" tty_rows="" wrapped_cmd=""
    # 全量输出通过全局变量回传给调用方（窗口只显示末尾 WIN 行）
    window_output=""

    # 开启 job control:让 & 启动的子命令(尤其管道)进入独立进程组。
    # bash 5 对异步管道的实现中,管道成员不再是 $! 的后代(pgrep -P 父链断裂,
    # kill_tree 找不到孩子,只剩孤儿攥着 FIFO 写端,EOF 永远不来,Ctrl+C 杀不掉)。
    # 按进程组杀是业界标准做法(Claude Code 同款)。
    set -m

    shopt -s extglob
    # 藏光标防闪烁；关自动换行 → 超宽行（含中文宽字符）在右边缘直接裁剪，
    # 光标不会掉到下一行，"上移 drawn 行"的算术就永远是对的
    printf '\033[?25l\033[?7l'

    # 剥掉 \r 和 CSI 转义序列（子命令的进度条/颜色码会画烂窗口）
    clean_line() {
        line=${line//$'\r'/}
        line=${line//$'\e'\[*([0-9;?])[[:alpha:]]/}
        # BSD script 在 stdin EOF 时向 pty 回显的 "^D" + 退格残留;
        # 先剥定向残留再剥散落的退格(窗口无法正确渲染 \b)
        line=${line//'^D'$'\b\b'/}
        line=${line//$'\b'/}
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
    # 输入 FIFO:子命令的 stdin 从这里读,由主 shell 代读 tty 后转发进来。
    # 后台进程组自己读 tty 会被 SIGTTIN 整树停住(npm create vite 实测
    # bash/npm/node 全部 T 状态挂死);主 shell 是前台进程组,读 tty 合法。
    # <> 打开:读写两端同时就位永不阻塞,且窗口存活期间写端常开,
    # 子命令的交互式提示会等待输入而非读到 EOF 直接返回。
    mkfifo "$win_tmpdir/in" || return 1
    exec {in_fd}<>"$win_tmpdir/in"
    # pty 的窗口尺寸默认 0×0(stdin 非 tty 时 script 无从继承):clack 等 UI 库
    # 会按宽度 0 折行,每字符一行。命令前注入 stty 把真实终端尺寸写进 pty slave
    tty_cols=$(tput cols 2>/dev/null); tty_rows=$(tput lines 2>/dev/null)
    wrapped_cmd="stty cols ${tty_cols:-120} rows ${tty_rows:-40} 2>/dev/null; $cmd"
    # 套 pty 启动(按启动时探测的语法家族分发):子命令把 stdout 当终端 →
    # 行缓冲,输出实时进 FIFO(实测:裸 FIFO 下 python 等程序憋到退出才吐)。
    case "$PTY_STYLE" in
        bsd)
            # stdin 经 cat 中转成匿名管道:macOS 的 FIFO 基于 socket 实现,
            # BSD script 对其 tcgetattr 会致命报错(管道只报 ENOTTY,被容忍)
            cat "$win_tmpdir/in" | script -q /dev/null "$BASH" -c "$wrapped_cmd" > "$win_tmpdir/fifo" 2>&1 & ;;
        util-linux)
            # util-linux script 用 $SHELL 跑命令,强制指到当前 bash;-e 透传退出码
            SHELL="$BASH" script -qec "$wrapped_cmd" /dev/null <"$win_tmpdir/in" > "$win_tmpdir/fifo" 2>&1 & ;;
        python)
            # 不用 pty.spawn:macOS 上子进程退出后它会挂在 master 读取,EOF 不来。
            # pty.fork + select 双向转发:master→stdout 及 stdin→master,
            # 读到 EOF/EIO(Linux 子进程退出报 EIO)都干净退出
            python3 -c '
import os, pty, select, sys
pid, fd = pty.fork()
if pid == 0:
    os.execvp(sys.argv[1], [sys.argv[1], "-c", sys.argv[2]])
stdin_open = True
while True:
    rfds = [fd] + ([0] if stdin_open else [])
    try:
        r, _, _ = select.select(rfds, [], [])
    except OSError:
        break
    if fd in r:
        try:
            data = os.read(fd, 65536)
        except OSError:
            break
        if not data:
            break
        os.write(1, data)
    if 0 in r:
        d = os.read(0, 65536)
        if d:
            os.write(fd, d)
        else:
            stdin_open = False
os.waitpid(pid, 0)' "$BASH" "$wrapped_cmd" <"$win_tmpdir/in" > "$win_tmpdir/fifo" 2>&1 & ;;
        *)
            # 无 pty 可用的兜底:丢实时性,但 stdin 同样接 FIFO,交互转发仍可用
            eval "$cmd" <"$win_tmpdir/in" > "$win_tmpdir/fifo" 2>&1 & ;;
    esac
    cmd_pid=$!
    exec {win_fd}<"$win_tmpdir/fifo"   # 读端与子命令的写端 rendezvous，不会死锁

    # 备份原 INT trap（通常是 handle_ctrl_c），窗口期间换成"只杀子命令"：
    # - 第一次 Ctrl+C：先按进程组整组 TERM(job control 下子命令在独立进程组),
    #   再用 kill_tree 递归兜底(杀不在组内的散兵),写端全部关闭 → EOF → 窗口正常收尾;
    # - 第二次 Ctrl+C：兜底恢复原 trap 并重新触发，走 handle_ctrl_c 整体退出。
    # trap 能在 0.1s 内执行的前提是下面 read 的 -t 轮询超时；无限阻塞的 read 会
    # 把 trap 推迟到 read 完成才执行（死锁），这里不存在。
    old_trap=$(trap -p INT)
    trap 'if (( interrupted )); then eval "$old_trap"; kill -INT $$; else interrupted=1; win_pgid=$(ps -o pgid= -p "$cmd_pid" 2>/dev/null | tr -d " "); [[ -n "$win_pgid" ]] && kill -TERM -- "-$win_pgid" 2>/dev/null; kill_tree "$cmd_pid"; fi' INT

    # 关本地回显:用户按键由子命令的 pty 回显(行提示原样回、菜单 UI 重画)
    # 后经输出 FIFO 画到窗口,避免 tty 与 pty 双重回显;ISIG 仍开,Ctrl+C 不受影响
    if [[ -t 0 ]]; then tty_save=$(stty -g); stty -echo; fi

    last_data=${EPOCHREALTIME/./}
    while :; do
        chunk=""
        # 按块读而非按行读：交互提示通常不带换行，按行 read 永远等不到 \n，
        # 用户只能面对空窗口；-N+超时能把无换行的残段也取出来实时展示
        IFS= read -r -t 0.1 -N 65536 -u "$win_fd" chunk
        rc=$?
        # 代读 tty:把用户按键(含方向键等转义序列)转发进子命令 stdin。
        # read -N 临时切非规范模式,无换行的字节也能立即取到
        if [[ -n "$tty_save" ]]; then
            key=""
            IFS= read -r -t 0.01 -N 4096 key
            if [[ -n "$key" ]]; then
                # 真实终端的 Enter 发 \r,本 tty 的 ICRNL 已转成 \n;raw 模式 UI
                # (vite/clack 等)只认 \r,还原后发送(规范模式侧 pty 会再转回 \n)
                key=${key//$'\n'/$'\r'}
                printf '%s' "$key" >&"$in_fd"
            fi
        fi
        if [[ -n "$chunk" ]]; then
            pending+="$chunk"
            while [[ "$pending" == *$'\n'* ]]; do
                line="${pending%%$'\n'*}"
                pending="${pending#*$'\n'}"
                clean_line
                window_output+="$line"$'\n'
                # 显示缓冲只存可视宽度:超长行若全量进 buf,paint 每帧向终端喷数 MB
                buf+=("${line:0:$DISP_COL}")
                (( ${#buf[@]} > WIN )) && buf=("${buf[@]:1}")
                (( total++ ))
            done
            # 底行 partial 同样只清洗/显示可视宽度,保证每轮循环 O(1),
            # 信号能在命令边界及时处理
            line="${pending:0:$DISP_COL}"; clean_line; partial="$line"
            dirty=1
            last_data=${EPOCHREALTIME/./}
        fi
        # 超时退出码 >128（继续等）；1~128 是 EOF（命令结束）
        (( rc > 0 && rc <= 128 )) && break
        now=${EPOCHREALTIME/./}
        # 硬承诺:用户已按 Ctrl+C(杀组已执行)后,若 EOF 仍未到来,静默超时强制
        # 收尾。无论杀组是否成功、写端是否被孤儿攥着,最多再卡 INT_FORCE_TIMEOUT。
        if (( interrupted )); then
            if (( now - last_data > INT_FORCE_TIMEOUT )); then
                forced=1
                break
            fi
        fi
        # 兜底:命令主体已退出(变僵尸/被回收)但 FIFO 写端仍被其后台子进程
        # 握着(AI 在命令里用 & 启动服务),EOF 永远不会来。静默超时提前收尾,
        # 防止窗口永久挂死。正常慢命令的进程还活着,不会触发。
        if [[ -n "$cmd_pid" ]]; then
            cmd_stat=$(ps -o stat= -p "$cmd_pid" 2>/dev/null | tr -d ' ')
            if [[ -z "$cmd_stat" || "$cmd_stat" == Z* ]]; then
                if (( now - last_data > BG_SILENT_TIMEOUT )); then
                    bg_leak=1
                    break
                fi
            fi
        fi
        if (( dirty && now - last_paint > THROTTLE )); then
            paint
            last_paint=$now
            dirty=0
        fi
    done

    # 恢复回显与原 INT trap，关闭两个 FIFO 端并清理临时目录
    [[ -n "$tty_save" ]] && stty "$tty_save"
    [[ -n "$old_trap" ]] && eval "$old_trap" || trap - INT
    eval "exec $win_fd<&-"
    eval "exec $in_fd<&-"
    rm -rf "$win_tmpdir"

    # 收尾：无换行的残段按一行落盘（清洗后为空的不算，如子进程退出时打的转义序列）
    if [[ -n "$pending" ]]; then
        line="$pending"; clean_line
        if [[ -n "$line" ]]; then
            window_output+="$line"$'\n'
            buf+=("${line:0:$DISP_COL}")
            (( ${#buf[@]} > WIN )) && buf=("${buf[@]:1}")
            (( total++ ))
        fi
    fi
    partial=""
    paint    # 节流可能吞掉最后的内容，收尾必须补画一帧
    printf '\033[?25h\033[?7h'
    if (( forced )); then
        printf '\033[2m└─ 已强制停止(输出管道未关闭,停止等待) · 共 %d 行 ─┘\033[0m\n' "$total"
        # 告诉模型:命令被用户中断,且部分后台进程可能仍在运行
        window_output+=$'\n(命令已被用户中断。若后台仍有残留进程,可用 ps 查询并清理。)'
    elif (( bg_leak )); then
        printf '\033[2m└─ 命令主体已结束,但输出管道仍被后台进程占用,已停止等待 ─┘\033[0m\n'
        # 告诉模型:疑似用 & 启动了长驻服务,应改用 run_in_background=true
        window_output+=$'\n(检测到命令使用了 & 后台符号,输出管道被后台进程占用。若为长驻服务,请改用 run_in_background=true 参数重新启动。)'
    elif (( interrupted )); then
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
    # 所以绝不能 echo 调试信息，只能输出纯净的 JSON。
    # 工具分工(业界标准,同 Claude Code):
    #   读代码/写代码/改代码 → read_file / write_file / edit_file(不要用 shell 技巧)
    #   找文件/找符号 → list_dir / grep
    #   编译/测试/安装/git → run_shell_command
    # 所有文件类工具的路径都强制限制在启动目录(WORK_ROOT)内,越界由代码层拒绝。
    printf '%s' '[
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "读取文件内容,返回带行号的文本。读取任何代码或文本文件时,优先使用本工具,不要用 cat 等 shell 命令。文件过大时自动截断并提示,此时应使用 offset/limit 参数分段读取。offset 为起始行号(从 1 开始,默认 1),limit 为读取行数(省略则读全文)。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件路径,相对路径基于当前工作目录"},
                        "offset": {"type": "integer", "description": "起始行号,从 1 开始,默认 1"},
                        "limit": {"type": "integer", "description": "读取行数,省略则读到文件末尾"}
                    },
                    "required": ["path"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "创建或整文件覆盖写入。编写代码或生成文件时,优先使用本工具,不要用 heredoc/echo 重定向等 shell 写法(引号转义极易出错)。父目录不存在会自动创建。路径只能在工作目录内。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件路径,相对路径基于当前工作目录"},
                        "content": {"type": "string", "description": "要写入的完整文件内容"}
                    },
                    "required": ["path", "content"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": "对现有文件做精确字符串替换,适合小范围修改(改几行、改一个函数),比重写整个文件更省 token、更安全。old_string 必须在文件中恰好出现一次,否则报错且不做任何修改;匹配到 0 次或多于 1 次时,请重新 read_file 确认原文或提供更长的唯一上下文。new_string 可为空字符串(表示删除)。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件路径,相对路径基于当前工作目录"},
                        "old_string": {"type": "string", "description": "文件中要替换的原文,必须唯一出现"},
                        "new_string": {"type": "string", "description": "替换后的新文本,可为空"}
                    },
                    "required": ["path", "old_string", "new_string"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "list_dir",
                "description": "列出目录下的文件和子目录名。了解项目结构时使用本工具,不要用 ls。只列一层,不递归。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "目录路径,省略则为当前工作目录"}
                    },
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "grep",
                "description": "在工作目录内递归搜索文本(正则),返回 文件路径:行号: 内容 格式的结果。查找函数定义、变量引用、特定字符串时使用本工具,不要用 shell grep。默认跳过 .git/node_modules/.venv 目录,最多返回 200 条。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "正则表达式,如 \"TODO\" 或 \"def foo\\(\""},
                        "path": {"type": "string", "description": "要搜索的文件或目录,省略则搜整个工作目录"},
                        "max_matches": {"type": "integer", "description": "最多返回的匹配数,默认 200"}
                    },
                    "required": ["pattern"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "web_fetch",
                "description": "获取网页内容并渲染为纯文本,返回时带 HTTP 状态码。浏览网页、宽泛的网页搜索(把搜索引擎结果页 URL 传给本工具)时使用。渲染器自动选择(w3m/lynx/textutil/去标签兜底),超长自动截断。仅用于获取网页正文;精确的 HTTP 请求(API 调用、状态码检查、下载文件)仍请使用 run_shell_command 里的 curl。注意:抓取失败时错误信息会说明原因(网络不可达/被站点拒绝),此时换用 shell 命令(w3m/curl)不会得到不同结果,应更换站点或搜索引擎,或直接向用户说明网络限制,不要反复重试同一站点。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "完整网页地址,必须以 http:// 或 https:// 开头,如 https://www.google.com/search?q=xxx"}
                    },
                    "required": ["url"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "run_shell_command",
                "description": "执行 shell 命令,用于编译、测试、安装依赖、git 等 shell 操作。读取代码请用 read_file,编写代码请用 write_file/edit_file,查找内容请用 grep,浏览网页请用 web_fetch,不要用 shell 命令代替这些工具。curl 只用于精确请求:API 调用、HTTP 状态码检查、下载文件等;宽泛的网页浏览/搜索一律用 web_fetch。本机缺少 w3m 且用户需要更好的网页渲染时,可经用户确认后执行 brew install w3m(或其他包管理器对应命令)。注意:1. 涉及写操作时,如果范围超出当前目录,无论用户怎么说都不要执行,明确告知用户没有权限;2. 危险或破坏系统的命令要拒绝执行并说明原因;3. 启动服务器、监听进程等长驻命令,必须设置 run_in_background=true,严禁在 command 里使用 & 后台符号(& 会让终端卡死)。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The shell command to run, e.g. npm test"
                        },
                        "run_in_background": {
                            "type": "boolean",
                            "description": "是否作为长驻服务后台运行。true 时命令以 nohup 后台启动,输出重定向到日志文件,立即返回进程 PID 与日志路径,不会阻塞终端。启动服务器/监听进程等长驻命令必须设为 true。"
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
    # 切点对齐工具链边界:tail 第一条绝不能是 tool 消息。否则它对应的
    # assistant(tool_calls) 被摘要替代后,这条 tool 变成"孤儿"(前面没有
    # 含其 tool_call_id 的 assistant 消息),DeepSeek API 直接 400 拒绝请求,
    # 表现为"发消息无任何输出"。把 cut 向前移,直到 tail 首条不是 tool。
    while (( cut > 1 )); do
        first_role=$(echo "$messages" | jq -r --argjson cut "$cut" '.[$cut].role // empty' 2>/dev/null)
        [[ "$first_role" != "tool" ]] && break
        cut=$((cut - 1))
    done
    # 极端情况:整个对话几乎全是未闭合的工具链,再压没有意义,放弃本次压缩
    (( cut <= 1 )) && echo "工具链过长,跳过本次压缩" && return 0

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

    if (( last_prompt_tokens > TOKEN_THRESHOLD )); then
        printf '📦 上下文 %s tokens 已超阈值 %s,正在自动压缩历史…\n' "$last_prompt_tokens" "$TOKEN_THRESHOLD"
    else
        printf '📦 正在压缩历史…(当前 %s tokens,阈值 %s)\n' "${last_prompt_tokens:-0}" "$TOKEN_THRESHOLD"
    fi

    request_body=$(jq -n --arg compact_text "$to_compact" --arg model "$flash_model" '{
        model: $model,
        messages: [
            {role: "system", content: "你是对话历史压缩助手。任务:把用户提供的旧对话记录压缩成一段精炼的摘要,替代原始记录用于后续继续对话。要求:1. 保留用户的整体目标与意图、关键决策与结论、已执行的命令及其重要结果、出现的错误与最终修复方式、涉及的重要文件路径、用户的个人偏好与约束;2. 丢弃客套话、重复内容与冗长的命令输出;3. 用中文输出,不超过 500 字,只输出摘要正文,不要任何前缀或解释。"},
            {role: "user", content: $compact_text}
        ],
        thinking: {type: "disabled"},
        reasoning_effort: "high",
        stream: false
    }')

    # 同步一次拿完。--max-time 兜底防卡死;失败时 summary 为空,走硬截断分支
    summary=$(curl -s --max-time 120 "$BASE_URL" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $DEEPSEEK_API_KEY" \
        -H "api-key: $DEEPSEEK_API_KEY" \
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

# 失败自愈:请求失败(无响应/传输层断连/上下文越界)时,若本地字节粗算已超阈值,
# 说明是上下文越界而非纯网络抖动——主动压缩历史后重发一次,打破
# "越界→请求失败→拿不到 usage→last_prompt_tokens 不更新→压缩永不触发"的死循环。
# 参数:$1 是否锁屏  $2 是否记录 usage(原样透传给重发的 ask_ai)。
# 返回 0 = 已压缩并重发(调用方应直接 return);返回 1 = 未处理(交给原兜底逻辑)。
retry_after_compact() {
    local is_lock=$1 is_record=$2
    local est_bytes est_tokens before_count after_count tools_json request_body
    # auth 旁路请求(is_record=0)用的是 auth_message,不是主对话 messages,
    # 不参与上下文自愈(否则会误用主对话 messages 重建请求体)
    (( is_record == 0 )) && return 1
    est_bytes=$(printf '%s' "$messages" | wc -c | tr -d ' ')
    est_tokens=$((est_bytes * 2 / 5))
    # 本地估算低于阈值 → 不是上下文问题(纯网络抖动),交给原重试/兜底逻辑
    (( est_tokens <= TOKEN_THRESHOLD )) && return 1

    printf '⚠️ 请求失败且本地估算 %s tokens 已超阈值 %s,自动压缩历史后重发…\n' "$est_tokens" "$TOKEN_THRESHOLD"
    before_count=$(echo "$messages" | jq 'length')
    compact_messages
    after_count=$(echo "$messages" | jq 'length')
    # 压缩未生效(消息条数没减,如单条超大消息/工具链过长):重发必然再失败,不再递归
    if (( after_count >= before_count )); then
        printf '⚠️ 自动压缩未减少消息量,请手动 -c 压缩或重开对话\n'
        return 1
    fi
    tools_json=$(tools)
    request_body=$(jq -n \
        --arg model "$model" \
        --argjson msgs "$messages" \
        --arg thinking "$thinking" \
        --argjson tool "$tools_json" \
        '{
        model: $model,
        messages: $msgs,
        thinking: {type: $thinking},
        reasoning_effort: "high",
        stream: true,
        tools: $tool,
        stream_options: {include_usage: true}
    }')
    # 透传 ask_ai 的返回码:130(Ctrl+C 打断)需要继续向上传播
    ask_ai "$request_body" "$is_lock" "$is_record"
    return $?
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
# 第 6 个参数 reasoning:thinking 模式下 assistant 消息必须回传 reasoning_content
add_message() {
    local addctx="$1"
    local role="$2"
    local tool_call_id="$3"
    local tool_call_name="$4"
    local tool_call_args="$5"
    local reasoning="$6"
    if [[ -z "$role" ]]; then
        # 默认 user 消息
        messages=$(echo "$messages" | jq --arg content "$addctx" '. + [{role: "user", content: $content}]')
    elif [[ "$role" == "assistant" && -n "$tool_call_name" ]]; then
        # assistant 发起工具调用：必须记录 tool_calls。若本轮已有正文输出，
        # 也要把正文写入 content，否则工具调用前的正文会丢失，messages 不完整。
        # thinking 模式下必须回传 reasoning_content(API 硬性要求),哪怕本轮
        # 模型没有输出思考(flash 模型 -t 下偶发只返回工具调用)也要带空串
        # 字段:省略字段会让下一轮请求 400(真实 API 实测复现)。
        if [[ -z "$addctx" ]]; then
            messages=$(echo "$messages" | jq --arg id "$tool_call_id" --arg name "$tool_call_name" --arg args "$tool_call_args" --arg reasoning "$reasoning" --arg thinking "$thinking" \
                '. + [{role: "assistant", content: null, tool_calls: [{id: $id, type: "function", function: {name: $name, arguments: $args}}]} + (if $reasoning != "" or $thinking == "enabled" then {reasoning_content: $reasoning} else {} end)]')
        else
            messages=$(echo "$messages" | jq --arg content "$addctx" --arg id "$tool_call_id" --arg name "$tool_call_name" --arg args "$tool_call_args" --arg reasoning "$reasoning" --arg thinking "$thinking" \
                '. + [{role: "assistant", content: $content, tool_calls: [{id: $id, type: "function", function: {name: $name, arguments: $args}}]} + (if $reasoning != "" or $thinking == "enabled" then {reasoning_content: $reasoning} else {} end)]')
        fi
    elif [[ -n "$tool_call_id" ]]; then
        # tool 角色：工具执行结果，带 tool_call_id
        messages=$(echo "$messages" | jq --arg content "$addctx" --arg role "$role" --arg tool_call_id "$tool_call_id" '. + [{role: $role, content: $content, tool_call_id: $tool_call_id}]')
    else
        # 普通角色（assistant 文本回复等）;thinking 模式下回传 reasoning_content,
        # 无思考时同样带空串字段(原因同上:省略字段会让下一轮请求 400)
        messages=$(echo "$messages" | jq --arg content "$addctx" --arg role "$role" --arg reasoning "$reasoning" --arg thinking "$thinking" '. + [{role: $role, content: $content} + (if $reasoning != "" or $thinking == "enabled" then {reasoning_content: $reasoning} else {} end)]')
    fi
}
auth_add_message() {
    local addctx="$1"
    local role="${2:-user}"
    # 基于 auth_message 自身拼接（原实现误用了 $messages，会丢掉对话历史）
    auth_message=$(echo "$auth_message" | jq --arg content "$addctx" --arg role "$role" '. + [{role: $role, content: $content}]')
}

# 工具调用处理器已删除:并行调用按 index 解析已内联进 ask_ai 的流式循环,
# 这里不再需要单工具解析(旧实现只取 .[0],会把并行调用的参数粘合导致 null 错误)。

auth_think() {
    local cmd=$1
    local request_body rc
    auth_add_message "$cmd"
    request_body=$(jq -n \
            --argjson msgs "$auth_message" \
            --arg model "$flash_model" \
            '{
            model: $model,
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
    rc=$?
    # Ctrl+C 打断 auth 判断流:直接向上传播 130,不写空 assistant 消息
    (( rc == 130 )) && return 130

    sleep 0.5

    auth_add_message "$full_response" "assistant"
}
# 路径沙箱:解析工具传入的路径为物理绝对路径,并强制落在 WORK_ROOT 内。
# 相对路径基于工作目录;.. 逃逸、绝对路径指向外部、symlink 指向外部都会被拒绝。
# 写文件时父目录可能尚未存在:向上找最近的已存在祖先做物理规范化,
# 缺失的段原样拼回(必须都是新目录名,任何一段为 .. 直接拒绝)。
# 成功输出解析后的绝对路径,失败返回非零。
resolve_path() {
    local p="$1" dir base canon missing seg
    [[ -z "$p" ]] && return 1
    [[ "$p" != /* ]] && p="$WORK_ROOT/$p"
    dir=$(dirname "$p")
    base=$(basename "$p")

    missing=""
    while [[ ! -d "$dir" ]]; do
        [[ "$dir" == "/" ]] && return 1
        missing="${dir##*/}/$missing"
        dir=$(dirname "$dir")
    done
    # 缺失段必须逐个是普通目录名,含 .. 的拒绝(此时无法安全规范化)
    for seg in ${missing//\// }; do
        [[ "$seg" == ".." ]] && return 1
    done

    canon=$(cd "$dir" 2>/dev/null && pwd -P) || return 1
    # 必须等于工作根目录,或以 工作根目录/ 开头
    [[ "$canon" == "$WORK_ROOT" || "$canon" == "$WORK_ROOT"/* ]] || return 1
    printf '%s/%s%s' "$canon" "$missing" "$base"
}

# 文件类工具的实现约定:结果写入全局变量 content(与 shell 工具一致),
# 作为 tool 角色消息回传给模型;屏幕只打印一行摘要,全量内容悄悄进上下文。

tool_read_file() {
    local path offset limit resolved out total_lines total_chars
    path=$(jq -r '.path // empty' <<< "$fn_args")
    offset=$(jq -r '.offset // empty' <<< "$fn_args")
    limit=$(jq -r '.limit // empty' <<< "$fn_args")
    [[ -z "$offset" || "$offset" == "null" ]] && offset=1
    [[ -z "$limit" || "$limit" == "null" ]] && limit=0

    resolved=$(resolve_path "$path") || { content="错误:路径不允许访问(必须位于工作目录内): $path"; printf ' ⛔ 路径越界:%s\n' "$path"; return 0; }
    [[ -f "$resolved" ]] || { content="错误:文件不存在: $path"; printf ' ⛔ 文件不存在:%s\n' "$path"; return 0; }

    total_lines=$(wc -l < "$resolved" | tr -d ' ')
    total_chars=$(wc -m < "$resolved" | tr -d ' ')

    # 带行号输出:行号供 edit_file 的 old_string 定位和后续沟通引用
    if (( limit > 0 )); then
        out=$(sed -n "${offset},$((offset + limit - 1))p" "$resolved" | \
            awk -v s="$offset" '{printf "%6d│ %s\n", s + NR - 1, $0}')
        content="📄 $path · 第 ${offset}-$((offset + limit - 1)) 行(共 ${total_lines} 行):\n\n$out"
    elif (( total_chars > READ_MAX_CHARS )); then
        # 按行粒度截断:awk 逐行累计字符数,超阈值即停,不会切断多字节字符
        out=$(awk -v max="$READ_MAX_CHARS" '{acc += length($0) + 1; if (acc > max) exit; printf "%6d│ %s\n", NR, $0}' "$resolved")
        content="📄 $path · 共 ${total_lines} 行 / ${total_chars} 字符,文件过大仅返回前半部分。请用 offset/limit 参数继续分段读取:\n\n$out"
    else
        out=$(awk '{printf "%6d│ %s\n", NR, $0}' "$resolved")
        content="📄 $path · 共 ${total_lines} 行:\n\n$out"
    fi
    printf ' 已读取 %s(%s 行)\n' "$path" "$total_lines"
}

tool_write_file() {
    local path resolved
    path=$(jq -r '.path // empty' <<< "$fn_args")
    resolved=$(resolve_path "$path") || { content="错误:路径不允许访问(必须位于工作目录内): $path"; printf ' ⛔ 路径越界:%s\n' "$path"; return 0; }

    mkdir -p "$(dirname "$resolved")" 2>/dev/null || { content="错误:无法创建目录: $path"; return 0; }
    # 内容直接由 jq 流式写入文件,不经过命令替换(命令替换会剥掉末尾换行)
    jq -r '.content' <<< "$fn_args" > "$resolved"
    content="✅ 已写入 $path($(wc -l < "$resolved" | tr -d ' ') 行)"
    printf ' 已写入 %s\n' "$path"
}

# 纯 bash 精确替换引擎:
#   出现次数 = (原文长度 - 删掉 old 后长度) / old 长度,多行字符串同样成立
tool_edit_file() {
    local path old_str new_str resolved data tmp count
    path=$(jq -r '.path // empty' <<< "$fn_args")
    old_str=$(jq -r '.old_string // empty' <<< "$fn_args")
    new_str=$(jq -r '.new_string // empty' <<< "$fn_args")

    resolved=$(resolve_path "$path") || { content="错误:路径不允许访问(必须位于工作目录内): $path"; printf ' ⛔ 路径越界:%s\n' "$path"; return 0; }
    [[ -f "$resolved" ]] || { content="错误:文件不存在: $path"; printf ' ⛔ 文件不存在:%s\n' "$path"; return 0; }
    [[ -z "$old_str" ]] && { content="错误:old_string 不能为空"; return 0; }

    data=$(<"$resolved")
    tmp=${data//"$old_str"/}
    count=$(( (${#data} - ${#tmp}) / ${#old_str} ))

    if (( count == 0 )); then
        content="错误:在 $path 中未找到指定内容,未做任何修改。请用 read_file 重新确认原文。"
        printf ' ⚠️  未找到待替换内容,文件未修改\n'
    elif (( count > 1 )); then
        content="错误:指定内容在 $path 中出现 ${count} 次,不唯一,未做任何修改。请提供更长的上下文使其唯一。"
        printf ' ⚠️  待替换内容出现 %s 次,不唯一,文件未修改\n' "$count"
    else
        data=${data//"$old_str"/"$new_str"}
        printf '%s' "$data" > "$resolved"
        content="✅ 已修改 $path(替换 1 处)"
        printf ' 已修改 %s\n' "$path"
    fi
}

tool_list_dir() {
    local path resolved files dirs
    path=$(jq -r '.path // empty' <<< "$fn_args")
    [[ -z "$path" || "$path" == "null" ]] && path="."
    resolved=$(resolve_path "$path") || { content="错误:路径不允许访问(必须位于工作目录内): $path"; printf ' ⛔ 路径越界:%s\n' "$path"; return 0; }
    [[ -d "$resolved" ]] || { content="错误:目录不存在: $path"; return 0; }

    files=$(find "$resolved" -maxdepth 1 -type f -exec basename {} \; | sort)
    dirs=$(find "$resolved" -maxdepth 1 -mindepth 1 -type d -exec basename {} \; | sort)
    content="目录 $path:\n[文件]\n${files:-"(空)"}\n\n[子目录]\n${dirs:-"(空)"}"
    printf ' 已列出 %s\n' "$path"
}

tool_grep() {
    local pattern path max_matches resolved out
    pattern=$(jq -r '.pattern // empty' <<< "$fn_args")
    path=$(jq -r '.path // empty' <<< "$fn_args")
    max_matches=$(jq -r '.max_matches // empty' <<< "$fn_args")
    [[ -z "$pattern" ]] && { content="错误:pattern 不能为空"; return 0; }
    [[ -z "$max_matches" || "$max_matches" == "null" ]] && max_matches=$GREP_MAX_MATCHES

    if [[ -n "$path" && "$path" != "null" ]]; then
        resolved=$(resolve_path "$path") || { content="错误:路径不允许访问(必须位于工作目录内): $path"; printf ' ⛔ 路径越界:%s\n' "$path"; return 0; }
    else
        resolved="$WORK_ROOT"
    fi
    [[ -e "$resolved" ]] || { content="错误:路径不存在: $path"; return 0; }

    # -E 正则 -r 递归 -n 行号 -I 跳过二进制;结果单行截宽,防 minified 长行污染上下文
    out=$(grep -rnIE --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=.venv \
        -e "$pattern" "$resolved" 2>/dev/null | head -n "$max_matches" | \
        awk -v max="$GREP_MAX_COL" 'length($0) > max { $0 = substr($0, 1, max) "…(过长已截断)" } { print }')

    if [[ -z "$out" ]]; then
        content="未找到匹配 \"$pattern\" 的内容。"
    else
        content="搜索 \"$pattern\" 的结果:\n$out"
    fi
    printf ' 已搜索:%s\n' "$pattern"
}

# 网页抓取:获取 URL 并渲染为纯文本。渲染器优先级 w3m > lynx > textutil(macOS)
# > sed 去标签兜底。仅抓 http(s) 地址;超长内容按行截断,与 read_file 同策略。
# 不在此处自动安装 w3m:安装是写操作,应走 shell 工具的用户确认流程。
tool_web_fetch() {
    local url html rendered renderer http_code resp
    url=$(jq -r '.url // empty' <<< "$fn_args")
    [[ "$url" == http://* || "$url" == https://* ]] || { content="错误:url 必须以 http:// 或 https:// 开头: $url"; printf ' ⛔ 非法 url\n'; return 0; }

    # -L 跟随重定向 -A 浏览器 UA(部分站点拒绝默认 UA) --max-time 兜底防卡死;
    # -w 追加 HTTP 状态码,让模型能区分"网络不可达"与"被站点拒绝"。
    resp=$(curl -sL -w $'\n%{http_code}' -A "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36" \
        --max-time 30 "$url" 2>/dev/null)
    http_code="${resp##*$'\n'}"
    html="${resp%$'\n'*}"

    if [[ -z "$html" ]]; then
        # 按状态码细分失败原因,让模型正确归因:这是网络/站点问题,
        # 换用 shell 命令(w3m/curl)不会得到不同结果,应换站点或告知用户
        case "$http_code" in
            000) content="错误:无法连接 $url(HTTP 000:网络不可达或超时。换用 shell 命令不会改善,请更换站点或搜索引擎,或向用户说明网络限制)" ;;
            403|429) content="错误:$url 返回 HTTP ${http_code}(被站点拒绝/限流,可能是反爬。换用 shell 命令不会改善,请更换站点)" ;;
            4*|5*) content="错误:$url 返回 HTTP ${http_code}(站点拒绝或服务器错误,请更换站点)" ;;
            *) content="错误:无法获取 $url(HTTP ${http_code},空响应)" ;;
        esac
        printf ' ⛔ 抓取失败:%s(HTTP %s)\n' "$url" "$http_code"
        return 0
    fi

    if command -v w3m >/dev/null 2>&1; then
        rendered=$(printf '%s' "$html" | w3m -dump -T text/html 2>/dev/null); renderer="w3m"
    elif command -v lynx >/dev/null 2>&1; then
        rendered=$(printf '%s' "$html" | lynx -dump -stdin -nolist 2>/dev/null); renderer="lynx"
    elif command -v textutil >/dev/null 2>&1; then
        # macOS 自带,HTML→纯文本质量不错,无需安装任何东西
        rendered=$(printf '%s' "$html" | textutil -convert txt -format html -stdin -stdout 2>/dev/null); renderer="textutil"
    else
        # 兜底:粗暴去标签(只保底可用,不追求完美;实体只解最常见的几个)
        rendered=$(printf '%s' "$html" | sed \
            -e 's/<script[^>]*>[^<]*<\/script>//g' \
            -e 's/<style[^>]*>[^<]*<\/style>//g' \
            -e 's/<[^>]*>//g' \
            -e 's/&nbsp;/ /g' -e 's/&amp;/\&/g' -e 's/&lt;/</g' -e 's/&gt;/>/g' -e 's/&quot;/"/g' 2>/dev/null)
        renderer="sed"
    fi
    [[ -z "$rendered" ]] && rendered="(渲染器 $renderer 未产出文本,原始 HTML 开头: ${html:0:500})"

    # 超长按行截断(awk 逐行累计,不会切断多字节字符)
    local total_chars out
    total_chars=$(printf '%s' "$rendered" | wc -m | tr -d ' ')
    if (( total_chars > WEB_MAX_CHARS )); then
        out=$(printf '%s' "$rendered" | awk -v max="$WEB_MAX_CHARS" '{acc += length($0) + 1; if (acc > max) exit; print}')
        content="🌐 $url · HTTP ${http_code:-?} · 渲染器 $renderer · 内容过长仅返回前 ${WEB_MAX_CHARS} 字符:\n\n$out"
    else
        content="🌐 $url · HTTP ${http_code:-?} · 渲染器 $renderer · ${total_chars} 字符:\n\n$rendered"
    fi
    printf ' 已抓取 %s(%s)\n' "$url" "$renderer"
}

# 工具调用执行器：串行执行收集到的所有并行调用(DeepSeek 一次可返回多个
# tool_calls)。每个调用独立产生一条 assistant(tool_calls) 消息和一条 tool
# 结果消息(API 要求每个 tool_call_id 都有响应,缺一报 400)。
tool_run_and_parse() {
    local tools request_body args_preview rc msgs_before_call
    local -a ids_arr names_arr args_arr
    local first_call=1 i

    # 快照收集区并按 index 升序,然后立即清空全局数组。
    # 立即清空是重入防护关键:shell 分支里 auth_think 会重入 ask_ai,
    # 若收集区仍残留,重入的 ask_ai 结束时又会误触发本函数(递归执行工具)。
    while IFS= read -r i; do
        [[ -z "$i" ]] && continue
        ids_arr+=("${tool_ids[$i]}")
        names_arr+=("${tool_names[$i]}")
        args_arr+=("${tool_args[$i]}")
    done < <(printf '%s\n' "${!tool_ids[@]}" | sort -n)
    tool_ids=()
    tool_names=()
    tool_args=()
    [[ ${#ids_arr[@]} -eq 0 ]] && return 0

    # 时间线渲染:本轮思考此前只画在交替屏幕上,解锁后被丢弃。在打印
    # 工具横幅之前把它渲染到主屏幕,主屏幕顺序才与真实流程一致:
    #   思考1 → 工具1 → 思考2 → 工具2 → ... → 正文
    flush_thinking

    # AI 在调用工具前输出的正文(如"我先看看这个文件"),只放进第一条 assistant 消息
    [[ -n "$full_response" ]] && render_markdown "$full_response"

    for i in "${!ids_arr[@]}"; do
        fn_id="${ids_arr[$i]}"
        fn_name="${names_arr[$i]}"
        fn_args="${args_arr[$i]}"
        [[ -z "$fn_name" || -z "$fn_id" ]] && continue

        # 快照本条调用写入前的 messages:若用户在权限确认(auth)流式期间
        # Ctrl+C 打断,已写入的 assistant(tool_calls) 尚无配对的 tool 结果,
        # 必须回滚,否则下一轮请求会因孤儿 tool_calls 报 400
        msgs_before_call=$messages
        # assistant 的 tool_calls 消息:并行调用拆成多条 assistant 消息时,
        # DeepSeek 要求每一条都回传 reasoning_content(实测缺一即 400),
        # 所以每条都带同一轮的思考原文;正文只在第一条。
        if (( first_call )); then
            add_message "$full_response" "assistant" "$fn_id" "$fn_name" "$fn_args" "$raw_reasoning"
            first_call=0
        else
            add_message "" "assistant" "$fn_id" "$fn_name" "$fn_args" "$raw_reasoning"
        fi

        if [[ "$fn_name" == "run_shell_command" ]]; then
            # // empty 兜底:参数缺字段时输出空,而不是字面 "null"(eval null 会报错)
            command=$(jq -r '.command // empty' <<< "$fn_args")
            run_in_bg=$(jq -r '.run_in_background // false' <<< "$fn_args")
            if [[ -z "$command" ]]; then
                content="错误:命令参数为空,工具调用参数解析失败: ${fn_args:-"(空)"}"
                add_message "$content" "tool" "$fn_id"
                continue
            fi
            # 命令只作为 %s 参数传入,绝不能拼进 format:命令里含 % 时
            # (如 curl -w "%{http_code}"),printf 会把 %{ 当格式符报错
            printf ' \033[43;30;1m  工具调用：%s  \033[0m\n' "$command"
            echo

            # 冲掉流式回复期间积压在 tty 队列的字节（粘贴/误触），
            # 防止残留的 "y" 自动确认执行命令（粘贴注入防护）
            local _junk=""
            while IFS= read -r -t 0.01 -N 1000000 _junk; [[ -n "$_junk" ]]; do _junk=""; done

            # 不能写 ai_auth=$(auth_think ...)：命令替换会捕获 auth_think 的
            # 全部 stdout，流式输出一旦超过管道缓冲就死锁。改为直接调用，
            # 结果从全局变量 full_response 取。
            # 重入安全:收集区已在函数开头清空,auth 内部 ask_ai 结束不会误触发本函数。
            auth_think "$command"
            rc=$?
            if (( rc == 130 )); then
                # 用户在权限判断流中被 Ctrl+C 打断:回滚本条未配对的
                # assistant(tool_calls),不执行该命令,向上传播 130
                messages=$msgs_before_call
                return 130
            fi
            ai_auth=$(echo -e "$full_response")

            if [[ "$ai_auth" == "N" ]]; then
                printf '\033[2K\033[1;41;37m是否执行该命令？(y/n): \033[0m'
                # 与历史选择的编号读取同策略:普通 read 不会被 SIGINT 打断,
                # 用短超时轮询检测 input_aborted,Ctrl+C 立即当作"拒绝执行"
                stty -echoctl 2>/dev/null
                answer=""
                while :; do
                    IFS= read -r -t 0.2 answer
                    rc=$?
                    (( input_aborted )) && break
                    (( rc <= 128 )) && break
                done
                stty echoctl 2>/dev/null
                if (( input_aborted )); then
                    input_aborted=0
                    printf '\r\033[2K\n'
                fi

            fi
            # echo "用户输入: $answer"
            if [[ "$ai_auth" == "Y" || "$answer" == "y" ]]; then
                if [[ "$run_in_bg" == "true" ]]; then
                    # 后台启动长驻服务:输出重定向到日志、stdin 断开终端、立即返回。
                    # 绝不能走 run_with_window:服务器进程继承 FIFO 写端,
                    # 窗口循环永远等不到 EOF,主 shell 会被永久卡死。
                    # 注意 BSD mktemp 要求 XXXXXX 在模板末尾,不能加 .log 后缀
                    bg_log=$(mktemp "${TMPDIR:-/tmp}/ct_bg_XXXXXX")
                    nohup bash -c "$command" </dev/null >"$bg_log" 2>&1 &
                    bg_pid=$!
                    content="✅ 服务已在后台启动:PID ${bg_pid}"$'\n'"日志: ${bg_log}"$'\n'"查看输出: 用 shell 工具执行 tail -f ${bg_log}"$'\n'"停止服务: kill ${bg_pid}(若有残留子进程,先 pkill -P ${bg_pid} 再 kill ${bg_pid})"
                    printf ' 后台启动 PID %s · 日志 %s\n' "$bg_pid" "$bg_log"
                else
                    # 窗口内执行命令；完整输出在 window_output，作为工具结果喂给 AI
                    # 先截断:超长输出保留头尾,砍掉重复性日志,控制上下文体积
                    run_with_window "$command"
                    content=$(truncate_output "$window_output")
                    sleep 2
                fi
            else
                content="用户拒绝执行命令: $command"
            fi
        else
            # 文件类工具:打印一行参数预览后直接执行。
            # 只读工具无需确认;写工具已由 resolve_path 强制限制在工作目录内,
            # git 可回滚,不再逐次询问(业界做法,Claude Code 同样不确认)。
            # 分发用白名单,防止未知函数名被动态执行。
            args_preview=$(jq -c '.' <<< "$fn_args" 2>/dev/null)
            [[ "$args_preview" == "null" ]] && args_preview="(参数为空)"
            (( ${#args_preview} > 160 )) && args_preview="${args_preview:0:160}…"
            printf ' \033[43;30;1m  工具调用：%s %s  \033[0m\n\n' "$fn_name" "$args_preview"
            case "$fn_name" in
                read_file|write_file|edit_file|list_dir|grep|web_fetch) "tool_$fn_name" ;;
                *) content="错误:未知工具 $fn_name" ;;
            esac
        fi

        add_message "$content" "tool" "$fn_id"
    done

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
}
# 核心函数：向模型发起请求，流式接收输出，处理工具调用
# 参数:$1 请求体  $2 是否锁屏  $3 是否记录 usage(默认 1;auth 等旁路请求传 0,
#      避免把小请求的 prompt_tokens 误记为上下文真实大小)
ask_ai() {
    
    # 拦截以选项开头的内容（如 "-l xxx"），避免被当成普通提问发给模型
    for option in "${options_list[@]}"; do
        [[ "$content" == "$option" || "$content" == "$option "* ]] && content="" && return 0
    done

    local think_start think_end
    local chunk=""
    local json=""
    local usage_pt=""
    local t_idx="" t_name="" t_args="" t_id="" tpiece=""
    local api_error="" err_msg="" chunk_err=""
    local request_body=$1
    local is_lock_screen=$2
    local is_record_usage=${3:-1}
    # 空响应重试:最多 max_attempts 次,递增退避(逻辑见下方 for 循环)
    local attempt max_attempts=3 think_save_len rc

    full_response=""
    # thinking_response 故意不在这里重置:工具链每一轮 ask_ai 的思考都要累积,
    # 由 flush_thinking 按时间线渲染增量(工具横幅之前),printContent 渲染最后
    # 一段并统一清空。若在这里重置,前几轮思考会被下一轮覆盖。
    # thinking_response=""
    # 本轮模型的思考原文(无装饰)。thinking 模式下 DeepSeek 要求把
    # reasoning_content 回传进下轮请求的 assistant 消息,丢失则 400:
    # "The reasoning_content in the thinking mode must be passed back"
    raw_reasoning=""
    # 每次请求开始清空工具调用收集区,防止上一轮残留导致误触发
    tool_ids=()
    tool_names=()
    tool_args=()

    # 进入"AI 输出阶段":期间 Ctrl+C 语义为打断本轮输出;同时清掉上一轮
    # 残留的打断标志。主屏幕输入阶段的 Ctrl+C 只置 input_aborted。
    ai_active=1
    ai_abort=0

    # 记录本轮请求开始前 thinking_response 的长度:若本轮完全空响应需要重试,
    # 重试前按此回滚上一尝试写进去的思考,防止思考被重复累积。
    think_save_len=${#thinking_response}
    [[  "$is_lock_screen" == 1 ]] && lock_screen
    for ((attempt=1; attempt<=max_attempts; attempt++)); do
        # 每轮尝试前复位单轮产物,上一尝试的残留会干扰"是否有产出"的判断
        full_response=""
        raw_reasoning=""
        api_error=""
        tool_ids=()
        tool_names=()
        tool_args=()
        think_start=0
        think_end=0
        if (( attempt > 1 )); then
            # 上一尝试零产出:回滚其思考残留、清掉交替屏幕残影、退避后重发
            # 同一请求体(零产出的请求无副作用,重发安全)
            thinking_response="${thinking_response:0:think_save_len}"
            printf '\033[2J\033[H'
            sleep "$attempt"
        fi

        # read 只对"以换行结尾的完整行"返回 0。DeepSeek 的整段 JSON 错误响应
        # (400 等)末尾没有换行符:若只写 while read,EOF 残段让 read 返回非零,
        # 循环体一次都不执行,api_error 永远为空,400 被误报成"无响应"
        # (历史 bug,真实 API 实测复现)。|| [[ -n "$line" ]] 让残段也进循环体。
        while IFS= read -r line || [[ -n "$line" ]]; do
        # Ctrl+C 打断:立即退出读循环(curl 同进程组一并收到 SIGINT 会自行
        # 终止;此检查兜底 read 被中断后仍带着残留行进入循环体的情况)
        (( ai_abort )) && break
        if [[ "$line" =~ ^data:\  ]]; then
            json="${line#data: }"
            [[ "$json" == "[DONE]" ]] && break

            # SSE 内嵌错误:部分错误(如 thinking 模式限制/限流)不是整段 JSON,
            # 而是包在 data: chunk 里。必须在此检查,否则表现为"空流/无响应"。
            chunk_err=$(printf '%s' "$json" | jq -r '.error.message // .error // empty' 2>/dev/null)
            [[ -n "$chunk_err" ]] && api_error="$chunk_err"            # stream_options.include_usage:流式最后一个 chunk 的 choices 为空、
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
            [[ -n "$thinking_chunk" ]] && { thinking_response+="$thinking_chunk"; raw_reasoning+="$thinking_chunk"; }


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
                # 并行调用:一次响应可含多个 tool_calls(index 区分)。每个调用
                # 的 name/id 只在其首片出现,arguments 分片按 index 各自累加。
                # 绝不能把不同 index 的参数拼在一起(历史 bug:参数粘合后
                # jq 解析出 null,shell 工具执行 eval "null" 报 command not found)。
                while IFS= read -r tpiece; do
                    t_idx=$(printf '%s' "$tpiece" | jq -r '.index // 0' 2>/dev/null)
                    t_name=$(printf '%s' "$tpiece" | jq -r '.function.name // empty' 2>/dev/null)
                    t_args=$(printf '%s' "$tpiece" | jq -r '.function.arguments // empty' 2>/dev/null)
                    t_id=$(printf '%s' "$tpiece" | jq -r '.id // empty' 2>/dev/null)
                    [[ -n "$t_name" ]] && tool_names[$t_idx]="$t_name"
                    [[ -n "$t_args" ]] && tool_args[$t_idx]+="$t_args"
                    [[ -n "$t_id" ]] && tool_ids[$t_idx]="$t_id"
                done < <(printf '%s' "$tool_chunk" | jq -c '.[]' 2>/dev/null)
            fi

            # chunk_end="$(printf '%s' "$json" | jq -j '.choices[0].finish_reason // empty')"
            # [[ "$chunk_end" == "tool_calls" ]] && tool_handler 0
        else
            # 非 SSE 行两类:1. API 报错(如 400 结构非法)时 curl 输出整段 JSON;
            # 2. curl 传输层错误(如 "curl: (52) Empty reply from server"、
            #    连接被断/超时)。都记录进 api_error,循环结束后显示,
            # 避免"发消息毫无输出"的静默失败。
            if [[ "$line" == \{* || "$line" == \[* || "$line" == curl:* ]]; then
                api_error+="$line"$'\n'
            fi
        fi
        done < <(
            # --connect-timeout 连接阶段超时;--retry 连接失败自动重试 2 次
            # (仅请求未发送时重试,不重复消费 token,不会破坏流式会话)
            # -sS:静默进度条但保留错误输出(传输层断连时报 "curl: (52)..." 等),
            # 由上方 else 分支记录进 api_error,否则错误被吞、误报成"无响应"。
            curl -sSN --connect-timeout 15 --retry 2 --retry-connrefused \
                "$BASE_URL" \
                -H "Content-Type: application/json" \
                -H "Authorization: Bearer $DEEPSEEK_API_KEY" \
                -H "api-key: $DEEPSEEK_API_KEY" \
                -d "$request_body"\
            2>&1
        )
        # 有任一产出(正文/思考/工具调用/明确 API 错误)→ 本轮完成,不再重试;
        # 完全零产出才进入下一尝试(退避重发);被 Ctrl+C 打断也绝不重试
        [[ -n "$full_response" || -n "$raw_reasoning" || -n "$api_error" ]] && break
        (( ${#tool_ids[@]} > 0 )) && break
        (( ai_abort )) && break
    done

    [[ "$is_lock_screen" == 1 ]] && unlock_screen
    ai_active=0

    # Ctrl+C 打断:丢弃本轮全部产出(正文/思考/工具调用),回滚本请求期间
    # 累积进 thinking_response 的思考(此前已渲染部分保留),返回 130 供
    # 上层识别"被打断";不进入 messages、不重试
    if (( ai_abort )); then
        ai_abort=0
        full_response=""
        raw_reasoning=""
        tool_ids=()
        tool_names=()
        tool_args=()
        thinking_response="${thinking_response:0:think_save_len}"
        return 130
    fi

    # API 请求失败(非流式错误响应):显示错误并写入 full_response,
    # 让错误可见且能进入 messages,模型下一轮可以看到失败原因。
    # 判断用 raw_reasoning(仅本轮)而非 thinking_response(跨轮累积):
    # 后者从第二轮起恒非空,会把链中间的 API 错误误判为"已有输出"而静默。
    if [[ -z "$full_response" && -z "$raw_reasoning" && -n "$api_error" ]]; then
        err_msg=$(printf '%s' "$api_error" | jq -r '.error.message // .error // empty' 2>/dev/null)
        [[ -z "$err_msg" ]] && err_msg=$api_error
        # 上下文越界(400 context/token/length exceeded):主动压缩后重发一次
        if [[ "$err_msg" == *[Cc]ontext* || "$err_msg" == *[Tt]oken* || "$err_msg" == *[Ll]ength* || "$err_msg" == *exceed* ]]; then
            retry_after_compact "$is_lock_screen" "$is_record_usage"
            rc=$?
            (( rc == 0 || rc == 130 )) && return "$rc"
        fi
        full_response="⚠️ API 请求失败: $err_msg"
        # 不在这里直接打印:printContent 会把 full_response 渲染到主屏幕,
        # 这里再打印会出现双份(历史问题:⚠️ 消息显示两次)
    fi

    # 无任何产出(内容/思考/工具/错误全空):网络中断或模型空回复。
    # 必须给可见反馈并写入 messages,否则表现为"回复突然中断"的静默失败。
    # 同样用 raw_reasoning 判断"本轮是否已有产出":跨轮累积的 thinking_response
    # 恒非空,会把链中间的网络中断误判为正常,导致静默。
    if [[ -z "$full_response" && -z "$raw_reasoning" && -z "$api_error" ]] \
        && (( ${#tool_ids[@]} == 0 )); then
        # 先尝试失败自愈:本地估算超限则压缩后重发一次(打破越界死循环);
        # 未超限才归为普通网络中断,给出兜底提示
        retry_after_compact "$is_lock_screen" "$is_record_usage"
        rc=$?
        (( rc == 0 || rc == 130 )) && return "$rc"
        full_response="⚠️ 本轮请求无响应(网络中断或空回复),请重试。"
        # 不直接打印,理由同上(防双份显示)
    fi

    if (( ${#tool_ids[@]} > 0 )); then
        tool_run_and_parse
        return $?
    fi
    return 0
}

# 把尚未渲染到主屏幕的思考增量渲染出来。思考随流只画在交替屏幕上,
# 解锁即被丢弃;调用点都在主屏幕活跃时:
#   - tool_run_and_parse 开头:把本轮思考渲染在工具横幅之前
#   - printContent:渲染最后一段思考,随后渲染最终正文
# thinking_rendered_len 记录已渲染到的字符位置,渲染后推进,
# 保证每段思考只渲染一次、且严格按时间线插入,不会一次性倾倒。
flush_thinking() {
    local delta="${thinking_response:thinking_rendered_len}"
    [[ -z "$delta" ]] && return 0
    render_markdown "$delta"
    thinking_rendered_len=${#thinking_response}
}

# 打印内容函数
printContent() {
    round_aborted=0

    add_message "$content"
    local tools
    local request_body rc

    # 上下文超阈值时先压缩再发送。只在"完整一轮的边界"(这里)触发:
    # 工具调用链中途压缩会破坏 tool_call_id 配对,API 直接报错。
    (( last_prompt_tokens > TOKEN_THRESHOLD )) && compact_messages

    # 构建请求体
    # messages 是 JSON 数组，必须用 --argjson 而不是 --arg
    # thinking 为空时不应发送 thinking 字段
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
    

    rc=$?
    if (( rc == 130 )); then
        # 被 Ctrl+C 打断:本轮 AI 输出已在 ask_ai 内丢弃,这里不渲染、不把
        # assistant 消息写入 messages(用户消息已在上方保留),回到主屏幕,
        # 用户可继续补充信息再次对话
        round_aborted=1
        printf '\n\033[2m⏹ 已中断,本轮输出已丢弃(-q 可退出)\033[0m\n'
        return 0
    fi

    # glow 是 markdown 渲染器，会把「行尾反斜杠 + 换行」当作 hard break（硬换行）
    # 语法而隐藏反斜杠，导致用户输入的 \\ 只保留的一个 \ 看起来也被删了。
    # 先用 sed 把行尾单个 \ 转义成 \\，glow 就能原样显示它。
    # 时间线渲染:先渲染最后一段思考的增量,再渲染最终正文。
    # 工具链中间的思考已由 flush_thinking 渲染在对应工具横幅之前。
    flush_thinking
    render_markdown "$full_response"
    # echo "$messages"

    # 思考内容已渲染到主屏幕,清空防止下一轮用户输入时累积旧思考
    thinking_response=""
    thinking_rendered_len=0

    # thinking 模式下回传本轮思考内容,否则下一轮 API 400
    [[ -n "$full_response" ]] && add_message "$full_response" "assistant" "" "" "" "$raw_reasoning"

}

# 根据聊天记录生成标题
overview() {

    local chunk=""
    # local res=""
    local msg
    local request_body

    # echo "记录是: $messages"
    msg=$(echo "$messages" | jq '. + [{role: "user", content: "根据我们的聊天对话，总结一个恰当的标题。输入格式如下: 标题内容。注意：除了按格式输出外，不可以有多余文字，标题内容不可有特殊字符换行符号等。"}]')

    request_body=$(jq -n  --argjson msgs "$msg" --arg model "$flash_model" '{
        model: $model,
        messages: $msgs,
        thinking: {type: "disabled"},
        reasoning_effort: "high",
        stream: true,
        stream_options: {include_usage: true}
    }')

    # ask_ai 会把流式正文直接 printf 到 stdout(主屏幕显示),而这里末尾又
    # printf 一次 full_response。若两者都进 stdout,chat_title=$(overview)
    # 会捕获两份标题,导致历史文件名重叠(历史 bug:标题重复两遍)。
    # 把 ask_ai 的 stdout 丢弃,只保留末尾这一次 printf 作为函数返回值。
    ask_ai "$request_body" >/dev/null

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
    local line rc

    local prompt
    if [[ "$1" == 0 ]]; then
        prompt="..  "
    else
        prompt="> "
    fi
    # 清掉上一阶段(工具链/压缩等)误触 Ctrl+C 留下的标志
    input_aborted=0
    while true; do
        # 读取期间临时解除 Ctrl+C→SIGINT 的驱动层映射(readline 会强制打开
        # ISIG,只有 intr undef 才能让 0x03 作为按键到达 readline),配合上方
        # bind 的 unix-line-discard 立即清空本行;读后马上恢复映射,其余阶段
        # 的 Ctrl+C 仍走 SIGINT 打断流程
        stty -isig intr undef 2>/dev/null
        IFS= read -e -r -p "$prompt" line
        rc=$?
        stty isig intr '^C' 2>/dev/null
        (( rc != 0 )) && break
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
        if [[ "$line" == "-q" ]]; then
            # 唯一退出方式:退出前保存历史(沿用原 Ctrl+C 退出的保存逻辑)
            save_history
            exit 0
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
    # 兜底:kill -INT 等外部 SIGINT 打断 read -e 时(readline 的 read 不会提前
    # 返回,回车后才走到这里),按惯例清空本次累计的全部输入,换行后返回,
    # 由主循环重新提示,程序不退出
    if (( input_aborted )); then
        input_aborted=0
        input=""
        printf '\n'
        return 0
    fi
    input="${input%$'\n'}"
}




# 解析命令行参数
parse_options "$@"
# 是否是第一次进入
first_time=1
if [[ -n "$content" ]]; then
    printContent
    # 本轮被打断时跳过标题生成,保留 first_time,下一轮成功后再生成
    if (( round_aborted )); then
        round_aborted=0
    else
        [[ -z "$chat_title" ]] && chat_title=$(overview) && first_time=0
    fi
fi

# 交互式输入循环
while true; do
    input=""
    normal_line 1
    content="$input"

    [[ -z "$content" ]] && continue

    printContent

    if (( round_aborted )); then
        round_aborted=0
        continue
    fi
    if [[ -z "$chat_title" && "$first_time" -eq 1 ]]; then
        chat_title=$(overview) && first_time=0
    fi
done



