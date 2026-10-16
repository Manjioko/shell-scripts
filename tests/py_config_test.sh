#!/usr/bin/env bash
# W12-1 配置打包闸门:bash 侧 `py_cfg_dump` 导出的 JSON 必须与 schema 一致、值透传正确。
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/py_prelude.sh" "${1:-wchat.sh}" || exit 2

RLDIR="$(dirname "$REAL")"
fnbody() { awk -v f="$1" '$0 ~ "^"f"\\(\\) \\{" {p=1} p {print} p && /^}$/ {exit}' "$SRC"; }
cfg() {  # <extra-snippet(可空)> -> 配置 JSON
    WCHAT_PY=1 "$BASHX" -c "
set +u; cd '$W'
_load() { _WCHAT_LIB_ONLY=1 source '$REAL' >/dev/null 2>&1; }
_load; trap - EXIT
WORK_ROOT='$W/root'; HOME='$W/home'; CT_HOME='$W/.ct'; HISTORY_DIR='$W/hist'
AUTO_DIR='$W/.ct/auto'; AUTO_AUDIT_LOG='$W/.ct/auto/audit.log'; AUTO_BACKUP_DIR='$W/.ct/auto/backup'
hd_cred_subdirs=(.ssh .aws .kube); auto_tmp_prefixes=(/tmp /var/tmp)
$1
py_cfg_dump
" 2>/dev/null
}
pkeys() { python3 "$RLDIR/wchat_py/_run.py" config "$@"; }

echo "=== 导出 / 校验 ==="
cfg '' > "$W/cfg.json"
ck "dump 是合法 JSON"        "$(jq -e . "$W/cfg.json" >/dev/null 2>&1 && echo y || echo n)" "y"
ck "validate 通过(rc=0)"     "$(python3 "$RLDIR/wchat_py/_run.py" config validate < "$W/cfg.json" >/dev/null 2>&1; echo $?)" "0"
ck "键集合与 schema 一致(不漏)" "$(jq -r 'keys[]' "$W/cfg.json" | sort | tr '\n' ' ')" \
                              "$({ pkeys keys scalar; pkeys keys array; } | sort | tr '\n' ' ')"

echo "=== 值透传 ==="
ck "WORK_ROOT 透传"          "$(jq -r '.WORK_ROOT' "$W/cfg.json")" "$W/root"
ck "HOME 透传"               "$(jq -r '.HOME' "$W/cfg.json")" "$W/home"
ck "hd_re_sys_redirect 非空含 /etc" "$(jq -r '.hd_re_sys_redirect' "$W/cfg.json" | grep -c '/etc')" "1"
ck "hd_re_write_verb 非空"    "$([[ -n "$(jq -r '.hd_re_write_verb' "$W/cfg.json")" ]] && echo y)" "y"
ck "hd_h11_sys_prefixes 非空" "$([[ -n "$(jq -r '.hd_h11_sys_prefixes' "$W/cfg.json")" ]] && echo y)" "y"
ck "hd_cred_subdirs 数组"     "$(jq -c '.hd_cred_subdirs' "$W/cfg.json")" '[".ssh",".aws",".kube"]'
ck "auto_tmp_prefixes 数组"   "$(jq -c '.auto_tmp_prefixes' "$W/cfg.json")" '["/tmp","/var/tmp"]'
ck "空数组编码为 []"          "$(cfg 'hd_cred_subdirs=()' | jq -c '.hd_cred_subdirs')" "[]"

echo "=== 负例 / 结构锁 ==="
ck "缺键 → validate rc=1"     "$(jq 'del(.WORK_ROOT)' "$W/cfg.json" | python3 "$RLDIR/wchat_py/_run.py" config validate >/dev/null 2>&1; echo $?)" "1"
ck "多键 → validate rc=1"     "$(jq '.zzz="x"' "$W/cfg.json" | python3 "$RLDIR/wchat_py/_run.py" config validate >/dev/null 2>&1; echo $?)" "1"
# schema 与 wchat.sh 实际的 hd_re_* 顶层赋值必须一一对应(新增正则忘了登记 = 假绿)
ck "hd_re_* schema 与脚本一致" "$(pkeys keys scalar | grep '^hd_re_' | sort | tr '\n' ' ')" \
                              "$(grep -oE '^hd_re_[a-z0-9_]+=' "$SRC" | sed 's/=$//' | sort | tr '\n' ' ')"
ck "py_cfg_dump 定义 1 处"    "$(grep -c '^py_cfg_dump() {' "$SRC")" "1"
ck "_cfg_arr_json 定义 1 处"  "$(grep -c '^_cfg_arr_json() {' "$SRC")" "1"

finish