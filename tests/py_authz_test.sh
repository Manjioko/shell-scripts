#!/usr/bin/env bash
# authz(W12-3)的 Python 路径闸门(**收口后**)。
#
# 收口后 `is_granted` / `auto_allow_hit` 已迁 `wchat_py/authz.py`(旧 bash 回退已删)。
# 钉:语义锚点 + 结构锁 + 不可用降级(fail-closed:未授权 / 不在名单)。
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/py_prelude.sh" "${1:-wchat.sh}" || exit 2

R="$W/root"; H="$W/home"
mkdir -p "$R/dir/sub" "$H" "$W/out"

fnbody() { awk -v f="$1" '$0 ~ "^"f"\\(\\) \\{" {p=1} p {print} p && /^}$/ {exit}' "$SRC"; }
ig() { printf '%s' "$1" | python3 "$RLDIR/wchat_py/_run.py" authz is_granted "$2" "$H" "$R" "$3" >/dev/null 2>&1; echo $?; }
ah() { printf '%s' "$1" | python3 "$RLDIR/wchat_py/_run.py" authz auto_allow_hit "$2" "$H" "$R" "$3" "$4" >/dev/null 2>&1; echo $?; }
dgn() {  # <fnsnippet>
    env WCHAT_PY_AUTHZ=0 WCHAT_PY=1 "$BASHX" -c "
set +u; cd '$W'
_load() { _WCHAT_LIB_ONLY=1 source '$REAL' >/dev/null 2>&1; }
_load; trap - EXIT
WORK_ROOT='$R'; HOME='$H'; granted_files=('rw|$R/f'); auto_allow_ro=('$W/out'); auto_allow_rw=('$W/out')
$1
" 2>/dev/null
}

echo "=== is_granted 语义(收口后) ==="
ck "空名单 → 不授"           "$(ig "$R/f" r "")" "1"
ck "精确命中 → 授"           "$(ig "$R/f" r "r|$R/f")" "0"
ck "模式不符 → 不授"         "$(ig "$R/f" rw "r|$R/f")" "1"
ck "rw 覆盖 r → 授"          "$(ig "$R/f" r "rw|$R/f")" "0"
ck "子树命中 → 授"           "$(ig "$R/dir/sub/f" r "r|$R/dir")" "0"
ck "非存在目录不子树 → 不授" "$(ig "$R/nodir/f" r "r|$R/nodir")" "1"
ck "他路径 → 不授"           "$(ig "$W/out/f" r "r|$R/f")" "1"

echo "=== auto_allow_hit 语义(收口后) ==="
ck "rw 名单命中"             "$(ah "$W/out/f" rw "" "$W/out")" "0"
ck "rw 不看 ro"              "$(ah "$W/out/f" rw "$W/out" "")" "1"
ck "r 看 ro"                 "$(ah "$W/out/f" r "$W/out" "")" "0"
ck "r 看 rw"                 "$(ah "$W/out/f" r "" "$W/out")" "0"
ck "不在名单 → 不中"         "$(ah "$W/else/f" rw "" "$W/out")" "1"

echo "=== 收口结构锁(只查函数体) ==="
for f in is_granted auto_allow_hit; do
    ck "$f 定义 1 处"         "$(grep -c "^$f() {" "$SRC")" "1"
    ck "  · 旧实现注释已删"    "$(fnbody "$f" | grep -c 'bash 旧实现')" "0"
    ck "  · 只剩一次 py_run"   "$(fnbody "$f" | grep -c "py_run authz $f")" "1"
done

echo "=== 不可用降级(fail-closed) ==="
ck "is_granted off → 未授权"    "$(dgn 'is_granted "'$R'/f" r; echo rc=$?')" "rc=1"
ck "auto_allow_hit off → 不在名单" "$(dgn 'auto_allow_hit "'$W'/out/f" rw; echo rc=$?')" "rc=1"

finish

