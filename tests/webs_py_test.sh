#!/usr/bin/env bash
# webs 模块的 Python 路径闸门(**收口后**)。
#
# 收口(方案铁律 3)后 `web_search_build_body` / `fetch_error` 已是**唯一活实现**,旧 bash
# 回退已删,所以不再有"py vs bash parity"。这道闸门改为钉三件事:
#   ① 语义锚点:输出对不对;
#   ② **结构锁**:旧 bash 回退确已从这个函数里删除(只查该函数体,不查全文件 ——
#      别的模块还处在并存期,全文件计数会误红);
#   ③ **不可用降级**:WCHAT_PY_WEBS=0 / 组件不可用时 `build_body` 返回 1(该功能不可用),
#      绝不静默成功。
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
SRC="${1:-wchat_ref}"
[[ -f "$SRC" ]] || { echo "找不到 $SRC"; exit 2; }
SRC=$(cd "$(dirname "$SRC")" && pwd -P)/$(basename "$SRC")
BASHX="${BASH:-bash}"
if [[ "${BASH_VERSINFO[0]:-0}" -lt 5 ]]; then
    for b in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        [[ -x "$b" ]] && BASHX="$b" && break
    done
fi
pass=0; fail=0
ck() { if [[ "$2" == "$3" ]]; then echo "  ✓ $1"; pass=$((pass + 1)); else echo "  FAIL ✗ $1"; echo "      期望 [$3] / 实际 [$2]"; fail=$((fail + 1)); fi; }
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
export HOME="$W/home"; mkdir -p "$HOME" "$W/tmp"
export TMPDIR="$W/tmp"
export WCHAT_API_KEY="${WCHAT_API_KEY:-sk-stub}"
export BASE_URL="${BASE_URL:-https://stub.invalid/v1}"
RLDIR="$(dirname "$SRC")"

fnbody() { awk -v f="$1" '$0 ~ "^"f"\\(\\) \\{" {p=1} p {print} p && /^}$/ {exit}' "$SRC"; }

wsb() { python3 "$RLDIR/wchat_py/_run.py" webs build_body "$1" "$2" "$3"; }
wfe() { python3 "$RLDIR/wchat_py/_run.py" webs fetch_error "$1" "$2"; }

echo "=== web_search_build_body 语义 ==="
ck "mimo → tools/web_search" "$(wsb mimo q m | jq -r '.tools[0].type')" "web_search"
ck "qwen → 关思考开联网" "$(wsb qwen q m | jq -c '{es:.enable_search,et:.enable_thinking}')" '{"es":true,"et":false}'
ck "未知 provider rc=1" "$(wsb nope q m >/dev/null 2>&1; echo $?)" "1"

echo "=== fetch_error 语义 ==="
ck "000 → 无法连接" "$(wfe 000 http://x/ | grep -c 无法连接)" "1"
ck "403 → 被拒绝/限流" "$(wfe 403 http://x/ | grep -c '被站点拒绝/限流')" "1"
ck "500 → 服务器错误" "$(wfe 500 http://x/ | grep -c 服务器错误)" "1"
ck "200 → 空响应" "$(wfe 200 http://x/ | grep -c 空响应)" "1"

echo "=== 收口结构锁:旧 bash 回退确已删除(只查该函数体) ==="
ck "web_search_build_body 定义恰好 1 处" "$(grep -c '^web_search_build_body() {' "$SRC")" "1"
ck "  · 旧 provider case 已删" "$(fnbody web_search_build_body | grep -c 'case \"\$WEB_SEARCH_PROVIDER\" in')" "0"
ck "  · 旧回退注释已删" "$(fnbody web_search_build_body | grep -c '回退路径')" "0"
ck "  · 只剩一行 py_run" "$(fnbody web_search_build_body | grep -c 'py_run webs build_body')" "1"
ck "tool_web_fetch 旧 http_code case 已删" "$(fnbody tool_web_fetch | grep -c 'case \"\$http_code\" in')" "0"
ck "  · 仍有 fetch_error 调用" "$(fnbody tool_web_fetch | grep -c 'py_run webs fetch_error')" "1"

echo "=== 不可用降级(不静默) ==="
run_build() {  # <WCHAT_PY_WEBS>
    WCHAT_PY_WEBS="$1" "$BASHX" -c "
set +u; cd '$W'
_load() { _WCHAT_LIB_ONLY=1 source '$SRC' >/dev/null 2>&1; }
_load; trap - EXIT
WEB_SEARCH_PROVIDER=mimo
web_search_build_body q m >/dev/null 2>&1; echo \$?
" 2>/dev/null
}
ck "WCHAT_PY_WEBS=0 → rc=1(不可用,明示)" "$(run_build 0)" "1"
ck "默认 → rc=0(可用)" "$(run_build 1)" "0"

echo "RESULT total=$((pass + fail)) pass=$pass fail=$fail"
[[ "$fail" -eq 0 ]]

