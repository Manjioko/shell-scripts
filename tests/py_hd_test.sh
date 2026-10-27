#!/usr/bin/env bash
# hd 模块的 Python 路径闸门(**收口后**)。
#
# 收口(方案铁律 3)后 8 个已路由函数(分词/脱敏/折叠/他人家凭据/H11 落点/剥注释)
# 已是唯一活实现(bash 回退已删)。本闸门钉:语义锚点 + 函数体内结构锁 + 不可用降级。
# 降级一律复刻"函数整体缺席"时的既有行为,保证**不新增放行面**。
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/py_prelude.sh" "${1:-wchat_ref}" || exit 2

RLDIR="$(dirname "$REAL")"
fnbody() { awk -v f="$1" '$0 ~ "^"f"\\(\\) \\{" {p=1} p {print} p && /^}$/ {exit}' "$SRC"; }
hpy() { python3 "$RLDIR/wchat_py/_run.py" hd "$@"; }
m() { printf '%s' "$2" | hpy "$1"; }     # stdin -> stdout
hcall() {  # <extra-env> <snippet>
    env $1 WCHAT_PY=1 "$BASHX" -c "
set +u; cd '$W'
_load() { _WCHAT_LIB_ONLY=1 source '$REAL' >/dev/null 2>&1; }
_load; trap - EXIT
$2"
}

echo "=== 语义锚点 ==="
ck "mask_readonly 非只读动词:原样"   "$(m mask_readonly 'rm -rf ~/x')" 'rm -rf ~/x'
ck "mask_readonly 只读动词:脱引号参数" "$(m mask_readonly "grep -n 'rm ' ~/.ct/auto/audit.log")" 'grep -n (xxx) ~/.ct/auto/audit.log'
ck "mask_msg 只脱消息类动词"        "$(m mask_msg 'echo "rm -rf /"')" 'echo (xxxxxxxx)'
ck "mask_msg 不脱 cat 的引号参数"    "$(m mask_msg 'cat "rm -rf /"')" 'cat "rm -rf /"'
ck "mask_search_pat 脱模式位"        "$(m mask_search_pat 'grep -n "\.npmrc" wchat_ref')" 'grep -n (xxxxxxx" wchat_ref'
ck "mask_search_pat 不脱尾部操作数"  "$(m mask_search_pat "grep 'x' '.npmrc'")" "grep (x' '.npmrc'"
ck "fold_lines 裸换行前补分号"       "$(m fold_lines $'echo x > /etc/hosts\nls')" $'echo x > /etc/hosts;\nls'
ck "fold_lines 单行原样"             "$(m fold_lines 'hello world')" 'hello world'
ck "split_words 基本分词"            "$(printf '%s' 'cp /tmp/x /etc/passwd' | hpy split_words | jq -cS .)" '{"empty_token":0,"words":["cp","/tmp/x","/etc/passwd"]}'
ck "split_words 引号内空格合并"      "$(printf '%s' 'echo "a b" c' | hpy split_words | jq -cS .)" '{"empty_token":0,"words":["echo","a b","c"]}'
ck "split_words 空引号对标记"        "$(printf '%s' 'a "" b' | hpy split_words | jq -cS .)" '{"empty_token":1,"words":["a","b"]}'
ck "split_words 反斜杠转义"          "$(printf '%s' 'foo\ bar' | hpy split_words | jq -cS .)" '{"empty_token":0,"words":["foo bar"]}'
ck "split_words 命令替换整块"        "$(printf '%s' 'x=$(date) y' | hpy split_words | jq -cS .)" '{"empty_token":0,"words":["x=$(date)","y"]}'
ck "strip_noncode 剥行尾注释"        "$(m strip_noncode 'echo hi # comment')|" 'echo hi |'
ck "strip_noncode 引号内不剥"        "$(m strip_noncode 'echo "a # b"')|" 'echo "a # b"|'
ck "foreign_cred_path 他人家凭据:命中" "$(HOME=/home/alice hpy foreign_cred_path '/home/bob/.aws/credentials' /home/alice .aws .ssh)" "0"
ck "foreign_cred_path 他人家非凭据:不命中" "$(HOME=/home/alice hpy foreign_cred_path '/home/bob/notes.txt' /home/alice .aws .ssh)" "1"
ck "foreign_cred_path 自己家:不命中"  "$(HOME=/Users/alice hpy foreign_cred_path '/Users/alice/.aws/credentials' /Users/alice .aws .ssh)" "1"
ck "h11_dest_hit 命中落点"           "$(hpy h11_dest_hit 'mv /tmp/x /usr/local/bin/y' 'etc|usr|bin')" '/usr/local/bin/y'
ck "h11_dest_hit 非写动词:rc=1"      "$(hpy h11_dest_hit 'echo hello' 'etc|usr|bin' >/dev/null 2>&1; echo $?)" "1"

