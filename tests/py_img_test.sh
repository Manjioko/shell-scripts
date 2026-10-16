#!/usr/bin/env bash
# img 模块的 Python 路径闸门(**收口后**)。
#
# 收口(方案铁律 3)后 `img_reject_wording` / `img_inline_sides` 已是**唯一活实现**。
# 本闸门钉:语义锚点 + 函数体内结构锁 + 不可用降级。
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/py_prelude.sh" "${1:-wchat.sh}" || exit 2

fnbody() { awk -v f="$1" '$0 ~ "^"f"\\(\\) \\{" {p=1} p {print} p && /^}$/ {exit}' "$SRC"; }

rw() {  # <text> [extra-env]
    local t="$1" extra="${2:-}"
    env $extra WCHAT_PY=1 "$BASHX" -c "
set +u; cd '$W'
_load() { _WCHAT_LIB_ONLY=1 source '$REAL' >/dev/null 2>&1; }
_load; trap - EXIT
if img_reject_wording \"\$1\"; then echo 0; else echo 1; fi
" _ "$t" 2>/dev/null
}
is2() {  # <max> <shrink> <ladder> [extra-env]
    local mx="$1" sh="$2" ld="$3" extra="${4:-}"
    env $extra WCHAT_PY=1 "$BASHX" -c "
set +u; cd '$W'
_load() { _WCHAT_LIB_ONLY=1 source '$REAL' >/dev/null 2>&1; }
_load; trap - EXIT
IMG_INLINE_MAX_SIDE='$mx'; IMG_INLINE_SHRINK='$sh'; IMG_INLINE_SHRINK_LADDER='$ld'
img_inline_sides
" 2>/dev/null
}

echo "=== img_reject_wording 语义(0=不收图) ==="
wcases=(
    'The error info is [Unexpected item type in content.]|0'
    'if content is list. item must be dict and key[type]|0'
    'The model does not support vision|0'
    'This image format is not supported by the decoder|1'
    'vision model not found|1'
    'geometry limit exceeded|1'
    'invalid api key|1'
    '图片不支持|0'
    'no vision capability|0'
    'lacks vision|0'
    '|1'
)
for c in "${wcases[@]}"; do
    t="${c%|*}"; want="${c##*|}"
    ck "reject_wording [${t:0:22}]" "$(rw "$t")" "$want"
done

echo "=== img_inline_sides 语义 ==="
ck "默认四档" "$(is2 2048 1 '1536 1024 768')" "2048 1536 1024 768"
ck "名单写乱仍严格递减" "$(is2 2048 1 '1024 1536 768')" "2048 1024 768"
ck "比首档大的档丢弃" "$(is2 2048 1 '3000 1536')" "2048 1536"
ck "shrink=0 只首档" "$(is2 2048 0 '1536 1024 768')" "2048"

echo "=== 收口结构锁:旧 bash 回退确已删除(只查函数体) ==="
ck "img_reject_wording 定义 1 处" "$(grep -c '^img_reject_wording() {' "$SRC")" "1"
ck "  · 回退注释已删" "$(fnbody img_reject_wording | grep -c '回退路径')" "0"
ck "  · 旧 body 已删(无 local t=)" "$(fnbody img_reject_wording | grep -c 'local t=')" "0"
ck "  · 只剩一次 py_run" "$(fnbody img_reject_wording | grep -c 'py_run img reject_wording')" "1"
ck "img_inline_sides 定义 1 处" "$(grep -c '^img_inline_sides() {' "$SRC")" "1"
ck "  · 回退注释已删" "$(fnbody img_inline_sides | grep -c '回退路径')" "0"
ck "  · 旧 body 已删(无 local out=)" "$(fnbody img_inline_sides | grep -c 'local out=')" "0"
ck "  · 只剩一次 py_run" "$(fnbody img_inline_sides | grep -c 'py_run img inline_sides')" "1"

echo "=== 不可用降级 ==="
ck "reject_wording off → 不判不收图(1)" "$(rw 'The model does not support vision' 'WCHAT_PY_IMG=0')" "1"
ck "inline_sides off → 输出首档" "$(is2 2048 1 '1536 1024 768' 'WCHAT_PY_IMG=0')" "2048"

finish

