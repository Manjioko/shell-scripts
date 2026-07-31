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

input=""

paste_line() {
    local line
    while IFS= read -r line; do
        # line="${line#$'\033[200~'}"
        # line="${line%$'\033[201~'}"
        if [[ "$line" == "//e" ]]; then
            normal_line 0
            break
        fi
        input+="$line"$'\n'
    done
    input="${input%$'\n'}"
}

normal_line() {
    # local input=""
    local line
    
    local prompt
    if [[ "$1" == 0 ]]; then
        prompt="..  "
    else
        prompt="->（输入 ;;e 结束）:"
    fi
    while IFS= read -e -r -p "$prompt" line; do
        if [[ "$line" == ";;e" ]]; then
            break
        fi
        if [[ "$line" == "//s" ]]; then
            paste_line
            break
        fi
        input+="$line"$'\n'
        prompt="..  "
    done
    input="${input%$'\n'}"
    # printf '%s\n' "$input"
}

read_multiline() {
   normal_line 1
}

# echo "请填写备注（支持多行，行末用 \\ 续行）："
read_multiline
echo "----- 收到以下内容 -----"
echo "$input"
