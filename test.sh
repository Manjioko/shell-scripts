#!/bin/bash
# 路线 A demo：固定 N 行滚动窗口显示命令输出
# 用法：./test.sh          跑全部演示场景
#       ./test.sh <场景名>  跑单个场景（见底部 case）
if [[ -z "$BREW_BASH_CHECKED" ]]; then
    for brew_bash in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        [[ -x "$brew_bash" ]] && export BREW_BASH_CHECKED=1 && exec "$brew_bash" "$0" "$@"
    done
fi

WIN=10           # 窗口行数
THROTTLE=50000   # 重画节流间隔（微秒）

# 任何退出路径都恢复光标显示 + 自动换行
trap 'printf "\033[?25h\033[?7h"' EXIT

run_with_window() {
    local cmd="$1"
    local -a buf=()
    local total=0 drawn=0
    local now last_paint=0 dirty=0
    local chunk rc line pending="" partial=""
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

    while :; do
        chunk=""
        # 按块读而非按行读：交互提示通常不带换行，按行 read 永远等不到 \n，
        # 用户只能面对空窗口；-N+超时能把无换行的残段也取出来实时展示
        IFS= read -r -t 0.1 -N 65536 chunk
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
    done < <(eval "$cmd" 2>&1)

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
    printf '\033[2m└─ 完成 · 共 %d 行 ─┘\033[0m\n' "$total"
}

# ---------- 测试数据生成器（内部用，--gen 前缀避免和演示场景名冲突） ----------
if [[ "$1" == "--gen" ]]; then
case "$2" in
    slow)
        for i in $(seq 1 30); do
            printf '慢速输出 第 %d 行：模拟 npm install 这类断续日志\n' "$i"
            sleep 0.15
        done
        exit 0 ;;
    nasty)
        # 超宽行 / 中文宽字符 / 颜色码 / \r 进度条，全是路线A的天敌
        printf '正常短行\n'
        printf '超宽行：%s\n' "$(printf 'x%.0s' $(seq 1 300))"
        printf '中文宽字符行：这是一段很长很长的中文内容，用来测试宽字符在窗口边缘的裁剪行为，看看会不会把光标挤到下一行去导致窗口撕裂\n'
        printf '\033[31m红色\033[0m \033[1;32m粗绿\033[0m 颜色码行\n'
        for p in 10 40 70 100; do printf '\r下载进度: %d%%' "$p"; sleep 0.2; done
        printf '\n进度条结束后的正常行\n'
        printf '制表符行:\tcol1\tcol2\tcol3\n'
        exit 0 ;;
    burst)
        # 一次性猛灌 2000 行，测节流和滚动稳定性
        seq -f '快速输出第 %.0f 行 —— 测试高频重画是否闪烁错位' 1 2000
        exit 0 ;;
    short)
        # 少于窗口高度的输出，测窗口"长不满"的情况
        printf '只有三行输出\n第二行\n第三行\n'
        exit 0 ;;
    interactive)
        # 交互式命令：提示不带换行，等用户从 tty 输入
        printf '开始执行...\n'
        printf '是否继续？(y/n): '
        IFS= read -r ans
        printf '收到应答: %s\n' "$ans"
        printf '后续处理中\n完成\n'
        exit 0 ;;
esac
exit 1
fi

# ---------- 演示场景 ----------
demo() {
    printf '\n\033[1;36m▶ %s\033[0m\n' "$1"
    run_with_window "$2"
}

case "$1" in
    burst|slow|nasty|short|interactive)
        demo "场景：$1" "\"$0\" --gen $1" ;;
    find)
        demo "场景：find /usr/share" "find /usr/share -type f" ;;
    cmd)
        demo "场景：自定义命令" "$2" ;;
    "")
        demo "场景1：海量快速输出（2000 行灌入）"      "\"$0\" --gen burst"
        demo "场景2：慢速断续输出（观察实时滚动）"      "\"$0\" --gen slow"
        demo "场景3：刁钻内容（超宽/中文/颜色/进度条）" "\"$0\" --gen nasty"
        demo "场景4：输出少于窗口高度"                 "\"$0\" --gen short"
        demo "场景5：交互式命令（等待 y/n 输入）"        "\"$0\" --gen interactive"
        demo "场景6：真实命令（find /usr/share）"      "find /usr/share -type f" ;;
    *)
        echo "未知场景: $1（可选: burst slow nasty short interactive find cmd）" ;;
esac
