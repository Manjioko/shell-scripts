#!/bin/bash

output=""
buf=()
drawn=0

printf '\033[?25l\033[?7l'  # 隐藏光标 + 关闭自动换行]]
while read -r line; do
    output+="$line"$'\n'
    buf+=("$line")
    (( ${#buf[@]} >3 )) && buf=("${buf[@]:1}")
    (( drawn > 0 )) && printf '\033[%dA' "$drawn"
    for i in "${buf[@]}"; do
        printf '\033[2K\033[2m│\033[0m %s\n' "$i"
    done
    drawn=${#buf[@]}
    sleep 0.1
done < <(seq 1 10)
printf '\033[2m└─ 完成 · 共 %d 行 ─┘\033[0m\n' 10
printf '\033[?25h\033[?7h'  # 显示光标 + 开启自动换行


# echo "输出内容："
# echo "$output"