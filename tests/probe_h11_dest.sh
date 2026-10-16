#!/usr/bin/env bash
# H11 写目标提取器的单元级探针(probe_shapes.sh 是端到端,这里只看提取本身)
#
# 为什么要单独一个文件:探针用的形状含 `/etc/passwd`、`> /tmp/../var/log/z` 这类串,
# 写在**命令行**上会被本进程已加载的那版门当场拒掉(H6 认领 /etc/passwd 只按字符串、
# P2 认领重定向落点并已做 .. 解析)。形状待在文件里才是安全的。
#
# 装载:自建沙箱 —— 顶层常量(按名 grep)+ 需要的函数(按名 awk 抽),并把迁移通道接到
# **Python 生产路**(`hd_h11_dest_hit`/`hd_strip_noncode`/`resolve_path` 已收口,旧 bash
# 回退已删;不接通道它们会走降级,探针会假报"提不到目标")。
# 早先版本按 `sed -n '1,95p' test_hardwall.sh` 取表头当 harness —— 表搬进 tests/ 后 TAB
# 路径失效,且行号范围也追不上 harness(抽函数名单 + HW_PY 段早已后移),故改为自建。
set +e
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$HERE/.." && pwd)"
TAB="$HERE/test_hardwall.sh"
[[ -r "$TAB" ]] || { echo "找不到 $TAB"; exit 2; }
SRC_TARGET="$ROOT/wchat.sh"
[[ -r "$SRC_TARGET" ]] || { echo "找不到 $SRC_TARGET"; exit 2; }

SB="$(mktemp "${TMPDIR:-/tmp}/probe_h11_XXXXXX.sh")" || exit 2
trap 'rm -f "$SB"' EXIT
{
    # 顶层常量(同全表做法:跳过右边是命令替换的)
    grep -E '^(hd_[a-z0-9_]*|HD_[A-Z0-9_]*|WCHAT_[A-Z0-9_]*)=' "$SRC_TARGET" | grep -vF '$('
    # 按名抽函数
    for fn in _py_on py_run hd_split_words hd_h11_norm hd_h11_prot hd_h11_dest_hit \
              hd_strip_noncode hd_h11_scan norm_path expand_tilde resolve_path; do
        awk -v f="$fn" '$0 ~ "^"f"\\(\\) \\{" {p=1} p {print} p && /^}$/ {exit}' "$SRC_TARGET"
    done
    # 迁移通道:已收口函数走 Python 生产路
    printf 'PY_HELPER_DIR=%q\n' "$ROOT"
    printf 'WCHAT_PY=1\n'
} > "$SB"
cat >> "$SB" <<'ZZZ'
echo "== 装载 =="
printf 'hd_h11_sys_prefixes=[%s]\n' "${hd_h11_sys_prefixes:-<未装载>}"
for f in hd_h11_norm hd_h11_prot hd_h11_dest_hit hd_h11_scan hd_strip_noncode resolve_path _py_on; do
    printf '  %-18s %s\n' "$f" "$(declare -F "$f" >/dev/null && echo 装载 || echo '<缺失>')"
done
echo "== 单函数 =="
printf '  norm  /tmp/../etc/passwd      -> [%s]\n' "$(hd_h11_norm /tmp/../etc/passwd)"
printf '  norm  /a/b/../c               -> [%s]\n' "$(hd_h11_norm /a/b/../c)"
printf '  prot  /usr/local/bin/y        -> %s\n' "$(hd_h11_prot /usr/local/bin/y && echo YES || echo no)"
printf '  prot  /var/tmp/x              -> %s\n' "$(hd_h11_prot /var/tmp/x && echo YES || echo no)"
printf '  prot  /var/log/app.log        -> %s\n' "$(hd_h11_prot /var/log/app.log && echo YES || echo no)"
printf '  prot  /tmp/../etc/passwd(norm)-> %s\n' "$(hd_h11_prot "$(hd_h11_norm /tmp/../etc/passwd)" && echo YES || echo no)"
printf '  resolve_path /tmp/../etc/hosts -> [%s]\n' "$(resolve_path /tmp/../etc/hosts 2>&1)"
echo "== 提取器(命令 -> 提出的目标)=="
while IFS='|' read -r want cmd; do
    [[ -n "$cmd" ]] || continue
    got="$(hd_h11_dest_hit "$cmd" 2>&1)"
    printf '  %-4s [%-22s] %s\n' "$want" "${got:-无}" "$cmd"
done <<'CASES'
HIT|mv /tmp/x /usr/local/bin/y
HIT|install -m 755 build/cli /usr/local/bin/cli
HIT|truncate -s 0 /var/log/app.log
HIT|dd if=/dev/zero of=/var/db/x bs=1 count=1
HIT|ln -sf /tmp/x /usr/local/bin/node
HIT|mv /etc/hosts /tmp/bak
HIT|sudo cp /tmp/x /usr/bin/y
HIT|echo x > /tmp/../etc/passwd
HIT|cat a > /var/spool/cron/crontabs/root
NP|cp /usr/share/doc/readme.md .
NP|sed -n '1,5p' /etc/hosts
NP|rsync -av ./src /var/tmp/mirror
NP|echo x > "$TMPDIR/out.log"
NP|cp /tmp/x "${HOME}/bin/y"
NP|mv ./local.txt /tmp/keep.txt
NP|cat notes.md > /var/tmp/x
CASES
echo "== scan 端到端(走 hd_h11_scan 的接线)=="
for c in "mv /tmp/x /usr/local/bin/y" "truncate -s 0 /var/log/app.log" "echo x > /tmp/../etc/passwd" "cp /usr/share/doc/readme.md ."; do
    if hd_h11_scan "$c"; then printf '  H11  %-44s HIT=[%s]\n' "$c" "$HD_H11_HIT"
    else printf '  no   %-44s EXEMPT=[%s]\n' "$c" "$HD_H11_EXEMPT"; fi
done
ZZZ
/opt/homebrew/bin/bash "$SB" "$SRC_TARGET" 2>&1 | tail -45

