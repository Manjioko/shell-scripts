#!/usr/bin/env bash
# msg 模块的 Python 路径闸门(**收口后**)。
#
# 收口(方案铁律 3)后 `add_message` / `auth_add_message` 已是**唯一活实现**。
# 本闸门钉:语义锚点 + 函数体内结构锁 + 不可用降级(不追加 + stderr 明示)。
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/py_prelude.sh" "${1:-wchat.sh}" || exit 2

fnbody() { awk -v f="$1" '$0 ~ "^"f"\\(\\) \\{" {p=1} p {print} p && /^}$/ {exit}' "$SRC"; }

printf '%s' '[{"role":"system","content":"sys"}]' > "$W/base"

am() {  # <base> <thinking> <extra-env> <a1..a6>  -> 新 messages
    local base="$1" th="$2" extra="$3"; shift 3
    env $extra WCHAT_PY=1 "$BASHX" -c "
set +u; cd '$W'
_load() { _WCHAT_LIB_ONLY=1 source '$REAL' >/dev/null 2>&1; }
_load; trap - EXIT
thinking='$th'
IFS= read -r -d '' messages < '$base'
add_message \"\$1\" \"\$2\" \"\$3\" \"\$4\" \"\$5\" \"\$6\"
printf '%s' \"\$messages\"
" _ "$@" 2>/dev/null
}
au() {  # <base> <extra-env> <content> <role> <keep>  -> 新 auth_message
    local base="$1" extra="$2" content="$3" role="$4" keep="$5"
    env $extra WCHAT_PY=1 "$BASHX" -c "
set +u; cd '$W'
_load() { _WCHAT_LIB_ONLY=1 source '$REAL' >/dev/null 2>&1; }
_load; trap - EXIT
AUTH_CTX_KEEP='$keep'
IFS= read -r -d '' auth_message < '$base'
auth_add_message \"\$1\" \"\$2\"
printf '%s' \"\$auth_message\"
" _ "$content" "$role" 2>/dev/null
}

echo "=== add_message 语义(4 分支) ==="
ck "user 消息" "$(am "$W/base" disabled '' hello '' '' '' '' | jq -c '.[-1]|{role,content}')" '{"role":"user","content":"hello"}'
ck "assistant 工具调用空正文 → content:null" "$(am "$W/base" disabled '' '' assistant call_1 read_file '{"p":1}' '' | jq -c '.[-1]|{role,content,tc:(.tool_calls|length)}')" '{"role":"assistant","content":null,"tc":1}'
ck "tool 角色带 tool_call_id" "$(am "$W/base" disabled '' 'tool out' tool call_1 '' '' '' | jq -c '.[-1]|{role,content,tcid:.tool_call_id}')" '{"role":"tool","content":"tool out","tcid":"call_1"}'
ck "thinking 开启必带 reasoning_content" "$(am "$W/base" enabled '' 回复 assistant '' '' '' '' | jq -c '.[-1]|has("reasoning_content")')" "true"
ck "非 thinking 且无理由 → 不带该字段" "$(am "$W/base" disabled '' 回复 assistant '' '' '' '' | jq -c '.[-1]|has("reasoning_content")')" "false"

echo "=== auth_add_message 语义 ==="
printf '%s' '[{"role":"system","content":"head"}]' > "$W/ab1"
printf '%s' '[{"role":"system","content":"head"},{"role":"user","content":"u1"},{"role":"assistant","content":"a1"},{"role":"user","content":"u2"},{"role":"assistant","content":"a2"},{"role":"user","content":"u3"},{"role":"assistant","content":"a3"}]' > "$W/ab7"
ck "追加后 length=2" "$(au "$W/ab1" '' cmd1 user 20 | jq 'length')" "2"
ck "裁剪到 1+2*keep=7" "$(au "$W/ab7" '' cmd8 user 3 | jq 'length')" "7"
ck "system 仍在首位" "$(au "$W/ab7" '' cmd8 user 3 | jq -r '.[0].content')" "head"

echo "=== 收口结构锁:旧 bash 回退确已删除(只查函数体) ==="
ck "add_message 定义 1 处" "$(grep -c '^add_message() {' "$SRC")" "1"
ck "  · 回退注释已删" "$(fnbody add_message | grep -c '回退路径')" "0"
ck "  · 旧 body 已删(无 jq)" "$(fnbody add_message | grep -c 'jq ')" "0"
ck "  · 只剩一次 py_run" "$(fnbody add_message | grep -c 'py_run msg add')" "1"
ck "auth_add_message 定义 1 处" "$(grep -c '^auth_add_message() {' "$SRC")" "1"
ck "  · 回退注释已删" "$(fnbody auth_add_message | grep -c '回退路径')" "0"
ck "  · 旧 body 已删(无 jq)" "$(fnbody auth_add_message | grep -c 'jq ')" "0"
ck "  · 只剩一次 py_run" "$(fnbody auth_add_message | grep -c 'py_run msg auth_add')" "1"

echo "=== 不可用降级(不追加 + stderr 明示) ==="
am "$W/base" disabled 'WCHAT_PY_MSG=0' hello '' '' '' '' > "$W/out" 2>/dev/null
cksame "add_message off → messages 原样不动" "$W/out" "$W/base"
au "$W/ab1" 'WCHAT_PY_MSG=0' cmd1 user 20 > "$W/out" 2>/dev/null
cksame "auth_add_message off → auth_message 原样不动" "$W/out" "$W/ab1"
warn=$(env WCHAT_PY_MSG=0 WCHAT_PY=1 "$BASHX" -c "
set +u; cd '$W'
_load() { _WCHAT_LIB_ONLY=1 source '$REAL' >/dev/null 2>&1; }
_load; trap - EXIT
thinking=disabled
messages='[{\"role\":\"system\",\"content\":\"s\"}]'
add_message hello
" _ 2>&1 >/dev/null)
ck "add_message off → stderr 有明示" "$([[ "$warn" == *"组件不可用"* ]] && echo yes || echo no)" "yes"

finish