#!/usr/bin/env bash
# pathrule(W12-2)的 Python 路径闸门(**收口后**)。
#
# 收口后 `hard_deny_path` 已迁 `wchat_py/pathrule.py`(旧 bash 全量回退已删)。
# 钉:语义锚点 + 结构锁 + 不可用降级(fail-closed 粗判据)。
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/py_prelude.sh" "${1:-wchat.sh}" || exit 2

R="$W/root"; H="$W/home"
mkdir -p "$R/dir" "$H/.ssh" "$H/.config" "$W/outside"
ln -s "$W/outside" "$R/link"          # 目标存在:norm_path 会解析
ln -s "$W/outside/missing" "$R/linkd" # 悬空链接:走 LINK 分支

fnbody() { awk -v f="$1" '$0 ~ "^"f"\\(\\) \\{" {p=1} p {print} p && /^}$/ {exit}' "$SRC"; }
# 直接调 python(py 侧语义锚点)
prule() {  # <path> <mode> <auto_mode> <allow_rw(空格分隔)>
    local subs tmp alw
    subs=$(printf '.ssh\n.aws'); tmp=$(printf '/tmp\n/var/tmp')
    alw=$(printf '%s' "$4" | tr ' ' '\n')
    printf '%s' "$1" | python3 "$RLDIR/wchat_py/_run.py" pathrule hard_deny_path \
        "$2" "$3" "$H" "$R" "$subs" "$tmp" "$alw"
}
rule() { prule "$1" "$2" "$3" "$4" | jq -r '.rule'; }

echo "=== hard_deny_path 语义(收口后) ==="
ck "根内写 → 放行"          "$(rule "$R/dir/f" rw '' '')" ""
ck "系统路径 → P2"          "$(rule /etc/hosts rw '' '')" "P2"
ck "只读系统路径不判"        "$(rule /etc/hosts r '' '')" ""
ck "HOME 凭据目录 → P1"     "$(rule "$H/.ssh/id_rsa" r '' '')" "P1"
ck "HOME 凭据文件 → P1"     "$(rule "$H/.npmrc" r '' '')" "P1"
ck "根内 .npmrc 不判"        "$(rule "$R/.npmrc" r '' '')" ""
ck "悬空链接穿越 → LINK"     "$(rule "$R/linkd" rw '' '')" "LINK"
ck "tmp 放行"               "$(rule /tmp/x rw '' '')" ""
ck "/dev/null 放行"          "$(rule /dev/null rw '' '')" ""
ck "HOME 隐藏配置 → P2"     "$(rule "$H/.config/x" rw '' '')" "P2"
ck "root → P2"              "$(rule /root/x rw '' '')" "P2"
ck "越界写 sandbox → P3"     "$(rule /wchat-fx/out.txt rw sandbox '')" "P3"
ck "越界写 full 无名单 → P3" "$(rule /wchat-fx/out.txt rw full '')" "P3"
ck "越界写 full+名单 → 放行" "$(rule /wchat-fx/out.txt rw full '/wchat-fx/out.txt')" ""
ck "他人家凭据 → P1"         "$(rule /home/bob/.aws/credentials r '' '')" "P1"
ck "P1 文案非空"             "$(prule "$H/.ssh/id_rsa" r '' '' | jq -r '.msg' | wc -c | tr -d ' ' | awk '{print ($1>1)?"y":"n"}')" "y"

echo "=== 收口结构锁(只查函数体) ==="
ck "hard_deny_path 定义 1 处" "$(grep -c '^hard_deny_path() {' "$SRC")" "1"
ck "  · 旧实现注释已删"       "$(fnbody hard_deny_path | grep -c 'bash 旧实现')" "0"
ck "  · 只剩一次 py_run"      "$(fnbody hard_deny_path | grep -c 'py_run pathrule hard_deny_path')" "1"
ck "  · 有粗判据降级"         "$(fnbody hard_deny_path | grep -c '降级为粗判据')" "3"

echo "=== 不可用降级(fail-closed 粗判据) ==="
dq() {  # <path> <mode>  -> "rc|rule"
    env WCHAT_PY_PATHRULE=0 WCHAT_PY=1 "$BASHX" -c "
set +u; cd '$W'
_load() { _WCHAT_LIB_ONLY=1 source '$REAL' >/dev/null 2>&1; }
_load; trap - EXIT
WORK_ROOT='$R'; HOME='$H'; auto_mode=''; auto_allow_ro=(); auto_allow_rw=()
hd_cred_subdirs=(.ssh .aws); auto_tmp_prefixes=(/tmp)
if hard_deny_path \"\$1\" \"\$2\"; then printf 'rc=0|%s' \"\$hd_rule\"; else printf 'rc=1|%s' \"\$hd_rule\"; fi
" _ "$1" "$2" 2>/dev/null
}
ck "降级:凭据目录仍拦 P1"    "$(dq "$H/.ssh/id_rsa" r)" "rc=0|P1"
ck "降级:系统路径仍拦 P2"    "$(dq /etc/hosts rw)" "rc=0|P2"
ck "降级:HOME 顶层仍拦 P2"   "$(dq "$H/.bashrc" rw)" "rc=0|P2"
ck "降级:根内写放行(未归一)" "$(dq "$R/dir/f" rw)" "rc=1|"
ck "降级:tmp 放行"           "$(dq /tmp/x rw)" "rc=1|"

finish

