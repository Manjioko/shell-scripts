#!/usr/bin/env bash
# pathutil(W3)的 Python 路径闸门(**收口后**)。
#
# 收口(铁律 3)后 `expand_tilde` / `norm_path` / `resolve_path` 已是唯一活实现
# (旧 bash 回退已删)。本闸门钉:语义锚点 + 函数体内结构锁 + 不可用降级。
# (parity 期已由本文件的上一形态证明 py 与 bash 逐向量一致,并经全表 569 向量验证。)
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/py_prelude.sh" "${1:-wchat_ref}" || exit 2

R="$W/root"; H="$W/home"
mkdir -p "$R/d1/d2" "$H" "$W/outside"
ln -s "$W/outside" "$R/lnk"          # 指到根外的目录链接
printf 'x\n' > "$R/f.txt"

fnbody() { awk -v f="$1" '$0 ~ "^"f"\\(\\) \\{" {p=1} p {print} p && /^}$/ {exit}' "$SRC"; }
run1() {  # <pyflag> <fn> <p>  → "OK:<结果>" / "FAIL"
    env WCHAT_PY_PATHUTIL="$1" WCHAT_PY=1 "$BASHX" -c "
set +u; cd '$W'
_load() { _WCHAT_LIB_ONLY=1 source '$REAL' >/dev/null 2>&1; }
_load; trap - EXIT
WORK_ROOT='$R'; HOME='$H'
if o=\$($2 \"\$1\"); then printf 'OK:%s' \"\$o\"; else printf 'FAIL'; fi
" _ "$3" 2>/dev/null
}

echo "=== expand_tilde 语义 ==="
ck "~ → HOME"     "$(run1 1 expand_tilde '~')" "OK:$H"
ck "~/a → HOME/a" "$(run1 1 expand_tilde '~/a')" "OK:$H/a"
ck "/abs 原样"    "$(run1 1 expand_tilde '/abs')" "OK:/abs"
ck "rel 原样"     "$(run1 1 expand_tilde 'rel')" "OK:rel"
ck "空 原样"      "$(run1 1 expand_tilde '')" "OK:"
ck "~user 不展开" "$(run1 1 expand_tilde '~user')" "OK:~user"

echo "=== norm_path 语义(只归一,不设根边界)==="
ck "/ → /"                       "$(run1 1 norm_path '/')" "OK:/"
ck "空 → FAIL"                   "$(run1 1 norm_path '')" "FAIL"
ck "相对 → 根下"                 "$(run1 1 norm_path 'd1')" "OK:$R/d1"
ck "含 /./ 归一"                 "$(run1 1 norm_path 'd1/./d2')" "OK:$R/d1/d2"
ck "尾斜杠目录归一"              "$(run1 1 norm_path 'd1/')" "OK:$R/d1"
ck "缺失父目录原样拼回"          "$(run1 1 norm_path 'new/x.txt')" "OK:$R/new/x.txt"
ck "缺失段含 .. → FAIL"          "$(run1 1 norm_path 'new/../y')" "FAIL"
ck ".. 上跳(存在目录)"           "$(run1 1 norm_path '..')" "OK:$W"
ck "越界照给(不设边界)"          "$(run1 1 norm_path '/etc/hosts')" "OK:/private/etc/hosts"
ck "~ 先展开"                    "$(run1 1 norm_path '~')" "OK:$H"

echo "=== resolve_path 语义(归一 + 强制根内)==="
ck "根内相对 → 绝对"             "$(run1 1 resolve_path 'd1')" "OK:$R/d1"
ck "缺失父目录仍给(在根内)"     "$(run1 1 resolve_path 'new/x.txt')" "OK:$R/new/x.txt"
ck "尾斜杠"                      "$(run1 1 resolve_path 'd1/')" "OK:$R/d1"
ck "缺 .. → FAIL"                "$(run1 1 resolve_path 'new/../y')" "FAIL"
ck ".. 逃逸 → FAIL"              "$(run1 1 resolve_path '../outside')" "FAIL"
ck "绝对越界 → FAIL"             "$(run1 1 resolve_path '/etc/hosts')" "FAIL"
ck "空 → FAIL"                   "$(run1 1 resolve_path '')" "FAIL"
ck "根内链接名本身保留"          "$(run1 1 resolve_path 'lnk')" "OK:$R/lnk"
ck "链接穿越 → FAIL"             "$(run1 1 resolve_path 'lnk/x')" "FAIL"
ck "HOME 在根外 → FAIL"          "$(run1 1 resolve_path '~')" "FAIL"

rce() {  # <pyflag> <work_root> <p>  → OK(豁免)/FAIL
    env WCHAT_PY=1 WCHAT_PY_PATHUTIL="$1" "$BASHX" -c "
set +u; cd '$W'
_load() { _WCHAT_LIB_ONLY=1 source '$REAL' >/dev/null 2>&1; }
_load; trap - EXIT
WORK_ROOT='$2'; HOME='$H'
in_root_exempt \"\$1\" && echo OK || echo FAIL
" _ "$3" 2>/dev/null
}

echo "=== in_root_exempt 语义(纯前缀判定)==="
ck "根内精确 → 豁免"        "$(rce 1 "$R" "$R")" "OK"
ck "根内子路径 → 豁免"      "$(rce 1 "$R" "$R/d1")" "OK"
ck "根外 → 不豁免"          "$(rce 1 "$R" "$W/outside")" "FAIL"
ck "空路径 → 不豁免"        "$(rce 1 "$R" "")" "FAIL"
ck "前缀相似目录不误判"     "$(rce 1 "$R" "${R}2")" "FAIL"
ck "根==HOME → 边界失效"    "$(rce 1 "$H" "$H/x")" "FAIL"
ck "根==/ → 边界失效"       "$(rce 1 "/" "/etc")" "FAIL"

echo "=== 收口结构锁:旧 bash 回退确已删除(只查函数体) ==="
for f in expand_tilde norm_path resolve_path in_root_exempt; do
    ck "$f 定义 1 处"        "$(grep -c "^$f() {" "$SRC")" "1"
    ck "  · 旧实现注释已删"   "$(fnbody "$f" | grep -c 'bash 旧实现')" "0"
    ck "  · 只剩一次 py_run"  "$(fnbody "$f" | grep -c "py_run pathutil $f")" "1"
done

echo "=== 不可用降级(复刻\"函数缺席\"行为) ==="
ck "expand_tilde off → 原样透传" "$(run1 0 expand_tilde '~/x')" "OK:~/x"
ck "norm_path off → FAIL"        "$(run1 0 norm_path 'd1')" "FAIL"
ck "resolve_path off → FAIL"     "$(run1 0 resolve_path 'd1')" "FAIL"
ck "in_root_exempt off → 不豁免" "$(rce 0 "$R" "$R/d1")" "FAIL"

finish