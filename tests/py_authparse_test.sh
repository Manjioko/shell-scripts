#!/usr/bin/env bash
# authparse 模块的 Python 路径闸门(**收口后**)。
#
# 收口(方案铁律 3)后 `auth_parse_verdict` 已是**唯一活实现**(旧 bash 回退已删),
# 不再有 py-vs-bash parity。本闸门钉三件事:
#   ① 语义锚点(T19 的十一组输入 → 判定|编号|理由);
#   ② **函数体内结构锁**(旧回退确已删、fail-closed 兜底在、只剩一次 py_run);
#   ③ **不可用降级**:WCHAT_PY_AUTHPARSE=0 或组件不可用 → **fail-closed 判 N**,绝不静默放行。
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/py_prelude.sh" "${1:-wchat_ref}" || exit 2

fnbody() { awk -v f="$1" '$0 ~ "^"f"\\(\\) \\{" {p=1} p {print} p && /^}$/ {exit}' "$SRC"; }

avp() {  # <额外 env(可空)> <raw>  -> "verdict|code|reason"
    local extra="$1" raw="$2"
    env $extra WCHAT_PY=1 "$BASHX" -c "
set +u
cd '$W'
_load() { _WCHAT_LIB_ONLY=1 source '$REAL' >/dev/null 2>&1; }
_load
trap - EXIT
av_verdict=''; av_code=''; av_reason=''
auth_parse_verdict \"\$1\"
printf '%s|%s|%s' \"\${av_verdict:-}\" \"\${av_code:-}\" \"\${av_reason:-}\"
" _ "$raw" 2>/dev/null
}

echo "=== 语义(T19) ==="
acases=(
    'N2:外部写入@@N|2|外部写入'
    'N0:可挂载宿主根@@N|0|可挂载宿主根'
    'N2: 外部写入@@N|2|外部写入'
    'N 越界写@@N||越界写'
    'Y@@Y||'
    'Y 这只是只读命令@@Y||'
    'N@@N||'
    '越界写风险@@N||越界写风险'
    'N2:外部写入。建议改用工作目录@@N|2|外部写入。建议改用工作目录'
    '@@N||'
    'n2:外部写入@@N|2|外部写入'
)
for c in "${acases[@]}"; do
    raw="${c%%@@*}"; want="${c#*@@}"
    ck "语义 T19 [$raw]" "$(avp "" "$raw")" "$want"
done

echo "=== 收口结构锁:旧 bash 回退确已删除(只查函数体) ==="
ck "定义恰好 1 处" "$(grep -c '^auth_parse_verdict() {' "$SRC")" "1"
ck "  · 旧回退注释已删" "$(fnbody auth_parse_verdict | grep -c '回退路径')" "0"
ck "  · 旧 case 已删" "$(fnbody auth_parse_verdict | grep -c 'case \"\$line\" in')" "0"
ck "  · fail-closed 兜底在" "$(fnbody auth_parse_verdict | grep -cF 'av_verdict="N"; av_code=""; av_reason=""')" "1"
ck "  · 只剩一次 py_run" "$(fnbody auth_parse_verdict | grep -c 'py_run authparse verdict')" "1"

echo "=== 不可用降级(fail-closed,不放行) ==="
ck "WCHAT_PY_AUTHPARSE=0 → 判 N" "$(avp 'WCHAT_PY_AUTHPARSE=0' 'Y')" "N||"
ck "组件不可用(坏 PY 目录)→ 判 N" "$(avp 'WCHAT_PY_DIR=/nonexistent' 'Y')" "N||"

finish

