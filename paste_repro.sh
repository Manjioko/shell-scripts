#!/bin/bash
# 最小复现:normal_line 的粘贴处理逻辑(与 ct.dev.sh 一致)
DBGLOG="${DBGLOG:-$TMPDIR/paste_dbg.log}"
: > "$DBGLOG"
bind 'set enable-bracketed-paste off' 2>/dev/null
[[ -t 1 ]] && printf '\033[?2004l'
input=""
paste_lines=""
paste_enter=0
while true; do
    prompt=".. "
    if [[ -z "$paste_lines" ]]; then
        printf '\033[s'
    fi
    IFS= read -e -r -p "$prompt" line
    rc=$?
    printf '[DBG readline done at %.3f rc=%s line=%q]\n' "$EPOCHREALTIME" "$rc" "$line" >> "$DBGLOG"
    (( rc != 0 )) && break
    rest=""
    chunk=""
    paste_enter=0
    stty_save=$(stty -g)
    # 排干用单次短窗读:read -e 返回前有 ~1s 延迟,粘贴剩余字节在此窗口
    # 已全部入队,一次读完即可。长窗循环反而会吞掉窗口内到达的用户回车
    # (read -N 阻塞期间到达的字节超时后被 bash 丢弃,字节不回流)。
    stty raw
    IFS= read -r -t 0.05 -N 1000000 rest
    stty "$stty_save"
    if [[ -n "$rest" ]]; then
        nl=$'\n'
        # 用户粘完立即按的回车:read -e 返回前有 ~1s 窗口,回车也被排干读入。
        # 粘贴行尾是 \r\n 对,独立裸 \r 只会来自用户 Enter——视为提交信号
        while [[ "$rest" == *$'\r' ]]; do
            rest="${rest%$'\r'}"
            paste_enter=1
        done
        rest="${rest%$nl}"
        rest=${rest//$'\r'/}
        rest="${rest#"$nl"}"
        line="${line%$'\r'}"
        printf '[DBG paste: line=%q rest=%q enter=%s]\n' "$line" "$rest" "$paste_enter" >> "$DBGLOG"
        paste_lines+="$line"$'\n'"$rest"$'\n'
        printf '\033[u\033[J'
        printf '%s%s' "$prompt" "$paste_lines"
        line+=$'\n'"$rest"
        input+="$line"$'\n'
        if (( paste_enter )); then
            printf '[DBG paste_enter -> break]\n' >> "$DBGLOG"
            break
        fi
        continue
    fi
    input+="$line"$'\n'
    printf '[DBG plain: line=%q]\n' "$line" >> "$DBGLOG"
    [[ "$line" == "END" ]] && break
    [[ -z "$line" ]] && break
done
printf '\n=== INPUT ===\n%s=== END INPUT ===\n' "$input"
