#!/usr/bin/env bash
# 可移植性闸门:测试夹具不许假设"我在 macOS、bash 在 /opt/homebrew/bin"。
#
# 为什么值得单独一道(都是 2026-10 在 Docker/Ubuntu 上实测出来的,不是假想):
#   1) 34 个测试文件写死 `/opt/homebrew/bin/bash`。在 Linux 上它**不报错** ——
#      子 shell 里失败,外层只拿到空串,断言于是"全空":
#        写熔断 21 红、权限状态 3 红、py工具运行全红。看着像产品坏了,其实是夹具不可移植。
#   2) `tests/fixtures/wchat_py` 是一条**绝对路径软链**(-> /Users/jwan/bin/wchat_py)。
#      换个检出位置/换台机器它就是断链,而断链不会让闸门报错:
#      夹具静默走降级(fail-closed),硬墙语料于是 6 条 DIFF,像是"Linux 上判据偏了"。
#      实测:同一个仓库、同一份代码,只是软链存不存在,判定就不同 —— 这是最贵的一种假红。
#
# 判据只查"测试自己的可移植性",不碰产品代码。
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
HERE="$(pwd)/tests"
. "$HERE/lib_bash.sh"

pass=0; fail=0
ck() { if [[ "$2" == "$3" ]]; then pass=$((pass+1)); printf '  ok   %s\n' "$1"
     else fail=$((fail+1)); printf '  FAIL %s: 得 [%s] 期望 [%s]\n' "$1" "$2" "$3"; fi; }

echo "=== 1. fixtures 软链必须是相对路径 ==="
link="$HERE/fixtures/wchat_py"
if [[ -L "$link" ]]; then
    ck "wchat_py 软链存在" "yes" "yes"
    tgt=$(readlink "$link")
    case "$tgt" in
        /*) ck "软链目标是相对路径(绝对路径换机器就断)" "绝对:$tgt" "相对" ;;
        *)  ck "软链目标是相对路径" "相对" "相对" ;;
    esac
    ck "软链能解析到仓库的 wchat_py/_run.py" "ok" "$([[ -f "$link/_run.py" ]] && echo ok || echo 断链)"
else
    ck "wchat_py 软链存在" "缺失" "yes"
fi

echo "=== 2. 两个 bash 解析器必须给出同一个 bash(一份实现,不是两份) ==="
PY_BASH=$(cd "$HERE" && python3 -c 'import _bashbin; print(_bashbin.bash_bin())' 2>/dev/null)
ck "lib_bash.sh 解析到 bash5+" "$([[ -n "$WCHAT_BASH" ]] && echo ok || echo 空)" "ok"
ck "bash 版本 >= 5" "$("$WCHAT_BASH" -c 'echo ${BASH_VERSINFO[0]}' 2>/dev/null)" "5"
ck "python 侧与 shell 侧解析一致" "$PY_BASH" "$WCHAT_BASH"

echo "=== 3. 驱动脚本里不许有写死的 mac bash 路径 ==="
# 只在**直接调用**的位置违规;候选列表(`for b in /opt/homebrew/bin/bash ...`)是正当的
# 回退顺序,不该报。冻结的 oracle/legacy 副本是"被测对象",也不该改。
ALLOW_FILES=" tests/legacy_wchat.sh tests/fixtures/wchat_hardwall_oracle.sh tests/fixtures/wchat_tools_oracle.sh tests/g6.sh tests/lib_bash.sh tests/_bashbin.py tests/portable_paths_test.sh "
bad=0; offenders=""
for f in tests/*.sh tests/*.py; do
    case "$ALLOW_FILES" in *" $f "*) continue ;; esac
    while IFS=: read -r ln _; do
        [[ -z "$ln" ]] && continue
        line=$(sed -n "${ln}p" "$f")
        # 候选列表/注释里的说明不算:真正危险的是"把路径当命令执行"
        case "$line" in
            *for*|*"#"*|*"\${WCHAT_BASH:-}"*|*command\ -v*|*printf*|expect\ *) continue ;;
        esac
        bad=$((bad+1)); offenders+="\n      $f:$ln: $line"
    done < <(grep -n "/opt/homebrew/bin/bash" "$f" 2>/dev/null || true)
done
ck "无写死调用的 mac bash 路径" "$bad" "0"
(( bad == 0 )) || printf "%b\n" "$offenders"

printf 'RESULT total=%d pass=%d fail=%d\n' "$((pass + fail))" "$pass" "$fail"
(( fail == 0 ))