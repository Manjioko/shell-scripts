#!/usr/bin/env bash
# H11 写目标提取器的单元级探针(probe_shapes.sh 是端到端,这里只看提取本身)
#
# 为什么要单独一个文件:探针用的形状含 `/etc/passwd`、`> /tmp/../var/log/z` 这类串,
# 写在**命令行**上会被本进程已加载的那版门当场拒掉(H6 认领 /etc/passwd 只按字符串、
# P2 认领重定向落点并已做 .. 解析)—— 本轮我自己就撞了两次。形状待在文件里才是安全的,
# 这也是 tests/probe_shapes.sh 存在的同一个理由。
#
# 装载方式与回归表完全一致(取表头 1..95 行的按名抽取 harness),这样测的就是表要测的那份代码。
set +e
cd -- "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || exit 1
TAB=../test_hardwall.sh
[[ -r "$TAB" ]] || { echo "找不到 $TAB"; exit 2; }
# harness 第 54 行用 SRC 推被测脚本绝对路径,而 SRC 取的是**位置参数**,不传就按 $0 所在
# 目录找 wchat.sh —— 本探针把 harness 落到 /tmp,不传参就报"找不到被测脚本: /tmp/wchat.sh"。
SRC_TARGET="$(cd .. && pwd)/wchat.sh"
[[ -r "$SRC_TARGET" ]] || { echo "找不到 $SRC_TARGET"; exit 2; }

sed -n '1,95p' "$TAB" > /tmp/probe_h11_dest_h.sh || exit 2
cat >> /tmp/probe_h11_dest_h.sh <<'ZZZ'
echo "== 装载 =="
printf 'hd_h11_sys_prefixes=[%s]\n' "${HD_H11_SYS_PREFIXES:-<未装载>}"
for f in hd_h11_norm hd_h11_prot hd_h11_dest_hit hd_h11_scan resolve_path path_access; do
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
/opt/homebrew/bin/bash /tmp/probe_h11_dest_h.sh "$SRC_TARGET" 2>&1 | tail -45
rm -f /tmp/probe_h11_dest_h.sh