#!/usr/bin/env bash
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/py_prelude.sh" "${1:-wchat_ref}" || exit 2

# 本闸门钉:语义锚点 + 函数体内结构锁 + 不可用降级(明示 + 不落盘 / 原样保留)。
# 收口后已无 bash jq 回退,故不再做 parity,改钉"降级行为本身"。
echo "=== V history pack / drop_orphan_tools(已收口) ==="
RLDIR="$(dirname "$REAL")"
pyrun() { python3 "$RLDIR/wchat_py/_run.py" history "$@"; }
# 装载子壳:PY_HELPER_DIR 默认取 wchat_ref 所在目录(=仓库根),故**不要**设 WCHAT_PY_DIR
# (设成 .../wchat_py 会让守卫找不到 wchat_py/_run.py 而把 WCHAT_PY 强制清零)。
child() {  # <额外env> <片段>
    local extra="$1" body="$2"
    env $extra WCHAT_PY=1 "$BASHX" -c "
set +u
cd '$W'
_WCHAT_LIB_ONLY=1 source '$REAL' >/dev/null 2>&1 || exit 9
$body"
}

# ---------- 语义锚点:pack ----------
printf '%s' '[{"role":"system","content":"hello M1 skill M2 auto M3 mcp tail"},{"role":"user","content":"hi"}]' > "$W/hp1"
printf '%s' '[{"role":"system","content":"no marks here"},{"role":"user","content":"hi"}]' > "$W/hp2"
hp() { pyrun pack "$1" "${2:-disabled}" M1 M2 M3; }   # <model> <thinking>
ck "pack 语义:剥掉注入段"        "$(hp "" < "$W/hp1" | jq -r '.messages[0].content')" "hello "
ck "pack 语义:无标记原样"        "$(hp "" < "$W/hp2" | jq -r '.messages[0].content')" "no marks here"
ck "pack 语义:thinking 字段"     "$(hp "" disabled < "$W/hp1" | jq -c '.thinking')" '{"type":"disabled"}'
ck "pack 语义:model 透传"        "$(hp deepseek-x < "$W/hp1" | jq -r '.model')" "deepseek-x"
ck "pack 语义:messages 条数不变" "$(hp "" < "$W/hp1" | jq '.messages | length')" "2"
ck "pack 语义:顶层固定字段"      "$(hp "" < "$W/hp1" | jq -c 'del(.messages,.model,.thinking)')" '{"reasoning_effort":"high","stream":true}'
ck "pack 语义:空标记不误切"      "$(pyrun pack "" disabled '' '' '' < "$W/hp1" | jq -r '.messages[0].content')" "hello M1 skill M2 auto M3 mcp tail"

# ---------- 语义锚点:drop_orphan_tools ----------
printf '%s' '[{"role":"user","content":"x"},{"role":"tool","content":"r","tool_call_id":"t1"}]' > "$W/hd1"
printf '%s' '[{"role":"assistant","content":null,"tool_calls":[{"id":"t1","type":"function","function":{"name":"f","arguments":"{}"}}]},{"role":"tool","content":"r","tool_call_id":"t1"}]' > "$W/hd2"
printf '%s' '[{"role":"assistant","content":null,"tool_calls":[{"id":"t1","type":"function","function":{"name":"f","arguments":"{}"}}]},{"role":"tool","content":"r","tool_call_id":"t9"}]' > "$W/hd3"
printf '%s' '[{"role":"user","content":"a"}]' > "$W/hd4"
dot() { pyrun drop_orphan_tools; }
ck "drop_orphan:孤儿剔除"       "$(dot < "$W/hd1" | jq 'length')" "1"
ck "drop_orphan:配对保留"       "$(dot < "$W/hd2" | jq 'length')" "2"
ck "drop_orphan:不匹配剔除"     "$(dot < "$W/hd3" | jq 'length')" "1"
ck "drop_orphan:无 tool 原样"   "$(dot < "$W/hd4" | jq -c .)" "$(jq -c . "$W/hd4")"
ck "drop_orphan:空数组"         "$(printf '[]' | dot | jq 'length')" "0"
ck "drop_orphan:非 tool 全留"   "$(printf '%s' '[{"role":"tool","content":"r","tool_call_id":"t1"},{"role":"user","content":"z"}]' | dot | jq -c '[.[].role]')" '["user"]'