echo "=== hd_h11_scan 语义(收口后) ==="
H11RE="$(grep '^hd_re_sys_redirect=' "$SRC" | cut -d= -f2- | tr -d "'")"
H11PFX="$(grep '^hd_h11_sys_prefixes=' "$SRC" | cut -d= -f2- | tr -d "'")"
hs() { printf '%b' "$1" | hpy h11_scan "$H11RE" "$H11PFX"; }
ck "无重定向 → 未命中"        "$(hs 'echo hi' | jq -r '.found')" "0"
ck "重定向写系统路径 → 命中"   "$(hs 'echo x > /etc/hosts' | jq -r '.found')" "1"
ck "命中片段含 /etc/"          "$(hs 'echo x > /etc/hosts' | jq -r '.hit' | grep -c '/etc/')" "1"
ck "mv 落点判据命中"           "$(hs 'mv a /usr/bin/b' | jq -r '.hit')" "/usr/bin/b"
ck "多行:行号=2"               "$(hs 'echo a\necho x > /usr/lib/y' | jq -r '.num')" "2"
ck "heredoc 正文不算"          "$(hs 'cat <<EOF\n> /etc/x\nEOF' | jq -r '.found')" "0"
ck "只读行无命中"              "$(hs 'ls /usr/bin' | jq -r '.found')" "0"
ck "命中即止:code 只到该行"    "$(hs 'echo x > /etc/hosts\necho y' | jq -r '.code' | grep -c 'echo y')" "0"
ck "剥注释后不再命中 → exempt" "$(hs 'cp x y # mv a /usr/bin/b' | jq -r '.exempt' | grep -c '剥掉注释后不再命中')" "1"
ck "code_ok 恒 1"              "$(hs 'echo hi' | jq -r '.code_ok')" "1"

echo "=== hd_noncode_payload 语义(收口后) ==="
ncp() { hpy noncode_payload; }        # stdin -> stdout
ncp_eq() {  # <名字> <输入(printf %b)> <期望字节 | __RC1__>
    local n="$1" in="$2" e="$3"
    if [[ "$e" == "__RC1__" ]]; then
        ck "$n" "$(printf '%b' "$in" | ncp >/dev/null 2>&1; echo $?)" "1"
    else
        printf '%b' "$in" | ncp > "$W/o"; printf '%b' "$e" > "$W/e"
        cksame "$n" "$W/o" "$W/e"
    fi
}
ncp_eq "无 heredoc → rc1"            'echo hi' '__RC1__'
ncp_eq "数据型 heredoc 剥正文"        "git commit -F - <<'MSG'\nrm -rf /\nMSG" "git commit -F - <<'MSG'\nMSG\n"
ncp_eq "执行型 heredoc 不剥(sh)"      "sh <<EOF\nrm -rf /\nEOF" '__RC1__'
ncp_eq "cat 数据型剥"                 "cat <<EOF\nhello\nEOF" "cat <<EOF\nEOF\n"
ncp_eq "here-string 不算 heredoc"     'x <<< y' '__RC1__'
ncp_eq "剥后保留后续行"               "cat <<EOF\nfoo\nEOF\necho done" "cat <<EOF\nEOF\necho done\n"
ncp_eq "空正文照剥"                   "cat <<EOF\nEOF" "cat <<EOF\nEOF\n"
ncp_eq "缩进结束符 <<-"               "cat <<-EOF\n\tbody\n\tEOF" "cat <<-EOF\n\tEOF\n"

