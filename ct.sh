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

# 最新一次用户输入的内容
content=""

# 记录所有的对话内容，包含用户和助手的消息
messages=$(jq -n '[{role: "system", content: "You are a helpful assistant."}]')


# 是否思考,默认关闭
thinking="disabled"

#模型，默认是 deepseek-v4-flash
model="deepseek-v4-flash"


for ((i=1; i<=$#; i++)); do
    if [[ "${!i}" != "-"* ]]; then
        content+="${!i}"
    fi
done


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
    if [[ "${!}" == "-t" ]]; then
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

printContent() {
    local full_response=""
    add_message "$content"

    # 构建请求体
    # messages 是 JSON 数组，必须用 --argjson 而不是 --arg
    # thinking 为空时不应发送 thinking 字段
    local request_body=$(jq -n --arg model "$model" --argjson msgs "$messages" --arg thinking "$thinking" '{
        model: $model,
        messages: $msgs,
        thinking: {type: $thinking},
        reasoning_effort: "high",
        stream: true
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
                    full_response+=$'\n\n----------思考开始------------\n\n\n'
                fi
                printf '%s' "$thinking_chunk"
            fi
            [[ -n "$thinking_chunk" ]] && full_response+="$thinking_chunk"


            # jq -j 不自动加末尾换行 + echo x 技巧保留内容中的 \n（$() 会截掉末尾换行符）
            chunk="$(printf '%s' "$json" | jq -j '.choices[0].delta.content // empty' 2>/dev/null; echo x)"
            chunk="${chunk%x}"

            if [[ $think_end -eq 0 && $think_start -eq 1 ]]; then
                if [[ -z "$thinking_chunk" && -n "$chunk" ]]; then
                    think_end=1
                    printf '\n\033[33m----------思考结束------------\033[0m\n'
                    full_response+=$'\n\n----------思考结束----------\n\n\n'
                fi
            fi

            if [[ -n "$chunk" ]]; then
                printf '%s' "$chunk"
            fi
            [[ -n "$chunk" ]] && full_response+="$chunk"
            # echo "$json"
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
    echo "$full_response" | sed 's/\\$/\\\\/' | glow -
    # echo "$messages"

    stty "$stty_save"

    # 新增历史记录到massages中去
    add_message "$full_response" "assistant"

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
        prompt="->（输入 ;;e 发送）: "
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
        input+="$line"$'\n'
        prompt=".. "

        break
    done
    input="${input%$'\n'}"
    # printf '%s\n' "$input"
}
# echo "请填写备注（支持多行，行末用 \\ 续行）："
# 是否是第一次进入
[[ -n "$content" ]] && printContent
while true; do
    input=""
    normal_line 1
    content="$input"
    [[ -z "$content" ]] && continue
    printContent
done