# ---------- 结构锁:函数体内无 bash jq 回退,且有降级明示 ----------
echo "--- 结构锁 ---"
sh_body() { awk -v fn="^$1()" '$0 ~ fn {f=1} f {print} f && /^}/ {exit}' "$REAL"; }
sh_body save_history > "$W/save.body"
sh_body get_history  > "$W/get.body"
ck "save_history 无 pack jq 回退"     "$(grep -c 'jq --arg mark' "$W/save.body")" "0"
ck "save_history 有降级明示"          "$(grep -c '\[save_history\] 组件不可用' "$W/save.body")" "1"
ck "get_history 无 drop_orphan jq 回退" "$(grep -c 'reduce .\[\] as \$m' "$W/get.body")" "0"
ck "get_history 有降级明示"           "$(grep -c '\[get_history\] 组件不可用' "$W/get.body")" "1"

# ---------- 降级:组件不可用(WCHAT_PY_HISTORY=0) ----------
echo "--- 不可用降级 ---"
mkdir -p "$W/hist"
printf 'PRE\n' > "$W/hist/t.json"
child 'WCHAT_PY_HISTORY=0' "
HISTORY_DIR='$W/hist'; chat_title='t'; forget_history=0
messages='[{\"role\":\"user\",\"content\":\"hi\"}]'
save_history 2>&1 >/dev/null
printf 'LAST=[%s]\n' \"\$last_saved_history\"
" > "$W/sd.out" 2>&1
ck "save_history 降级:stderr 有明示"      "$(grep -q '\[save_history\] 组件不可用' "$W/sd.out" && echo yes || echo no)" "yes"
ck "save_history 降级:last_saved_history 空" "$(grep -q 'LAST=\[\]' "$W/sd.out" && echo yes || echo no)" "yes"
ck "save_history 降级:已存在文件未被覆盖"  "$(cat "$W/hist/t.json")" "PRE"

printf '%s' '{"model":"m","messages":[{"role":"tool","content":"orphan","tool_call_id":"tX"},{"role":"user","content":"z"}]}' > "$W/hist/g.json"
child 'WCHAT_PY_HISTORY=0' "
get_history '$W/hist/g.json' 2>&1 >/dev/null
printf 'MSGS=%s\n' \"\$(printf '%s' \"\$messages\" | jq -c .)\"
" > "$W/gd.out" 2>&1
ck "get_history 降级:stderr 有明示"        "$(grep -q '\[get_history\] 组件不可用' "$W/gd.out" && echo yes || echo no)" "yes"
ck "get_history 降级:消息原样保留(孤儿仍在)" "$(grep -q 'orphan' "$W/gd.out" && echo yes || echo no)" "yes"
ck "get_history 降级:条数=2"              "$(sed -n 's/^MSGS=//p' "$W/gd.out" | jq 'length')" "2"

# ---------- 控制组:组件可用时降级路径不触发 ----------
echo "--- 正常控制组 ---"
mkdir -p "$W/hist2"
child '' "
HISTORY_DIR='$W/hist2'; chat_title='t2'; forget_history=0; model='m1'; thinking='disabled'
SKILLS_MARK='M1'; AUTO_MARK='M2'; MCP_MARK='M3'
messages='[{\"role\":\"system\",\"content\":\"hi M1 tail\"},{\"role\":\"user\",\"content\":\"q\"}]'
save_history >/dev/null 2>&1
printf 'LAST=[%s]\n' \"\$last_saved_history\"
" > "$W/so.out" 2>&1
ck "save_history 正常:回传真实路径" "$(grep -q "LAST=\[$W/hist2/t2.json\]" "$W/so.out" && echo yes || echo no)" "yes"
ck "save_history 正常:落盘含 model" "$(jq -r '.model' "$W/hist2/t2.json")" "m1"
ck "save_history 正常:落盘已剥注入段" "$(jq -r '.messages[0].content' "$W/hist2/t2.json")" "hi "

child '' "
get_history '$W/hist/g.json' 2>&1 >/dev/null
printf 'MSGS=%s\n' \"\$(printf '%s' \"\$messages\" | jq -c .)\"
" > "$W/go.out" 2>&1
ck "get_history 正常:孤儿被剔(条数=1)" "$(sed -n 's/^MSGS=//p' "$W/go.out" | jq 'length')" "1"

finish