echo "=== hd_write_targets 语义(收口后;parity 期已由 30 向量 + 全表 569 验证)==="
WTRE="$(grep '^hd_re_write_verb=' "$SRC" | cut -d= -f2- | tr -d "'")"
wtc() { printf '%s' "$1" | hpy write_targets "$WTRE"; }
wt_eq() {  # <名字> <命令> <期望(不含尾换行;空=无输出)>
    local n="$1" c="$2" e="$3"
    wtc "$c" > "$W/o"
    if [[ -z "$e" ]]; then : > "$W/e"; else printf '%s\n' "$e" > "$W/e"; fi
    cksame "$n" "$W/o" "$W/e"
}
wt_eq "cp 取末位操作数"        'cp a /etc/passwd' '/etc/passwd'
wt_eq "重定向目标"             'echo x > /tmp/../var/log/z' '/tmp/../var/log/z'
wt_eq "rm 全部操作数"          'rm -rf /usr/lib/x' '/usr/lib/x'
wt_eq "sed -i 只取真文件名"     'sed -i s/a/b/ ./f.txt' './f.txt'
wt_eq "sed 替换式含 -i 不算写"  "sed 's/a/-i/' f.txt" ''
wt_eq "多段:mv 目标+重定向"     'mv a b && echo hi > out.log' $'b\nout.log'
wt_eq "只读命令无目标"          'ls -la a b c' ''
wt_eq "tar 建档取归档名"        'tar -czf a.tgz ./src' 'a.tgz'
wt_eq "tar 解包只取 -C 目标"    'tar -xf /x/a.tgz -C ./out' './out'
wt_eq "多段分号"               'cp a b; cp c d' $'b\nd'
wt_eq "去重保序"               'cp a a' 'a'
# ⚠ 输出必须以换行结尾(调用方 `< <(hd_write_targets …)` 喂 `while read`,末行无换行会丢)
ck "输出以换行结尾"            "$(wtc 'cp a b' | tail -c1 | od -An -c | tr -d ' ')" '\n'

