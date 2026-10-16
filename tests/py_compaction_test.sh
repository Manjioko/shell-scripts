#!/usr/bin/env bash
# compaction 模块的 Python 路径闸门(**收口后**)。
#
# 收口(方案铁律 3)后 `compact_due` / `compact_apply` 已是**唯一活实现**。
# (`summary_pin_count` 未收口——它与 `$_JQ_CONTENT_TEXT` 片段强耦合,另有压缩探针守着。)
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/py_prelude.sh" "${1:-wchat.sh}" || exit 2

fnbody() { awk -v f="$1" '$0 ~ "^"f"\\(\\) \\{" {p=1} p {print} p && /^}$/ {exit}' "$SRC"; }

due() {  # <t> <g> [extra-env]
    local t="$1" g="$2" extra="${3:-}"
    env $extra WCHAT_PY=1 "$BASHX" -c "
set +u; cd '$W'
_load() { _WCHAT_LIB_ONLY=1 source '$REAL' >/dev/null 2>&1; }
_load; trap - EXIT
TOKEN_THRESHOLD='$g'
if compact_due '$t'; then echo 压; else echo 不压; fi
" 2>/dev/null
}
ap() {  # <pin> <summary> <cut> <msgsfile> <outfile> [extra-env]
    local pin="$1" sum="$2" cut="$3" inf="$4" outf="$5" extra="${6:-}"
    env $extra WCHAT_PY=1 "$BASHX" -c "
set +u; cd '$W'
_load() { _WCHAT_LIB_ONLY=1 source '$REAL' >/dev/null 2>&1; }
_load; trap - EXIT
SUMMARY_PREFIX='__SUM__'
IFS= read -r -d '' messages < '$inf'
compact_apply \"\$1\" \"\$2\" \"\$3\"
printf '%s' \"\$messages\"
" _ "$pin" "$sum" "$cut" > "$outf" 2>/dev/null
}

echo "=== compact_due 语义 ==="
ck "t=0 不压" "$(due 0 100000)" "不压"
ck "阈值下一格不压" "$(due 99999 100000)" "不压"
ck "正好等于阈值不压(严格 >)" "$(due 100000 100000)" "不压"
ck "超一格就压" "$(due 100001 100000)" "压"
ck "阈值 0 不压" "$(due 500000 0)" "不压"
ck "t 非数字不压" "$(due abc 100000)" "不压"

echo "=== compact_apply 语义 ==="
printf '%s' '[{"role":"system","content":"s"},{"role":"user","content":"__SUM__\n旧1"},{"role":"user","content":"__SUM__\n旧2"},{"role":"user","content":"普通"},{"role":"user","content":"尾"}]' > "$W/msgs"
ap 2 "新摘要正文" 4 "$W/msgs" "$W/out"
ck "成功路径:总条数 1+2+1+1" "$(jq 'length' "$W/out")" "5"
ck "成功路径:system 在首位" "$(jq -r '.[0].role' "$W/out")" "system"
ck "成功路径:旧摘要逐字保留" "$(jq -r '.[1].content' "$W/out")" $'__SUM__\n旧1'
ck "成功路径:新摘要末行" "$(jq -r '.[3].content' "$W/out" | tail -1)" "新摘要正文"
ap 2 "" 4 "$W/msgs" "$W/out"
ck "失败路径(摘要空):旧摘要不丢,条数 4" "$(jq 'length' "$W/out")" "4"
ap 9 "越界钳制" 3 "$W/msgs" "$W/out"
ck "pin 越界后旧摘要没被数两遍" "$(jq -r '.[1].content' "$W/out" | grep -o '旧1' | wc -l | tr -d ' ')" "1"

echo "=== 收口结构锁:旧 bash 回退确已删除(只查函数体) ==="
ck "compact_due 定义 1 处" "$(grep -c '^compact_due() {' "$SRC")" "1"
ck "  · 回退注释已删" "$(fnbody compact_due | grep -c '回退路径')" "0"
ck "  · 旧 body 已删(无 local t=…g=)" "$(fnbody compact_due | grep -c 'TOKEN_THRESHOLD:-0}$')" "0"
ck "  · 只剩一次 py_run" "$(fnbody compact_due | grep -c 'py_run compaction due')" "1"
ck "compact_apply 定义 1 处" "$(grep -c '^compact_apply() {' "$SRC")" "1"
ck "  · 回退注释已删" "$(fnbody compact_apply | grep -c '回退路径')" "0"
ck "  · 旧 body 已删(无 mktemp ct_jq)" "$(fnbody compact_apply | grep -c 'ct_jq_')" "0"
ck "  · 只剩一次 py_run" "$(fnbody compact_apply | grep -c 'py_run compaction apply')" "1"

echo "=== 不可用降级 ==="
ck "compact_due off → 不压" "$(due 500000 100000 'WCHAT_PY_COMPACTION=0')" "不压"
ap 2 "新摘要正文" 4 "$W/msgs" "$W/out" "WCHAT_PY_COMPACTION=0"
cksame "compact_apply off → messages 原样不动" "$W/out" "$W/msgs"

finish

