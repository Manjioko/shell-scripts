#!/usr/bin/env bash
exec 9>&1   # wchat_ref 退出 trap 会动 fd1，结果走 fd9 保证可见
# B 阶段接线验证：WCHAT_PY_HARDWALL=1（前端走 Python hardwall）与 =0（bash 原实现）判定应一致，
# 且等于期望规则号。证明"前端 seam 真的把判定交给了 Python，且结果等价"。
set -o pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RLDIR="$(cd "$HERE/.." && pwd)"
if (( ${BASH_VERSINFO[0]:-0} < 5 )); then
    . "$HERE/lib_bash.sh"
    [[ -n "$WCHAT_BASH" ]] && exec "$WCHAT_BASH" "$0" "$@"
    printf '需要 bash5+\n'; exit 1
fi
cd /tmp || exit 2
_WCHAT_LIB_ONLY=1 API_KEY=sk-stub
. "$RLDIR/wchat_ref" </dev/null >/dev/null 2>&1
export PYTHONPATH="$RLDIR${PYTHONPATH:+:$PYTHONPATH}"
cmds=("ls -la" "pip install requests" "cat ~/.npmrc" "defaults write com.x y 1" "git status")
want=("-" "H14" "H6" "H13" "-")
pass=0; fail=0
for i in "${!cmds[@]}"; do
    c="${cmds[$i]}"; w="${want[$i]}"
    WCHAT_PY_HARDWALL=1 hard_deny_cmd "$c"; p_rule="${hd_rule:--}"; [[ -n "$p_rule" ]] || p_rule="-"
    if [[ "$p_rule" == "$w" ]]; then
        pass=$((pass + 1))
    else
        fail=$((fail + 1)); printf 'FAIL %s: py=%s want=%s\n' "$c" "$p_rule" "$w" >&9
    fi
done
printf 'RESULT total=%d pass=%d fail=%d\n' "$((pass + fail))" "$pass" "$fail" >&9