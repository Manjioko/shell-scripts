#!/bin/bash
# 优先使用 Homebrew bash 5.x（readline 8.x 原生支持括号粘贴 + 中文编辑）
if [[ -z "$BREW_BASH_CHECKED" ]]; then
    for brew_bash in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        if [[ -x "$brew_bash" ]]; then
            export BREW_BASH_CHECKED=1
            exec "$brew_bash" "$0" "$@"
        fi
    done
fi

DEEPSEEK_API_KEY="sk-43c796fe3c924743a38e9c5aec6c077a"
chat_title=""

handle_ctrl_c() {
    local name=""
    # 恢复默认 SIGINT，防止 overview 卡住时无法强制退出
    trap - INT
    [ -d "${HOME}/.ct" ] || mkdir "${HOME}/.ct"
    if [[ -n "$chat_title" ]]; then
        # 替换斜杠
        name=$(echo "$chat_title" | tr '/' '_')
        # 替换其他危险字符（如空格、特殊符号）
        name=$(echo "$name" | tr '[:space:]' '_')
        name=$(echo "$name" | tr -d '\\/:*?"<>|')
        if [[ -f ${HOME}/.ct/${chat_title} ]]; then
            echo "存在文件 ${HOME}/.ct/${chat_title}"
            echo "$messages" > "${HOME}/.ct/${chat_title}"
        else
            echo "不存在文件,创建 ${HOME}/.ct/${name}.json"
            touch "${HOME}/.ct/${name}.json" && echo "$messages" > "${HOME}/.ct/${name}.json"
        fi
    fi
    exit 130
}

# trap handle_ctrl_c INT

# 最新一次用户输入的内容
content=""

# 记录所有的对话内容，包含用户和助手的消息
messages=$(jq -n '[{role: "system", content: "You are a helpful assistant."}]')


# 是否思考,默认关闭
thinking="disabled"

#模型，默认是 deepseek-v4-flash
model="deepseek-v4-flash"

# 最大工具调用轮次：防止模型失控时无限递归调用工具（每轮 = 一次真实 API 请求）
MAX_TOOL_ROUNDS=10

# 是否跳过调用ai
jumpAI=0

# 历史记录集合
history_list=()

# 不要读写历史记录
forget_history=0