echo "=== 收口结构锁:旧 bash 回退确已删除(只查函数体) ==="
for f in hd_split_words hd_mask_readonly hd_mask_msg hd_mask_search_pat hd_fold_lines \
         hd_foreign_cred_path hd_strip_noncode hd_h11_dest_hit hd_write_targets \
         hd_noncode_payload hd_h11_scan; do
    ck "$f 定义 1 处"        "$(grep -c "^$f() {" "$SRC")" "1"
    ck "  · 回退注释已删"     "$(fnbody "$f" | grep -c '回退路径')" "0"
    ck "  · 只剩一次 py_run"  "$(fnbody "$f" | grep -c "py_run hd ")" "1"
done

echo "=== 不可用降级(复刻\"函数缺席\"行为,不新增放行面) ==="
ck "mask_readonly off → 原样透传"  "$(hcall 'WCHAT_PY_HD=0' 'printf "%s" "$(hd_mask_readonly "abc def")"')" 'abc def'
ck "mask_msg off → 原样透传"       "$(hcall 'WCHAT_PY_HD=0' 'printf "%s" "$(hd_mask_msg "abc")"')" 'abc'
ck "mask_search_pat off → 原样透传" "$(hcall 'WCHAT_PY_HD=0' 'printf "%s" "$(hd_mask_search_pat "abc")"')" 'abc'
ck "fold_lines off → HD_FOLDED=原串" "$(hcall 'WCHAT_PY_HD=0' 'hd_fold_lines "a
b"; printf "%s" "$HD_FOLDED"')" $'a\nb'
ck "split_words off → 词表空+rc1"  "$(hcall 'WCHAT_PY_HD=0' 'hd_split_words "a b"; rc=$?; printf "N=%s E=%s RC=%s" "${#hd_words[@]}" "$hd_empty_token" "$rc"')" "N=0 E=0 RC=1"
ck "strip_noncode off → HD_STRIP 空+rc1" "$(hcall 'WCHAT_PY_HD=0' 'hd_strip_noncode "echo x # c"; rc=$?; printf "S=[%s] RC=%s" "$HD_STRIP" "$rc"')" "S=[] RC=1"
ck "foreign_cred_path off → rc1"   "$(hcall 'WCHAT_PY_HD=0' 'hd_foreign_cred_path "/home/bob/.ssh"; echo $?')" "1"
ck "h11_dest_hit off → rc1 空"     "$(hcall 'WCHAT_PY_HD=0' 'o=$(hd_h11_dest_hit "mv a /usr/b"); rc=$?; printf "[%s] rc=%s" "$o" "$rc"')" "[] rc=1"
# hd_write_targets 的退化:split_words 不可用必须退空白切分(不能返回空目标=fail-open)
ck "write_targets off → rc1 空" "$(hcall 'WCHAT_PY_HD=0' 'o=$(hd_write_targets "cp a /b"); rc=$?; printf "[%s]rc=%s" "$o" "$rc"')" "[]rc=1"
ck "write_targets off → stderr 明示" "$(hcall 'WCHAT_PY_HD=0' 'hd_write_targets "cp a /b"' 2>&1 >/dev/null | grep -c '组件不可用')" "1"
ck "noncode_payload off → rc1" "$(hcall 'WCHAT_PY_HD=0' 'hd_noncode_payload "echo hi"; echo rc=$?')" "rc=1"
ck "noncode_payload off → 正文不剥(fail-closed)" "$(hcall 'WCHAT_PY_HD=0' 'printf "[%s]" "$HD_PAYLOAD"')" "[]"
ck "h11_scan off → 粗判据仍拦重定向" "$(hcall 'WCHAT_PY_HD=0' 'hd_h11_scan "echo x > /etc/hosts" >/dev/null 2>&1; echo rc=$?')" "rc=0"
ck "h11_scan off → HD_CODE 置原文"   "$(hcall 'WCHAT_PY_HD=0' 'hd_h11_scan "echo hi" >/dev/null 2>&1; printf "%s" "$HD_CODE"')" "echo hi"
ck "h11_scan off → 未命中 rc1"        "$(hcall 'WCHAT_PY_HD=0' 'hd_h11_scan "echo hi" >/dev/null 2>&1; echo rc=$?')" "rc=1"

echo "=== 凭据豁免的边界:WORK_ROOT==HOME 时不许白读全局凭据 ==="
# 语义:`_creds_all_exempt` 在"整台机器都是工作区"时必须失效,否则从家目录启动
# wchat 就等于放行 `cat ~/<cred>`。这条曾被 hdcmd 语料闸门的 WORK_ROOT 继承问题
# 遮挡过(两侧喂了不同的工作根),所以在这里按显式工作根单独钉一次。
pyr() { python3 - "$@" <<'EOP'
import sys
sys.path.insert(0, "%s")
from wchat_py import hardwall
cmd, wr, home = sys.argv[1], sys.argv[2], sys.argv[3]
r, _s = hardwall.rule(cmd, False, work_root=wr, home=home)
print(r or "-")
EOP
}
ck "WORK_ROOT==HOME:拒绝读全局 npmrc" "$(pyr 'cat .npmrc' "$HOME" "$HOME")" "H6"
ck "WORK_ROOT==HOME:拒绝读全局 pypirc" "$(pyr 'cat .pypirc' "$HOME" "$HOME")" "H6"
ck "普通子目录工作根:本项目的 npmrc 不算洞" "$(pyr 'cat .npmrc' "$HOME/proj" "$HOME")" "-"
ck "WORK_ROOT==HOME 也不误伤普通文件" "$(pyr 'cat notes.md' "$HOME" "$HOME")" "-"

finish
