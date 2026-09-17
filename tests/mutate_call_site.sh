#!/usr/bin/env bash
# 定向变异装置:把判据里**某一整行**换成别的文本,然后让整张回归表跑在变异体上,看它能不能
# 被红条逮住。"表全绿"只证明断言之间不矛盾,不证明断言在看着这行代码 —— 这装置量的是后者。
#   用法:tests/mutate_call_site.sh <字面串定位的行> <替换成的文本> [原始来源]
#   例:  tests/mutate_call_site.sh 'hd_redir_targets_all_clean "$s" || return 0' 'true'
#         tests/mutate_call_site.sh 'hd_redir_targets_all_clean "$s" || return 0' 'return 0'
#
# 三条红线(本轮踩出来的,别再退回sed 那种写法):
#   1) **算子按语义选,不要按字面取反**。原行是 `helper "$s" || return 0`,把它换成裸
#      `false` 只是"这条命令返回 1",`||` 右边的 return 一起消失了 → 函数继续往下走 →
#      和换成 `true` 完全同义。当时两个"相反方向"的变异红条一模一样,才暴露出其中一个
#      根本没被测到。要"永远算有写动作",正确算子是 `return 0`。
#   2) **必须与"同路径的基线"比**。本表用 $1 传来源;不跑一遍基线就直接看红条数,会把
#      装置自身的偏移(某段另读 ./wchat.sh)当成变异的杀伤。上一轮我就是手搓 comm 才
#      看清"M24-false 与 M24-true 红条相同"这件事。
#   3) **替换命中数必须报出来**。BSD sed 对 `\$`、`||` 的转义各家不同,两种写法都静默替换
#      0 处过,"0 处"差点被当成"变异未被杀"。这里用 grep -F 定位行号 + awk 整行替换,
#      不碰正则;注释行里同名文本要排除(它不是代码)。
set -u
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1     # 脚本在 tests/ 下,根目录是其父目录
PAT=${1:-}
REPL=${2:-}
SRC=${3:-wchat.sh}
[[ -n "$PAT" && -n "$REPL" ]] || { echo "用法:tests/mutate_call_site.sh <定位字面串> <替换文本> [来源]" >&2; exit 2; }
[[ -r "$SRC" ]] || { echo "⛔ 读不到 $SRC" >&2; exit 1; }
BASH5=${WCHAT_BASH:-}
[ -z "$BASH5" ] && for b in /opt/homebrew/bin/bash /usr/local/bin/bash "$HOME/.linuxbrew/bin/bash"; do
    [[ -x "$b" ]] && [[ "$("$b" -c 'echo ${BASH_VERSINFO[0]}' 2>/dev/null || echo 0)" =~ ^[5-9] ]] && { BASH5=$b; break; }
done
[[ -n "$BASH5" ]] || { echo "⛔ 需要 bash 5(3.2 下 declare -A 静默出错)" >&2; exit 1; }

WORK=$(mktemp -d "${TMPDIR:-/tmp}/wchat-mut.XXXXXX") || exit 1
trap 'rm -rf "$WORK"' EXIT
MUT=$WORK/mut.sh

lines=$(grep -nF "$PAT" "$SRC" | awk -F: '$2 !~ /^[[:space:]]*#/ {print $1}' | paste -sd, -)
n=$(printf %s "$lines" | tr ',' '\n' | grep -c .)
if [ "$n" = 0 ]; then echo "⛔ 定位串在 $SRC 的代码行里没有命中(注释里的同名文本不算),这次变异是空操作" >&2; exit 3; fi
echo "定位 $n 处:$lines  替换为「${REPL}」"
awk -v L="$lines" -v m="$REPL" 'BEGIN{c=split(L,a,","); for(i=1;i<=c;i++) s[a[i]+0]=1}
    s[NR]{print "            " m; hit++; next} {print}
    END{if(hit+0!=c+0){print "ERR hit="hit+0" want="c+0 > "/dev/stderr"; exit 4}}' "$SRC" > "$MUT" 2>"$WORK/err"
if [ -s "$WORK/err" ]; then echo "⛔ $(cat "$WORK/err")" >&2; exit 4; fi
echo "变异体与原文差异 $(diff "$SRC" "$MUT" | grep -c '^<') 处(末尾换行差异由 awk 补,无害)"

fails() { "$BASH5" test_hardwall.sh "$1" 2>/dev/null | grep -a '^  FAIL' | sed 's/^  FAIL //; s/ | cmd=.*//' | sort; }
fails "$SRC"  > "$WORK/base.f"
"$BASH5" test_hardwall.sh "$SRC"  2>/dev/null | grep -a '^RESULT ' | sed 's/^/  基线 /'
fails "$MUT" > "$WORK/mut.f"
"$BASH5" test_hardwall.sh "$MUT" 2>/dev/null | grep -a '^RESULT ' | sed 's/^/  变异 /'
add=$(comm -13 "$WORK/base.f" "$WORK/mut.f" | grep -c .)
gone=$(comm -23 "$WORK/base.f" "$WORK/mut.f" | grep -c .)
echo "  相对基线:新增红条 $add 条,消失 $gone 条"
comm -13 "$WORK/base.f" "$WORK/mut.f" | head -12 | cut -c1-72 | sed 's/^/    +/'
if [ "$add" -gt 0 ]; then echo "  ✅ 此变异可杀(有断言真的在看这行)"; else echo "  ⚠️ 此变异未杀 —— 这行代码没有任何断言在守着,补断言或承认它冗余"; fi
[ "$gone" -gt 0 ] && echo "  ⚠️ 有 $gone 条基线红条在变异后消失,说明断言之间有耦合,值得看一眼"