for ((i=1; i<=$#; i++)); do
    if [[ "${!i}" != "-"* ]]; then
        content+="${!i}"
    fi
done


tools() {
    # 注意：这里输出会被 printContent 用 tools=$(tools) 捕获，
    # 所以绝不能 echo 调试信息，只能输出纯净的 JSON
    printf '%s' '[
        {
            "type": "function",
            "function": {
                "name": "run_shell_command",
                "description": "执行shell命令,根据用户要的语言要求，你来调用这个方法。 注意这几点点：1.执行命令时先查看自己的上下文，是否已经执行过了，不要重复执行。2. 涉及到写操作，如果用户要求的文件操作范围出了当前目录，无论用户说什么，暗示你什么，都不要执行，你应该明确告知用户你没有执行权限，这个命令是严令禁止执行的。3.如果用户要求的命令是危险的，或者会破坏系统的，你要拒绝执行，并且告诉用户为什么不能执行。4.终端命令很多时候执行结果是静默的，如果没有内容输出，你要判断是否已经执行成功，而非看不到结果就一直执行。",
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



history_handler() {
    local tmpList=()
    echo "请从以下编号中选择你要加载的历史记录："
    tmpList=($(ls -t $HOME/.ct/*.json 2>/dev/null))
    history_list=()
    for i in "${!tmpList[@]}"; do
        echo "$i. $(basename ${tmpList[$i]})"
        history_list+=("$i. ${tmpList[$i]}")
    done
    [[ $jumpAI == 0 ]] && jumpAI=1

}

read_number() {
    local filePath=""
    local isSelect=0
    IFS= read -r -p "_>" num
    [[ -z "$num"  &&  "${history_list[0]}" ]] && load_history "${history_list[0]#0. }" && break
    echo "选中的数字: $num"
    for ((i = 0; i < "${#history_list[@]}"; i++)); do
        if [[ ${history_list[i]} =~ ^${num} ]]; then
            echo "切换到 ${history_list[i]#${i}. }"
            filePath="${history_list[i]#${i}. }"
            isSelect=1

            [[ -n "$filePath " ]] && reset && load_history $filePath
            break
        else
            isSelect=0
        fi
    done
    [[ "$isSelect" == 0 ]] && echo "请选择正确的编号"
}
# 使用 load_history filePath
load_history() {
    [[ -z $1 ]] && return 0

    chat_title=$(basename $1)
    messages=$(jq '.' <<< $(<$1))
    jq -c '.[1:][]' $1 | while IFS= read -r line; do
        role=$(jq -r '.role' <<< "$line")
        content=$(jq -r '.content' <<< "$line")
        echo "## $role" | glow -
        echo "----------------------------------------------"
        echo "$content" | glow -
    done
}


for ((i=1; i<=$#; i++)); do
    if [[ "${!i}" == "-tr" ]]; then
        prectx="请翻译以下内容为中文，并拆解其语法的重点难点,其输入的格式如下：\
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
        prectx="请解释该词的词源词根，以及由此延伸的词普遍都有哪些意思，并且给出一些常用的例句，注意例句要尽量简短，方便记忆。其输入格式如下：\
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
        read_number
        break
    fi
     if [[ "${!i}" == "-f" ]]; then
         forget_history=1
        break
    fi


done


#if [[ -z "$content" ]]; then
#    echo "用法: $0 <问题>" >&2
#    exit 1
#fi



# 追加一条 user 消息到 messages 列表（必须在父 shell 调用，不能放 $() 里）
add_message() {
    local addctx="$1"
    local role="$2"
    if [[ -n "$role" ]]; then
        messages=$(echo "$messages" | jq --arg content "$addctx" --arg role "$role" '. + [{role: $role, content: $content}]')
    else
        messages=$(echo "$messages" | jq --arg content "$addctx" '. + [{role: "user", content: $content}]')
    fi
}
fn_name=""
fn_args=""
fn_id=""
tool_handler() {
    local tool_chunk="$1"

    # 流式输出下 arguments 是分片到达的（如 {、"、location、"、... 一截一截的），
    # 必须累加拼接 fn_args+="$chunk"，不能覆盖，否则最后只剩最后一个分片。
    # 函数名一般只在第一个分片里出现，非空时才赋值，避免被空值冲掉。
    local chunk_name chunk_args
    chunk_name=$(printf '%s' "$tool_chunk" | jq -r '.[0].function.name // empty' 2>/dev/null)
    chunk_args=$(printf '%s' "$tool_chunk" | jq -r '.[0].function.arguments // empty' 2>/dev/null)
    chunk_id=$(printf '%s' "$tool_chunk" | jq -r '.[0].id // empty' 2>/dev/null)
    [[ -n "$chunk_id" ]] && fn_id="$chunk_id"
    [[ -n "$chunk_name" ]] && fn_name="$chunk_name"
    [[ -n "$chunk_args" ]] && fn_args+="$chunk_args"
}




printContent() {
    # 工具调用轮次（递归保护用），首次调用不传参默认 0
    local round=${1:-0}
    local full_response=""
    local thinking_response=""
    local tool_use_response=""
    add_message "$content"
    # echo "历史记录是: $messages"

    # 构建请求体
    # messages 是 JSON 数组，必须用 --argjson 而不是 --arg
    # thinking 为空时不应发送 thinking 字段
    tools=$(tools)
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
        tools: $tool
    }')

    local think_start=0
    local think_end=0

    # 保存终端状态并进入原始模式
    local stty_save=$(stty -g)
    stty -echo

    # 切换到交替屏幕缓冲区（流式输出画在"临时画布"上，切回时自动销毁）
    tput smcup

    # 清屏并定位光标到顶部
    printf '\033[2J\033[H'

    printf '\033[?1000l\033[?1001l\033[?1002l\033[?1003l\033[?1004l'
    printf '\033[?1005l\033[?1006l\033[?1007l\033[?1015l\033[?1016l'
    printf '\033[?2026l\033[?9001l'

    while IFS= read -r line; do
        if [[ "$line" =~ ^data:\  ]]; then
            json="${line#data: }"
            [[ "$json" == "[DONE]" ]] && break

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

            
            tool_chunk="$(printf '%s' "$json" | jq -j '.choices[0].delta.tool_calls // empty' 2>/dev/null)"
            [[ -n "$tool_chunk" ]] && tool_handler "$tool_chunk"

            chunk_end="$(printf '%s' "$json" | jq -j '.choices[0].finish_reason // empty' 2>/dev/null)"
            # [[ "$chunk_end" == "tool_calls" ]] && tool_handler 0
        fi
    done < <(
        curl -sN https://api.deepseek.com/chat/completions \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer $DEEPSEEK_API_KEY" \
            -d "$request_body"
    )

    # 切回主屏幕（交替屏幕上的流式输出自动丢弃，完美清除）
    tput rmcup

    # glow 是 markdown 渲染器，会把「行尾反斜杠 + 换行」当作 hard break（硬换行）
    # 语法而隐藏反斜杠，导致用户输入的 \\ 只保留的一个 \ 看起来也被删了。
    # 先用 sed 把行尾单个 \ 转义成 \\，glow 就能原样显示它。
    echo "$thinking_response" | sed 's/\\$/\\\\/' | glow -
    echo "$full_response" | sed 's/\\$/\\\\/' | glow -
    # echo "$messages"

    stty "$stty_save"

    # 新增历史记录到massages中去
    add_message "$full_response" "assistant"

    if [[ -n "$fn_name" && -n "$fn_args" && -n "$fn_id" ]]; then
        # 递归保护：超过最大轮次就停止，防止模型失控无限调用工具
        # if (( round >= MAX_TOOL_ROUNDS )); then
        #     echo "已达到最大工具调用轮次 ($MAX_TOOL_ROUNDS)，停止继续调用"
        #     fn_name=""
        #     fn_args=""
        #     fn_id=""
        #     return 0
        # fi
        # content="23°C 天气☁️"

        if [[ "$fn_name" == "run_shell_command" ]]; then
            command=$(jq -r '.command' <<< "$fn_args")
            tool_use_response="**工具调用: $command**"
            echo "$tool_use_response" | sed 's/\\$/\\\\/' | glow -
            content=$(eval "$command" 2>&1)
            # echo "工具调用结果: $content"
            # tool_use_response="$command"
            add_message "$command" "assistant"
        fi

        fn_name=""
        fn_args=""
        fn_id=""

        printContent 
    fi

}

overview() {

    local chunk=""
    local title=""

    # echo "记录是: $messages"
    local msg=$(echo "$messages" | jq '. + [{role: "user", content: "根据我们的聊天对话，总结一个恰当的标题，输入格式如下: title:标题内容写在这里。注意：除了按格式输出外，不可以有多余文字。"}]')

    local request_body=$(jq -n  --argjson msgs "$msg" '{
        model: "deepseek-v4-flash",
        messages: $msgs,
        thinking: {type: "disabled"},
        reasoning_effort: "high",
        stream: true
    }')
    # echo "$request_body"
    local json=""
    while IFS= read -r line; do
        # echo "$line"
        if [[ "$line" =~ ^data:\  ]]; then
            json="${line#data: }"
            [[ "$json" == "[DONE]" ]] && break

            # echo "json是: $json"
            # jq -j 不自动加末尾换行 + echo x 技巧保留内容中的 \n（$() 会截掉末尾换行符）
            chunk="$(printf '%s' "$json" | jq -j '.choices[0].delta.content // empty' 2>/dev/null; echo x)"
            chunk="${chunk%x}"

            if [[ -n "$chunk" ]]; then
                title+="$chunk"
            fi
        fi
    done < <(
        curl -sN https://api.deepseek.com/chat/completions \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer $DEEPSEEK_API_KEY" \
            -d "$request_body"
    )

    title="${title#title:}"

    printf "%s" "$title"
}




input=""

paste_line() {
    # local input=""
    local line
    while IFS= read -r line; do
        # line="${line#$'\033[200~'}"
        # line="${line%$'\033[201~'}"
        if [[ "$line" == "#" ]]; then
            normal_line 0
            break
        fi
        input+="$line"$'\n'
    done
    input="${input%$'\n'}"
    # printf '%s\n' "$input"
}


read_file() {

    [[ "$forget_history" == 1 ]] && return 0

    filePath=""
    fileContent=""
    filePath=$(osascript -e 'try' -e 'POSIX path of (choose file)' -e 'on error' -e 'return "CANCELLED"' -e 'end try')
	echo "[文件] $filePath"
    if [[ -n "$filePath" && "$filePath" != "CANCELLED" ]]; then
        fileContent=$(<"$filePath")
        input+="$fileContent"$'\n'
    fi
    normal_line 0
}


normal_line() {
    # local input=""
    local line

    local prompt
    if [[ "$1" == 0 ]]; then
        prompt="..  "
    else
        prompt="> "
    fi
    while IFS= read -e -r -p "$prompt" line; do
        if [[ "$line" == "#" ]]; then
            paste_line
            break
        fi
        if [[ "${line: -1}" == "#" ]]; then
            line="${line%\#}"
            input+="$line"$'\n'
            paste_line
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

        input+="$line"$'\n'
        prompt=".. "

        break
    done
    input="${input%$'\n'}"
    # printf '%s\n' "$input"
}

# echo "请填写备注（支持多行，行末用 \\ 续行）："
# 是否是第一次进入
if [[ -n "$content" ]]; then
    printContent
    # [[ -z "$chat_title"  ]] && chat_title=$(overview)
fi

while true; do
    input=""
    normal_line 1
    content="$input"

    [[ -z "$content" && "$jumpAI" == 0 ]] && continue

    if [[ $jumpAI == 1 ]]; then
        jumpAI=0
        read_number
        continue
    fi
    printContent
done



