#!/usr/bin/env bash
# vision 模块的 Python 路径闸门(**收口后**)。
#
# 收口(铁律 3)后 `vision_build_body` / `vision_parse_resp` 已是唯一活实现(旧 bash jq 回退已删)。
# 本闸门钉:语义锚点 + 函数体内结构锁 + 不可用降级。
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/py_prelude.sh" "${1:-wchat.sh}" || exit 2

RLDIR="$(dirname "$REAL")"
fnbody() { awk -v f="$1" '$0 ~ "^"f"\\(\\) \\{" {p=1} p {print} p && /^}$/ {exit}' "$SRC"; }
vb() { python3 "$RLDIR/wchat_py/_run.py" vision build_body "$@"; }
vp() { python3 "$RLDIR/wchat_py/_run.py" vision parse_resp; }
hcall() {  # <extra-env> <snippet>
    env $1 WCHAT_PY=1 "$BASHX" -c "
set +u; cd '$W'
_load() { _WCHAT_LIB_ONLY=1 source '$REAL' >/dev/null 2>&1; }
_load; trap - EXIT
$2"
}

printf '%s' '[{"type":"image_url","image_url":{"url":"data:image/png;base64,AAAA"}}]' > "$W/parts"

echo "=== vision_build_body 语义 ==="
ck "无 sys → 1 条消息"        "$(vb vm "$W/parts" p "" | jq '.messages|length')" "1"
ck "有 sys → 2 条消息"        "$(vb vm "$W/parts" p s | jq '.messages|length')" "2"
ck "sys 放在首位"             "$(vb vm "$W/parts" p SYS | jq -r '.messages[0].content')" "SYS"
ck "parts+text 拼接顺序"      "$(vb vm "$W/parts" p "" | jq -c '.messages[-1].content|map(.type)')" '["image_url","text"]'
ck "prompt 落到 text 部件"     "$(vb vm "$W/parts" 描述 "" | jq -r '.messages[-1].content[-1].text')" "描述"
ck "model 透传"               "$(vb m1 "$W/parts" p "" | jq -r '.model')" "m1"
ck "thinking 固定 disabled"   "$(vb vm "$W/parts" p "" | jq -c '.thinking')" '{"type":"disabled"}'
ck "stream=false"             "$(vb vm "$W/parts" p "" | jq -r '.stream')" "false"

echo "=== vision_parse_resp 语义 ==="
ck "字符串 content 直取"      "$(printf '%s' '{"choices":[{"message":{"content":"hello"}}]}' | vp)" "hello"
ck "数组 parts 拼接"          "$(printf '%s' '{"choices":[{"message":{"content":[{"text":"a"},{"text":"b"}]}}]}' | vp)" "ab"
ck "null → 空串"              "$(printf '%s' '{"choices":[{"message":{"content":null}}]}' | vp)" ""
ck "无 choices → 空串"        "$(printf '%s' '{}' | vp)" ""
ck "非法 JSON → 空串"         "$(printf '%s' 'not json' | vp)" ""
ck "非 text 部件只取 text"    "$(printf '%s' '{"choices":[{"message":{"content":[{"foo":"x"},{"text":"y"}]}}]}' | vp)" "y"
ck "未知子命令 rc=1"          "$(python3 "$RLDIR/wchat_py/_run.py" vision nope >/dev/null 2>&1; echo $?)" "1"

echo "=== 收口结构锁:旧 bash jq 回退确已删除(只查函数体) ==="
for f in vision_build_body vision_parse_resp; do
    ck "$f 定义 1 处"       "$(grep -c "^$f() {" "$SRC")" "1"
    ck "  · 回退注释已删"    "$(fnbody "$f" | grep -c '回退路径')" "0"
    ck "  · 只剩一次 py_run" "$(fnbody "$f" | grep -c "py_run vision ")" "1"
    ck "  · 不再调 jq"       "$(fnbody "$f" | grep -c 'jq -')" "0"
done

echo "=== 不可用降级(明示,不产半截) ==="
ck "build_body off → rc=1 空"  "$(hcall 'WCHAT_PY_VISION=0' 'o=$(vision_build_body m "'$W'/parts" p ""); rc=$?; printf "[%s]rc=%s" "$o" "$rc"')" "[]rc=1"
ck "parse_resp off → 空串"     "$(hcall 'WCHAT_PY_VISION=0' 'printf "[%s]" "$(vision_parse_resp "{\"choices\":[{\"message\":{\"content\":\"x\"}}]}")"')" "[]"

finish