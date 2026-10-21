#!/usr/bin/env bash
# 命令形状试金石:把一批形状喂给判据,只看端到端结论,不执行任何一条。
#   用法:tests/probe_shapes.sh [来源] [形状文件]
#         来源 = 工作树 wchat.sh(默认)/ 任意文件路径 / git 参数(如 HEAD^:wchat.sh)
#         形状文件默认 tests/probe_shapes.txt,每行 `RULE|命令`(RULE 留空 = 只要现象)
#
# 为什么要有它:很多形状**上不了命令行** —— 含 dotfile + `>` 的串会被"当前进程已加载的那版
# 门"当场拦掉(改完代码只对之后新起的进程生效,所以拿旧门验新形状时,命令本身先死了)。
# 形状写在文件里,判据读的是文件,这条路径不经过 shell 门,才量得准。
#
# 为什么它不是权威:结论只打印不判定,改错了没人报警。**权威是 test_hardwall.sh**;这里的
# 作用是"新形状先在这里看现象,确认口径后写成 expect 断言进表",避免拿一屏现象当结论。
#
# 三条红线(照抄 replay_audit.sh 的口径,都是一轮轮换出来的):
#   1) 必须落到 bash 5:3.2 下 `declare -A` 静默出错,不报错只给错结论。$0 是真脚本文件,
#      re-exec 安全(见 test_hardwall.sh T25)。
#   2) 判据函数没装载 ≠ 放行:缺函数直接 !NOFUNC 中止,否则一屏"全部放行"是假绿。
#   3) 不吞 stderr:装载段的报错必须能看见(上一轮 .r9_probe.sh 把 2>&1 吞进 /dev/null,
#      语法错误被伪装成"这条不拦")。
set -u
if (( ${BASH_VERSINFO[0]:-0} < 5 )); then
    for _b in "${WCHAT_BASH:-}" /opt/homebrew/bin/bash /usr/local/bin/bash "${HOME}/.linuxbrew/bin/bash"; do
        [[ -x "$_b" ]] || continue
        [[ "$("$_b" -c 'echo "${BASH_VERSINFO[0]}"' 2>/dev/null || echo 0)" =~ ^[5-9] ]] || continue
        exec "$_b" "$0" "$@"
    done
    echo "⛔ 需要 bash 5+" >&2; exit 1
fi
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
SRC_ARG=${1:-$(cd "$(dirname "$0")" && pwd)/fixtures/wchat_hardwall_oracle.sh}
VEC=${2:-tests/probe_shapes.txt}

WORK=$(mktemp -d "${TMPDIR:-/tmp}/wchat-probe.XXXXXX") || exit 1
trap 'rm -rf "$WORK"' EXIT
# 来源可以是 git 参数(含冒号且不是绝对路径),先取成文件,装载路径只走真文件
case $SRC_ARG in
    *:*) SRC=$WORK/src.sh
         git show "$SRC_ARG" > "$SRC" 2>/dev/null || { echo "⛔ git 取不到 $SRC_ARG" >&2; exit 1; } ;;
    *)   SRC=$SRC_ARG
         [[ -r "$SRC" ]] || { echo "⛔ 读不到 $SRC" >&2; exit 1; } ;;
esac
[[ -r "$VEC" ]] || { echo "⛔ 读不到形状文件 $VEC" >&2; exit 1; }

_WCHAT_LIB_ONLY=1 . "$SRC" >/dev/null || true      # 顶层代码的提示声丢掉,报错留在 stderr
declare -F hard_deny_cmd >/dev/null || { echo "⛔ !NOFUNC hard_deny_cmd 没装载(来源:$SRC_ARG)—— 下面任何一行都不许采信" >&2; exit 3; }
echo "══ 来源 $SRC_ARG  形状 $(grep -cvE '^[[:space:]]*(#|$)' "$VEC") 条"
# 断言总数守卫(第十二轮加,与回归表的 EXPECT_TOTAL 同口径)。
# 起因:升级 EOF 那条形状为断言后,输出是"19 通过 / 0 不符",而文件里有 20 条带期望行 ——
# 少掉的那条不是判错,是**根本没跑**。原因是 `while read` 在末行无换行时直接丢行:
# 产品码 hd_h11_scan 里写的正是 `while ... read -r _line || [[ -n "$_line" ]]`,
# 这个装置反而漏了。没有这条守卫,以后任何"行被跳过"都会以"全绿"的形态出现。
_exp=$(awk -F'|' '$1 != "" && $1 !~ /^[ \t]*#/ && NF >= 2 && $2 != "" {n++} END {print n+0}' "$VEC")
same=0; diffn=0; miss=0
while IFS='|' read -r want cmd || [[ -n "$cmd" ]]; do
    [[ -n "$cmd" ]] || continue
    # 注释行必须在这里跳过:注释里也可能出现 `|`(我就写过 "RULE|"),而下面把"期望列为空"
    # 当作"只要现象"正常打印 —— 不早退的话,一条注释会被当成一个命令形状混进结果里。
    case $want in \#*) continue ;; esac
    got=$( { hd_rule=""; if hard_deny_cmd "$cmd"; then printf '%s' "${hd_rule:-HIT}"; else printf 'NP'; fi; } 2>/dev/null )
    rc=$?
    if (( rc > 1 )); then printf '!ERR(%s) ≤ %s\n' "$rc" "$cmd"; miss=$((miss+1)); continue; fi
    if [ -z "$want" ]; then
        printf '%-5s ≤ %s\n' "${got:-NP}" "$cmd"
    elif [ "$got" = "$want" ]; then
        same=$((same+1)); printf '  ok  %-4s ≤ %s\n' "$got" "$cmd"
    else
        diffn=$((diffn+1)); printf '  ✗差 期望 %s 实得 %-4s ≤ %s\n' "$want" "$got" "$cmd"
    fi
done < "$VEC"
[ "$miss" -gt 0 ] && echo "⚠️ 有 $miss 条判据内部出错,别按上面的行数下结论"
echo "── 符合期望 $same / 不符 $diffn"
if (( same + diffn + miss != _exp )); then
    echo "⛔ 断言行 $_exp 条,实际判定 $((same + diffn + miss)) 条 —— 有断言被静默跳过(检查读循环与文件末行)"
    exit 4
fi
if (( diffn > 0 || miss > 0 )); then exit 1; fi
exit 0