#!/bin/bash
# 最小复现:normal_line 的粘贴处理逻辑(与 ct.dev.sh 一致)
bind 'set enable-bracketed-paste off' 2>/dev/null
[[ -t 1 ]] && printf '\033[?2004l'
input=""
while true; do
    prompt=".. "
    printf '\033[s'
    IFS= read -e -r -p "$prompt" line
    rc=$?
    (( rc != 0 )) && break
    rest=""
    chunk=""
    stty_save=$(stty -g)
    stty -echo
    while IFS= read -r -t 0.05 -N 1000000 chunk; [[ -n "$chunk" ]]; do
        rest+="$chunk"
        printf '[DRAIN %d bytes]\n' "${#chunk}" >&2
        chunk=""
    done
    stty "$stty_save"
    if [[ -n "$rest" ]]; then
        nl=$'\n'
        rest="${rest%$nl}"
        rest=${rest//$'\r'/}
        rest=${rest//"$nl$nl"/"$nl"}
        rest="${rest#"$nl"}"
        line="${line%$'\r'}"
        printf '\033[u\033[J'
        printf '%s%s\n' "$prompt" "$line"$'\n'"$rest"
        line+=$'\n'"$rest"
        input+="$line"$'\n'
        continue
    fi
    input+="$line"$'\n'
    [[ "$line" == "END" ]] && break
done
printf '\n=== INPUT ===\n%s=== END INPUT ===\n' "$input"
