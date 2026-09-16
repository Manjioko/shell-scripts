#!/usr/bin/env bash
# 端到端探针:在**你自己的真实终端**(Ghostty/iTerm/Terminal)里跑它,验证 wchat 的
# "终端应答剥离"两个方向都没坏:
#   A) 不漏:终端对查询的应答(光标位置、终端属性、kitty 查询…)不能变成按键流/正文;
#   B) 不吞:真按键(方向键、Ctrl-R、Esc)必须原样到达 —— 误吞比泄漏更难查。
# 不联网、不调模型、不改任何文件。只读 wchat.sh 里的 tty_* 函数来测。
# 用法:  bash tty_e2e_check.sh [wchat.sh 路径]
set -uo pipefail

SRC="${1:-$HOME/bin/wchat.sh}"
[[ -f "$SRC" ]] || { echo "找不到被测脚本:$SRC(用法: bash $0 /path/to/wchat.sh)"; exit 2; }
if (( ${BASH_VERSINFO[0]:-0} < 5 )); then
    # 必须 re-exec,不能只拒绝:本机 PATH 里第一个 bash 常是 /bin/bash 3.2,而脚本
    # 用了 ${var,,}、关联数组这类 5.x 语法。判据用 BASH_VERSINFO(幂等,不会 exec 循环)。
    for _b in "${BASH:-}" /opt/homebrew/bin/bash /usr/local/bin/bash /usr/bin/bash; do
        [[ -x "${_b:-}" ]] || continue
        if "${_b}" -c '(( ${BASH_VERSINFO[0]} >= 5 ))' 2>/dev/null; then
            exec "${_b}" "$0" "$@"
        fi
    done
    echo "需要 bash 5.x(当前 ${BASH_VERSION:-?},且找不到 5.x 的 bash)"; exit 2
fi

# 与硬墙表同一套抽取:顶层常量 + 按名字抽函数(整文件 source 会起 TUI)
eval "$(grep -E '^(TTY_[A-Z0-9_]*|tty_[a-z_0-9]*=)' "$SRC" 2>/dev/null)" 2>/dev/null
for fn in tty_strip_init tty_strip_responses tty_hold_tick tty_drain_responses; do
    eval "$(awk -v f="$fn" '$0 ~ "^"f"\\(\\) \\{" {p=1} p {print} p && /^}$/ {exit}' "$SRC")" 2>/dev/null
done
if ! declare -f tty_strip_responses >/dev/null 2>&1; then
    echo "抽取失败:$SRC 里没有 tty_strip_responses(或函数写法变了,awk 抽不到 = 测了个寂寞)"
    exit 2
fi

# 前置:必须是真终端。非 tty(管道/CI/`ssh -T`)下 stty 失败、查询没人应答,
# 结果会一边"看着通过"一边"看着失败"——那是假绿加假红,直接拒跑比硬跑有用。
if [[ ! -t 0 || ! -t 1 ]]; then
    echo "本探针要在**真实终端**里跑(当前 stdin/stdout 不是 tty)。"
    echo "直接在 Ghostty/iTerm 里执行:bash $0 $SRC"
    exit 2
fi

E=$'\e'
show() { printf '%s' "$1" | sed -e 's/\x1b/^[/g' -e 's/\x03/^C/g' -e 's/\x12/^R/g'; }

# ─────────────── A. 真终端应答:必须被剥掉 ───────────────
echo "=== A 应答剥离(自动判定,不用按键)==="
tty_strip_init
_saved=$(stty -g) 2>/dev/null || _saved=""
stty -icanon min 0 time 0 2>/dev/null
# 让**真实终端**回答这几条查询:DA1/DA2/DSR 光标位置/kitty 键盘查询/XTVERSION
printf '%s' "${E}[c${E}[>c${E}[6n${E}[?u${E}[>0q"
kept=""
_deadline=$((SECONDS + 2))
while (( SECONDS < _deadline )); do
    chunk=""
    IFS= read -r -s -t 0.15 -n 32 chunk 2>/dev/null || true
    [[ -z "$chunk" ]] && continue
    out=$(tty_strip_responses "$chunk")
    kept+="$out"
done
stty "$_saved" 2>/dev/null
# 尾部若还滞留在半截应答里,产品的 TTL 老化会原样转发出去(那是产品的事,不在本探针
# 判定范围;这里只判"已经剥完的部分有没有把应答漏进按键流")。

leak=0
case "$kept" in
    *"${E}[?1"*c*|*"${E}[?62"*|*"${E}[>"*c*|*"${E}[?u"*|*"${E}[?1;3u"*) leak=1 ;;
esac
printf '  真终端回了:%s\n' "$(show "$kept")"
printf '  剥离后剩下:%s\n' "$(show "$kept")"
if (( leak )); then
    echo "  FAIL A 应答泄漏:上面'剩下'里还有属性应答形态,过滤器没剥住"
    echo "       (把这两行原文发给维护者,含终端类型:$TERM_PROGRAM/${TERM_FEATURES:-无})"
else
    echo "  PASS A 无应答泄漏"
fi

# ─────────────── B. 真按键:必须活着 ───────────────
echo
echo "=== B 按键不被误吞(需要你按键)==="
echo "  请在 10 秒内依次按:上箭头、下箭头、Ctrl-R、Esc(单独一下),最后按回车"
tty_strip_init
_saved2=$(stty -g) 2>/dev/null || _saved2=""
stty -icanon min 1 time 0 2>/dev/null
keys=""; n=0
while (( n < 40 )); do
    k=""
    IFS= read -r -s -t 10 -n 1 k 2>/dev/null || break
    keys+=$(tty_strip_responses "$k")
    (( ++n ))
    [[ "$k" == $'\n' || "$k" == $'\r' ]] && break
done
stty "$_saved2" 2>/dev/null
printf '  收到的原始按键:%s\n' "$(show "$keys")"
sw=0
case "$keys" in
    *"${E}[A"*) sw=$((sw+1)); echo "    上箭头 OK" ;;
    *"${E}[B"*) sw=$((sw+1)); echo "    下箭头 OK" ;;
    *$'\022'*)  sw=$((sw+1)); echo "    Ctrl-R OK" ;;
    *"${E}"*)   sw=$((sw+1)); echo "    Esc OK" ;;
esac
if [[ -z "$keys" ]]; then
    echo "  SKIP B 没收到按键(超时或你直接退出了)——不构成'误吞'结论,重跑并按键即可"
elif (( sw >= 3 )); then
    echo "  PASS B 按键活着(识别到 $sw/4 类)"
else
    echo "  FAIL B 疑似误吞:只识别到 $sw/4 类。若你确实按了却看不到,说明过滤器把真按键当应答丢了"
fi
echo
echo "说明:A 判'泄漏'、B 判'误吞',两条都要 PASS。B 里 Esc 单独一下会与"
echo "     Alt/Meta 组合共用一个 ESC 前缀,脚本按'序列'判定,不是逐字节判。"

