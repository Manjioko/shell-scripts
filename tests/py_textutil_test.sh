#!/usr/bin/env bash
# textutil 模块的 Python 路径闸门(**收口后**)。
#
# 收口(方案铁律 3)后 `clip_line_width` / `truncate_output` 已是**唯一活实现**(旧 awk/bash
# 回退已删),不再有 py-vs-bash parity。本闸门钉:
#   ① 语义锚点(字节级);② **函数体内结构锁**;③ **不可用降级**(透传,不丢内容,不跑旧代码)。
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/py_prelude.sh" "${1:-wchat.sh}" || exit 2

fnbody() { awk -v f="$1" '$0 ~ "^"f"\\(\\) \\{" {p=1} p {print} p && /^}$/ {exit}' "$SRC"; }

echo "=== clip_line_width 语义 ==="
printf 'hello world' > "$W/in"
cw 1 "" 5 '…' "$W/in" "$W/py"; printf 'hello\xe2\x80\xa6\n' > "$W/exp"
cksame "ascii-clip = hello…" "$W/py" "$W/exp"
printf '中文测试' > "$W/in"
cw 1 "" 5 '…' "$W/in" "$W/py"; printf '中\xe2\x80\xa6\n' > "$W/exp"
cksame "cjk-boundary 不切半字 = 中…" "$W/py" "$W/exp"
printf '😀' > "$W/in"
cw 1 "" 3 '…' "$W/in" "$W/py"; printf '\xe2\x80\xa6\n' > "$W/exp"
cksame "emoji 放不下则整体让位 = …" "$W/py" "$W/exp"
python3 "$RLDIR/wchat_py/_run.py" textutil bogus >/dev/null 2>&1
ck "未知子命令退出码=1" "$?" "1"

echo "=== truncate_output 语义 ==="
printf '%b' '1\n2\n3\n4\n5\n6\n7\n8\n9\n' > "$W/in"
ct 1 2000 4 "$W/in" "$W/py"
printf '%b' '1\n2\n\n... (中间 5 行已省略) ...\n\n8\n9' > "$W/exp"
cksame "trunc 布局:头2 + 省略行 + 尾2" "$W/py" "$W/exp"

echo "=== 收口结构锁:旧实现确已删除(只查函数体) ==="
ck "clip_line_width 定义 1 处" "$(grep -c '^clip_line_width() {' "$SRC")" "1"
ck "  · 旧 awk 回退已删" "$(fnbody clip_line_width | grep -c 'LC_ALL=C awk')" "0"
ck "  · 回退注释已删" "$(fnbody clip_line_width | grep -c '回退路径')" "0"
ck "  · 只剩一次 py_run" "$(fnbody clip_line_width | grep -c 'py_run textutil clip_line_width')" "1"
ck "  · 不可用透传在" "$(fnbody clip_line_width | grep -c '{ cat; return 0; }')" "1"
ck "truncate_output 定义 1 处" "$(grep -c '^truncate_output() {' "$SRC")" "1"
ck "  · 旧实现(head -n/awk)已删" "$(fnbody truncate_output | grep -cE 'head -n|wc -l')" "0"
ck "  · 回退注释已删" "$(fnbody truncate_output | grep -c '回退路径')" "0"
ck "  · 只剩一次 py_run" "$(fnbody truncate_output | grep -c 'py_run textutil truncate_output')" "1"

echo "=== 不可用降级(透传,不丢内容) ==="
# clip:WCHAT_PY_TEXTUTIL=0 → cat 原样透传(不截断)
printf 'hello world' > "$W/in"
cw 1 "" 5 '…' "$W/in" "$W/py" "WCHAT_PY_TEXTUTIL=0"
printf 'hello world' > "$W/exp"
cksame "clip off → 原样透传" "$W/py" "$W/exp"
# truncate:WCHAT_PY_TEXTUTIL=0 → 原样透传
WCHAT_PY_TEXTUTIL=0 "$BASHX" -c "
set +u; cd '$W'
_load() { _WCHAT_LIB_ONLY=1 source '$REAL' >/dev/null 2>&1; }
_load; trap - EXIT
TOOL_OUTPUT_MAX_COL=2000; TOOL_OUTPUT_MAX_LINES=4
IFS= read -r -d '' _t < '$W/in'
truncate_output \"\$_t\"
" > "$W/py" 2>/dev/null
cksame "truncate off → 原样透传" "$W/py" "$W/in"

finish

