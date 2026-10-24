#!/usr/bin/env bash
# mcp_client 模块的 Python 路径闸门(**收口后**)。
#
# 收口(方案铁律 3)后 `mcp_extract_json` 已是**唯一活实现**(旧 bash 逐行试起点回退已删)。
# 本闸门钉:语义锚点 + 函数体内结构锁 + 不可用降级(按"未命中"处理 + stderr 明示)。
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/py_prelude.sh" "${1:-wchat_ref}" || exit 2

RLDIR="$(dirname "$REAL")"
fnbody() { awk -v f="$1" '$0 ~ "^"f"\\(\\) \\{" {p=1} p {print} p && /^}$/ {exit}' "$SRC"; }
ex() { python3 "$RLDIR/wchat_py/_run.py" mcp_client extract_json "$@"; }
exout() {  # <cap-or-empty> <printf-format> → stdout(命中主体)
    if [[ -n "$1" ]]; then printf '%b' "$2" | ex "$1"
    else printf '%b' "$2" | ex; fi
}
exrc() {   # <cap-or-empty> <printf-format> → rc
    if [[ -n "$1" ]]; then printf '%b' "$2" | ex "$1" >/dev/null 2>&1
    else printf '%b' "$2" | ex >/dev/null 2>&1; fi
    echo $?
}

echo "=== mcp_extract_json 语义 ==="
ck "plain 命中"                 "$(exout '' '{"a": 1}')" '{"a": 1}'
ck "前缀噪声后命中"             "$(exout '' 'warn noise\n{"a": 1}')" '{"a": 1}'
ck "多行 JSON 完整保留"         "$(exout '' '{\n  "a": 1\n}')" $'{\n  "a": 1\n}'
ck "方括号告警不误切"           "$(exout '' '[mcporter] warn\n{\n  "x": 2\n}')" $'{\n  "x": 2\n}'
ck "非 JSON 未命中 rc=1"        "$(exrc '' 'hello world')" "1"
ck "空输入未命中 rc=1"          "$(exrc '' '')" "1"
ck "null 不算命中(对齐 jq -e)"  "$(exrc '' 'null')" "1"
ck "false 不算命中"             "$(exrc '' 'false')" "1"
ck "0 算命中(jq -e 真值)"       "$(exout '' '0')" "0"
ck "[] 算命中"                  "$(exout '' '[]')" "[]"
ck "截断 JSON 未命中 rc=1"      "$(exrc '' '{"a":')" "1"
ck "cap 限制:JSON 在起点之外则未命中" "$(exrc 2 'n1\nn2\nn3\n{"z":1}')" "1"
ck "cap 放宽:上限内命中"        "$(exout 25 'n1\nn2\nn3\n{"z":1}')" '{"z":1}'

echo "=== 收口结构锁:旧 bash 回退确已删除(只查函数体) ==="
ck "mcp_extract_json 定义 1 处"      "$(grep -c '^mcp_extract_json() {' "$SRC")" "1"
ck "  · 回退注释已删"                "$(fnbody mcp_extract_json | grep -c '回退路径')" "0"
ck "  · 只剩一次 py_run mcp_client"  "$(fnbody mcp_extract_json | grep -c 'py_run mcp_client extract_json')" "1"
ck "  · 不再有 jq 逐行试起点"        "$(fnbody mcp_extract_json | grep -cE 'jq -e|tail -n \+|wc -l')" "0"
ck "  · 有降级明示(注释+stderr 各 1)" "$(fnbody mcp_extract_json | grep -c '组件不可用')" "2"

echo "=== 降级:工具口走真实函数 ==="
me() {  # <extra-env> <input> → stdout; stderr 落 $W/me_err
    local extra="$1" in="$2"
    env $extra WCHAT_PY=1 "$BASHX" -c "
set +u; cd '$W'
_load() { _WCHAT_LIB_ONLY=1 source '$REAL' >/dev/null 2>&1; }
_load; trap - EXIT
if _o=\$(mcp_extract_json \"\$1\"); then printf 'HIT<%s>' \"\$_o\"; else printf 'MISS'; fi
" _ "$in" 2>"$W/me_err"
}
ck "正常:命中且回传主体"        "$(me '' '{"a": 1}')" 'HIT<{"a": 1}>'
ck "正常:未命中回 MISS"         "$(me '' 'no json')" "MISS"
ck "组件不可用 → 按未命中(MISS)" "$(me 'WCHAT_PY_MCP_CLIENT=0' '{"a": 1}')" "MISS"
ck "组件不可用 → stderr 有明示"   "$(grep -c '组件不可用' "$W/me_err")" "1"

finish